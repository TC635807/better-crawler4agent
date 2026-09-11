"""MCP 启动器。

MCP 客户端拉起本文件时，环境里未必有正确的 Python 和浏览器路径。
这里按优先级解析二者，再进入 server，避免"装了但跑不起来"。

解释器选择顺序：
  1. BETTER_CRAWLER_PYTHON 环境变量
  2. 当前解释器（若已具备依赖）
  3. 仓库内 .venv
  4. PATH 上的 python

浏览器选择顺序：
  1. BETTER_CRAWLER_BROWSERS_PATH / PLAYWRIGHT_BROWSERS_PATH
  2. 仓库内 .playwright
  3. Playwright 默认目录
"""

from __future__ import annotations

import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

_REQUIRED = ("playwright", "mcp", "trafilatura", "bs4", "httpx")


def _has_deps(python: str) -> bool:
    code = "import " + ", ".join(_REQUIRED)
    try:
        proc = subprocess.run(
            [python, "-c", code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _candidate_pythons() -> list[str]:
    out: list[str] = []
    env_py = os.getenv("BETTER_CRAWLER_PYTHON", "").strip()
    if env_py:
        out.append(env_py)
    out.append(sys.executable)
    for rel in (
        os.path.join(".venv", "Scripts", "python.exe"),
        os.path.join(".venv", "bin", "python"),
    ):
        out.append(os.path.join(_ROOT, rel))
    out.append("python")
    seen, uniq = set(), []
    for p in out:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def _resolve_python() -> str:
    """返回具备依赖的解释器；都不可用则返回当前解释器（让 server 报明确错误）。"""
    for candidate in _candidate_pythons():
        if os.path.sep in candidate or candidate.endswith("python.exe"):
            if not os.path.exists(candidate):
                continue
        if _has_deps(candidate):
            return candidate
    return sys.executable


def _has_chromium(path: str) -> bool:
    try:
        return any(e.startswith("chromium") for e in os.listdir(path))
    except OSError:
        return False


def _resolve_browsers_path() -> None:
    """设置 PLAYWRIGHT_BROWSERS_PATH，只在确实存在浏览器时设置。

    顺序：显式环境变量 → 仓库内 .playwright → Playwright 默认目录。
    不设环境变量时 Playwright 会自己找默认目录，所以探测到才覆盖。
    """
    for var in ("BETTER_CRAWLER_BROWSERS_PATH", "PLAYWRIGHT_BROWSERS_PATH"):
        val = os.getenv(var, "").strip()
        if val and os.path.isdir(val):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = val
            return

    local = os.path.join(_ROOT, ".playwright")
    if os.path.isdir(local) and _has_chromium(local):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = local
        return

    # 系统默认位置（Windows: %LOCALAPPDATA%\ms-playwright，Linux/mac: ~/.cache/ms-playwright）
    for default in (
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
        os.path.expanduser("~/.cache/ms-playwright"),
        os.path.expanduser("~/Library/Caches/ms-playwright"),
    ):
        if default and os.path.isdir(default) and _has_chromium(default):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = default
            return
    # 都没找到：不设，让 Playwright 自己报缺浏览器


def main() -> None:
    _resolve_browsers_path()

    chosen = _resolve_python()
    if os.path.abspath(chosen) != os.path.abspath(sys.executable):
        # 换用具备依赖的解释器重新执行本文件
        os.execv(chosen, [chosen, os.path.abspath(__file__), *sys.argv[1:]])
        return

    src = os.path.join(_ROOT, "src")
    if os.path.isdir(src) and src not in sys.path:
        sys.path.insert(0, src)

    from better_crawler.mcp_server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
