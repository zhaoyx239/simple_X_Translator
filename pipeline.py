"""离线语音翻译链路: X-ASR -> HY-MT -> X-Voice。

仅实现最简单的一次性(离线)调用，不做流式。链路契约与 xtranslate 保持一致:
- X-ASR : websocket，start -> 发送 int16 PCM -> end -> final 文本
- HY-MT : OpenAI chat-completions 兼容接口
- X-Voice: HTTP POST /synthesize，返回 WAV bytes
"""
from __future__ import annotations

import audioop
import io
import json
import wave

import requests
import websockets

# 语言代码到英文名，供翻译 prompt 使用。
LANGUAGE_NAMES = {
    "zh": "Chinese",
    "en": "English",
    "fr": "French",
    "pt": "Portuguese",
    "es": "Spanish",
    "ja": "Japanese",
    "ru": "Russian",
    "ko": "Korean",
    "th": "Thai",
    "it": "Italian",
    "de": "German",
    "vi": "Vietnamese",
    "id": "Indonesian",
}


async def asr_recognize(
    pcm_bytes: bytes,
    sample_rate: int,
    server_uri: str,
    chunk_samples: int = 8000,
    tail_silence_seconds: float = 1.0,
) -> str:
    """把整段 16-bit PCM 喂给 X-ASR，返回最终识别文本。

    X-ASR 按 1 秒为粒度识别，结尾不足 1 秒的部分会被丢掉，因此在末尾补一段
    静音(默认 1 秒)再送，保证最后几个字也能识别出来。
    """
    if not pcm_bytes:
        return ""

    tail_silence_bytes = max(0, int(sample_rate * tail_silence_seconds)) * 2
    if tail_silence_bytes:
        pcm_bytes = pcm_bytes + b"\x00" * tail_silence_bytes

    chunk_bytes = max(2, chunk_samples * 2)
    async with websockets.connect(server_uri, max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "sample_rate": sample_rate}))
        started = json.loads(await ws.recv())
        if started.get("type") != "started":
            raise RuntimeError(f"X-ASR start failed: {started}")

        # 逐块发送，每块都要读取一次 partial 响应避免阻塞。
        for offset in range(0, len(pcm_bytes), chunk_bytes):
            await ws.send(pcm_bytes[offset:offset + chunk_bytes])
            await ws.recv()  # partial，离线模式直接丢弃

        await ws.send(json.dumps({"type": "end"}))
        final = json.loads(await ws.recv())
        return str(final.get("text", "") or "").strip()


def translate(
    text: str,
    api_url: str,
    model: str,
    source_lang: str,
    target_lang: str,
    max_tokens: int = 256,
) -> str:
    """调用 HY-MT 翻译，返回译文。"""
    text = (text or "").strip()
    if not text:
        return ""

    target_name = LANGUAGE_NAMES.get(target_lang, target_lang)
    system_prompt = (
        "You are a real-time speech translator. "
        f"If the input language is already {target_name}, output the input text unchanged. "
        f"Translate the input into natural, fluent {target_name}. "
        "Output translation only. Do not explain."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "top_k": 20,
        "top_p": 0.6,
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }
    response = requests.post(api_url, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    return (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )


def tts_synthesize(
    text: str,
    api_url: str,
    target_lang: str,
    clone_audio_path: str,
    output_sample_rate: int = 48000,
) -> bytes:
    """调用 X-Voice 合成语音，返回标准 WAV bytes(单声道 16-bit)。

    X-Voice 需要一段可读的参考音频(audio_paths)做声音克隆，这里直接复用用户
    本次录音作为参考。clone_audio_path 必须是 X-Voice 服务可访问的本地路径。
    """
    text = (text or "").strip()
    if not text:
        return b""

    payload = {
        "text": text,
        "audio_paths": [clone_audio_path] if clone_audio_path else [],
        "language": target_lang,
    }
    response = requests.post(api_url, json=payload, timeout=90)
    response.raise_for_status()
    pcm = _wav_to_pcm16(response.content, output_sample_rate)
    return _pcm16_to_wav(pcm, output_sample_rate)


def _wav_to_pcm16(wav_bytes: bytes, target_rate: int) -> bytes:
    """把任意 WAV 归一化为单声道 16-bit PCM，并重采样到 target_rate。"""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        src_rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    if channels > 1:
        frames = audioop.tomono(frames, sample_width, 0.5, 0.5)
        channels = 1
    if sample_width != 2:
        frames = audioop.lin2lin(frames, sample_width, 2)
        sample_width = 2
    if src_rate != target_rate:
        frames, _ = audioop.ratecv(frames, 2, 1, src_rate, target_rate, None)
    return frames


def _pcm16_to_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
    """把裸 PCM 包成 WAV bytes，方便前端直接播放。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buffer.getvalue()


def wav_bytes_to_pcm16(wav_bytes: bytes, target_rate: int) -> tuple[bytes, int]:
    """读取前端上传的 WAV，转成单声道 16-bit PCM。返回 (pcm, sample_rate)。"""
    pcm = _wav_to_pcm16(wav_bytes, target_rate)
    return pcm, target_rate
