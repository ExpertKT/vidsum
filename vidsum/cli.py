"""命令行入口：``vidsum run`` / ``vidsum doctor``。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import __version__

BANNER = f"vidsum {__version__} —— 长视频 → 转写 + 分段摘要 + 手机可读报告"


def slugify(text: str, fallback: str = "video") -> str:
    text = re.sub(r"[\\/:*?\"<>|\s]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return (text[:60] or fallback)


def _wav_seconds(wav: Path) -> float:
    """读 WAV 真实时长（--limit-seconds 试跑时，它比视频总长更准）。"""
    import wave
    try:
        with wave.open(str(wav), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 16000)
    except Exception:                                  # noqa: BLE001
        return 0.0


def cmd_run(args: argparse.Namespace) -> int:
    from . import audio, asr, cards, net, sources, summarize
    from .dashboard import Dashboard
    from .llm import load_config

    if args.no_proxy:
        cleared = net.disable_proxy()
        print("  --no-proxy：" + ("已清掉 " + "，".join(cleared) if cleared
                                  else "环境里本来就没有代理变量"))
    else:
        proxy = net.current_proxy()
        if proxy and net.proxy_reachable(proxy) is False:
            print(f"  [!] 环境里的代理 {proxy} 连不上。若下载失败，"
                  f"试试加 --no-proxy。")

    out_root = Path(args.out).resolve()
    dash = Dashboard(host="127.0.0.1", port=args.port)
    if not args.no_dashboard:
        url = dash.start()
        print(f"\n  [dashboard] 进度看板：{url}\n")
    else:
        dash.update = lambda stage, pct, detail: print(f"[{stage}] {pct:5.1f}%  {detail}",
                                                       flush=True)

    cfg = load_config()
    if cfg.ready:
        print(f"  LLM: {cfg.model}  (key 来源: {cfg.source})")
    else:
        print("  [!] 没有找到 LLM API key，最后一步会失败。"
              "设置 LLM_API_KEY 或 ~/.dsh/.credentials.yaml 里的 DEEPSEEK_API_KEY。")

    rc = 0
    try:
        # ── 1. 解析目标 ──
        dash.update("probe", 5, f"解析目标：{args.target}")
        src = sources.resolve(args.target, timeout=args.timeout,
                              cookies_from_browser=args.cookies_from_browser)
        outdir = Path(args.outdir) if args.outdir else out_root / slugify(
            src.pid or Path(src.title).stem, "video")
        outdir.mkdir(parents=True, exist_ok=True)
        dash.title = src.title
        dash.subtitle = f"{src.kind} · {src.duration_s / 60:.1f} 分钟 · {src.segment_count} 个分段"
        dash.finish("probe", f"{src.title}（{src.duration_s / 60:.1f} 分钟）")
        print(f"\n  标题：{src.title}\n  时长：{src.duration_s / 60:.1f} 分钟"
              f"\n  分段：{src.segment_count}\n  输出：{outdir}\n")

        (outdir / "source.json").write_text(json.dumps({
            "title": src.title, "kind": src.kind, "pid": src.pid,
            "page_url": src.page_url, "duration_s": src.duration_s,
            "segments": src.segments, "candidates": src.candidates,
        }, ensure_ascii=False, indent=1), encoding="utf-8")

        # ── 2. 音频 ──
        wav = outdir / "audio_16k.wav"
        limit = args.limit_seconds or 0
        reuse = not args.no_reuse

        if src.kind == "ytdlp":
            # 通用站点：交给 yt-dlp 把音轨下到本地，再走同一条 PyAV 解码
            from . import ytdlp
            media = ytdlp.download_audio(
                src.page_url, outdir, timeout=args.timeout,
                cookies_from_browser=args.cookies_from_browser,
                on_progress=lambda p, d: dash.update("audio", p, d))
            dash.update("audio", 97.0, f"下载完成：{media.name}，开始解码")
            wav = audio.decode_to_wav(str(media), wav, rate=16000,
                                      on_progress=lambda p, d: dash.update("audio", p, d),
                                      reuse=reuse, limit_s=limit)
        elif src.kind == "cctv":
            # CCTV 常有多个 CDN 变体，逐个降级尝试
            wav, used = audio.decode_first(
                src.candidates, wav, rate=16000,
                on_progress=lambda p, d: dash.update("audio", min(95.0, p) if p else 0.0, d),
                reuse=reuse, limit_s=limit)
            print(f"  音频地址：{used[:100]}")
        else:
            wav = audio.decode_to_wav(src.candidates[0], wav, rate=16000,
                                      on_progress=lambda p, d: dash.update("audio", p, d),
                                      reuse=reuse, limit_s=limit)
        dash.finish("audio", f"{wav.name} · {wav.stat().st_size / 1e6:.0f} MB")

        # ── 3. 转写 ──
        transcript_path = outdir / "transcript.jsonl"
        rows = asr.transcribe(
            wav, transcript_path, model=args.asr_model, device=args.device,
            language=args.language, total_seconds=_wav_seconds(wav),
            on_progress=lambda p, d: dash.update("asr", p, d))
        dash.finish("asr", f"{len(rows)} 句")

        transcript_md = outdir / "transcript.md"
        summarize.write_transcript_md(rows, transcript_md,
                                      asr_label=f"faster-whisper {args.asr_model}")
        print(f"  转写稿：{transcript_md}")

        # ── 4. 摘要 ──
        if not cfg.ready:
            dash.fail("summary", "缺少 LLM API key，跳过摘要")
            print("\n  跳过摘要：没有 LLM API key。转写稿仍然可用。")
            rc = 2
        else:
            chapters = src.segments
            if limit:
                chapters = [c for c in chapters if c["start"] < limit] or []
            result = summarize.summarize(
                cfg, rows, chapters, outdir / "summary.md", title=src.title,
                duration_s=src.duration_s, transcript_path=transcript_path,
                asr_label=f"faster-whisper {args.asr_model}",
                on_progress=lambda p, d: dash.update("summary", p, d))
            if result.get("empty"):
                dash.finish("summary", "没有语音内容，跳过摘要")
                print("\n  [!] 没有识别到语音内容（纯音乐 / 无对白？），已跳过摘要。")
                rc = 3
            else:
                dash.finish("summary", f"{len(result['chapters'])} 段 + 全片总览")

            if not args.no_report:
                cards.render_report(
                    outdir / "report.html", title=src.title,
                    subtitle=src.kind + (" · " + src.pid if src.pid else ""),
                    overview=result["overview"], chapters=result["chapters"],
                    transcript=rows,
                    meta={
                        "时长": f"{result['total_s'] / 3600:.2f} 小时",
                        "转写": f"{len(rows)} 句",
                        "分段": f"{len(result['chapters'])} 段",
                        "ASR": f"faster-whisper {args.asr_model}",
                        "摘要模型": cfg.model,
                    })
                print(f"  手机版报告：{outdir / 'report.html'}")

        print(f"\n  [ok] 完成 → {outdir}\n")
    except KeyboardInterrupt:
        dash.fail("summary", "被用户中断")
        print("\n  已中断。")
        rc = 130
    except Exception as exc:                          # noqa: BLE001
        dash.fail("summary", f"{type(exc).__name__}: {exc}")
        print(f"\n  [x] 失败：{type(exc).__name__}: {exc}\n", file=sys.stderr)
        rc = 1
    finally:
        dash.stop()
    return rc


def cmd_doctor(args: argparse.Namespace) -> int:
    import importlib
    import os

    ok = True
    print(BANNER)
    print("\n环境自检\n" + "-" * 46)

    print(f"  Python          {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info[:2] >= (3, 13):
        print("                  [!] ctranslate2 在 3.13+ 上可能没有轮子，建议 3.12")

    for mod, hint in (("av", "pip install av"),
                      ("faster_whisper", "pip install faster-whisper"),
                      ("numpy", "pip install numpy"),
                      ("yt_dlp", "pip install yt-dlp —— 不装就只能处理 CCTV 和直链")):
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "")
            if not ver:                               # yt-dlp 的版本在 yt_dlp.version 里
                ver = getattr(getattr(m, "version", None), "__version__", "?")
            print(f"  {mod:<15} {ver}")
        except ImportError:
            if mod == "yt_dlp":
                print(f"  {mod:<15} X 缺失 —— {hint}")
            else:
                ok = False
                print(f"  {mod:<15} X 缺失 —— {hint}")

    try:
        from .asr import add_cuda_dll_dirs, pick_device
        dirs = add_cuda_dll_dirs()
        dev = pick_device("auto")
        print(f"  计算设备        {dev}")
        print(f"  CUDA 运行库     {len(dirs)} 个 bin 目录"
              + (f"（例：{os.path.basename(os.path.dirname(os.path.dirname(dirs[0])))}）"
                 if dirs else "  [!] 没找到 nvidia-*-cu12 轮子，GPU 会加载失败"))
        if dev == "cuda" and not dirs:
            ok = False
    except Exception as exc:                          # noqa: BLE001
        print(f"  计算设备        探测失败：{exc}")
        ok = False

    try:
        from .llm import load_config
        cfg = load_config()
        if cfg.ready:
            print(f"  LLM             {cfg.model} @ {cfg.url}")
            print(f"  LLM key 来源    {cfg.source}")
        else:
            ok = False
            print("  LLM             X 没有可用 key（设 LLM_API_KEY 或 ~/.dsh/.credentials.yaml）")
    except Exception as exc:                          # noqa: BLE001
        print(f"  LLM             检查失败：{exc}")
        ok = False

    from . import net
    if args.no_proxy:
        cleared = net.disable_proxy()
        print("  代理            --no-proxy，已清掉：" +
              ("，".join(cleared) if cleared else "（本来就没有）"))
    else:
        proxy = net.current_proxy()
        state = net.proxy_reachable(proxy)
        if state is None:
            print("  代理            未设置（直连）")
        elif state:
            print(f"  代理            {proxy}（端口可达）")
        else:
            ok = False
            print(f"  代理            X {proxy} 端口连不上！")
            print("                  这会让所有联网请求失败。加 --no-proxy 重跑，")
            print("                  或清掉 HTTPS_PROXY / HTTP_PROXY。")

    print("\n  " + ("[ok] 环境就绪" if ok else "[!] 有项目需要处理，见上面提示"))
    print("  提示：不需要 ffmpeg —— 音频解码走 PyAV。\n")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vidsum", description=BANNER,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="跑完整流水线")
    r.add_argument("target", help="视频链接 / CCTV pid / 本地媒体文件")
    r.add_argument("--out", default="./vidsum-out", help="输出根目录（默认 ./vidsum-out）")
    r.add_argument("--outdir", default="", help="直接指定本次输出目录")
    r.add_argument("--asr-model", default="small",
                   help="faster-whisper 模型名或本地模型目录（默认 small）")
    r.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    r.add_argument("--language", default="zh")
    r.add_argument("--cookies-from-browser", default="",
                   help="从浏览器取 cookie，用于需要登录的站点"
                        "（chrome / edge / firefox / chromium…）")
    r.add_argument("--no-proxy", action="store_true",
                   help="忽略环境里的 HTTP(S)_PROXY。环境里的代理挂了时用这个救急")
    r.add_argument("--port", type=int, default=8777, help="进度看板端口")
    r.add_argument("--no-dashboard", action="store_true", help="不开看板，只在终端打印进度")
    r.add_argument("--no-report", action="store_true", help="不生成 report.html")
    r.add_argument("--no-reuse", action="store_true", help="不复用已有 WAV，强制重下")
    r.add_argument("--limit-seconds", type=float, default=0,
                   help="只处理前 N 秒（试跑用）")
    r.add_argument("--timeout", type=int, default=30, help="网络超时（秒）")
    r.set_defaults(func=cmd_run)

    d = sub.add_parser("doctor", help="环境自检")
    d.add_argument("--no-proxy", action="store_true",
                   help="忽略环境里的 HTTP(S)_PROXY")
    d.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认是 GBK(cp936)，直接 print ✅/⚠️ 会抛 UnicodeEncodeError。
    # 就地放宽错误处理：不换编码（换了中文会乱码），只保证不崩。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
