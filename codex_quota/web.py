"""内置 Web 服务：在手机上查看额度。

- 数据源是 HUD 的 StateStore 快照（get_views 回调），Web 端零额外查询开销
- 鉴权：随机 token 藏在 URL 路径里（/t/<token>/…），无 token 一律 404
- 仅监听局域网；外出访问请走 Tailscale 等私有组网
- 纯标准库（http.server），不引入新依赖
"""

from __future__ import annotations

import ipaddress
import json
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from .i18n import tr
from .state import ProviderView

DEFAULT_PORT = 8642
MAX_PORT_ATTEMPTS = 20

_BENCHMARK_NET = ipaddress.ip_network("198.18.0.0/15")
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
_RFC1918_NETS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)

# 与 HUD 一致的三档阈值（按剩余量）
THRESHOLDS = {"warn": 30, "crit": 10}


def generate_token() -> str:
    return secrets.token_urlsafe(16)


def _usable_lan_ipv4(value: str) -> bool:
    """是否可作为手机访问地址。

    198.18.0.0/15 常被代理/TUN 软件用作虚拟默认路由；它和 loopback、
    link-local 等地址都不应出现在发给手机的 LAN URL 里。
    100.64.0.0/10（CGNAT）放行：Tailscale 等 overlay 网络里手机与电脑
    正是通过这个段互访，过滤掉会让 URL 退化成 127.0.0.1。
    """
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not isinstance(ip, ipaddress.IPv4Address) or ip in _BENCHMARK_NET:
        return False
    if ip.is_unspecified or ip.is_loopback or ip.is_link_local or ip.is_multicast:
        return False
    if ip in _CGNAT_NET:
        return True
    # 手机同网段通常是 RFC1918；也允许真实公网 IPv4 直连的少数网络。
    return any(ip in network for network in _RFC1918_NETS) or ip.is_global


def _parse_windows_default_routes(output: str) -> list[str]:
    """从 ``route print -4`` 提取默认路由的接口 IPv4，按 metric 排序。"""
    routes: list[tuple[int, str]] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[:2] != ["0.0.0.0", "0.0.0.0"]:
            continue
        interface = fields[3]
        try:
            metric = int(fields[4])
        except ValueError:
            continue
        if _usable_lan_ipv4(interface):
            routes.append((metric, interface))
    return [interface for _, interface in sorted(routes)]


def _windows_default_route_ips() -> list[str]:
    """Windows 上列出真实默认路由；失败时由 lan_ip() 的 socket 路径兜底。"""
    if sys.platform != "win32":
        return []
    try:
        from .proc import hidden_console_kwargs, run_external

        # errors="replace"：Windows 开"Beta: UTF-8 全球语言支持"时 route.exe 仍
        # 输出 OEM 代码页（GBK），硬解码会抛 UnicodeDecodeError 一路炸进 Qt 槽
        completed = run_external(
            ["route", "print", "-4"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=3,
            **hidden_console_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return _parse_windows_default_routes(completed.stdout) if completed.returncode == 0 else []


def lan_ip() -> str:
    """返回适合手机同局域网访问的本机 IPv4。

    Windows 代理/TUN 软件可能把 UDP 默认路由指到 198.18/15；先从路由表
    过滤这类地址并选下一条真实默认路由，再回退到 socket 探测/主机地址。
    """
    candidates = _windows_default_route_ips()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))  # 只选路由，不真正发包
            candidates.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        candidates.extend(
            item[4][0]
            for item in socket.getaddrinfo(
                socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM
            )
        )
    except OSError:
        pass
    return next((ip for ip in candidates if _usable_lan_ipv4(ip)), "127.0.0.1")


def views_to_payload(views: list[ProviderView]) -> dict[str, Any]:
    """把 provider 视图序列化为 Web 端 JSON。"""
    providers = []
    for v in views:
        st = v.state
        entry: dict[str, Any] = {
            "name": v.name,
            "display_name": v.display_name,
            "stale": st.stale,
            "error": st.error,
            "fetched_at": st.fetched_at,
            "plan": None,
            "windows": [],
        }
        snap = st.snapshot
        if snap is not None:
            entry["plan"] = snap.plan_type
            for limit in snap.limits:
                bucket = None if limit is snap.primary_limit else (limit.limit_name or limit.limit_id)
                for w in (limit.primary, limit.secondary):
                    if w is None:
                        continue
                    entry["windows"].append({
                        "bucket": bucket,
                        "label": w.label,
                        "remaining": w.remaining_percent,
                        "reset_at": w.reset_at,
                        "abs_text": w.abs_text,      # 余额型（无百分比时用）
                        "abs_level": w.abs_level,
                    })
        providers.append(entry)
    return {"server_time": time.time(), "providers": providers}


_PAGE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0d1117">
<title>{title}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; margin: 0; }}
  body {{ background: #0d1117; color: #e6edf3; font-family: system-ui, sans-serif;
         padding: 16px; max-width: 480px; margin: 0 auto; }}
  h1 {{ font-size: 18px; margin-bottom: 4px; }}
  .sub {{ color: #8b949e; font-size: 12px; margin-bottom: 16px; }}
  .provider {{ background: #161b22; border: 1px solid #30363d; border-radius: 12px;
              padding: 14px; margin-bottom: 12px; }}
  .phead {{ font-weight: 600; font-size: 14px; margin-bottom: 10px; }}
  .dot {{ font-size: 10px; vertical-align: 1px; }}
  .plan {{ color: #8b949e; font-weight: 400; font-size: 12px; }}
  .bucket {{ color: #8b949e; font-size: 11px; margin: 8px 0 4px; }}
  .wrow {{ margin: 10px 0; }}
  .wtop {{ display: flex; align-items: center; gap: 8px; }}
  .wlabel {{ font-weight: 600; font-size: 13px; width: 44px; flex: none; }}
  .track {{ flex: 1; height: 10px; background: #30363d; border-radius: 5px; overflow: hidden; }}
  .fill {{ height: 100%; border-radius: 5px; transition: width .4s; }}
  .pct {{ font-size: 13px; font-weight: 600; width: 64px; text-align: right; flex: none; }}
  .cd {{ color: #8b949e; font-size: 11px; margin-top: 3px; padding-left: 52px; }}
  .err {{ color: #d29922; font-size: 12px; word-break: break-all; }}
  .stale {{ color: #d29922; font-size: 11px; }}
  footer {{ color: #8b949e; font-size: 11px; text-align: center; margin-top: 14px; }}
  button {{ background: #21262d; color: #e6edf3; border: 1px solid #30363d;
           border-radius: 8px; padding: 6px 14px; font-size: 13px; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="sub">{subtitle}</div>
<div id="app"><p class="sub">{loading}</p></div>
<footer><span id="fresh"></span> &nbsp; <button onclick="load()">{refresh}</button></footer>
<script>
const LANG = {lang_js};
const T = {{
  zh: {{left: "剩", reset_unknown: "重置时间未知", resetting: "即将重置",
       d: "天", h: "小时", m: "分后重置", suffix: "后重置",
       ago_s: "秒前", ago_m: "分钟前", ago_h: "小时前", updated: "更新于 ",
       nodata: "无数据", stale: "数据陈旧"}},
  en: {{left: "", reset_unknown: "reset unknown", resetting: "resetting soon",
       d: "d", h: "h", m: "m", suffix: "",
       ago_s: "s ago", ago_m: "m ago", ago_h: "h ago", updated: "updated ",
       nodata: "no data", stale: "stale"}}
}}[LANG];
const PCOLORS = {{codex: "#3fb950", kimi: "#a371f7"}};
function color(rem) {{
  if (rem == null) return "#8b949e";
  return rem <= {crit} ? "#f85149" : rem <= {warn} ? "#d29922" : "#3fb950";
}}
function absColor(level) {{
  return {{crit: "#f85149", warn: "#d29922", ok: "#3fb950"}}[level] || "#8b949e";
}}
function pctText(rem) {{
  if (rem == null) return "?";
  return LANG === "zh" ? "剩 " + Math.round(rem) + "%" : Math.round(rem) + "% left";
}}
function cd(resetAt) {{
  if (!resetAt) return T.reset_unknown;
  let s = Math.floor(resetAt - Date.now() / 1000);
  if (s <= 0) return T.resetting;
  const d = Math.floor(s / 86400); s %= 86400;
  const h = Math.floor(s / 3600); const m = Math.floor(s % 3600 / 60);
  const date = new Date(resetAt * 1000);
  const mm = String(date.getMonth()+1).padStart(2,"0"), dd = String(date.getDate()).padStart(2,"0");
  const hm = String(date.getHours()).padStart(2,"0") + ":" + String(date.getMinutes()).padStart(2,"0");
  let t;
  if (LANG === "zh") {{
    t = d ? `${{d}} 天 ${{h}} 小时后重置` : h ? `${{h}} 小时 ${{m}} 分后重置` : `${{m}} 分后重置`;
    return t + `（${{mm}}-${{dd}} ${{hm}}）`;
  }}
  t = d ? `resets in ${{d}}d ${{h}}h` : h ? `resets in ${{h}}h ${{m}}m` : `resets in ${{m}}m`;
  return t + ` (${{mm}}-${{dd}} ${{hm}})`;
}}
function ago(ts) {{
  if (!ts) return T.nodata;
  const a = Math.max(0, Date.now()/1000 - ts);
  if (a < 60) return Math.floor(a) + T.ago_s;
  if (a < 3600) return Math.floor(a/60) + T.ago_m;
  return Math.floor(a/3600) + T.ago_h;
}}
function render(data) {{
  const app = document.getElementById("app");
  app.innerHTML = "";
  for (const p of data.providers) {{
    const card = document.createElement("div"); card.className = "provider";
    const c = PCOLORS[p.name] || "#8b949e";
    let html = `<div class="phead"><span class="dot" style="color:${{c}}">●</span> ` +
               `${{p.display_name}} <span class="plan">${{p.plan || ""}}</span></div>`;
    if (!p.windows.length) {{
      html += `<div class="err">⚠ ${{p.error || T.nodata}}</div>`;
    }} else {{
      let bucket = null;
      for (const w of p.windows) {{
        if (w.bucket !== bucket) {{
          bucket = w.bucket;
          if (bucket) html += `<div class="bucket">── ${{bucket}} ──</div>`;
        }}
        const rem = w.remaining;
        if (w.abs_text) {{
          // 余额型：无进度条，显示绝对余额
          html += `<div class="wrow"><div class="wtop"><span class="wlabel">${{w.label}}</span>` +
            `<span class="pct" style="flex:1;color:${{absColor(w.abs_level)}}">${{w.abs_text}}</span></div></div>`;
        }} else {{
          html += `<div class="wrow"><div class="wtop"><span class="wlabel">${{w.label}}</span>` +
            `<div class="track"><div class="fill" data-reset="${{w.reset_at||""}}" ` +
            `style="width:${{rem==null?0:Math.min(rem,100)}}%;background:${{color(rem)}}"></div></div>` +
            `<span class="pct" style="color:${{color(rem)}}">${{pctText(rem)}}</span></div>` +
            `<div class="cd" data-cd="${{w.reset_at||""}}">${{cd(w.reset_at)}}</div></div>`;
        }}
      }}
      if (p.stale) html += `<div class="stale">⚠ ${{T.stale}} · ${{T.updated}}${{ago(p.fetched_at)}}</div>`;
    }}
    card.innerHTML = html;
    app.appendChild(card);
  }}
  document.getElementById("fresh").textContent =
    T.updated + ago(Math.max(...data.providers.map(p => p.fetched_at || 0)));
}}
async function load() {{
  try {{
    const r = await fetch("api/quotas");
    if (r.ok) render(await r.json());
  }} catch (e) {{ /* 保持旧数据，下轮再试 */ }}
}}
setInterval(load, 30000);
setInterval(() => {{
  document.querySelectorAll("[data-cd]").forEach(el => {{
    const ts = parseFloat(el.dataset.cd);
    el.textContent = cd(ts || null);
  }});
}}, 15000);
load();
</script>
</body>
</html>
"""


def render_page(token: str) -> str:
    lang = "zh" if tr("本周") == "本周" else "en"  # 跟随服务进程语言
    return _PAGE.format(
        lang=lang,
        lang_js=json.dumps(lang),
        title=tr("⚡ 额度监控"),
        subtitle="codex-quota · LAN",
        loading=tr("加载中…"),
        refresh=tr("立即刷新"),
        warn=THRESHOLDS["warn"],
        crit=THRESHOLDS["crit"],
    )


class WebServer:
    """托管手机页面的 HTTP 服务。start() 后从 url 取访问地址。"""

    def __init__(self, get_views: Callable[[], list[ProviderView]], *,
                 port: int = DEFAULT_PORT, token: Optional[str] = None,
                 host: str = "0.0.0.0"):
        self._get_views = get_views
        self._req_port = port
        self.token = token or generate_token()
        self._host = host
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> Optional[int]:
        return self._httpd.server_address[1] if self._httpd else None

    @property
    def url(self) -> Optional[str]:
        if self._httpd is None:
            return None
        return f"http://{lan_ip()}:{self.port}/t/{self.token}/"

    def start(self) -> int:
        """启动并返回实际端口；端口占用时自动递增。"""
        last_err: Optional[OSError] = None
        for attempt in range(MAX_PORT_ATTEMPTS):
            port = self._req_port + attempt
            try:
                self._httpd = ThreadingHTTPServer((self._host, port), self._make_handler())
                break
            except OSError as exc:
                last_err = exc
        if self._httpd is None:
            raise OSError(f"无可用端口（{self._req_port} 起尝试 {MAX_PORT_ATTEMPTS} 个）: {last_err}")
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def _make_handler(self):
        token = self.token
        get_views = self._get_views

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = urlparse(self.path).path.rstrip("/")
                if path == f"/t/{token}":
                    self._send(200, render_page(token).encode(), "text/html; charset=utf-8")
                elif path == f"/t/{token}/api/quotas":
                    payload = json.dumps(views_to_payload(get_views()), ensure_ascii=False)
                    self._send(200, payload.encode(), "application/json; charset=utf-8")
                else:
                    self._send(404, b"not found", "text/plain")  # 无 token 一律 404

            def _send(self, code: int, body: bytes, content_type: str):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass  # 静默

        return Handler
