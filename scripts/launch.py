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
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

_REQUIRED = ("playwright", "mcp", "trafilatura", "bs4", "httpx")

# 除了能导入，还得确认 API 可用：mcp 1.x 没有 mcp.server.mcpserver，
# 只查 import 会选中旧版本，然后在真正启动时炸掉。
_PROBE = (
    "import " + ", ".join(_REQUIRED) + "\n"
    "from mcp.server.mcpserver import MCPServer\n"
)


def _has_deps(python: str) -> bool:
    try:
        proc = subprocess.run(
            [python, "-c", _PROBE],
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
    """返回具备依赖的解释器的绝对路径；都不可用则返回当前解释器。

    必须返回绝对路径：re-exec 用的是 os.execv，它在 Windows 上不搜索 PATH，
    传 "python" 会直接 FileNotFoundError。
    """
    for candidate in _candidate_pythons():
        if candidate == "python":
            # 裸名字先解析成绝对路径再用
            resolved = shutil.which(candidate)
            if not resolved:
                continue
            if _has_deps(resolved):
                return os.path.abspath(resolved)
            continue

        if not os.path.exists(candidate):
            continue
        if _has_deps(candidate):
            return os.path.abspath(candidate)
    return os.path.abspath(sys.executable)


_BROWSER_PROBE = (
    "import json,os,playwright;"
    "d=os.path.join(os.path.dirname(playwright.__file__),'driver','package','browsers.json');"
    "print(','.join(b['revision'] for b in json.load(open(d))['browsers']"
    " if b['name']=='chromium'))"
)


def _required_revisions(python: str) -> list[str]:
    """指定解释器所装 playwright 需要的 chromium revision。

    浏览器与 playwright 版本强绑定（1.61→1228，1.62→1234），
    目录名对不上时启动会失败，所以必须核对 revision 而不是只看目录存在。
    """
    try:
        out = subprocess.run(
            [python, "-c", _BROWSER_PROBE],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode == 0 and out.stdout.strip():
            return [r for r in out.stdout.strip().split(",") if r]
    except Exception:  # noqa: BLE001
        pass
    return []


def _has_matching_chromium(path: str, python: str) -> bool:
    """目录里有该解释器所需版本的 chromium（含 headless shell）。"""
    revisions = _required_revisions(python)
    if not revisions:
        # 探测不出 revision 时退回宽松判断，让 Playwright 自己报错
        try:
            return any(e.startswith("chromium") for e in os.listdir(path))
        except OSError:
            return False
    try:
        entries = os.listdir(path)
    except OSError:
        return False
    for rev in revisions:
        # headless 模式用的是 chromium_headless_shell，两个都要在
        if f"chromium-{rev}" not in entries:
            return False
        if f"chromium_headless_shell-{rev}" not in entries:
            return False
    return True


def _resolve_browsers_path(python: str) -> None:
    """设置 PLAYWRIGHT_BROWSERS_PATH，只在确实存在版本匹配的浏览器时设置。

    顺序：显式环境变量 → 仓库内 .playwright → 系统默认位置。
    指向版本不匹配的目录比不设更糟（会覆盖 Playwright 自己的默认查找），
    所以每种来源都要核对 revision。
    """
    for var in ("BETTER_CRAWLER_BROWSERS_PATH", "PLAYWRIGHT_BROWSERS_PATH"):
        val = os.getenv(var, "").strip()
        if val and os.path.isdir(val):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = val
            return

    local = os.path.join(_ROOT, ".playwright")
    if os.path.isdir(local) and _has_matching_chromium(local, python):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = local
        return

    # 系统默认位置（Windows: %LOCALAPPDATA%\ms-playwright，Linux/mac: ~/.cache/ms-playwright）
    for default in (
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
        os.path.expanduser("~/.cache/ms-playwright"),
        os.path.expanduser("~/Library/Caches/ms-playwright"),
    ):
        if default and os.path.isdir(default) and _has_matching_chromium(default, python):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = default
            return
    # 都没找到：不设，让 Playwright 自己报缺浏览器


def _report_missing_deps() -> None:
    """依赖不全时给出可操作的指引，而不是让 import 抛晦涩错误。"""
    print(
        "[better-crawler] 找不到可用的 Python 环境。\n"
        "\n"
        "需要一个同时具备以下条件的解释器：\n"
        "  playwright, mcp>=2.0, trafilatura, beautifulsoup4, httpx\n"
        "\n"
        "最快的修法（会自动建 .venv 并装依赖）:\n"
        f'  python "{os.path.join(_ROOT, "scripts", "install.py")}"\n'
        "\n"
        "已经装好了但装在别处？在 MCP 配置的 env 里指定:\n"
        '  "BETTER_CRAWLER_PYTHON": "<那个解释器的绝对路径>"\n'
        "\n"
        f"当前解释器: {sys.executable}",
        file=sys.stderr,
    )
    raise SystemExit(1)


def main() -> None:
    # 顺序很重要：先定解释器，再用它探测浏览器 revision（revision 由
    # 所装 playwright 版本决定），最后才可能 re-exec。
    chosen = _resolve_python()
    if os.path.abspath(chosen) != os.path.abspath(sys.executable):
        # 换用具备依赖的解释器重新执行本文件。
        # 注意：os.execv 不搜索 PATH，_resolve_python 已保证返回绝对路径。
        os.execv(chosen, [chosen, os.path.abspath(__file__), *sys.argv[1:]])
        return

    if not _has_deps(sys.executable):
        _report_missing_deps()

    _resolve_browsers_path(sys.executable)

    src = os.path.join(_ROOT, "src")
    if os.path.isdir(src) and src not in sys.path:
        sys.path.insert(0, src)

    from better_crawler.mcp_server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
