// 最简录音 + 离线语音翻译前端。
// 录音用 Web Audio 采集 16kHz 单声道 PCM，结束后打包成 WAV 上传 /api/translate。

const SAMPLE_RATE = 16000;

let audioContext = null;
let mediaStream = null;
let processor = null;
let sourceNode = null;
let pcmChunks = [];

const recordBtn = document.getElementById("recordBtn");
const stopBtn = document.getElementById("stopBtn");
const targetLangSel = document.getElementById("targetLang");
const statusEl = document.getElementById("status");
const asrTextEl = document.getElementById("asrText");
const mtTextEl = document.getElementById("mtText");
const ttsAudioEl = document.getElementById("ttsAudio");

recordBtn.addEventListener("click", startRecording);
stopBtn.addEventListener("click", stopRecording);

function setStatus(text) {
  statusEl.textContent = text;
}

async function startRecording() {
  recordBtn.disabled = true;
  asrTextEl.textContent = "";
  mtTextEl.textContent = "";
  ttsAudioEl.removeAttribute("src");
  pcmChunks = [];

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: false },
    });
  } catch (err) {
    setStatus("无法获取麦克风: " + err.message);
    recordBtn.disabled = false;
    return;
  }

  audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });
  sourceNode = audioContext.createMediaStreamSource(mediaStream);
  processor = audioContext.createScriptProcessor(4096, 1, 1);

  processor.onaudioprocess = (event) => {
    const input = event.inputBuffer.getChannelData(0);
    const pcm = new Int16Array(input.length);
    for (let i = 0; i < input.length; i += 1) {
      const s = Math.max(-1, Math.min(1, input[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    pcmChunks.push(pcm);
  };

  sourceNode.connect(processor);
  processor.connect(audioContext.destination);

  stopBtn.disabled = false;
  setStatus("录音中…");
}

async function stopRecording() {
  stopBtn.disabled = true;

  if (processor) processor.disconnect();
  if (sourceNode) sourceNode.disconnect();
  if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop());
  const ctxRate = audioContext ? audioContext.sampleRate : SAMPLE_RATE;
  if (audioContext) await audioContext.close();
  processor = sourceNode = mediaStream = audioContext = null;

  const wavBlob = encodeWav(mergeChunks(pcmChunks), ctxRate);
  pcmChunks = [];

  if (wavBlob.size <= 44) {
    setStatus("没有录到音频");
    recordBtn.disabled = false;
    return;
  }

  setStatus("处理中：ASR → 翻译 → TTS…");
  const url = "/api/translate?target_lang=" + encodeURIComponent(targetLangSel.value);

  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "audio/wav" },
      body: wavBlob,
    });
    if (!resp.ok) {
      const detail = await resp.text();
      throw new Error(detail || resp.statusText);
    }
    const data = await resp.json();
    asrTextEl.textContent = data.asr_text || "(空)";
    mtTextEl.textContent = data.translated_text || "(空)";
    if (data.audio_wav_base64) {
      ttsAudioEl.src = "data:audio/wav;base64," + data.audio_wav_base64;
      ttsAudioEl.play().catch(() => {});
    }
    setStatus("完成");
  } catch (err) {
    setStatus("失败: " + err.message);
  } finally {
    recordBtn.disabled = false;
  }
}

function mergeChunks(chunks) {
  let total = 0;
  for (const c of chunks) total += c.length;
  const merged = new Int16Array(total);
  let offset = 0;
  for (const c of chunks) {
    merged.set(c, offset);
    offset += c.length;
  }
  return merged;
}

// 把 Int16 PCM 打包成 16-bit 单声道 WAV。
function encodeWav(pcm, sampleRate) {
  const buffer = new ArrayBuffer(44 + pcm.length * 2);
  const view = new DataView(buffer);
  const writeStr = (off, str) => {
    for (let i = 0; i < str.length; i += 1) view.setUint8(off + i, str.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + pcm.length * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, "data");
  view.setUint32(40, pcm.length * 2, true);
  let offset = 44;
  for (let i = 0; i < pcm.length; i += 1, offset += 2) {
    view.setInt16(offset, pcm[i], true);
  }
  return new Blob([view], { type: "audio/wav" });
}
