"""把结果渲染成一个自包含的、手机上好读的 HTML 报告。

设计取舍（都是手机上真会用到的东西）：
- ``viewport`` + 相对单位：手机上按屏宽排版，不用左右滑。
- 顶部吸附的 **字号按钮**：14–34px 可调，选完记住（localStorage）。这比给一张固定
  像素的图靠谱——图在相册里会被整图缩放，字号再大也可能被压小。
- 跟随系统深色模式；零外部依赖，断网也能打开。
"""
from __future__ import annotations

import html
import json
from pathlib import Path


def _esc(x: object) -> str:
    return html.escape(str(x))


def render_report(out_html: Path, *, title: str, subtitle: str = "",
                  overview: str = "", chapters: list[dict] | None = None,
                  transcript: list[dict] | None = None,
                  meta: dict | None = None) -> Path:
    chapters = chapters or []
    transcript = transcript or []
    meta = meta or {}

    chips = "".join(f"<span>{_esc(v)}</span>" for v in meta.values() if v)

    chap_html = "\n".join(
        f'<section class="ch"><h3>{i:02d}. {_esc(c.get("title", ""))}</h3>'
        f'<div class="span">{_esc(c.get("span", ""))}</div>'
        f'<p>{_esc(c.get("body", ""))}</p></section>'
        for i, c in enumerate(chapters, 1)
    )

    tx_html = "\n".join(
        f'<p><span class="t">{_esc(_hhmmss(r["start"]))}</span>{_esc(r.get("text", ""))}</p>'
        for r in transcript if r.get("text")
    )

    page = f"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>{_esc(title)} · 摘要</title>
<style>
:root {{
  --fs: 19px;
  --bg:#fbfbfc; --fg:#1a1a1a; --dim:#6b7280;
  --card:#f1f3f6; --line:#e3e6ea; --accent:#2563eb;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#16181d; --fg:#e8eaee; --dim:#9aa3b2;
           --card:#1e2128; --line:#2c313a; --accent:#79a6ff; }}
}}
* {{ box-sizing:border-box; -webkit-text-size-adjust:100%; }}
body {{ margin:0; background:var(--bg); color:var(--fg);
  font:var(--fs)/1.9 "PingFang SC","Microsoft YaHei","Noto Sans CJK SC",system-ui,sans-serif;
  overflow-wrap:break-word; }}
.bar {{ position:sticky; top:0; z-index:10; display:flex; gap:10px; align-items:center;
  justify-content:space-between; flex-wrap:wrap; padding:8px 12px;
  background:color-mix(in srgb, var(--bg) 88%, transparent);
  backdrop-filter:saturate(180%) blur(12px); border-bottom:1px solid var(--line); }}
.tabs {{ display:flex; gap:6px; flex:1 1 auto; min-width:0; }}
.tabs button {{ flex:1 1 0; min-width:0; font:inherit; font-size:14px; padding:9px 8px;
  border:1px solid var(--line); background:transparent; color:var(--dim);
  border-radius:9px; cursor:pointer; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; }}
.tabs button.on {{ background:var(--fg); color:var(--bg); border-color:var(--fg); font-weight:600; }}
.zoom {{ display:flex; gap:6px; align-items:center; flex:0 0 auto; }}
.zoom button {{ font:inherit; font-size:15px; font-weight:700; width:40px; height:38px;
  border:1px solid var(--line); background:transparent; color:var(--fg);
  border-radius:9px; cursor:pointer; line-height:1; }}
.zoom .val {{ font-size:12px; color:var(--dim); min-width:32px; text-align:center;
  font-variant-numeric:tabular-nums; }}
main {{ max-width:46rem; margin:0 auto; padding:20px 18px 90px; }}
section[hidden] {{ display:none; }}
h1 {{ font-size:1.45em; line-height:1.35; margin:0 0 6px; }}
.lead {{ color:var(--dim); font-size:.82em; margin-bottom:10px; }}
.chips {{ margin-bottom:20px; }}
.chips span {{ display:inline-block; background:var(--card); border-radius:6px;
  padding:2px 9px; margin:0 8px 6px 0; font-size:.74em; color:var(--dim); }}
article p, .ch p {{ margin:0 0 .9em; text-align:justify; }}
h2 {{ font-size:1.05em; margin:30px 0 12px; padding-top:20px; border-top:1px solid var(--line); }}
h2:first-child {{ border-top:0; padding-top:0; margin-top:6px; }}
.ch {{ background:var(--card); border-radius:14px; padding:16px; margin-bottom:14px; }}
.ch h3 {{ font-size:.95em; margin:0 0 4px; line-height:1.5; }}
.ch .span {{ color:var(--accent); font-size:.74em; font-variant-numeric:tabular-nums;
  margin-bottom:10px; font-weight:600; }}
.ch p {{ font-size:.88em; margin:0; }}
.tx p {{ font-size:.86em; margin:0 0 .7em; line-height:1.75; }}
.tx .t {{ color:var(--accent); font-variant-numeric:tabular-nums; font-size:.86em;
  margin-right:9px; font-weight:600; }}
.tip {{ color:var(--dim); font-size:.74em; text-align:center; margin-top:34px;
  padding-top:16px; border-top:1px solid var(--line); }}
</style>
</head>
<body>
<div class="bar">
  <div class="tabs">
    <button data-tab="ov">总览</button>
    <button data-tab="ch">分段摘要</button>
    <button data-tab="tx">完整转写</button>
  </div>
  <div class="zoom">
    <button id="minus" aria-label="缩小字号">A−</button>
    <span class="val" id="fsval">19</span>
    <button id="plus" aria-label="放大字号">A+</button>
  </div>
</div>
<main>
  <section id="tab-ov">
    <h1>{_esc(title)}</h1>
    <div class="lead">{_esc(subtitle)}</div>
    <div class="chips">{chips}</div>
    <article><p>{_esc(overview).replace(chr(10), "</p><p>")}</p></article>
  </section>

  <section id="tab-ch" hidden>
    <h2>分段摘要（{len(chapters)} 段）</h2>
    {chap_html}
  </section>

  <section id="tab-tx" hidden>
    <h2>完整转写（{len(transcript)} 句）</h2>
    <div class="tx">{tx_html}</div>
  </section>

  <div class="tip">字号按钮可调 · 也可双指缩放 · 单文件、可离线打开</div>
</main>
<script>
(function () {{
  var root = document.documentElement;
  var MIN = 14, MAX = 34, STEP = 2;
  var cur = 19;
  try {{ cur = parseInt(localStorage.getItem('vidsum.fs') || '19', 10); }} catch (e) {{}}
  var fsval = document.getElementById('fsval');
  function apply() {{
    cur = Math.min(MAX, Math.max(MIN, cur));
    root.style.setProperty('--fs', cur + 'px');
    fsval.textContent = cur;
    try {{ localStorage.setItem('vidsum.fs', cur); }} catch (e) {{}}
  }}
  document.getElementById('plus').onclick = function () {{ cur += STEP; apply(); }};
  document.getElementById('minus').onclick = function () {{ cur -= STEP; apply(); }};
  apply();

  var tabs = document.querySelectorAll('.tabs button');
  var panes = {{ ov: document.getElementById('tab-ov'),
                 ch: document.getElementById('tab-ch'),
                 tx: document.getElementById('tab-tx') }};
  function show(key) {{
    if (!panes[key]) key = 'ov';
    for (var k in panes) panes[k].hidden = (k !== key);
    tabs.forEach(function (b) {{ b.classList.toggle('on', b.dataset.tab === key); }});
    try {{ localStorage.setItem('vidsum.tab', key); }} catch (e) {{}}
    window.scrollTo(0, 0);
  }}
  tabs.forEach(function (b) {{ b.onclick = function () {{ show(b.dataset.tab); }}; }});
  var q = (location.search.match(/[?&]tab=(ov|ch|tx)/i) || [])[1];
  var saved = 'ov';
  try {{ saved = localStorage.getItem('vidsum.tab') || 'ov'; }} catch (e) {{}}
  show(q ? q.toLowerCase() : saved);
}})();
</script>
</body></html>
"""

    out_html = Path(out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(page, encoding="utf-8")
    return out_html


def _hhmmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def load_transcript(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
