"""目标解析：把「任意视频链接 / CCTV 页 / 本地文件」统一成 VideoSource。

四种来源，按这个顺序判定：

1. **本地文件** —— 直接交给 PyAV。
2. **直链媒体**（``.m3u8`` / ``.mp4`` / ``.m4a`` / ``.mp3`` …）—— 直接交给 PyAV。
3. **CCTV 专线** —— 见下。只有它的接口能给出**官方分段**，是摘要骨架的最佳来源。
4. **其它站点** —— 交给 yt-dlp（1800+ 站点：B站、YouTube 及大量国内站点），
   顺带读它的 ``chapters``，YouTube 这类也能拿到章节骨架。

CCTV 这条路上有两个必须记住的坑，都是实测踩出来的：

1. 地址栏里那段 ``VIDETLqINVezlJzyxy6Sc8Nv240713`` **不是** pid。真正的 guid 藏在
   页面 HTML 的 ``var guid = "..."`` 里（32 位十六进制）。拿地址栏那段去问接口，会得到
   ``{"ack":"no","status":"004","tip_msg":"该视频不存在，请观看其他视频"}``。
2. VDN 接口返回的 ``video.chapters*`` 数组，每个元素的 ``url`` 字段都是空的；
   ``zy.api.cntv.cn`` 和 ``api.cntv.cn`` 两条旁路也都不通（"无效服务" / "拒绝访问"）。
   可用的播放地址只在 ``hls_url`` / ``manifest`` 里。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

VDN_API = "https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do?pid={pid}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

GUID_IN_HTML = re.compile(r'var\s+guid\s*=\s*"([0-9a-fA-F]{32})"')
HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")

# 播放地址候选，按「音频优先、体积最小」排序。
# 注意 manifest.audio_mp3 对部分视频是 404，所以放在最后兜底。
AUDIO_FIRST = ("hls_audio_url", "audio_mp3")

#: 这些后缀的 URL 当直链媒体，直接交给 PyAV，不绕 yt-dlp
MEDIA_EXT = {
    ".m3u8", ".mpd", ".mp4", ".mkv", ".webm", ".flv", ".ts", ".mov", ".avi", ".wmv",
    ".m4a", ".m4v", ".mp3", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma",
}


class SourceError(RuntimeError):
    """目标不可用（链接失效、接口拒绝、guid 找不到等）。"""


def looks_like_media(url: str) -> bool:
    """URL 路径是不是以已知媒体后缀结尾（忽略查询串）。"""
    try:
        path = urlparse(url).path
    except ValueError:
        return False
    dot = path.rfind(".")
    if dot < 0:
        return False
    return path[dot:].lower() in MEDIA_EXT


@dataclass
class VideoSource:
    title: str
    duration_s: float
    #: "local" | "direct" | "cctv" | "ytdlp"
    kind: str
    page_url: str = ""
    pid: str = ""
    #: cctv / direct / local：可直接交给 PyAV 的地址（按优先级排列）。
    #: ytdlp：仅作记录，实际由 ytdlp.download_audio() 下载。
    candidates: list[str] = field(default_factory=list)
    #: 分段 [{start, end, title}]，秒；CCTV 来自官方接口，yt-dlp 来自 chapters
    segments: list[dict] = field(default_factory=list)

    @property
    def segment_count(self) -> int:
        return len(self.segments)


def http_get(url: str, timeout: int = 30) -> bytes:
    from .net import proxy_hint

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise SourceError(f"请求 {url} 返回 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise SourceError(f"请求 {url} 失败：{exc.reason}" + proxy_hint(exc)) from exc


def _text(url: str, timeout: int = 30) -> str:
    """按响应头猜编码；CCTV 页面是 UTF-8，VDN 是 JSON。"""
    raw = http_get(url, timeout)
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def extract_guid(html: str) -> str:
    """从节目页 HTML 里取真正的 pid。"""
    m = GUID_IN_HTML.search(html)
    if not m:
        raise SourceError(
            "页面里找不到 var guid = \"...\"。可能不是 CCTV 节目页，或页面结构变了。"
        )
    return m.group(1)


def build_candidates(video: dict, manifest: dict, hls_url: str) -> list[str]:
    out: list[str] = []
    for key in AUDIO_FIRST:
        url = manifest.get(key)
        if url:
            out.append(url)
    if hls_url:
        out.append(hls_url)
    for key, url in manifest.items():
        if url and url not in out:
            out.append(url)
    return out


def parse_vdn(payload: str) -> VideoSource:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SourceError(f"VDN 返回的不是 JSON: {exc}") from exc

    if data.get("ack") != "yes":
        raise SourceError(
            f"VDN 拒绝了该 pid：status={data.get('status')} tip={data.get('tip_msg')}"
        )

    video = data.get("video") or {}
    manifest = data.get("manifest") or {}
    hls_url = data.get("hls_url") or ""

    segments = []
    for s in data.get("segments") or []:
        try:
            segments.append({
                "start": float(s["start"]) / 1000.0,
                "end": float(s["end"]) / 1000.0,
                "title": str(s.get("title", "")).replace("[国家宝藏第四季]", "").strip(),
            })
        except (KeyError, TypeError, ValueError):
            continue
    segments.sort(key=lambda x: x["start"])

    duration = float(video.get("totalLength") or 0) or (
        segments[-1]["end"] if segments else 0.0
    )
    page = data.get("title") or "CCTV 视频"
    channel = data.get("play_channel") or ""
    title = f"{page}（{channel}）" if channel else page

    candidates = build_candidates(video, manifest, hls_url)
    if not candidates:
        raise SourceError("VDN 没给任何可用的播放地址（hls_url / manifest 都是空的）。")

    return VideoSource(title=title, duration_s=duration, kind="cctv",
                       pid=data.get("_pid", ""), candidates=candidates,
                       segments=segments)


def resolve_cctv(page_url: str, timeout: int = 30) -> VideoSource:
    html = _text(page_url, timeout)
    pid = extract_guid(html)
    vdn = _text(VDN_API.format(pid=pid), timeout)
    src = parse_vdn(vdn)
    src.page_url = page_url
    src.pid = pid
    return src


def resolve_local(path: str) -> VideoSource:
    p = Path(path)
    if not p.exists():
        raise SourceError(f"文件不存在：{p}")
    duration = 0.0
    try:
        import av
        with av.open(str(p)) as c:
            if c.duration:
                duration = c.duration / av.time_base
    except Exception:
        pass
    return VideoSource(title=p.name, duration_s=duration, kind="local",
                       candidates=[str(p)])


def resolve_direct(url: str) -> VideoSource:
    """直链媒体（.m3u8 / .mp4 / .m4a …）：交给 PyAV 直接开，不过 yt-dlp。"""
    name = unquote(Path(urlparse(url).path).name) or "video"
    return VideoSource(title=name, duration_s=0.0, kind="direct",
                       page_url=url, candidates=[url])


def resolve_generic(url: str, timeout: int = 30,
                    cookies_from_browser: str = "") -> VideoSource:
    """其它站点：交给 yt-dlp 解析元数据，顺便捡它的 chapters 当摘要骨架。"""
    from . import ytdlp

    if not ytdlp.is_available():
        raise SourceError(
            "这个链接需要 yt-dlp 才能解析，但没装。\n"
            "请执行：pip install yt-dlp"
        )
    info = ytdlp.probe(url, timeout=timeout,
                       cookies_from_browser=cookies_from_browser)
    return VideoSource(
        title=info["title"],
        duration_s=info["duration_s"],
        kind="ytdlp",
        page_url=info["webpage_url"] or url,
        candidates=[url],
        segments=info["segments"],
    )


def resolve(target: str, timeout: int = 30,
            cookies_from_browser: str = "") -> VideoSource:
    """统一入口。

    target 可以是：本地媒体文件、直链媒体 URL（.m3u8/.mp4/.m4a…）、
    CCTV 节目页 URL、32 位 CCTV pid、或任意 yt-dlp 支持的站点链接。
    """
    if Path(target).exists():
        return resolve_local(target)

    if HEX32.match(target):
        src = parse_vdn(_text(VDN_API.format(pid=target), timeout))
        src.pid = target
        return src

    if not re.match(r"^https?://", target):
        raise SourceError(f"无法识别的目标：{target}")

    # 直链媒体优先：CCTV 的 CDN 主机名里也含 cntv，不能靠域名判断
    if looks_like_media(target):
        return resolve_direct(target)

    host = (urlparse(target).hostname or "").lower()
    if "cctv" in host or "cntv" in host:
        try:
            return resolve_cctv(target, timeout)
        except SourceError:
            # 不是节目页（例如改版、或只是域名像），退回通用解析
            pass

    return resolve_generic(target, timeout, cookies_from_browser)
