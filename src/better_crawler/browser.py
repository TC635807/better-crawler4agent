"""浏览器抓取引擎。

共享单例 Chromium，负责取回渲染后的 HTML 或正文。这里的工程细节
都来自实战踩坑，改动前请先读注释：

  - 单例 + 信号量：每 URL 起一个 Chromium 会迅速吃满内存
  - asyncio.shield + done_callback：超时取消会让 Playwright 内部 future
    异常泄漏（TargetClosedError: future exception was never retrieved）
  - 崩溃检测 + 单例重置：Chromium 挂掉后必须重建，否则后续全失败
  - --headless=new + 完整 sec-ch-ua：缺了这两样，知乎等站只返回空壳
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

logger = logging.getLogger(__name__)

# 一次只跑少量页面，太多会导致 Chromium OOM/崩溃
_MAX_CONCURRENCY = 5

# 与 UA 保持一致的客户端提示头。UA 和 sec-ch-ua 版本不一致会被反爬识别
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_SEC_CH_UA = '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"'

# 覆盖常见的自动化指纹。crawl4ai 也注入同一份脚本
_NAVIGATOR_OVERRIDE = """
Object.defineProperty(navigator, "webdriver", { get: () => undefined });
window.navigator.chrome = { runtime: {} };
Object.defineProperty(navigator, "plugins", { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, "languages", { get: () => ["zh-CN", "zh", "en"] });
Object.defineProperty(document, "hidden", { get: () => false });
Object.defineProperty(document, "visibilityState", { get: () => "visible" });
const _origQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (p) =>
    p.name === "notifications"
        ? Promise.resolve({ state: Notification.permission })
        : _origQuery(p);
"""

_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
]

_CRASH_MARKERS = (
    "Target page, context or browser has been closed",
    "Protocol error",
    "Browser has been closed",
    "Target closed",
    "has been closed",
)


class BrowserEngine:
    """共享 Chromium 的异步封装。"""

    def __init__(self, headless: bool = True, concurrency: int = _MAX_CONCURRENCY):
        self.headless = headless
        self._sem = asyncio.Semaphore(concurrency)
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._lock = asyncio.Lock()
        self._unavailable_reason: str | None = None

    # ── 生命周期 ────────────────────────────────────────

    @property
    def unavailable_reason(self) -> str | None:
        """浏览器不可用的原因；None 表示可用或尚未尝试。"""
        return self._unavailable_reason

    async def start(self) -> bool:
        """启动浏览器。返回是否可用。失败原因记在 unavailable_reason。"""
        if self._browser is not None:
            return True
        if self._unavailable_reason is not None:
            return False
        async with self._lock:
            if self._browser is not None:
                return True
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                self._unavailable_reason = (
                    "未安装 playwright。请执行: pip install playwright && playwright install chromium"
                )
                logger.warning("[engine] %s", self._unavailable_reason)
                return False
            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=self.headless,
                    args=_LAUNCH_ARGS,
                )
                self._context = await self._browser.new_context(
                    user_agent=_UA,
                    viewport={"width": 1080, "height": 600},
                    locale="zh-CN",
                    extra_http_headers={
                        "sec-ch-ua": _SEC_CH_UA,
                        "sec-ch-ua-mobile": "?0",
                        "sec-ch-ua-platform": '"Windows"',
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    },
                )
                await self._context.add_init_script(_NAVIGATOR_OVERRIDE)
                logger.info("[engine] 浏览器已启动")
                return True
            except Exception as exc:  # noqa: BLE001 - 任何启动失败都降级
                self._unavailable_reason = f"浏览器启动失败: {exc}"
                logger.warning("[engine] %s", self._unavailable_reason)
                await self._cleanup_locked()
                return False

    async def close(self) -> None:
        async with self._lock:
            await self._cleanup_locked()

    async def _cleanup_locked(self) -> None:
        for obj, closer in (
            (self._context, "close"),
            (self._browser, "close"),
        ):
            if obj is None:
                continue
            try:
                await getattr(obj, closer)()
            except Exception:  # noqa: BLE001 - 关闭失败不影响主流程
                pass
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
        self._context = None
        self._browser = None
        self._playwright = None

    def _mark_crashed(self) -> None:
        """Chromium 崩溃后丢弃句柄，下次调用自动重建。"""
        logger.info("[engine] 浏览器疑似崩溃，重置单例")
        self._context = None
        self._browser = None
        self._playwright = None

    # ── 抓取 ────────────────────────────────────────────

    async def fetch_html(
        self,
        url: str,
        timeout: float = 25.0,
        wait_after_load: float = 1.2,
        simulate_user: bool = True,
    ) -> str | None:
        """取回渲染后的 HTML。失败返回 None。

        Args:
            url: 目标地址
            timeout: 整页超时（秒）
            wait_after_load: 加载后额外等待，给 JS 渲染留时间
            simulate_user: 是否模拟鼠标移动/滚动
        """
        if not await self.start():
            return None

        page_timeout_ms = max(int(timeout * 1000), 5000)

        async with self._sem:
            page = None
            try:
                page = await self._context.new_page()
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=page_timeout_ms
                )
                if simulate_user:
                    # 只发鼠标/滚轮信号，不点固定坐标（可能误触链接跳走）
                    await page.mouse.move(
                        random.randint(100, 300), random.randint(150, 300)
                    )
                    await page.mouse.wheel(0, random.randint(200, 400))
                if wait_after_load > 0:
                    await page.wait_for_timeout(int(wait_after_load * 1000))
                return await page.content()
            except asyncio.TimeoutError:
                logger.debug("[engine] 超时: %s", url[:60])
                return None
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if any(marker in msg for marker in _CRASH_MARKERS):
                    self._mark_crashed()
                logger.debug("[engine] 抓取失败: %s - %s", msg[:80], url[:60])
                return None
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except Exception:  # noqa: BLE001
                        pass

    async def fetch_html_guarded(
        self,
        url: str,
        timeout: float = 25.0,
        wait_after_load: float = 1.2,
        simulate_user: bool = True,
    ) -> str | None:
        """带整体超时保护的 fetch_html。

        超时时不取消底层任务（取消会让 Playwright future 异常泄漏），
        而是挂一个 done_callback 把结果/异常消费掉，然后放弃等待。
        """
        task = asyncio.ensure_future(
            self.fetch_html(url, timeout, wait_after_load, simulate_user)
        )
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout + 5)
        except asyncio.TimeoutError:
            task.add_done_callback(
                lambda t: t.exception() if not t.cancelled() else None
            )
            logger.debug("[engine] 整体超时，放弃等待: %s", url[:60])
            return None
        except Exception:  # noqa: BLE001
            return None
