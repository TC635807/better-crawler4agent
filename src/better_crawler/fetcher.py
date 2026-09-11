"""抓取编排：URL -> 正文。

分层策略（按实测收益排序）：

  1. 浏览器渲染 —— 主路径。知乎/CSDN/掘金等反爬站只有真浏览器能拿到正文
  2. trafilatura 直取 —— 静态站快路径，约 1s，浏览器不可用时是唯一依赖
  3. httpx + 浏览器指纹 —— 二级兜底，补 Sec-Ch-Ua/Sec-Fetch-* 全套头

每层都记录耗时和命中层级，返回给调用方判断，而不是只给一个成功/失败。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from .browser import BrowserEngine
from .extract import MIN_CONTENT_LENGTH, detect_error_page, extract_static
from .safety import UnsafeURLError, validate_url

logger = logging.getLogger(__name__)

# 反爬站的 trafilatura 必失败，TTL 内跳过，省掉每次几秒的试错
_FAIL_TTL = 600.0


@dataclass
class FetchResult:
    """一次抓取的完整结果。"""
    url: str
    ok: bool
    content: str = ""
    title: str = ""
    tier: str = ""                    # browser | trafilatura | httpx | none
    content_length: int = 0
    elapsed: float = 0.0
    error: str = ""
    truncated: bool = False
    attempts: list[dict] = field(default_factory=list)

    def to_dict(self, max_chars: int = 0) -> dict:
        content = self.content
        if max_chars and len(content) > max_chars:
            content = content[:max_chars]
        return {
            "url": self.url,
            "ok": self.ok,
            "title": self.title,
            "content": content,
            "content_length": self.content_length,
            "tier": self.tier,
            "elapsed": round(self.elapsed, 2),
            "error": self.error,
            "truncated": self.truncated or (bool(max_chars) and len(self.content) > max_chars),
            "attempts": self.attempts,
        }


class Fetcher:
    """分层网页抓取器。"""

    def __init__(
        self,
        engine: BrowserEngine | None = None,
        timeout: float = 25.0,
        use_browser: bool = True,
        use_static: bool = True,
        use_httpx: bool = True,
    ):
        self.engine = engine or BrowserEngine()
        self.timeout = timeout
        self.use_browser = use_browser
        self.use_static = use_static
        self.use_httpx = use_httpx
        self._traf_fail: dict[str, float] = {}

    async def start(self) -> None:
        """预热浏览器，避免首次调用被冷启动阻塞。"""
        if self.use_browser:
            await self.engine.start()

    async def close(self) -> None:
        await self.engine.close()

    # ── 对外接口 ────────────────────────────────────────

    async def fetch(self, url: str) -> FetchResult:
        """抓取单个 URL。始终返回 FetchResult，不抛异常。"""
        started = time.monotonic()
        result = FetchResult(url=url, ok=False)

        try:
            url = validate_url(url)
            result.url = url
        except UnsafeURLError as exc:
            result.error = str(exc)
            result.elapsed = time.monotonic() - started
            return result

        domain = _domain_of(url)

        # Tier 1: 浏览器
        if self.use_browser:
            html = await self.engine.fetch_html_guarded(url, timeout=self.timeout)
            if html:
                note = await self._consume_html(html, result, "browser")
                if note is None:
                    result.elapsed = time.monotonic() - started
                    return result

        # Tier 2: trafilatura 直取
        if self.use_static and not self._is_traf_cooling(domain):
            html = await self._fetch_static(url)
            if html:
                note = await self._consume_html(html, result, "trafilatura")
                if note is None:
                    result.elapsed = time.monotonic() - started
                    return result
                self._mark_traf_fail(domain)

        # Tier 3: httpx + 浏览器指纹
        if self.use_httpx:
            html = await self._fetch_httpx(url)
            if html:
                note = await self._consume_html(html, result, "httpx")
                if note is None:
                    result.elapsed = time.monotonic() - started
                    return result
                self._mark_traf_fail(domain)

        result.elapsed = time.monotonic() - started
        if not result.error:
            result.error = "所有抓取方式均未取到有效正文"
            result.attempts.append({"tier": "none", "ok": False, "reason": result.error})
        return result

    async def fetch_many(
        self, urls: list[str], max_concurrent: int = 5
    ) -> list[FetchResult]:
        """并发抓取多个 URL，逐条返回结果（含失败原因）。"""
        sem = asyncio.Semaphore(max_concurrent)

        async def one(u: str) -> FetchResult:
            async with sem:
                return await self.fetch(u)

        return list(await asyncio.gather(*(one(u) for u in urls)))

    # ── 内部 ────────────────────────────────────────────

    async def _consume_html(self, html: str, result: FetchResult, tier: str) -> str | None:
        """从 HTML 提取正文并写入 result。成功返回 None，否则返回失败原因。"""
        extracted = await asyncio.to_thread(extract_static, html)
        if extracted is None or len(extracted.content) < MIN_CONTENT_LENGTH:
            reason = "提取不到有效正文"
            result.attempts.append({"tier": tier, "ok": False, "reason": reason})
            return reason

        # 长度够但可能是错误页（反爬/文章被删），单独识别
        err = detect_error_page(html, extracted.content)
        if err and len(extracted.content) < 500:
            result.attempts.append({"tier": tier, "ok": False, "reason": err})
            return err

        result.ok = True
        result.content = extracted.content
        result.title = extracted.title
        result.content_length = len(extracted.content)
        result.tier = tier
        result.error = ""
        result.attempts.append({"tier": tier, "ok": True, "chars": len(extracted.content)})
        return None

    async def _fetch_static(self, url: str) -> str | None:
        try:
            import trafilatura

            fast = min(max(self.timeout, 5), 15)
            html = await asyncio.wait_for(
                asyncio.to_thread(trafilatura.fetch_url, url), timeout=fast
            )
            return html or None
        except asyncio.TimeoutError:
            logger.debug("[fetch] trafilatura 超时: %s", url[:60])
            return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("[fetch] trafilatura 失败: %s", str(exc)[:80])
            return None

    async def _fetch_httpx(self, url: str) -> str | None:
        """httpx + 完整浏览器指纹。

        缺 Sec-Ch-Ua/Sec-Fetch-*/HTTP2 时 CSDN 等站会概率性返回 521，
        这套头是实测补出来的，别删。
        """
        try:
            import httpx
        except ImportError:
            return None

        from urllib.parse import urlparse

        parsed = urlparse(url)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": f"{parsed.scheme}://{parsed.netloc}/",
            "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-site",
            "Upgrade-Insecure-Requests": "1",
        }
        try:
            async with httpx.AsyncClient(
                timeout=min(self.timeout, 15), follow_redirects=True, http2=True
            ) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    return None
                return resp.text or None
        except Exception as exc:  # noqa: BLE001
            logger.debug("[fetch] httpx 失败: %s", str(exc)[:80])
            return None

    def _is_traf_cooling(self, domain: str) -> bool:
        ts = self._traf_fail.get(domain)
        return ts is not None and (time.time() - ts) < _FAIL_TTL

    def _mark_traf_fail(self, domain: str) -> None:
        self._traf_fail[domain] = time.time()


def _domain_of(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""
