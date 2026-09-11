"""自检：确认依赖、浏览器、抓取、安全校验都正常。

用法:
    python scripts/selfcheck.py            # 含真实抓取
    python scripts/selfcheck.py --offline  # 只查依赖与浏览器
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

# 公开的测试页：一条静态站快路径，一条需要浏览器渲染的中文博客
# 注意别用 example.com —— 它正文只有 142 字符，低于 MIN_CONTENT_LENGTH
_TEST_URLS = [
    ("https://docs.python.org/3/tutorial/classes.html", "英文文档（静态）"),
    ("https://www.cnblogs.com/buptl/p/20866967", "中文博客（浏览器）"),
]

_ok_count = 0
_fail_count = 0


def check(label: str, passed: bool, detail: str = "") -> None:
    global _ok_count, _fail_count
    mark = "OK  " if passed else "FAIL"
    if passed:
        _ok_count += 1
    else:
        _fail_count += 1
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))


def check_imports() -> None:
    print("依赖:")
    for mod, label in (
        ("playwright", "playwright"),
        ("mcp", "mcp SDK"),
        ("trafilatura", "trafilatura"),
        ("bs4", "beautifulsoup4"),
        ("httpx", "httpx"),
        ("lxml", "lxml"),
    ):
        try:
            __import__(mod)
            check(label, True)
        except ImportError as exc:
            check(label, False, str(exc))


def check_browsers_path() -> None:
    path = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "")
    if not path:
        check("浏览器路径", True, "未设置，将用 Playwright 默认目录")
        return
    if os.path.isdir(path):
        entries = [e for e in os.listdir(path) if e.startswith("chromium")]
        check("浏览器路径", bool(entries), f"{path} ({len(entries)} 个 chromium)")
    else:
        check("浏览器路径", False, f"目录不存在: {path}")


def check_safety() -> None:
    print("安全校验:")
    from better_crawler import UnsafeURLError, validate_url

    for url, should_pass, label in (
        ("https://example.com", True, "公网 http(s) 放行"),
        ("http://127.0.0.1:8000/x", False, "拒绝回环地址"),
        ("http://192.168.1.1/", False, "拒绝内网地址"),
        ("file:///etc/passwd", False, "拒绝 file:// 协议"),
        ("http://169.254.169.254/latest/meta-data/", False, "拒绝云元数据地址"),
    ):
        try:
            validate_url(url)
            passed = should_pass
            detail = "" if passed else "本应拒绝却放行了"
        except UnsafeURLError:
            passed = not should_pass
            detail = "" if passed else "本应放行却被拒绝"
        except Exception as exc:  # noqa: BLE001
            passed, detail = False, str(exc)[:60]
        check(label, passed, detail)


async def check_fetch() -> None:
    print("抓取:")
    from better_crawler import Fetcher

    f = Fetcher(timeout=25)
    await f.start()
    reason = f.engine.unavailable_reason
    check("浏览器启动", reason is None, reason or "")
    for url, label in _TEST_URLS:
        r = await f.fetch(url)
        detail = f"{r.content_length} 字符, tier={r.tier}, {r.elapsed:.1f}s"
        if not r.ok:
            detail = r.error or "失败"
        check(label, r.ok and r.content_length > 200, detail)
    await f.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="跳过真实抓取")
    args = ap.parse_args()

    print(f"better-crawler-4-agent 自检  (root={_ROOT})\n")
    check_imports()
    check_browsers_path()
    check_safety()
    if args.offline:
        print("\n(--offline 跳过抓取测试)")
    else:
        asyncio.run(check_fetch())

    print(f"\n结果: {_ok_count} 通过, {_fail_count} 失败")
    return 1 if _fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
