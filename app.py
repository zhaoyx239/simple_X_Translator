"""最简离线语音翻译服务。

前端录音 -> /api/translate 一次性跑完 X-ASR -> HY-MT -> X-Voice，
返回 ASR 文本、译文和 TTS 音频(base64 WAV)。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("simple_x_translator")

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"


def load_config() -> dict:
    with (BASE_DIR / "config.json").open("r", encoding="utf-8") as fp:
        return json.load(fp)


CONFIG = load_config()
SAMPLE_RATE = int(CONFIG["audio"]["sample_rate"])
TTS_SAMPLE_RATE = int(CONFIG["audio"]["tts_sample_rate"])

app = FastAPI(title="simple_X_Translator")
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


def _write_temp_wav(pcm_bytes: bytes, sample_rate: int) -> str:
    """把 PCM 写成临时 WAV 文件，返回绝对路径(供 X-Voice 读取)。"""
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="sxt_clone_")
    os.close(fd)
    with open(path, "wb") as fp:
        fp.write(pipeline._pcm16_to_wav(pcm_bytes, sample_rate))
    return os.path.abspath(path)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.post("/api/translate")
async def translate_endpoint(request: Request, target_lang: str = "en") -> JSONResponse:
    """离线链路: 上传 WAV(请求体原始字节) -> ASR -> MT -> TTS。"""
    wav_bytes = await request.body()
    if not wav_bytes:
        raise HTTPException(status_code=400, detail="empty audio")

    source_lang = CONFIG["translation"].get("source_lang", "zh")

    try:
        pcm_bytes, _ = pipeline.wav_bytes_to_pcm16(wav_bytes, SAMPLE_RATE)
    except Exception as exc:  # 损坏的 WAV
        raise HTTPException(status_code=400, detail=f"bad wav: {exc}") from exc

    # 1) X-ASR
    asr_text = await pipeline.asr_recognize(
        pcm_bytes,
        sample_rate=SAMPLE_RATE,
        server_uri=CONFIG["asr"]["xasr_server_uri"],
    )
    logger.info("ASR -> %s", asr_text)

    # 2) HY-MT
    translated_text = ""
    if asr_text:
        translated_text = await asyncio.to_thread(
            pipeline.translate,
            asr_text,
            CONFIG["translation"]["hunyuan_url"],
            CONFIG["translation"].get("model", "hunyuan_mt"),
            source_lang,
            target_lang,
            int(CONFIG["translation"].get("max_tokens", 256)),
        )
    logger.info("MT  -> %s", translated_text)

    # 3) X-Voice
    # X-Voice 需要一段参考音频做声音克隆，直接复用用户本次录音(16k 单声道 WAV)。
    audio_b64 = ""
    if translated_text:
        clone_path = _write_temp_wav(pcm_bytes, SAMPLE_RATE)
        try:
            wav_out = await asyncio.to_thread(
                pipeline.tts_synthesize,
                translated_text,
                CONFIG["tts"]["xvoice_tts_url"],
                target_lang,
                clone_path,
                TTS_SAMPLE_RATE,
            )
            if wav_out:
                audio_b64 = base64.b64encode(wav_out).decode("ascii")
        finally:
            try:
                os.unlink(clone_path)
            except OSError:
                pass
    logger.info("TTS -> %s bytes", len(audio_b64))

    return JSONResponse(
        {
            "asr_text": asr_text,
            "translated_text": translated_text,
            "audio_wav_base64": audio_b64,
        }
    )
