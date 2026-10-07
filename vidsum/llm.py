"""LLM 调用：只用标准库 urllib，不引 httpx。

**为什么不引 httpx / requests**：本机实测，当 ``NO_PROXY`` 与 ``no_proxy`` 同时存在、
且值里含 ``[::1]`` 这类条目时，httpx 会在构造客户端时直接抛
``httpx.InvalidURL: Invalid port: ':1]'``，连请求都发不出去。urllib 对同样的环境变量
处理正常。少一个依赖，也少一类环境坑。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
MIMO_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MIMO_MODEL = "mimo-v2.5"

DSH_CREDENTIALS = Path.home() / ".dsh" / ".credentials.yaml"


class LLMError(RuntimeError):
    pass


@dataclass
class LLMConfig:
    key: str
    url: str
    model: str
    source: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.key)


def _read_dsh_credentials() -> dict[str, str]:
    """从 DSH 的 credentials 文件里抠出 API key（只认 key: value 行）。"""
    keys: dict[str, str] = {}
    if not DSH_CREDENTIALS.exists():
        return keys
    try:
        for line in DSH_CREDENTIALS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY:"):
                keys["deepseek"] = line.split(":", 1)[1].strip().strip("'\"")
            elif line.startswith("XIAOMI_API_KEY:"):
                keys["xiaomi"] = line.split(":", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return keys


def load_config(model: str = "", url: str = "", key: str = "") -> LLMConfig:
    """优先级：显式传入 → 环境变量 → DSH credentials 文件。

    配 key 与 endpoint 是成对的，绝不把 DeepSeek 的 key 发到小米的地址上。
    """
    env_key = key or os.environ.get("LLM_API_KEY", "")
    env_url = url or os.environ.get("LLM_API_URL", "")
    env_model = model or os.environ.get("LLM_MODEL", "")

    if env_key:
        return LLMConfig(env_key, env_url or MIMO_URL, env_model or MIMO_MODEL,
                         source="explicit")

    ds_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if ds_key and not env_url:
        return LLMConfig(ds_key, DEEPSEEK_URL, env_model or DEEPSEEK_MODEL,
                         source="env:DEEPSEEK_API_KEY")

    creds = _read_dsh_credentials()
    if "deepseek" in creds:
        return LLMConfig(creds["deepseek"], env_url or DEEPSEEK_URL,
                         env_model or DEEPSEEK_MODEL, source=str(DSH_CREDENTIALS))
    if "xiaomi" in creds:
        return LLMConfig(creds["xiaomi"], env_url or MIMO_URL,
                         env_model or MIMO_MODEL, source=str(DSH_CREDENTIALS))

    return LLMConfig("", env_url or DEEPSEEK_URL, env_model or DEEPSEEK_MODEL,
                     source="none")


def chat(cfg: LLMConfig, messages: list[dict], max_tokens: int = 2048,
         temperature: float = 0.3, timeout: int = 180) -> tuple[str, dict]:
    if not cfg.ready:
        raise LLMError(
            "没有可用的 LLM API key。请设置环境变量 LLM_API_KEY，"
            "或把 DEEPSEEK_API_KEY 写进 ~/.dsh/.credentials.yaml。"
        )
    body = json.dumps({
        "model": cfg.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")
    req = urllib.request.Request(
        cfg.url, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg.key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise LLMError(f"LLM 返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"连不上 LLM 端点 {cfg.url}：{exc.reason}") from exc

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"LLM 响应结构异常：{str(data)[:300]}") from exc
    return text, data.get("usage", {})
