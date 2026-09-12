"""安装脚本：建虚拟环境、装依赖、下载浏览器，并打印各 agent 的接入配置。

用法:
    python scripts/install.py                      # 完整安装
    python scripts/install.py --no-browser         # 跳过浏览器下载
    python scripts/install.py --reuse-env <path>   # 复用已有 venv
    python scripts/install.py --browsers-path <p>  # 指定已有浏览器目录
    python scripts/install.py --print-config       # 只打印配置，不做安装

本工具是标准 stdio MCP server，不绑定任何厂商。安装后把打印出的配置
贴到对应 agent 的配置里即可。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_LAUNCH = os.path.join(_ROOT, "scripts", "launch.py")


def _run(cmd: list[str], **kw) -> int:
    print("+", " ".join(cmd))
    return subprocess.call(cmd, **kw)


def _venv_python(venv_dir: str) -> str:
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def _required_revision(python: str) -> str | None:
    """读出该解释器所装 playwright 需要的 chromium revision。"""
    code = (
        "import json,os,playwright;"
        "d=os.path.join(os.path.dirname(playwright.__file__),'driver','package','browsers.json');"
        "print(next(b['revision'] for b in json.load(open(d))['browsers']"
        " if b['name']=='chromium'))"
    )
    try:
        out = subprocess.run(
            [python, "-c", code], capture_output=True, text=True, timeout=60
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def _find_existing_browsers(python: str) -> str | None:
    """找一个已有且 revision 匹配的 Playwright 浏览器目录。

    浏览器与 playwright 版本强绑定（1.61→rev 1228，1.62→rev 1234），
    只看目录存在会选到不匹配的，启动时才失败。所以这里要核对 revision。
    """
    want = _required_revision(python)
    if not want:
        return None
    target = f"chromium-{want}"
    candidates = [
        os.getenv("BETTER_CRAWLER_BROWSERS_PATH", ""),
        os.getenv("PLAYWRIGHT_BROWSERS_PATH", ""),
        os.path.join(_ROOT, ".playwright"),
        os.path.expanduser("~/.cache/ms-playwright"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
        os.path.expanduser("~/Library/Caches/ms-playwright"),
    ]
    for path in candidates:
        if not path or not os.path.isdir(path):
            continue
        try:
            if target in os.listdir(path):
                return path
        except OSError:
            continue
    return None


def _json_snippet(python: str, browsers: str | None) -> str:
    env = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "BETTER_CRAWLER_TIMEOUT": "25",
        "BETTER_CRAWLER_MAX_CHARS": "50000",
    }
    if browsers:
        env["BETTER_CRAWLER_BROWSERS_PATH"] = browsers
    return json.dumps(
        {
            "mcpServers": {
                "better-crawler": {
                    "command": python,
                    "args": [_LAUNCH],
                    "env": env,
                }
            }
        },
        ensure_ascii=False,
        indent=2,
    )


def _toml_snippet(python: str, browsers: str | None) -> str:
    lines = [
        "[mcp_servers.better-crawler]",
        f"command = {json.dumps(python)}",
        f"args = [{json.dumps(_LAUNCH)}]",
        "",
        "[mcp_servers.better-crawler.env]",
        'PYTHONIOENCODING = "utf-8"',
        'PYTHONUTF8 = "1"',
        'BETTER_CRAWLER_TIMEOUT = "25"',
        'BETTER_CRAWLER_MAX_CHARS = "50000"',
    ]
    if browsers:
        lines.append(f"BETTER_CRAWLER_BROWSERS_PATH = {json.dumps(browsers)}")
    return "\n".join(lines)


def print_configs(python: str, browsers: str | None) -> None:
    """打印各 agent 的接入配置。"""
    print("\n" + "=" * 72)
    print("接入配置（按你使用的 agent 选一段）")
    print("=" * 72)

    json_block = _json_snippet(python, browsers)

    print("\n【通用 mcpServers 格式】Claude Code / Cursor / Windsurf / Cline 等")
    print("  文件位置：")
    print("    - 项目级: <项目根>/.mcp.json")
    print("    - Claude 用户级: ~/.claude.json 的 mcpServers 字段")
    print("    - Cursor: ~/.cursor/mcp.json")
    print(json_block)

    print("\n【ZCode】~/.zcode/cli/config.json 的 mcp.servers 字段")
    print("  （键名是 mcp.servers，不是 mcpServers；内容同上）")
    nested = json.loads(json_block)
    print(json.dumps({"mcp": {"servers": nested["mcpServers"]}}, ensure_ascii=False, indent=2))

    print("\n【Codex】~/.codex/config.toml")
    print(_toml_snippet(python, browsers))

    print("\n【作为 Python 库直接用】不走 MCP 时：")
    print(f'  import sys; sys.path.insert(0, r"{os.path.join(_ROOT, "src")}")')
    print("  from better_crawler import Fetcher")
    print("\n" + "=" * 72)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env", help="复用已有的 Python 环境")
    ap.add_argument("--no-browser", action="store_true", help="不下载浏览器")
    ap.add_argument("--browsers-path", help="指定浏览器目录")
    ap.add_argument("--print-config", action="store_true", help="只打印配置")
    args = ap.parse_args()

    if args.print_config:
        python = args.reuse_env and _venv_python(args.reuse_env) or sys.executable
        print_configs(python, args.browsers_path or _find_existing_browsers(python))
        return 0

    # 1) 解释器
    if args.reuse_env:
        python = _venv_python(args.reuse_env)
        if not os.path.exists(python):
            python = os.path.join(args.reuse_env, "python.exe")
        if not os.path.exists(python):
            print(f"[x] 找不到解释器: {args.reuse_env}", file=sys.stderr)
            return 1
        print(f"[1/3] 复用环境: {python}")
    else:
        venv_dir = os.path.join(_ROOT, ".venv")
        if not os.path.exists(_venv_python(venv_dir)):
            print(f"[1/3] 创建虚拟环境: {venv_dir}")
            if _run([sys.executable, "-m", "venv", venv_dir]) != 0:
                return 1
        python = _venv_python(venv_dir)
        print(f"[1/3] 使用环境: {python}")

    # 2) 依赖
    print("[2/3] 安装依赖...")
    if _run([python, "-m", "pip", "install", "-q", "-r",
             os.path.join(_ROOT, "requirements.txt")]) != 0:
        print("[x] 依赖安装失败", file=sys.stderr)
        return 1

    # 3) 浏览器
    browsers = args.browsers_path or os.getenv("BETTER_CRAWLER_BROWSERS_PATH", "")
    if args.no_browser:
        print("[3/3] 跳过浏览器下载 (--no-browser)")
    elif browsers:
        print(f"[3/3] 使用指定浏览器目录: {browsers}")
    else:
        found = _find_existing_browsers(python)
        if found:
            browsers = found
            print(f"[3/3] 复用已有且版本匹配的浏览器: {found}")
        else:
            print("[3/3] 下载 Chromium（约 170MB，国内网络可能较慢）...")
            target = os.path.join(_ROOT, ".playwright")
            os.makedirs(target, exist_ok=True)
            env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=target)
            if _run([python, "-m", "playwright", "install", "chromium"], env=env) != 0:
                print("[!] 浏览器下载失败，静态站点仍可用。", file=sys.stderr)
                print("    可手动设置 BETTER_CRAWLER_BROWSERS_PATH 指向已有浏览器。",
                      file=sys.stderr)
            else:
                browsers = target

    print_configs(python, browsers)

    print("\n自检:")
    print(f"  {python} {os.path.join(_ROOT, 'scripts', 'selfcheck.py')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
