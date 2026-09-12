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

from .browser import BrowserEngine, PageFetch
from .errors import describe_status
from .extract import (
    MIN_CONTENT_LENGTH,
    detect_error_page,
    extract_static,
    looks_unrendered,
)
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
    status: int | None = None         # 服务器返回的 HTTP 状态码
    content_length: int = 0
    elapsed: float = 0.0
    error: str = ""
    warning: str = ""                 # 拿到了内容但可能不完整时的提示
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
            "status": self.status,
            "tier": self.tier,
            "elapsed": round(self.elapsed, 2),
            "error": self.error,
            "warning": self.warning,
            "truncated": self.truncated or (bool(max_chars) and len(self.content) > max_chars),
            "attempts": self.attempts,
        }


def _clear_extracted(result: FetchResult) -> None:
    """清空已提取的正文，为重试让位（保留 attempts 记录）。"""
    result.ok = False
    result.content = ""
    result.title = ""
    result.content_length = 0
    result.tier = ""
    result.error = ""


def _worth_retrying(status: int | None, note: str) -> bool:
    """判断这次失败重试是否有意义。

    重试只在"服务器临时问题"或"页面没渲染出来"时有用：
      - 5xx / 429：服务端异常或限流，换个时刻可能就好
      - 骨架页（正文过少）：接口概率性失败，重试常能拿到
    而 404 / 403 这类是确定性的，重试只是浪费一次加载。
    """
    if status is not None:
        if status >= 500 or status == 429:
            return True
        if status >= 400:
            return False
    return "无有效正文" in note or "过短" in note or "未渲染" in note


class Fetcher:
    """分层网页抓取器。"""

    def __init__(
        self,
        engine: BrowserEngine | None = None,
        timeout: float = 25.0,
        use_browser: bool = True,
        use_static: bool = True,
        use_httpx: bool = True,
        browser_retries: int = 2,
    ):
        self.engine = engine or BrowserEngine()
        self.timeout = timeout
        self.use_browser = use_browser
        self.use_static = use_static
        self.use_httpx = use_httpx
        # 疑似未渲染时的额外尝试次数。默认 2 次：实测万方这类站
        # 单次成功率约一半，两次重试后成功率明显提升，而耗时可控。
        self.browser_retries = max(0, browser_retries)
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

        # Tier 1: 浏览器（内容疑似没渲染出来时重试）。
        # 部分站点的内容靠接口异步填充，而接口会概率性失败，页面停在
        # 只有导航的骨架状态。此时抓取"成功"了但内容不对，重试通常能拿到。
        # 全程保留最完整的一次结果，避免重试反而丢掉已拿到的内容。
        if self.use_browser:
            best: tuple[str, str] = ("", "")
            last_note = ""
            for attempt in range(self.browser_retries + 1):
                last = attempt >= self.browser_retries
                page = PageFetch()
                html = await self.engine.fetch_html_guarded(
                    url, timeout=self.timeout, result=page
                )
                if page.status is not None:
                    result.status = page.status
                if not html:
                    last_note = page.error or "浏览器未取到页面"
                    result.attempts.append(
                        {"tier": "browser", "ok": False, "reason": last_note}
                    )
                    break

                note = await self._consume_html(
                    html, result, "browser", page.status, record=False
                )
                if note is None:
                    if len(result.content) > len(best[0]):
                        best = (result.content, result.title or "")
                    if not looks_unrendered(html, result.content):
                        # 内容正常，记一条成功即可收工
                        result.attempts.append(
                            {"tier": "browser", "ok": True,
                             "chars": result.content_length}
                        )
                        result.elapsed = time.monotonic() - started
                        return result
                    last_note = (
                        f"正文仅 {result.content_length} 字符，疑似未渲染出内容"
                    )
                    if last:
                        break
                    _clear_extracted(result)
                    continue

                last_note = note
                # 骨架页／5xx 重试有意义；404 这类确定性的重试无意义
                if last or not _worth_retrying(page.status, note):
                    break
                _clear_extracted(result)

            # 重试用尽仍未拿到正常内容，用历次最完整的一次兜底。
            # 标 ok=True 但带 warning——内容可能不完整，让调用方能判断。
            if best[0]:
                result.ok = True
                result.content = best[0]
                result.title = best[1]
                result.content_length = len(best[0])
                result.tier = "browser"
                result.error = ""
                result.warning = (
                    f"{last_note}；已返回最完整的一次结果"
                    f"（{len(best[0])} 字符，共尝试 {self.browser_retries + 1} 次）"
                )
                result.attempts.append(
                    {"tier": "browser", "ok": True, "chars": len(best[0]),
                     "note": "重试后采用最完整的一次结果"}
                )
                # 已拿到内容（可能不完整）：不再降级。降级层拿到的是同一份
                # HTML 的更差提取结果，只会把内容换得更少。
                result.elapsed = time.monotonic() - started
                return result
            elif last_note:
                result.error = last_note
                result.attempts.append(
                    {"tier": "browser", "ok": False, "reason": last_note}
                )
                # 确定性失败（404 等）不必再试其他层
                if not _worth_retrying(result.status, last_note):
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
            html, status = await self._fetch_httpx(url)
            if html:
                note = await self._consume_html(html, result, "httpx", status)
                if note is None:
                    result.elapsed = time.monotonic() - started
                    return result
                self._mark_traf_fail(domain)
                if not result.error:
                    result.error = note
                if result.status is None:
                    result.status = status
            elif status is not None and status >= 400:
                hint = describe_status(status)
                result.attempts.append({"tier": "httpx", "ok": False, "reason": hint})
                if not result.error:
                    result.error = hint
                if result.status is None:
                    result.status = status

        result.elapsed = time.monotonic() - started
        if not result.error:
            result.error = "所有抓取方式均未取到有效正文"
            result.attempts.append({"tier": "none", "ok": False, "reason": result.error})
        elif result.content_length == 0:
            # 有明确错误原因时，补一条汇总便于快速定位
            result.attempts.append(
                {"tier": "none", "ok": False, "reason": result.error}
            )
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

    async def _consume_html(
        self,
        html: str,
        result: FetchResult,
        tier: str,
        status: int | None = None,
        record: bool = True,
    ) -> str | None:
        """从 HTML 提取正文并写入 result。成功返回 None，否则返回失败原因。

        status >= 400 时即使提取到文字也不算成功：错误页往往带导航和
        站点说明，长度可能过阈值，但内容不是正文。

        record=False 时不写 attempts：重试场景下由调用方统一记录，
        否则每次尝试都留一条成功记录，语义会乱。
        """
        extracted = await asyncio.to_thread(extract_static, html)
        if extracted is None or len(extracted.content) < MIN_CONTENT_LENGTH:
            # 先看是不是"带 200 状态码的错误页"：这类站点返回正常状态码，
            # 页面里却写着"页面不存在"，只报"无有效正文"会掩盖真实原因
            page_err = detect_error_page(html, extracted.content if extracted else "")
            if page_err:
                if record:
                    result.attempts.append(
                        {"tier": tier, "ok": False, "reason": page_err}
                    )
                return page_err

            # 优先用错误状态码解释；2xx 但无正文属于内容问题，单独说明
            reason = describe_status(status)
            if not reason:
                reason = (
                    "服务器返回 HTTP 200 但页面无有效正文"
                    if status == 200
                    else "提取不到有效正文"
                )
            if record:
                result.attempts.append({"tier": tier, "ok": False, "reason": reason})
            return reason

        # 长度够但可能是错误页（反爬/文章被删/404 页），单独识别
        err = detect_error_page(html, extracted.content)
        if err and len(extracted.content) < 500:
            if record:
                result.attempts.append({"tier": tier, "ok": False, "reason": err})
            return err

        if status is not None and status >= 400:
            reason = describe_status(status)
            if record:
                result.attempts.append(
                    {"tier": tier, "ok": False,
                     "reason": f"{reason}；页面文字疑似错误页内容"}
                )
            return reason

        result.ok = True
        result.content = extracted.content
        result.title = extracted.title
        result.content_length = len(extracted.content)
        result.tier = tier
        result.error = ""
        # 状态码以"真正拿到正文的那一层"为准：浏览器层可能碰到 502，
        # 而正文是从 trafilatura 层取到的，留旧的 502 会误导调用方。
        # 该层没提供状态码（如 trafilatura）时置空，表示未知。
        result.status = status
        if record:
            result.attempts.append(
                {"tier": tier, "ok": True, "chars": len(extracted.content)}
            )
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

    async def _fetch_httpx(self, url: str) -> tuple[str | None, int | None]:
        """httpx + 完整浏览器指纹。返回 (HTML, 状态码)。

        缺 Sec-Ch-Ua/Sec-Fetch-*/HTTP2 时 CSDN 等站会概率性返回 521，
        这套头是实测补出来的，别删。
        """
        try:
            import httpx
        except ImportError:
            return None, None

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
                    return None, resp.status_code
                return (resp.text or None), resp.status_code
        except Exception as exc:  # noqa: BLE001
            logger.debug("[fetch] httpx 失败: %s", str(exc)[:80])
            return None, None

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
