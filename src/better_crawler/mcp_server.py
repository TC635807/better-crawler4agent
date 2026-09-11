"""MCP server：把 URL 变成正文。

只做一件事——URL 到正文。URL 从哪来不在本服务的职责内，由调用方决定。

传输：stdio。注意 stdout 只能有 MCP 协议帧，任何日志必须走 stderr
（logging 默认就是 stderr，不要改成 stdout）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

# 允许直接以脚本方式运行（ZCode 插件场景下没有安装到 site-packages）
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from better_crawler import Fetcher, __version__  # noqa: E402

from mcp.server.mcpserver import MCPServer  # noqa: E402

logging.basicConfig(
    level=os.getenv("BETTER_CRAWLER_LOG_LEVEL", "INFO").upper(),
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("better_crawler.mcp")

# 单次返回给模型的正文上限，避免一条结果吃掉整个上下文
DEFAULT_MAX_CHARS = int(os.getenv("BETTER_CRAWLER_MAX_CHARS", "50000"))

_server = MCPServer(
    name="better-crawler",
    version=__version__,
    instructions=(
        "把 URL 转换成干净的正文文本。抓取使用真实浏览器渲染，"
        "对知乎、CSDN、掘金等反爬站点有效。本服务只负责抓取，"
        "不提供搜索能力——URL 请由调用方自行获取。"
    ),
)

_fetcher: Fetcher | None = None
_fetcher_lock = asyncio.Lock()


async def _get_fetcher() -> Fetcher:
    """延迟初始化，避免服务器启动时就拉起 Chromium。"""
    global _fetcher
    if _fetcher is not None:
        return _fetcher
    async with _fetcher_lock:
        if _fetcher is None:
            f = Fetcher(timeout=float(os.getenv("BETTER_CRAWLER_TIMEOUT", "25")))
            await f.start()
            _fetcher = f
    return _fetcher


@_server.tool(
    name="fetch_url",
    description=(
        "抓取单个网页并返回正文。使用真实浏览器渲染，支持 JS 站点与常见反爬站点。"
        "返回内容含 tier 字段说明命中哪条抓取路径，以及 ok/error 供判断结果。"
    ),
)
async def fetch_url(url: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """抓取一个 URL 的正文。

    Args:
        url: 目标网页地址，必须是 http/https。
        max_chars: 正文最大返回字符数，超出会截断并在 truncated 标记。
    """
    f = await _get_fetcher()
    result = await f.fetch(url)
    return json.dumps(
        result.to_dict(max_chars=max_chars), ensure_ascii=False, indent=2
    )


@_server.tool(
    name="fetch_urls",
    description=(
        "并发抓取多个网页并逐条返回正文。单条失败不影响其他条目，"
        "每条结果都带独立的 ok/error 字段。适合一次处理多个已知 URL。"
    ),
)
async def fetch_urls(
    urls: list[str],
    max_chars: int = DEFAULT_MAX_CHARS,
    max_concurrent: int = 5,
) -> str:
    """并发抓取多个 URL。

    Args:
        urls: 目标地址列表，最多 20 条。
        max_chars: 每条正文的最大字符数。
        max_concurrent: 并发数，上限 8。
    """
    if not urls:
        return json.dumps({"results": [], "error": "urls 为空"}, ensure_ascii=False)
    if len(urls) > 20:
        return json.dumps(
            {"error": f"一次最多 20 条，收到 {len(urls)} 条"}, ensure_ascii=False
        )
    f = await _get_fetcher()
    results = await f.fetch_many(urls, max_concurrent=max(1, min(max_concurrent, 8)))
    payload = {
        "results": [r.to_dict(max_chars=max_chars) for r in results],
        "ok_count": sum(1 for r in results if r.ok),
        "total": len(results),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@_server.tool(
    name="crawler_status",
    description="查看抓取器状态：浏览器是否可用、不可用原因、当前配置。",
)
async def crawler_status() -> str:
    """返回抓取器运行状态，用于排查浏览器启动失败等问题。"""
    info: dict = {
        "version": __version__,
        "timeout": os.getenv("BETTER_CRAWLER_TIMEOUT", "25"),
        "max_chars_default": DEFAULT_MAX_CHARS,
        "allow_private": os.getenv("BETTER_CRAWLER_ALLOW_PRIVATE", "") or "0",
    }
    if _fetcher is None:
        info["browser"] = "not_started"
        info["hint"] = "首次调用 fetch_url 时才会启动浏览器"
    else:
        reason = _fetcher.engine.unavailable_reason
        info["browser"] = "unavailable" if reason else "ready"
        if reason:
            info["browser_error"] = reason
            info["hint"] = (
                "浏览器不可用时仍可用 trafilatura/httpx 抓静态站点，"
                "但 JS 站点与反爬站点会失败。"
            )
    return json.dumps(info, ensure_ascii=False, indent=2)


async def _shutdown() -> None:
    if _fetcher is not None:
        await _fetcher.close()


def main() -> None:
    """stdio 入口。"""
    logger.info("better-crawler %s 启动 (stdio)", __version__)
    try:
        _server.run("stdio")
    finally:
        try:
            asyncio.get_event_loop().run_until_complete(_shutdown())
        except Exception:  # noqa: BLE001 - 关闭失败不影响退出
            pass


if __name__ == "__main__":
    main()
