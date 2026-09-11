"""安装脚本：建虚拟环境、装依赖、下载浏览器，并把绝对路径写回插件清单。

用法:
    python scripts/install.py                # 完整安装
    python scripts/install.py --no-browser   # 跳过浏览器下载（复用已有的）
    python scripts/install.py --reuse-env <path>   # 复用已有 venv，不新建

Windows 下浏览器下载常很慢，若本机已有 Playwright 浏览器，
用 --reuse-env 或设置 BETTER_CRAWLER_BROWSERS_PATH 指向它即可。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_MANIFEST = os.path.join(_ROOT, ".zcode-plugin", "plugin.json")


def _run(cmd: list[str], **kw) -> int:
    print("+", " ".join(cmd))
    return subprocess.call(cmd, **kw)


def _venv_python(venv_dir: str) -> str:
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def _find_existing_browsers() -> str | None:
    """找一个已有的 Playwright 浏览器目录，避免重复下载 ~700MB。"""
    candidates = [
        os.getenv("BETTER_CRAWLER_BROWSERS_PATH", ""),
        os.getenv("PLAYWRIGHT_BROWSERS_PATH", ""),
        os.path.join(_ROOT, ".playwright"),
        os.path.expanduser("~/.cache/ms-playwright"),
        os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "ms-playwright"
        ),
    ]
    for path in candidates:
        if not path or not os.path.isdir(path):
            continue
        # 目录里要有 chromium 才算数
        for entry in os.listdir(path):
            if entry.startswith("chromium"):
                return path
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env", help="复用已有的 Python 环境（venv 目录）")
    ap.add_argument("--no-browser", action="store_true", help="不下载浏览器")
    ap.add_argument("--browsers-path", help="指定浏览器目录")
    args = ap.parse_args()

    # 1) 准备解释器
    if args.reuse_env:
        python = _venv_python(args.reuse_env)
        if not os.path.exists(python):
            python = os.path.join(args.reuse_env, "python.exe")
        if not os.path.exists(python):
            print(f"[x] 找不到解释器: {args.reuse_env}", file=sys.stderr)
            return 1
        print(f"[1/4] 复用环境: {python}")
    else:
        venv_dir = os.path.join(_ROOT, ".venv")
        if not os.path.exists(_venv_python(venv_dir)):
            print(f"[1/4] 创建虚拟环境: {venv_dir}")
            if _run([sys.executable, "-m", "venv", venv_dir]) != 0:
                return 1
        python = _venv_python(venv_dir)
        print(f"[1/4] 使用环境: {python}")

    # 2) 装依赖
    print("[2/4] 安装依赖...")
    if _run([python, "-m", "pip", "install", "-q", "-r",
             os.path.join(_ROOT, "requirements.txt")]) != 0:
        print("[x] 依赖安装失败", file=sys.stderr)
        return 1

    # 3) 浏览器
    browsers_path = args.browsers_path or os.getenv("BETTER_CRAWLER_BROWSERS_PATH", "")
    if args.no_browser:
        print("[3/4] 跳过浏览器下载 (--no-browser)")
    elif browsers_path:
        print(f"[3/4] 使用指定浏览器目录: {browsers_path}")
    else:
        found = _find_existing_browsers()
        if found:
            browsers_path = found
            print(f"[3/4] 复用已有浏览器: {found}")
        else:
            print("[3/4] 下载 Chromium（约 170MB，国内网络可能较慢）...")
            target = os.path.join(_ROOT, ".playwright")
            os.makedirs(target, exist_ok=True)
            env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=target)
            if _run([python, "-m", "playwright", "install", "chromium"], env=env) != 0:
                print("[!] 浏览器下载失败，静态站点仍可用。", file=sys.stderr)
                print("    可手动设置 BETTER_CRAWLER_BROWSERS_PATH 指向已有浏览器。",
                      file=sys.stderr)
            else:
                browsers_path = target

    # 4) 回写清单里的解释器与浏览器路径
    print("[4/4] 更新插件清单...")
    try:
        with open(_MANIFEST, encoding="utf-8") as fh:
            manifest = json.load(fh)
        server = manifest["mcpServers"]["better-crawler"]
        server["command"] = python
        env_block = server.setdefault("env", {})
        env_block["PYTHONIOENCODING"] = "utf-8"
        env_block["PYTHONUTF8"] = "1"
        if browsers_path:
            env_block["BETTER_CRAWLER_BROWSERS_PATH"] = browsers_path
        with open(_MANIFEST, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(f"      command = {python}")
        print(f"      browsers = {browsers_path or '(默认目录)'}")
    except Exception as exc:  # noqa: BLE001
        print(f"[!] 清单更新失败: {exc}", file=sys.stderr)
        return 1

    print("\n完成。自检:")
    print(f"  {python} {os.path.join(_ROOT, 'scripts', 'selfcheck.py')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
