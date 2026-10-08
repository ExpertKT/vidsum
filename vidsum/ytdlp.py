"""通用站点支持：借 yt-dlp 处理 B站 / YouTube / 以及其它 1800+ 站点。

分工原则：
- **CCTV 走专线**（:mod:`vidsum.sources`）。只有它的接口能给官方分段，
  yt-dlp 拿不到。
- **其它站点走 yt-dlp**。顺带也读它的 ``chapters`` —— YouTube 等站点的章节
  信息同样能当摘要骨架，比按时长硬切好。

下载时**刻意不用任何 postprocessor**：只挑单个音频格式直接落盘，因此不需要 ffmpeg。
选到 ``best``（视频容器）也没关系，后面 PyAV 只解音轨。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

Progress = Callable[[float, str], None]

#: 只挑单格式，绝不加 "+"（那会触发 yt-dlp 调 ffmpeg 合并）
AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best"

#: 下载文件名。**不要**用 "source"：cli 会在同一个目录写 source.json 记录元数据，
#: 用 source.* 通配会把那个 JSON 当成音频文件交给 PyAV（实测报 EOFError）。
OUTTMPL = "media.%(ext)s"

#: yt-dlp 会留下的边角文件，不能当成媒体
_JUNK_SUFFIX = {".part", ".ytdl", ".json", ".txt", ".temp", ".description", ".srt", ".vtt"}


def _find_media(outdir: Path) -> list[Path]:
    """找出下载目录里真正的媒体文件（排除 .part / .info.json 之类）。"""
    found = []
    for p in outdir.glob("media.*"):
        if not p.is_file():
            continue
        if p.name.endswith(".info.json") or p.suffix.lower() in _JUNK_SUFFIX:
            continue
        if p.stat().st_size <= 0:
            continue
        found.append(p)
    return sorted(found)


class YtdlpError(RuntimeError):
    pass


def is_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def _import():
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:                        # pragma: no cover
        raise YtdlpError(
            "没装 yt-dlp。请执行：pip install yt-dlp"
        ) from exc
    return YoutubeDL


def _base_opts(timeout: int = 30, cookies_from_browser: str = "") -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": timeout,
        "retries": 3,
        "extractor_retries": 2,
    }
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    return opts


def _first_entry(info: dict) -> dict:
    """播放列表 / 多 P 时只取第一条。"""
    if info.get("_type") == "playlist":
        for entry in info.get("entries") or []:
            if entry:
                return entry
    return info


def chapters_to_segments(info: dict) -> list[dict]:
    """把 yt-dlp 的 chapters 转成 vidsum 的分段结构；不可用时返回 []。"""
    raw = info.get("chapters") or []
    out: list[dict] = []
    for ch in raw:
        try:
            start = float(ch.get("start_time") or 0.0)
            end = float(ch.get("end_time") or 0.0)
        except (TypeError, ValueError):
            continue
        title = str(ch.get("title") or "").strip()
        if end <= start:
            end = start + 1.0
        out.append({"start": start, "end": end, "title": title})
    out.sort(key=lambda x: x["start"])
    # 少于 2 章、或时间明显不可信时，当作没有章节
    if len(out) < 2:
        return []
    return out


def probe(url: str, timeout: int = 30,
          cookies_from_browser: str = "") -> dict:
    """只取元数据，不下载。返回 {title, duration_s, segments, uploader, extractor}。"""
    YoutubeDL = _import()
    opts = _base_opts(timeout, cookies_from_browser)
    opts["skip_download"] = True
    try:
        with YoutubeDL(opts) as ydl:
            info = _first_entry(ydl.extract_info(url, download=False) or {})
    except Exception as exc:                          # noqa: BLE001
        from .net import proxy_hint
        raise YtdlpError(
            f"yt-dlp 解析失败：{type(exc).__name__}: {exc}" + proxy_hint(exc)
        ) from exc

    if not info:
        raise YtdlpError("yt-dlp 没返回任何视频信息。")

    title = info.get("title") or info.get("id") or "视频"
    uploader = info.get("uploader") or info.get("channel") or ""
    return {
        "title": f"{title}（{uploader}）" if uploader else title,
        "duration_s": float(info.get("duration") or 0.0),
        "segments": chapters_to_segments(info),
        "webpage_url": info.get("webpage_url") or url,
        "extractor": info.get("extractor_key") or info.get("extractor") or "yt-dlp",
        "id": str(info.get("id") or ""),
    }


def download_audio(url: str, outdir: Path, timeout: int = 30,
                   cookies_from_browser: str = "",
                   on_progress: Progress | None = None) -> Path:
    """把音轨下到 ``outdir/media.<ext>``，返回文件路径。

    文件名刻意不叫 ``source`` —— cli 会在同一目录写 ``source.json`` 记录元数据，
    用 ``source.*`` 通配会把那个 JSON 当成音频交给 PyAV（实测报
    ``EOFError: [Errno 541478725] End of file``）。

    目录里已有下载好的媒体时直接复用（重跑不用再下）。
    """
    YoutubeDL = _import()
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    existing = _find_media(outdir)
    if existing:
        if on_progress:
            on_progress(100.0, f"复用已下载的 {existing[0].name}")
        return existing[0]

    def hook(d: dict) -> None:
        if not on_progress:
            return
        status = d.get("status")
        if status == "downloading":
            got = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed = d.get("speed") or 0
            if total:
                mb = f"{got / 1e6:.0f}/{total / 1e6:.0f} MB"
            else:
                mb = f"{got / 1e6:.0f} MB"
            spd = f" · {speed / 1e6:.1f} MB/s" if speed else ""
            on_progress(min(95.0, got / total * 100) if total else 0.0,
                        f"下载音轨 {mb}{spd}")
        elif status == "finished":
            on_progress(97.0, "下载完成，准备解码")

    opts = _base_opts(timeout, cookies_from_browser)
    opts.update({
        "format": AUDIO_FORMAT,
        "outtmpl": str(outdir / OUTTMPL),
        # 关键：不要后处理，否则 yt-dlp 会去找 ffmpeg
        "postprocessors": [],
        "progress_hooks": [hook],
    })
    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:                          # noqa: BLE001
        from .net import proxy_hint
        raise YtdlpError(
            f"yt-dlp 下载失败：{type(exc).__name__}: {exc}" + proxy_hint(exc)
        ) from exc

    files = _find_media(outdir)
    if not files:
        raise YtdlpError("yt-dlp 跑完了，但没找到下载下来的媒体文件。")
    return files[0]
