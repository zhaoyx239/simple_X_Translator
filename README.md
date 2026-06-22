# Simple X-Translator

这是 `xtranslate` 的**极简离线版**，只做一件事：**你说一句话，它帮你翻译成另一种语言，并用语音念出来。**

整个过程分三步，串成一条链路：

```
你的录音  ──►  X-ASR  ──►  HY-MT  ──►  X-Voice  ──►  翻译文本 + 翻译语音
（中文语音）   语音转文字    机器翻译     文字转语音
```

- **X-ASR**：把语音变成文字（ASR = Automatic Speech Recognition，自动语音识别）
- **HY-MT**：把识别出来的文字翻译成目标语言（MT = Machine Translation，机器翻译）
- **X-Voice**：把翻译后的文字合成成语音（TTS = Text To Speech，文字转语音）

---
## 一、先理解整体结构

这个项目本身**只是一个网页 + 一个调度服务**，真正干活的三个 AI 模型
（X-ASR / HY-MT / X-Voice）是跑在别处的**独立服务**，我们只是去调用它们。

```
simple_X_Translator/
├── config.json        # 配置文件：端口、三个 AI 服务的地址
├── main.py            # 程序入口，启动网页服务
├── app.py            # 后端核心：收到录音后，依次调用 ASR→MT→TTS
├── pipeline.py        # 三个 AI 服务的「客户端」，封装了怎么调用它们
├── frontend/
│   ├── index.html     # 网页界面（录音按钮、结果显示）
│   └── main.js        # 网页逻辑：录音、打包成 WAV、发给后端、播放结果
├── requirements.txt   # 本项目需要的 Python 依赖
├── start.sh           # 启动脚本
└── README.md          # 介绍文档
```

数据是怎么流动的（建议对照代码看一遍，能快速理解全貌）：

1. 浏览器用麦克风录音，结束时把音频打包成 WAV 文件（`frontend/main.js`）
2. WAV 通过 HTTP 发给后端的 `/api/translate` 接口（`app.py`）
3. 后端把音频送给 X-ASR 得到文字 → 送给 HY-MT 得到译文 → 送给 X-Voice 得到语音（`pipeline.py`）
4. 后端把「识别文字 + 译文 + 语音」打包成 JSON 返回给浏览器
5. 浏览器把结果显示出来，并自动播放翻译语音

---

## 二、运行前的准备：先启动三个 AI 服务

这些地址都写在 `config.json` 里，如果服务跑在别的机器或端口，改这里即可。

---

## 三、安装本项目的环境

新建环境只需运行如下命令：

```bash
cd /root/shared-nvme/yuxiangzhao/simple_X_Translator

# 1. 创建虚拟环境（venv 是 Python 自带的环境隔离工具，避免污染系统）
python3 -m venv .venv

# 2. 激活环境（激活后命令行前面会出现 (.venv) 字样）
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt
```

依赖很少，只有 4 个：

- `fastapi` + `uvicorn`：搭建网页后端服务
- `requests`：调用 HY-MT、X-Voice 这两个 HTTP 服务
- `websockets`：调用 X-ASR 这个 websocket 服务

---

## 四、启动并使用

确认三个 AI 服务都已运行后，启动本项目：

```bash
cd /root/shared-nvme/yuxiangzhao/simple_X_Translator
source .venv/bin/activate
python main.py
```

看到类似 `Uvicorn running on http://0.0.0.0:7655` 就说明启动成功了。

然后用浏览器打开：

```
http://<服务器IP>:7655
```

（如果就在本机浏览器访问，用 `http://localhost:7655`）

**使用步骤：**

1. 在「输出语言」下拉框选择要翻译成的语言（默认 English）
2. 点 **开始录音**，对着麦克风说一句中文
3. 点 **结束录音**
4. 等几秒，页面会依次显示：**ASR 识别结果** → **翻译结果**，并自动播放 **TTS 语音**

> 💡 浏览器使用麦克风需要安全环境。用 `localhost` 访问没问题；
> 如果用 IP 访问且浏览器不让录音，通常是因为不是 HTTPS，换成在本机用 `localhost` 即可。

---

## 五、config.json 配置说明

```json
{
  "server": {
    "host": "0.0.0.0",   // 监听地址，0.0.0.0 表示允许外部访问
    "port": 7655          // 网页服务端口（浏览器访问的就是这个）
  },
  "audio": {
    "sample_rate": 16000,      // 送给 ASR 的采样率，X-ASR 要求 16000
    "tts_sample_rate": 48000   // TTS 输出音频的采样率
  },
  "asr": {
    "xasr_server_uri": "ws://127.0.0.1:8765"   // X-ASR 服务地址
  },
  "translation": {
    "hunyuan_url": "http://localhost:8000/v1/chat/completions",  // HY-MT 地址
    "model": "hunyuan_mt",     // 翻译模型名
    "max_tokens": 256,         // 译文最大长度
    "source_lang": "zh",       // 源语言（默认中文）
    "target_lang": "en"        // 默认目标语言（网页下拉框会覆盖它）
  },
  "tts": {
    "xvoice_tts_url": "http://localhost:11998/synthesize"   // X-Voice 地址
  }
}
```

---

## 六、两个值得注意的小设计

1. **末尾补 1 秒静音**（`pipeline.py` 的 `asr_recognize`）
   X-ASR 是按「每 1 秒」为单位识别的，录音结尾不足 1 秒的部分会被丢掉、漏字。
   所以送去识别前，我们在音频末尾补了 1 秒静音，把最后几个字「推」进一个完整的识别窗口里。

2. **TTS 需要一段参考音频**（`app.py` + `pipeline.py` 的 `tts_synthesize`）
   X-Voice 是「克隆音色」的语音合成，请求里的 `audio_paths` 必须给一段它能读到的参考音频，
   否则会报 `400 Bad Request`。本项目直接**复用你自己这次的录音**作为参考音色，
   做法是把录音临时存成一个 WAV 文件、把路径传给 X-Voice，调用完再删掉临时文件。