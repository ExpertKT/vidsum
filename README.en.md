# vidsum

**Long video → transcript + chaptered summary + phone-friendly report, in one command. No ffmpeg required.**

```bash
vidsum run https://tv.cctv.cn/2024/07/13/VIDETLqINVezlJzyxy6Sc8Nv240713.shtml
vidsum run https://www.bilibili.com/video/BV1xx411c7mD
vidsum run D:\videos\lecture.mp4
```

You get a timestamped transcript, a summary organized by the video's own chapter markers, and a
single self-contained HTML report that reads well on a phone.

> 中文说明见 [README.md](README.md)（更详细，含踩坑手册）。

## What it does

Grabs only the audio, transcribes it locally on the GPU, then sends a few thousand tokens of text
(not pixels, not audio) to an LLM for chapter-by-chapter summaries.

| | |
|---|---|
| **Many sites** | CCTV has a dedicated path (it is the only source exposing official chapter markers); Bilibili, YouTube and 1800+ other sites go through yt-dlp; direct `.m3u8` / `.mp4` URLs are decoded without the detour. |
| **No ffmpeg** | Audio decoding uses PyAV, which ships with faster-whisper. One less external binary to install. |
| **GPU transcription** | faster-whisper + CTranslate2. Measured: 112 min of Chinese audio in **5 minutes** on an RTX 5070 Ti Laptop. |
| **Chapter-aware** | Uses the video's own chapters when available: CCTV API `segments`, or yt-dlp `chapters`. Falls back to fixed-length windows. |
| **Live dashboard** | A local read-only web page showing four-stage progress while a long video runs. |
| **Phone-friendly report** | Single-file HTML: dark mode, adjustable font size, pinch-zoom, zero external requests, works offline. |
| **Does not invent** | Incomplete material explicitly forbids the model from guessing, with the coverage ratio written into the output. With **no speech at all**, the model is not called (see exit code 3). |

## Install

Not on PyPI yet — install from source:

```bash
git clone https://github.com/ExpertKT/vidsum
cd vidsum
pip install -e .            # CPU-only
pip install -e ".[nvidia]"  # Windows + NVIDIA, for GPU
```

Requires **Python 3.10 – 3.12** (CTranslate2 often has no wheels for 3.13+ yet). No ffmpeg needed.

## Usage

```bash
vidsum run <url|pid|file>                 # full pipeline, with dashboard
vidsum run <target> --limit-seconds 240   # quick preview
vidsum run <target> --no-dashboard        # terminal output only
vidsum run <target> --asr-model D:\models\faster-whisper-small
vidsum run <target> --cookies-from-browser edge   # for login-walled sites
vidsum run <target> --no-proxy            # when the env proxy is dead
vidsum doctor                             # environment self-check
```

### Supported targets

| Form | Example | Path |
|---|---|---|
| CCTV program page | `https://tv.cctv.cn/2024/07/13/VIDETLq...shtml` | dedicated path, official segments |
| CCTV pid | `ea3b1f6c67b843c58594ca43323f6dd4` | same |
| Bilibili | `https://www.bilibili.com/video/BV1xx411c7mD` | yt-dlp |
| YouTube | `https://www.youtube.com/watch?v=...` | yt-dlp |
| Other sites | Weibo / Douyin / Vimeo / 1800+ more | yt-dlp |
| Direct media | `https://.../main.m3u8`, `.../a.m4a` | PyAV |
| Local file | `D:\videos\talk.mp4` | PyAV |

DRM-protected paid content (iQIYI, Tencent Video, Youku VIP) is not accessible — download or record
it yourself, then pass the local path.

Output goes to `vidsum-out/<id>/`: `transcript.jsonl`, `transcript.md`, `summary.md`,
`report.html`, `audio_16k.wav`, `media.m4a` (yt-dlp sources), `source.json`.

### Exit codes

`0` all done · `1` failure · `2` no LLM key, transcript only · `3` no speech detected, summary skipped.

## LLM key

Resolution order: explicit argument → `LLM_API_KEY` env → `~/.dsh/.credentials.yaml`.
`DEEPSEEK_API_KEY` is auto-paired with `api.deepseek.com` + `deepseek-chat`; keys are never sent to
an endpoint they were not chosen for.

## Notes

Upstream issues worked around, all documented in the Chinese README:

- CTranslate2 cannot find `cublas64_12.dll` from the pip `nvidia-*-cu12` wheels — vidsum adds those
  `bin` directories to the DLL search path automatically.
- faster-whisper 1.2.x calls `av.open(..., metadata_errors=...)`, which PyAV 19 removed — vidsum
  reads the WAV itself with `wave` + numpy and never hits that code path.
- A **dead proxy** in `HTTP(S)_PROXY` makes every request fail with "connection refused" while the
  error blames the target site. `vidsum doctor` detects it; `--no-proxy` bypasses it.

## License

MIT
