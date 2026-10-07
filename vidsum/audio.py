"""音频获取：用 PyAV 直接把 HLS / 本地媒体解成 16kHz 单声道 WAV。

**为什么不用 ffmpeg**：实测 PyAV（faster-whisper 的依赖，装好就有）能直接打开 CCTV 的
``.m3u8``，按音轨解码并重采样，整条链路不需要外部二进制。省掉一个安装步骤，也省掉
"ffmpeg 没装 / 装了但不在 PATH" 这一类问题。
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Callable, Iterable

Progress = Callable[[float, str], None]


class AudioError(RuntimeError):
    pass


def decode_to_wav(media: str, wav: Path, rate: int = 16000,
                  on_progress: Progress | None = None,
                  reuse: bool = True, limit_s: float = 0.0) -> Path:
    """把单个媒体地址解成 16k 单声道 WAV。

    ``limit_s`` > 0 时只解前 N 秒（试跑用），达到就停，不继续拉后面的分片。
    已存在且非空则直接复用（``reuse=False`` 强制重解）。
    """
    import av

    wav = Path(wav)
    if reuse and limit_s <= 0 and wav.exists() and wav.stat().st_size > 1024:
        if on_progress:
            on_progress(100.0, f"复用已有 WAV（{wav.stat().st_size / 1e6:.0f} MB）")
        return wav

    wav.parent.mkdir(parents=True, exist_ok=True)
    tmp = wav.with_suffix(".part")
    if tmp.exists():
        tmp.unlink()

    if on_progress:
        on_progress(0.0, f"PyAV 打开：{media[:90]}")

    container = av.open(media, timeout=(30.0, 120.0))
    try:
        if not container.streams.audio:
            raise AudioError("该地址里没有音轨。")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        total_bytes = 0
        frames = 0
        cap_bytes = int(limit_s * rate * 2) if limit_s > 0 else 0
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            for frame in container.decode(audio=0):
                for res in resampler.resample(frame):
                    w.writeframes(res.to_ndarray().tobytes())
                    total_bytes += res.samples * 2
                frames += 1
                if frames % 2000 == 0 and on_progress:
                    on_progress(0.0, f"已解码 {total_bytes / 2 / rate / 60:.1f} 分钟音频")
                if cap_bytes and total_bytes >= cap_bytes:
                    break
            for res in resampler.resample(None):
                w.writeframes(res.to_ndarray().tobytes())
                total_bytes += res.samples * 2
    finally:
        container.close()

    if total_bytes == 0:
        tmp.unlink(missing_ok=True)
        raise AudioError("解出来 0 字节音频。")

    tmp.replace(wav)
    minutes = total_bytes / 2 / rate / 60
    if on_progress:
        on_progress(100.0, f"WAV 就绪：{minutes:.1f} 分钟 / {wav.stat().st_size / 1e6:.0f} MB")
    return wav


def decode_first(candidates: Iterable[str], wav: Path, rate: int = 16000,
                 on_progress: Progress | None = None,
                 reuse: bool = True, limit_s: float = 0.0) -> tuple[Path, str]:
    """按优先级依次尝试候选地址，返回 (wav 路径, 实际用成功的地址)。"""
    errors = []
    for url in candidates:
        try:
            return decode_to_wav(url, wav, rate=rate, on_progress=on_progress,
                                 reuse=reuse, limit_s=limit_s), url
        except Exception as exc:                      # noqa: BLE001 - 逐个降级
            errors.append(f"  - {url[:80]}: {type(exc).__name__}: {exc}")
            if on_progress:
                on_progress(0.0, f"地址不可用，试下一个（{type(exc).__name__}）")
    raise AudioError("所有播放地址都拿不到音频：\n" + "\n".join(errors))
