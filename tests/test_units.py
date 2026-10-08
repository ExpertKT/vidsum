# -*- coding: utf-8 -*-
"""离线单元测试：不联网、不加载模型。直接 ``python tests/test_units.py`` 即可跑。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vidsum import cards, summarize  # noqa: E402
from vidsum.cli import slugify  # noqa: E402
from vidsum.sources import SourceError, build_candidates, extract_guid, parse_vdn  # noqa: E402

PAGE_HTML = """
<html><body>
<script>
  var channel_id = "CCTV3";
  var guid = "ea3b1f6c67b843c58594ca43323f6dd4";
  var guid_Ad_VideoCode = guid;
</script>
</body></html>
"""

VDN_OK = json.dumps({
    "ack": "yes", "status": "001",
    "title": "《国家宝藏》第四季 20240713", "play_channel": "CCTV-3高清",
    "segments": [
        {"guid": "a", "title": "[国家宝藏第四季]走进江淮大地——安徽博物院",
         "start": 121280, "end": 241240},
        {"guid": "b", "title": "[国家宝藏第四季]铸客大鼎 国宝守护人：岳跃利",
         "start": 2358920, "end": 2527880},
        {"bad": True},
    ],
    "hls_url": "https://example.com/main.m3u8?maxbr=2048",
    "manifest": {"audio_mp3": "https://a/mp3.m3u8",
                 "hls_audio_url": "https://a/audio.m3u8"},
    "video": {"totalLength": 6719.40, "chapters": [{"url": ""}]},
}, ensure_ascii=False)

VDN_BAD = json.dumps({"ack": "no", "status": "004", "tip_msg": "该视频不存在，请观看其他视频"},
                     ensure_ascii=False)


def test_extract_guid():
    assert extract_guid(PAGE_HTML) == "ea3b1f6c67b843c58594ca43323f6dd4"
    try:
        extract_guid("<html>nothing here</html>")
    except SourceError:
        pass
    else:
        raise AssertionError("没有 guid 时应该抛 SourceError")


def test_parse_vdn_ok():
    src = parse_vdn(VDN_OK)
    assert src.kind == "cctv"
    assert abs(src.duration_s - 6719.40) < 0.01, src.duration_s
    # 坏的那条段被跳过，剩下 2 条，且已按时间排序
    assert [s["title"] for s in src.segments] == [
        "走进江淮大地——安徽博物院", "铸客大鼎 国宝守护人：岳跃利"]
    assert src.segments[0]["start"] == 121.28     # 毫秒 → 秒
    assert "CCTV-3高清" in src.title
    # 音频优先：hls_audio_url 应排在 audio_mp3 和 hls_url 之前
    assert src.candidates[0] == "https://a/audio.m3u8", src.candidates


def test_candidates_audio_first():
    got = build_candidates({}, {"audio_mp3": "M", "hls_audio_url": "A",
                                "hls_enc_url": "E"}, "H")
    assert got[0] == "A" and got[1] == "M", got
    assert set(got) == {"A", "M", "H", "E"}, got


def test_parse_vdn_rejected():
    try:
        parse_vdn(VDN_BAD)
    except SourceError as exc:
        assert "该视频不存在" in str(exc)
    else:
        raise AssertionError("ack != yes 时应该抛 SourceError")


def test_bucket_and_chapters():
    chs = [{"start": 0, "end": 100, "title": "一"},
           {"start": 100, "end": 200, "title": "二"},
           {"start": 200, "end": 300, "title": "三"}]
    rows = [
        {"start": 10, "end": 20, "text": "a"},     # 一
        {"start": 110, "end": 130, "text": "b"},   # 二
        {"start": 250, "end": 260, "text": "c"},   # 三
        {"start": 305, "end": 310, "text": "d"},   # 尾巴 → 归最后一个
        {"start": 90, "end": 99, "text": "e"},     # 一
    ]
    b = summarize.bucket(rows, chs)
    assert [len(x) for x in b] == [2, 1, 2], [len(x) for x in b]
    assert [r["text"] for r in b[2]] == ["c", "d"]


def test_fixed_chapters():
    chs = summarize.fixed_chapters(1000, window_s=400)
    assert [(c["start"], c["end"]) for c in chs] == [(0, 400), (400, 800), (800, 1000)]
    assert summarize.fixed_chapters(0) == []


def test_hhmmss():
    assert summarize.hhmmss(0) == "00:00:00"
    assert summarize.hhmmss(6719.4) == "01:51:59"
    assert cards._hhmmss(3661) == "01:01:01"


def test_slugify():
    assert slugify("《国家宝藏》第四季 20240713") == "《国家宝藏》第四季-20240713"
    assert slugify("///") == "video"
    assert slugify("a" * 200).__len__() <= 60


def test_llm_config_prefers_explicit_key(monkeypatch_env=None):
    from vidsum.llm import DEEPSEEK_URL, MIMO_URL, load_config
    old = dict(os.environ)
    try:
        os.environ.pop("LLM_API_KEY", None)
        os.environ.pop("DEEPSEEK_API_KEY", None)
        cfg = load_config(key="k", url="", model="")
        assert cfg.ready and cfg.key == "k"
        # 没给 url/model 时应落回 MiMo 默认值，而不是把 key 发到别处
        assert cfg.url == MIMO_URL, cfg.url
        cfg2 = load_config(key="k2", url="https://x/y", model="m")
        assert (cfg2.url, cfg2.model) == ("https://x/y", "m")
    finally:
        os.environ.clear()
        os.environ.update(old)


def test_render_report(tmpdir=None):
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "report.html"
        cards.render_report(
            out, title="测试标题", subtitle="sub", overview="第一段\n第二段",
            chapters=[{"title": "章节一", "span": "00:00:00–00:01:00", "body": "摘要正文"}],
            transcript=[{"start": 1.5, "end": 3.0, "text": "你好世界"}],
            meta={"时长": "1 小时"})
        html = out.read_text(encoding="utf-8")
        assert "测试标题" in html and "章节一" in html and "你好世界" in html
        assert "width=device-width" in html, "必须有 viewport，否则手机上排版会错"
        assert "localStorage" in html, "字号记忆应存在"
        assert "http://" not in html and "https://" not in html, "报告不该有外部依赖"


def test_partial_coverage_guard():
    """只覆盖一部分素材时，必须显式禁止模型脑补，并在产物里标注。

    这是实测踩出来的：喂 4 分钟素材却要它写「全片总览」，模型会把整期节目
    脑补成另一套文物（凭空写出商周礼器、徽商文房、革命志士）。
    """
    from vidsum import summarize as sm

    prompts = []

    def fake_chat(cfg, messages, **kw):
        prompts.append(messages[0]["content"])
        return "摘要正文", {}

    class Cfg:
        model = "fake"
        ready = True

    orig = sm.chat
    sm.chat = fake_chat
    try:
        with tempfile.TemporaryDirectory() as td:
            rows = [{"start": 0, "end": 60, "text": "开场白"}]
            chs = [{"start": 0, "end": 60, "title": "第一段"}]

            partial = sm.summarize(Cfg(), rows, chs, Path(td) / "p.md",
                                   title="测试片", duration_s=3600)
            assert partial["partial"] is True
            assert "严禁推测" in prompts[-1], "局部覆盖时总览 prompt 必须禁止脑补"
            assert "只覆盖了约" in partial["markdown"] or "只覆盖" in partial["markdown"]

            prompts.clear()
            full = sm.summarize(Cfg(), rows, chs, Path(td) / "f.md",
                                title="测试片", duration_s=60)
            assert full["partial"] is False
            assert "严禁推测" not in prompts[-1], "全覆盖时不该加约束"
    finally:
        sm.chat = orig


def test_looks_like_media():
    from vidsum.sources import looks_like_media
    assert looks_like_media("https://x/a/b/main.m3u8")
    assert looks_like_media("https://x/a.mp4?token=1&x=2")
    assert looks_like_media(
        "https://dhlswx.cntv.kfcbest.com/asp/hlsaudio/hls/main/0303000a/3/"
        "default/ea3b1f6c67b843c58594ca43323f6dd4/main.m3u8")
    # 节目页 / 普通页面不能当成媒体直链
    assert not looks_like_media("https://tv.cctv.cn/2024/07/13/VIDETLq.shtml")
    assert not looks_like_media("https://www.bilibili.com/video/BV1xx411c7mD")
    assert not looks_like_media("https://example.com/page")


def test_resolve_routing_no_network():
    """直链媒体应走 direct 分支 —— 这条路径不发任何网络请求。"""
    from vidsum.sources import resolve
    url = "https://cdn.example.com/a/b/lecture.m4a?sign=abc"
    src = resolve(url)
    assert src.kind == "direct", src.kind
    assert src.candidates == [url]
    assert src.title == "lecture.m4a"


def test_chapters_to_segments():
    from vidsum.ytdlp import chapters_to_segments

    info = {"chapters": [{"start_time": 0.0, "end_time": 60.0, "title": "开场"},
                         {"start_time": 60.0, "end_time": 120.0, "title": "正题"}]}
    segs = chapters_to_segments(info)
    assert [s["title"] for s in segs] == ["开场", "正题"]

    # 只有一章 → 当作没有章节，让上层的等长窗口兜底
    assert chapters_to_segments(
        {"chapters": [{"start_time": 0, "end_time": 10, "title": "x"}]}) == []
    assert chapters_to_segments({}) == []

    # 乱序要排好；end 缺失时兜底成 start+1，不能出现 end<=start
    segs2 = chapters_to_segments({"chapters": [
        {"start_time": 100, "end_time": 200, "title": "后"},
        {"start_time": 0, "title": "前"},
    ]})
    assert [s["title"] for s in segs2] == ["前", "后"]
    assert segs2[0]["end"] == 1.0, segs2


def test_ytdlp_module_imports_without_dependency():
    """没装 yt-dlp 时也要能 import（CI 上不装它）——真用到才报错。"""
    from vidsum import ytdlp
    assert isinstance(ytdlp.is_available(), bool)


def test_find_media_ignores_sidecar_files():
    """实测踩过：cli 写的 source.json 被当成音频丢给 PyAV，报 EOFError。"""
    from vidsum.ytdlp import _find_media
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "source.json").write_text("{}", encoding="utf-8")   # 元数据，不是音频
        (d / "media.m4a.part").write_bytes(b"x")
        (d / "media.m4a.info.json").write_text("{}", encoding="utf-8")
        (d / "media.json").write_text("{}", encoding="utf-8")
        assert _find_media(d) == [], _find_media(d)
        (d / "media.m4a").write_bytes(b"\x00" * 64)
        assert [p.name for p in _find_media(d)] == ["media.m4a"]


def test_empty_transcript_skips_llm():
    """纯音乐（0 句转写）时绝不能调模型 —— 实测它会编出一整篇不存在的影评。"""
    from vidsum import summarize as sm

    calls = []

    def fake_chat(cfg, messages, **kw):
        calls.append(messages[0]["content"])
        return "编的", {}

    class Cfg:
        model = "fake"
        ready = True

    orig = sm.chat
    sm.chat = fake_chat
    try:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "s.md"
            res = sm.summarize(Cfg(), [], [], out, title="纯音乐MV", duration_s=212)
            assert res["empty"] is True
            assert calls == [], f"没有素材却调了模型：{calls}"
            assert "未生成摘要" in res["markdown"]

            rows = [{"start": 0, "end": 5, "text": "大家好"}]
            res2 = sm.summarize(Cfg(), rows, [], out, title="讲座", duration_s=300)
            assert not res2.get("empty"), res2.get("empty")
            assert calls, "有内容时应当正常调用模型"
    finally:
        sm.chat = orig


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:                      # noqa: BLE001
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
