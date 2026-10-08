# vidsum

**长视频 → 转写 + 分段摘要 + 手机可读报告，一条命令。不需要 ffmpeg。**

给它一个视频链接（CCTV / B站 / YouTube / 1800+ 站点）、一个 CCTV pid，或者一个本地文件：

```bash
vidsum run https://tv.cctv.cn/2024/07/13/VIDETLqINVezlJzyxy6Sc8Nv240713.shtml
vidsum run https://www.bilibili.com/video/BV1xx411c7mD
vidsum run D:\videos\lecture.mp4
```

它会给你一份带时间轴的完整转写稿、一份按章节组织的摘要，和一个在手机上读起来舒服的单文件网页。

---

## 它解决什么

一个两小时的访谈 / 讲座 / 纪录片，想看完得花两小时，想找里面讲了什么更麻烦。现有做法要么把整段音频丢给云端 API（贵、慢），要么下整段视频（几个 G）。

vidsum 走的是这条路：**只取音频 → 本地 GPU 转写 → 按章节摘要 → LLM 只读文本**。转写全程在本机跑，只有摘要那几千个 token 出网。

### 特点

| | |
|---|---|
| **多站点** | CCTV 走专线（只有它给得出官方分段）；B站 / YouTube / 其它 1800+ 站点走 yt-dlp；`.m3u8` / `.mp4` 这类直链直接解，不绕道。 |
| **不需要 ffmpeg** | 音频解码走 PyAV（faster-whisper 自带依赖）。少一个外部二进制，少一类"没装 / 不在 PATH"的故障。 |
| **GPU 转写** | faster-whisper + CTranslate2。实测 RTX 5070 Ti Laptop 上 112 分钟中文音频转写 **5 分钟**。 |
| **按章节摘要** | 优先用视频自带的章节：CCTV 取接口的 `segments`（本例 17 段，毫秒级起止），yt-dlp 取 `chapters`。都没有才按固定时长切。 |
| **进度看板** | 跑长视频时开一个本地网页，四个阶段实时进度 + 日志，不用盯着终端猜。 |
| **手机可读报告** | 输出单文件 HTML：深色模式自适应、字号可调（14–34px）、双指缩放、零外部依赖，断网也能开。 |
| **不编造** | 素材不完整时显式禁止模型推测并标注覆盖率；**完全没有语音内容时直接不调模型**（见下面踩坑手册）。 |

### 实测数据

以《国家宝藏》第四季 20240713（CCTV-3，1:51:59）为例，RTX 5070 Ti Laptop + `small` 模型：

| 阶段 | 耗时 | 备注 |
|---|---|---|
| 音频解码（PyAV 直读 HLS → 16k 单声道 WAV） | 19 分钟 | 约 11× 实时；WAV 215 MB |
| 语音转写（faster-whisper `small`, CUDA float16） | **5 分钟** | 2964 句，约 22× 实时 |
| 分段摘要 + 全片总览（deepseek-chat，17 段） | 40 秒 | |
| 合计 | **约 25 分钟** | 相对 112 分钟的原片 |

---

## 安装

尚未发布到 PyPI，先从源码装：

```bash
git clone https://github.com/ExpertKT/vidsum
cd vidsum
pip install -e .            # 不要 GPU 就这样
pip install -e ".[nvidia]"  # Windows + NVIDIA，要 GPU 再加这个
```

> **Python 版本**：请用 **3.10 – 3.12**。CTranslate2 在 3.13+ 上常常还没有轮子。
> 没把握就用 uv：`uv venv --python 3.12 && uv pip install -e .`

**不需要** `ffmpeg`。`yt-dlp` 会随依赖一起装上；只处理 CCTV 和直链的话用不到它。

---

## 用法

```bash
# 完整跑一遍，开进度看板
vidsum run <视频链接 / CCTV pid / 本地文件>

# 试跑：只处理前 4 分钟，几十秒就能看到结果长什么样
vidsum run <target> --limit-seconds 240

# 不开看板，只在终端看进度
vidsum run <target> --no-dashboard

# 指定输出目录 / 模型 / 设备
vidsum run <target> --out D:\summaries --asr-model large-v3 --device cuda

# 用本地已下载的模型（推荐，不联网）
vidsum run <target> --asr-model D:\models\faster-whisper-small

# 需要登录才能看的视频：借用浏览器的 cookie
vidsum run <target> --cookies-from-browser edge

# 环境里的代理挂了（报"连接被拒绝"）时绕过它
vidsum run <target> --no-proxy

# 环境自检
vidsum doctor
```

### 目标支持

| 形式 | 例子 | 走哪条路 |
|---|---|---|
| CCTV 节目页 | `https://tv.cctv.cn/2024/07/13/VIDETLq...shtml` | CCTV 专线，自动从页面里取 guid，能拿到官方分段 |
| CCTV pid | `ea3b1f6c67b843c58594ca43323f6dd4` | 同上，32 位十六进制 |
| B站 | `https://www.bilibili.com/video/BV1xx411c7mD` | yt-dlp |
| YouTube | `https://www.youtube.com/watch?v=...` | yt-dlp（国内需自备网络） |
| 其它站点 | 微博 / 抖音 / Vimeo / 大量国内站点 | yt-dlp 官方支持列表里 1800+ 站点 |
| 直链媒体 | `https://.../main.m3u8`、`.../a.m4a` | PyAV 直读，不绕 yt-dlp |
| 本地文件 | `D:\videos\talk.mp4` | PyAV |

有 `chapters` 的站点（YouTube 等）会自动拿它当摘要骨架。

**取不到的**：DRM 保护的付费正片（爱奇艺、腾讯视频、优酷会员片）。这类站点 yt-dlp 也无能为力，
只能自己录屏或下载后再把本地路径传进来。

### 输出

```
vidsum-out/<pid 或标题>/
├── source.json       # 解析结果：标题、时长、分段、可用播放地址
├── media.m4a         # 仅通用站点：yt-dlp 下回来的音轨（可复用，重跑不再下）
├── audio_16k.wav     # 16kHz 单声道（可复用，重跑跳过解码）
├── transcript.jsonl  # 句级 + 词级时间戳
├── transcript.md     # 带时间轴的完整转写稿
├── summary.md        # 全片总览 + 分段摘要
└── report.html       # 手机可读报告（单文件、可离线）
```

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 全部完成 |
| 1 | 失败（网络、解析、解码……） |
| 2 | 没有 LLM API key，只出了转写稿，没出摘要 |
| 3 | 没有识别到语音内容（纯音乐 / 无对白），已跳过摘要 |

---

## 环境变量

| 变量 | 必需 | 说明 |
|---|---|---|
| `LLM_API_KEY` | 视情况 | LLM 密钥。默认端点按下面规则配对 |
| `LLM_API_URL` | 否 | 自定义端点 |
| `LLM_MODEL` | 否 | 自定义模型名 |
| `DEEPSEEK_API_KEY` | 否 | 没设 `LLM_API_KEY` 时用这个，自动配 `api.deepseek.com` + `deepseek-chat` |
| `HF_ENDPOINT` | 否 | 国内建议设 `https://hf-mirror.com`，加速模型下载 |
| `HTTP_PROXY` / `HTTPS_PROXY` | 否 | 标准代理变量；**设了就要保证它在运行**，否则所有请求都会失败（见踩坑手册） |

密钥解析顺序：显式参数 → 环境变量 → `~/.dsh/.credentials.yaml`。
**key 与端点是成对配的，不会把 DeepSeek 的 key 发到别家地址上。**

模型别名的下载走 HuggingFace；如果只想离线，把模型目录传给 `--asr-model` 即可，vidsum 会强制 `HF_HUB_OFFLINE=1`。

---

## 踩坑手册

这些都是开发过程中真实撞到并修掉的问题，写下来省得你重踩。

### 所有站点都报「连接被拒绝 / WinError 10061」

先跑 `vidsum doctor`。如果它报出：

```
代理            X http://127.0.0.1:3128 端口连不上！
```

说明环境变量里挂着一个**已经死掉的代理**。这时候错不在目标站点——请求根本没发出去。

vidsum 遇到过一模一样的情况：`HTTPS_PROXY=http://127.0.0.1:3128` 被写进环境，
但那个端口没人监听；直连 B站是 200，走这个代理是 000。

```bash
vidsum run <target> --no-proxy          # 救急
```

或手动清掉（注意：Windows 上如果同名变量有大小写重复项，PowerShell 里 `$env:X=""` 可能改不动，
最可靠的是直接重启终端或用 `--no-proxy`）：

```powershell
$env:HTTPS_PROXY=""; $env:HTTP_PROXY=""
```

### `Library cublas64_12.dll is not found or cannot be loaded`

pip 装的 `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` 把 DLL 放在 `site-packages/nvidia/*/bin`，这个路径不在 `PATH` 里，CTranslate2 找不到。

vidsum 启动时会自动把这些 `bin` 目录挂进 DLL 搜索路径（`vidsum.asr.add_cuda_dll_dirs()`），所以你只要装上 `vidsum[nvidia]` 就行。用 `vidsum doctor` 能看到挂了几个目录。

### `TypeError: open() got an unexpected keyword argument 'metadata_errors'`

faster-whisper 1.2.x 的 `decode_audio()` 调用 `av.open(path, metadata_errors="ignore")`，而 PyAV 19 删掉了这个参数。

vidsum **不走那条路**：自己用标准库 `wave` + numpy 把 WAV 读成 float32 数组再交给 `model.transcribe()`。因此不受 PyAV 版本影响。

### `EOFError: [Errno 541478725] End of file: ...\source.json`

内部 bug，已修。yt-dlp 的下载文件名原本用 `source.%(ext)s`，和 vidsum 自己写的元数据 `source.json` 撞名，
通配时把那个 JSON 当成了音频交给 PyAV。现在下载文件叫 `media.<ext>`，并且会过滤掉 `.part` / `.info.json`。

### HuggingFace 下载报 `httpx.InvalidURL: Invalid port: ':1]'`

某些环境（尤其是被统一管控的桌面应用）会同时注入 `NO_PROXY` 和 `no_proxy`，值里带 `[::1]` 这类条目，httpx 解析时直接崩，请求都发不出去。

vidsum 全程只用标准库 `urllib`，不依赖 httpx。模型也建议用本地目录。

### 摘要编造了视频里没有的内容

两道防线，都是被真实案例打出来的：

1. **只覆盖了一部分素材**（比如 `--limit-seconds` 试跑）—— 实测喂 4 分钟素材却要它写「全片总览」，
   模型会把整期节目脑补成另一套内容。现在覆盖率低于 95% 时会显式注入「严禁推测、材料支撑不了就别写」，
   并在 `summary.md` 里标注实际覆盖率。

2. **完全没有语音内容**（纯音乐 MV、无人声画面）—— 实测转写 0 句时，模型照样写出了一千多字的
   「全片总览」，镜头、服装、乐手全是它从标题和常识里编的。现在检测到 0 句就**直接不调模型**，
   写一份说明并返回退出码 3。

---

## 开发

```bash
git clone https://github.com/ExpertKT/vidsum
cd vidsum
python -m venv .venv && .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

python tests/test_units.py     # 离线单元测试，不需要网络、不需要装 faster-whisper、不加载模型
```

## 许可

MIT
