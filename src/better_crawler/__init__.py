"""better-crawler-4-agent：把 URL 变成正文。

对外暴露 Fetcher（分层抓取）与 BrowserEngine（共享浏览器）。
"""

from .browser import BrowserEngine, PageFetch
from .errors import describe_status
from .extract import Extracted, extract_static, looks_unrendered
from .fetcher import Fetcher, FetchResult
from .safety import UnsafeURLError, validate_url

__version__ = "0.2.0"

__all__ = [
    "BrowserEngine",
    "Extracted",
    "FetchResult",
    "Fetcher",
    "PageFetch",
    "UnsafeURLError",
    "describe_status",
    "extract_static",
    "looks_unrendered",
    "validate_url",
    "__version__",
]
