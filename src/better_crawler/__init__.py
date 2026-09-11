"""better-crawler-4-agent：把 URL 变成正文。

对外暴露 Fetcher（分层抓取）与 BrowserEngine（共享浏览器）。
"""

from .browser import BrowserEngine
from .extract import Extracted, extract_static
from .fetcher import Fetcher, FetchResult
from .safety import UnsafeURLError, validate_url

__version__ = "0.1.0"

__all__ = [
    "BrowserEngine",
    "Extracted",
    "FetchResult",
    "Fetcher",
    "UnsafeURLError",
    "extract_static",
    "validate_url",
    "__version__",
]
