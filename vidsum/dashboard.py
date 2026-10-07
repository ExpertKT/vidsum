"""进度看板：一个只读的本地网页，跑长视频时能看见进行到哪一步。

只用标准库 ``http.server``。看板与流水线在同一进程里，状态放内存，页面每 3 秒拉一次
``/api/state``。
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>vidsum 进度</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0f1115;--card:#171a21;--fg:#e6e9ef;--dim:#8b93a7;--ok:#3ddc97;--run:#4c9aff;--wait:#3a4050}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:15px/1.6 "PingFang SC","Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:26px 18px 60px}
h1{font-size:19px;margin:0 0 4px}
.sub{color:var(--dim);font-size:13px;margin-bottom:20px;word-break:break-all}
.overall{background:var(--card);border-radius:12px;padding:16px 18px;margin-bottom:18px}
.row{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:9px}
.pct{font-size:28px;font-weight:600;font-variant-numeric:tabular-nums}
.bar{height:10px;background:#0b0d12;border-radius:6px;overflow:hidden}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,#4c9aff,#3ddc97);
 transition:width .5s}
.stage{background:var(--card);border-radius:10px;padding:12px 15px;margin-bottom:9px;
 display:flex;gap:13px;align-items:center}
.dot{width:10px;height:10px;border-radius:50%;flex:0 0 auto;background:var(--wait)}
.dot.done{background:var(--ok)}
.dot.run{background:var(--run);box-shadow:0 0 0 4px #4c9aff22;animation:p 1.4s infinite}
@keyframes p{50%{opacity:.35}}
.body{flex:1;min-width:0}
.name{font-weight:600}
.detail{color:var(--dim);font-size:12.5px;word-break:break-all}
.mini{height:4px;background:#0b0d12;border-radius:3px;margin-top:6px;overflow:hidden}
.mini>i{display:block;height:100%;background:var(--run);transition:width .5s}
.p{font-variant-numeric:tabular-nums;color:var(--dim);font-size:13px;flex:0 0 auto}
pre{background:#0b0d12;border-radius:10px;padding:13px;overflow:auto;max-height:320px;
 font-size:12px;line-height:1.55;color:#b9c2d4;margin:16px 0 0}
</style></head>
<body><div class="wrap">
<h1 id="t">vidsum</h1><div class="sub" id="s"></div>
<div class="overall"><div class="row"><span id="lbl" style="color:var(--dim)"></span>
<span class="pct" id="pct">0%</span></div>
<div class="bar"><i id="obar" style="width:0"></i></div></div>
<div id="stages"></div><pre id="log">-</pre></div>
<script>
const $=id=>document.getElementById(id);
const esc=x=>String(x).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function tick(){
  let d; try{ d=await (await fetch('/api/state',{cache:'no-store'})).json(); }
  catch(e){ $('log').textContent='连接已断开'; return; }
  $('t').textContent=d.title; $('s').textContent=d.subtitle+' · '+d.server_time;
  $('pct').textContent=d.overall_pct.toFixed(1)+'%';
  $('obar').style.width=d.overall_pct+'%';
  $('lbl').textContent=d.message||'处理中';
  $('stages').innerHTML=d.stages.map(s=>{
    const cls=s.done?'done':(s.pct>0?'run':'');
    return `<div class="stage"><span class="dot ${cls}"></span><div class="body">
      <div class="name">${esc(s.name)}</div><div class="detail">${esc(s.detail)}</div>
      <div class="mini"><i style="width:${s.pct}%"></i></div></div>
      <span class="p">${s.done?'✅':s.pct.toFixed(0)+'%'}</span></div>`;}).join('');
  $('log').textContent=d.log.length?d.log.join('\\n'):'（暂无日志）';
}
tick(); setInterval(tick,3000);
</script></body></html>
"""

STAGES = [
    ("probe", "解析目标"),
    ("audio", "获取音频"),
    ("asr", "语音转写"),
    ("summary", "分段摘要"),
]


class Dashboard:
    def __init__(self, title: str = "", subtitle: str = "",
                 host: str = "127.0.0.1", port: int = 8777):
        self.title = title or "vidsum"
        self.subtitle = subtitle
        self.host, self.port = host, port
        self.pct = {k: 0.0 for k, _ in STAGES}
        self.detail = {k: "等待中" for k, _ in STAGES}
        self.done = {k: False for k, _ in STAGES}
        self.message = ""
        self.log: list[str] = []
        self._httpd = None
        self._thread = None

    # ── 生命周期 ──
    def start(self) -> str:
        dash = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):                       # noqa: N802
                path = self.path.split("?")[0]
                if path in ("/", "/index.html"):
                    body = PAGE.encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif path == "/api/state":
                    body = json.dumps(dash.state(), ensure_ascii=False).encode("utf-8")
                    ctype = "application/json; charset=utf-8"
                else:
                    body, ctype = b"not found", "text/plain; charset=utf-8"
                self.send_response(200 if ctype.startswith("text/html")
                                   or "json" in ctype else 404)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return f"http://{self.host}:{self.port}/"

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    # ── 进度上报 ──
    def update(self, stage: str, pct: float, detail: str) -> None:
        if stage in self.pct:
            self.pct[stage] = max(0.0, min(100.0, pct))
            self.detail[stage] = detail
            self.done[stage] = pct >= 100
        self.message = detail
        self.log.append(f"[{time.strftime('%H:%M:%S')}] {detail}")
        self.log = self.log[-60:]
        print(f"[{stage}] {pct:5.1f}%  {detail}", flush=True)

    def finish(self, stage: str, detail: str = "完成") -> None:
        self.update(stage, 100.0, detail)

    def fail(self, stage: str, detail: str) -> None:
        self.message = detail
        self.detail[stage] = detail
        self.log.append(f"[{time.strftime('%H:%M:%S')}] ✗ {detail}")
        print(f"[{stage}] ✗ {detail}", flush=True)

    def state(self) -> dict:
        stages = [{"key": k, "name": name, "pct": self.pct[k],
                   "detail": self.detail[k], "done": self.done[k]}
                  for k, name in STAGES]
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "stages": stages,
            "overall_pct": sum(s["pct"] for s in stages) / len(stages),
            "message": self.message,
            "server_time": time.strftime("%H:%M:%S"),
            "log": self.log,
        }
