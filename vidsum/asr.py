"""语音转写：faster-whisper（GPU 优先），带两个必须的兼容处理。

1. **CUDA 运行库路径**。pip 装的 ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12`` 把
   DLL 放在 ``site-packages/nvidia/*/bin``，不在 PATH 上。ctranslate2 于是抛
   ``RuntimeError: Library cublas64_12.dll is not found or cannot be loaded``。
   解决：把那些 bin 目录挂进 DLL 搜索路径（见 :func:`add_cuda_dll_dirs`）。

2. **绕开 faster-whisper 自带的 decode_audio**。它调用
   ``av.open(path, mode="r", metadata_errors="ignore")``，而 PyAV 19 已删掉
   ``metadata_errors`` 参数 → ``TypeError``。我们不传路径，改为用标准库 ``wave`` +
   numpy 自己读出 float32 数组再交给 ``transcribe``，因此**不受 av 版本影响**。
"""
from __future__ import annotations

import json
import os
import site
import wave
from pathlib import Path
from typing import Callable

Progress = Callable[[float, str], None]

#: 传给 faster-whisper 的默认模型（也可给本地目录）
DEFAULT_MODEL = "small"


def add_cuda_dll_dirs() -> list[str]:
    """把 pip 装的 nvidia-*-cu12 轮子里的 bin 目录挂进 DLL 搜索路径。"""
    added: list[str] = []
    roots: list[str] = []
    try:
        roots.extend(site.getsitepackages())
    except Exception:
        pass
    try:
        roots.append(site.getusersitepackages())
    except Exception:
        pass

    for root in roots:
        base = os.path.join(root, "nvidia")
        if not os.path.isdir(base):
            continue
        for sub in sorted(os.listdir(base)):
            bin_dir = os.path.join(base, sub, "bin")
            if not os.path.isdir(bin_dir):
                continue
            try:
                os.add_dll_directory(bin_dir)      # Windows only
            except (AttributeError, OSError):
                pass
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
            added.append(bin_dir)
    return added


def read_wav_float32(path: Path, target_rate: int = 16000):
    """读 16-bit 单声道 WAV → float32 ndarray（-1..1）。"""
    import numpy as np

    with wave.open(str(path), "rb") as w:
        if w.getframerate() != target_rate or w.getsampwidth() != 2:
            raise ValueError(
                f"WAV 必须是 {target_rate}Hz / 16-bit，实际 "
                f"{w.getframerate()}Hz / {w.getsampwidth() * 8}-bit"
            )
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype("float32") / 32768.0


def pick_device(requested: str = "auto") -> str:
    if requested != "auto":
        return requested
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def load_model(model: str = DEFAULT_MODEL, device: str = "auto",
               on_progress: Progress | None = None):
    """加载模型。

    ``model`` 可以是模型名（``small`` / ``large-v3``…，走 HuggingFace 下载），
    也可以是**本地模型目录**（推荐：不联网、不受代理与限速影响）。

    传目录时会强制 ``HF_HUB_OFFLINE=1``，避免 huggingface_hub 去联网；传模型名时
    保持联网以便下载。
    """
    is_local = Path(str(model)).is_dir()
    if is_local:
        os.environ["HF_HUB_OFFLINE"] = "1"

    dirs = add_cuda_dll_dirs()
    if dirs and on_progress:
        on_progress(0.0, f"CUDA 运行库路径 ×{len(dirs)}")

    dev = pick_device(device)
    compute = "float16" if dev == "cuda" else "int8"
    if on_progress:
        on_progress(0.0, f"加载模型 {model}（device={dev}, compute={compute}）")

    from faster_whisper import WhisperModel
    try:
        return WhisperModel(model, device=dev, compute_type=compute), dev
    except Exception as exc:                          # noqa: BLE001
        if dev != "cuda":
            raise
        if on_progress:
            on_progress(0.0, f"CUDA 加载失败（{exc}），回退 CPU int8")
        return WhisperModel(model, device="cpu", compute_type="int8"), "cpu"


def transcribe(wav: Path, out_jsonl: Path, model: str = DEFAULT_MODEL,
               device: str = "auto", language: str = "zh",
               total_seconds: float = 0.0,
               on_progress: Progress | None = None) -> list[dict]:
    """转写并写 JSONL（每行 {start,end,text,words}）。返回段落列表。"""
    m, dev = load_model(model, device, on_progress)
    audio = read_wav_float32(wav)

    if on_progress:
        on_progress(1.0, f"开始转写（{len(audio) / 16000 / 60:.1f} 分钟，device={dev}）")

    segments, info = m.transcribe(audio, language=language,
                                  word_timestamps=True, vad_filter=True)

    out_jsonl = Path(out_jsonl)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    total = total_seconds or (len(audio) / 16000)
    rows: list[dict] = []
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for seg in segments:
            row = {
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
                "words": [[w.word, round(w.start, 2), round(w.end, 2)]
                          for w in (seg.words or [])],
            }
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if on_progress and len(rows) % 25 == 0:
                pct = min(99.0, row["end"] / total * 100) if total else 0.0
                on_progress(pct, f"{len(rows)} 段 · 覆盖 {row['end'] / 60:.1f} / "
                                 f"{total / 60:.1f} 分钟")

    if on_progress:
        on_progress(100.0, f"转写完成：{len(rows)} 段")
    return rows
