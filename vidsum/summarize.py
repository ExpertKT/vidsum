"""分段摘要：把转写稿切成章节，逐章摘要，再合成全片总览。

章节优先用视频自带的官方分段（CCTV 的 ``segments``）；没有（例如本地文件）就按时长
切成等长窗口，保证长视频不会一次性塞爆上下文。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from .llm import LLMConfig, chat

Progress = Callable[[float, str], None]

DEFAULT_WINDOW_S = 600          # 没有官方分段时，每 10 分钟一章
CHAPTER_TOKENS = 1600
OVERVIEW_TOKENS = 2400


def load_transcript(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def fixed_chapters(total_s: float, window_s: int = DEFAULT_WINDOW_S) -> list[dict]:
    out, t = [], 0.0
    n = 1
    while t < total_s:
        end = min(t + window_s, total_s)
        out.append({"start": t, "end": end, "title": f"第 {n} 段"})
        t = end
        n += 1
    return out


def bucket(transcript: list[dict], chapters: list[dict]) -> list[list[dict]]:
    """按时间中点把每句归入章节；落在空档里的归给前一个章节。"""
    buckets: list[list[dict]] = [[] for _ in chapters]
    for row in transcript:
        mid = (row["start"] + row["end"]) / 2.0
        idx = None
        for i, c in enumerate(chapters):
            if c["start"] <= mid <= c["end"]:
                idx = i
                break
        if idx is None:
            for i, c in enumerate(chapters):
                if c["start"] <= mid:
                    idx = i
        buckets[0 if idx is None else idx].append(row)
    return buckets


def hhmmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def clean(text: str) -> str:
    return re.sub(r"\s+\n", "\n", text).strip()


def summarize_chapter(cfg: LLMConfig, title: str, span: str, body: str,
                      context: str = "") -> tuple[str, dict]:
    prompt = (
        f"以下是节目《{context}》中一个段落的语音转写稿。\n"
        f"段落标题：{title}\n时间：{span}\n\n"
        f"转写稿：\n{body}\n\n"
        "请用中文写 150-300 字的内容摘要。要点：这一段讲了什么、涉及哪些人物与知识点、"
        "有哪些值得记住的细节或原话。只输出摘要正文，不要任何客套话。"
    )
    return chat(cfg, [{"role": "user", "content": prompt}], max_tokens=CHAPTER_TOKENS)


def summarize_overview(cfg: LLMConfig, title: str, digest: str,
                       scope_note: str = "") -> tuple[str, dict]:
    prompt = (
        f"下面是《{title}》的分段摘要。请写一份 500-800 字的全片总览，"
        "覆盖主线、关键人物与事件、以及全片想传达的核心内容。只输出正文。\n"
        + (scope_note + "\n" if scope_note else "")
        + "\n" + digest
    )
    return chat(cfg, [{"role": "user", "content": prompt}], max_tokens=OVERVIEW_TOKENS)


def _empty_result(out_md: Path, title: str, total: float,
                  transcript_path: Path | None, asr_label: str,
                  on_progress: Progress | None) -> dict:
    """没有任何语音内容时，**不要**调模型。

    实测：把一段纯音乐 MV 喂进去，转写是 0 句，模型照样写出一千多字的「全片总览」——
    镜头、风衣、键盘手、伴舞，全是从歌名和常识里编的。没有素材就不该产生摘要。
    """
    note = "（没有识别到任何语音内容——可能是纯音乐、纯环境声，或者音轨有问题。）"
    lines = [
        f"# {title or '视频'} — 未生成摘要",
        "",
        f"- 时长：{hhmmss(total)}（约 {total / 60:.0f} 分钟）",
        "- 转写句数：0",
        f"- ASR：{asr_label}",
    ]
    if transcript_path:
        lines.append(f"- 完整转写稿：`{Path(transcript_path).name}`")
    lines += [
        "",
        "## 说明",
        "",
        note,
        "",
        "vidsum 不会在没有任何素材的情况下调用模型——那样只会得到一段听起来很合理"
        "但完全是编造的摘要。",
    ]
    text = "\n".join(lines)
    Path(out_md).write_text(text, encoding="utf-8")
    if on_progress:
        on_progress(100, "没有语音内容，已跳过摘要")
    return {"overview": note, "chapters": [], "markdown": text,
            "total_s": total, "partial": False, "covered_s": 0.0, "empty": True}


def summarize(cfg: LLMConfig, transcript: list[dict], chapters: list[dict],
              out_md: Path, title: str = "", duration_s: float = 0.0,
              transcript_path: Path | None = None,
              asr_label: str = "faster-whisper",
              on_progress: Progress | None = None) -> dict:
    """返回 {overview, chapters:[{title,span,body}], markdown, partial, empty}"""
    total = duration_s or (transcript[-1]["end"] if transcript else 0.0)

    spoken = [r for r in transcript if (r.get("text") or "").strip()]
    if not spoken:
        return _empty_result(out_md, title, total, transcript_path, asr_label, on_progress)

    if not chapters:
        chapters = fixed_chapters(total)
    buckets = bucket(transcript, chapters)

    parts = []
    for i, (ch, rows) in enumerate(zip(chapters, buckets), 1):
        body = "\n".join(r["text"] for r in rows if r.get("text"))
        span = f"{hhmmss(ch['start'])}–{hhmmss(ch['end'])}"
        label = ch["title"] or f"第 {i} 段"
        if not body.strip():
            parts.append({"title": label, "span": span, "body": "（此段无语音转写内容）"})
            continue
        if on_progress:
            on_progress((i - 0.5) / len(chapters) * 90,
                        f"分段摘要 {i}/{len(chapters)}：{label[:24]}")
        text, _usage = summarize_chapter(cfg, label, span, body, context=title or "本视频")
        parts.append({"title": label, "span": span, "body": clean(text)})
        if on_progress:
            on_progress(i / len(chapters) * 90, f"完成 {i}/{len(chapters)} 段")

    digest = "\n\n".join(
        f"【{i:02d}】{p['title']}（{p['span']}）\n{p['body']}"
        for i, p in enumerate(parts, 1)
    )

    # 只覆盖了一部分时，必须告诉模型「别编」。实测：喂 4 分钟素材却要它写「全片总览」，
    # 它会把整期节目脑补成另一套文物。这里显式约束，并把覆盖率写进产物。
    covered = sum(max(0.0, min(c["end"], total) - c["start"]) for c in chapters) if total else 0.0
    partial = bool(total) and covered < total * 0.95
    scope_note = ""
    if partial:
        scope_note = (
            f"注意：以上材料只覆盖全片约 {covered / 60:.0f} 分钟（全片约 {total / 60:.0f} 分钟），"
            "属于不完整材料。严禁推测、补足或虚构材料里没有出现的人物、文物、情节和结论；"
            "材料支撑不了的地方就不要写，宁可短。"
        )
        if on_progress:
            on_progress(92, f"注意：仅覆盖 {covered / 60:.0f}/{total / 60:.0f} 分钟，已启用防脑补约束")

    if on_progress:
        on_progress(93, "生成全片总览…")
    overview, _ = summarize_overview(cfg, title or "本视频", digest, scope_note)

    lines = [
        f"# {title or '视频摘要'} — 全片摘要",
        "",
        f"- 时长：{hhmmss(total)}（约 {total / 60:.0f} 分钟）",
        f"- 转写句数：{len(transcript)}",
        f"- 分段：{len(parts)} 段",
        f"- ASR：{asr_label} · 摘要模型：{cfg.model}",
    ]
    if partial:
        lines.append(
            f"- **注意：只覆盖了约 {covered / 60:.0f} 分钟（全片 {total / 60:.0f} 分钟），"
            "摘要是局部的，不是全片结论。**"
        )
    if transcript_path:
        lines.append(f"- 完整转写稿：`{transcript_path.name}`")
    lines += ["", "## 全片总览", "", clean(overview), "", "## 分段摘要", ""]
    for i, p in enumerate(parts, 1):
        lines += [f"### {i:02d}. {p['title']}", "", f"`{p['span']}`", "", p["body"], ""]

    out_md = Path(out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines), encoding="utf-8")

    if on_progress:
        on_progress(100, f"摘要写好：{out_md.name}")

    return {"overview": clean(overview), "chapters": parts,
            "markdown": "\n".join(lines), "total_s": total,
            "partial": partial, "covered_s": covered}


def write_transcript_md(transcript: list[dict], path: Path,
                        asr_label: str = "faster-whisper") -> None:
    lines = [f"# 完整转写稿（{asr_label}）", ""]
    for row in transcript:
        if row.get("text"):
            lines.append(f"`{hhmmss(row['start'])}` {row['text']}")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
