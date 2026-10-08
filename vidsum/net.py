"""网络环境的小工具：代理相关的诊断与「逃生门」。

**为什么需要这个**：实测遇到过一种很坑的环境——``HTTPS_PROXY=http://127.0.0.1:3128``
被写进了环境变量，但那个端口根本没人监听。于是所有联网请求都以
``WinError 10061 目标计算机积极拒绝`` 告终，而错误信息里完全看不出是"代理挂了"。

这里的两个能力：
- :func:`proxy_hint` —— 把这句话补到错误信息后面；
- :func:`disable_proxy` —— ``vidsum run --no-proxy`` 用来临时清掉代理。
"""
from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

PROXY_VARS = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
              "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy")

_ORDER = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
          "ALL_PROXY", "all_proxy")

_CONN_MARKERS = (
    "10061", "10060", "connection refused", "could not connect",
    "failed to connect", "econnrefused", "connectionerror",
    "temporary failure in name resolution", "name or service not known",
    "无法连接", "积极拒绝",
)


def current_proxy() -> str:
    for key in _ORDER:
        value = os.environ.get(key)
        if value:
            return value
    return ""


def disable_proxy() -> list[str]:
    """清掉当前进程里的代理环境变量，返回被清掉的 ``KEY=值`` 列表。"""
    cleared = []
    for key in PROXY_VARS:
        value = os.environ.pop(key, None)
        if value:
            cleared.append(f"{key}={value}")
    return cleared


def is_connection_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(m in text for m in _CONN_MARKERS)


def proxy_hint(exc: BaseException | None = None) -> str:
    """连接类错误且配了代理时，给一句人能看懂的提示；否则返回空串。"""
    proxy = current_proxy()
    if not proxy:
        return ""
    if exc is not None and not is_connection_error(exc):
        return ""
    return (
        f"\n\n提示：环境变量里配了代理 {proxy}。如果它没有在运行，所有联网请求都会"
        "以「连接被拒绝」失败，而错在代理不在目标站点。\n"
        "可以加 --no-proxy 重跑，或手动清掉：\n"
        '  PowerShell: $env:HTTPS_PROXY=""; $env:HTTP_PROXY=""\n'
        "  bash:       unset HTTPS_PROXY HTTP_PROXY"
    )


def proxy_reachable(proxy: str = "", timeout: float = 1.5) -> bool | None:
    """探测代理端口是否有人监听。

    返回 ``True``/``False``；没配代理时返回 ``None``。
    """
    proxy = proxy or current_proxy()
    if not proxy:
        return None
    if "://" not in proxy:
        proxy = "http://" + proxy
    parsed = urlparse(proxy)
    host, port = parsed.hostname, parsed.port or 80
    if not host:
        return None
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
