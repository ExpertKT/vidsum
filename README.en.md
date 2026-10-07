# vidsum

**Long video → transcript + chaptered summary + phone-friendly report, in one command. No ffmpeg required.**

```bash
vidsum run https://tv.cctv.cn/2024/07/13/VIDETLqINVezlJzyxy6Sc8Nv240713.shtml
```

You get a timestamped transcript, a summary organized by the program's own chapter markers, and a
single self-contained HTML report that reads well on a phone.

> 中文说明见 [README.md](README.md)（更详细，含踩坑手册）。

## What it does

Grabs only the audio, transcribes it locally on the GPU, then sends a few thousand tokens of text
(not pixels, not audio) to an LLM for chapter-by-chapter summaries.

| | |
|---|---|
| **No ffmpeg** | Audio decoding uses PyAV, which ships with faster-whisper. One less external binary to install. |
| **GPU transcription** | faster-whisper + CTranslate2. Measured: 112 min of Chinese audio in **5 minutes** on an RTX 5070 Ti Laptop. |
| **Chapter-aware** | CCTV's metadata API already returns official chapter markers; vidsum uses them as the summary skeleton. Falls back to fixed-length windows for local files. |
| **Live dashboard** | A local read-only web page showing four-stage progress while a long video runs. |
| **Phone-friendly report** | Single-file HTML: dark mode, adjustable font size, pinch-zoom, zero external requests, works offline. |
| **No hallucinated content** | When only part of the source is processed, the model is explicitly forbidden from inventing the rest, and the coverage ratio is written into the output. |

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
vidsum run <url|pid|file>            # full pipeline, with dashboard
vidsum run <target> --limit-seconds 240   # quick preview
vidsum run <target> --no-dashboard        # terminal output only
vidsum run <target> --asr-model D:\models\faster-whisper-small
vidsum doctor                        # environment self-check
```

Targets: a CCTV program page URL, a 32-hex CCTV pid, or any local media file.
Other sites: download first, then pass the local path.

Output goes to `vidsum-out/<id>/`: `transcript.jsonl`, `transcript.md`, `summary.md`,
`report.html`, `audio_16k.wav`, `source.json`.

## LLM key

Resolution order: explicit argument → `LLM_API_KEY` env → `~/.dsh/.credentials.yaml`.
`DEEPSEEK_API_KEY` is auto-paired with `api.deepseek.com` + `deepseek-chat`; keys are never sent to
an endpoint they were not chosen for.

## Notes

Two upstream issues are worked around and documented in the Chinese README: CTranslate2 not finding
`cublas64_12.dll` from the pip `nvidia-*-cu12` wheels, and faster-whisper 1.2.x calling
`av.open(..., metadata_errors=...)`, which PyAV 19 removed.

## License

MIT
