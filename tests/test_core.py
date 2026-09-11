"""单元测试：不联网的部分。

运行: python -m pytest tests/ -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from better_crawler import UnsafeURLError, extract_static, validate_url  # noqa: E402
from better_crawler.extract import detect_error_page, is_honeypot  # noqa: E402


# ── SSRF 防护 ──────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:8000/",
        "http://192.168.1.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com/",
        "",
        "http://",
    ],
)
def test_rejects_unsafe_urls(url):
    with pytest.raises(UnsafeURLError):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    ["https://example.com/x", "http://example.com/", "https://zhuanlan.zhihu.com/p/1"],
)
def test_accepts_public_urls(url):
    assert validate_url(url) == url


# ── 蜜罐识别 ────────────────────────────────────────────


def test_honeypot_detects_repeated_token():
    assert is_honeypot("mmmmmmmmmmlli " * 200)


def test_honeypot_detects_dense_repeat():
    assert is_honeypot("mmmmmmmmmmlli" * 200)


def test_honeypot_allows_real_text():
    text = (
        "在2021年1月13日，教育部发布了《各专业学位类别领域设置情况》的文件。"
        "其中电子信息专硕一共有12个专业方向，有5个是属于计算机类的。"
    ) * 6
    assert not is_honeypot(text)


def test_honeypot_ignores_short_text():
    assert not is_honeypot("mmmlli")


# ── 正文提取 ────────────────────────────────────────────


def _page(body: str, extra: str = "") -> str:
    return f"""<html><head><title>测试标题</title></head>
    <body><nav>导航栏内容</nav>
    <div id="content_views"><p>{body}</p></div>
    {extra}
    <footer>页脚</footer></body></html>"""


def test_extract_finds_content_and_title():
    body = "这是一段足够长的正文内容，用来验证提取器能够正常工作。" * 12
    result = extract_static(_page(body))
    assert result is not None
    assert result.title == "测试标题"
    assert "足够长的正文内容" in result.content
    assert result.method.startswith(("trafilatura", "selector"))


def test_extract_returns_none_on_short_content():
    assert extract_static("<html><body><p>短</p></body></html>") is None


def test_extract_returns_none_on_empty():
    assert extract_static("") is None
    assert extract_static("<html></html>") is None


def test_extract_skips_honeypot_node():
    """蜜罐比正文长时不能选中它。"""
    real = "这是真正的正文段落，内容充实且有意义，需要被正确提取出来。" * 10
    honey = "mmmmmmmmmmlli " * 800
    html = f"""<html><head><title>T</title></head><body>
    <div id="content_views"><p>{real}</p></div>
    <div class="honey">{honey}</div>
    </body></html>"""
    result = extract_static(html)
    assert result is not None
    assert "真正的正文段落" in result.content
    assert "mmmlli" not in result.content


# ── 错误页识别 ──────────────────────────────────────────


def test_detect_deleted_zhihu_page():
    html = '<html><body><img src="https://static.zhihu.com/heifetz/assets/liukanshan_desert.ecf3c388.svg"></body></html>'
    assert detect_error_page(html, "短内容") is not None


def test_detect_captcha():
    html = "<html><body>请完成验证码验证后继续访问</body></html>"
    assert detect_error_page(html, "短") is not None


def test_detect_normal_page_returns_none():
    html = "<html><body>" + "正常正文内容" * 100 + "</body></html>"
    assert detect_error_page(html, "正常正文内容" * 100) is None
