"""目标解析：把「CCTV 节目页 / 32 位 pid / 本地媒体文件」统一成 VideoSource。

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

VDN_API = "https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do?pid={pid}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

GUID_IN_HTML = re.compile(r'var\s+guid\s*=\s*"([0-9a-fA-F]{32})"')
HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")

# 播放地址候选，按「音频优先、体积最小」排序。
# 注意 manifest.audio_mp3 对部分视频是 404，所以放在最后兜底。
AUDIO_FIRST = ("hls_audio_url", "audio_mp3")


class SourceError(RuntimeError):
    """目标不可用（链接失效、接口拒绝、guid 找不到等）。"""


@dataclass
class VideoSource:
    title: str
    duration_s: float
    kind: str                                  # "cctv" | "local"
    page_url: str = ""
    pid: str = ""
    # 播放地址候选（HLS/媒体 URL 或本地路径），按优先级排列
    candidates: list[str] = field(default_factory=list)
    # 官方分段 [{start, end, title}]，秒；本地文件为空
    segments: list[dict] = field(default_factory=list)

    @property
    def segment_count(self) -> int:
        return len(self.segments)


def http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


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


def resolve(target: str, timeout: int = 30) -> VideoSource:
    """target 可以是 CCTV 节目页 URL、32 位 pid，或本地媒体文件。"""
    if Path(target).exists():
        return resolve_local(target)

    if HEX32.match(target):
        src = parse_vdn(_text(VDN_API.format(pid=target), timeout))
        src.pid = target
        return src

    if re.match(r"^https?://", target):
        if "cctv" in target:
            return resolve_cctv(target, timeout)
        # 其他站点暂不解析页内 guid，交给调用方
        raise SourceError(
            "vidsum 目前只解析 CCTV 节目页、32 位 pid 和本地文件。\n"
            "其他站点请先自行下载，再把本地文件路径传进来。"
        )

    raise SourceError(f"无法识别的目标：{target}")
