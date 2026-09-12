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


def test_detect_soft_404_returns_200_error_page():
    """站点用 200 状态码渲染"页面不存在"，必须识别为错误页。"""
    html = (
        "<html><head><title>指定的页面不存在</title></head>"
        "<body>指定的页面不存在 404 错误 站长请点击 返回上一级>></body></html>"
    )
    err = detect_error_page(html, "指定的页面不存在 404 错误")
    assert err is not None
    assert "不存在" in err


def test_detect_short_page_is_not_error():
    """页面只是短（如图片为主的文章）不该误报成错误页。"""
    html = "<html><head><title>图片文章</title></head><body><p>见下图</p></body></html>"
    assert detect_error_page(html, "见下图") is None


# ── 状态码说明 ──────────────────────────────────────────


def test_describe_status_maps_common_codes():
    from better_crawler import describe_status

    assert "404" in describe_status(404)
    assert "不存在" in describe_status(404)
    assert "502" in describe_status(502)
    assert "限流" in describe_status(429)
    assert "反爬" in describe_status(403)


def test_describe_status_ignores_success_codes():
    """2xx/3xx 表示请求成功，不该被当作错误原因。"""
    from better_crawler import describe_status

    for code in (200, 201, 204, 301, 302, 304):
        assert describe_status(code) == ""
    assert describe_status(None) == ""


def test_describe_browser_error_maps_network_errors():
    from better_crawler.errors import describe_browser_error

    assert "DNS" in describe_browser_error("net::ERR_NAME_NOT_RESOLVED")
    assert "超时" in describe_browser_error("net::ERR_CONNECTION_TIMED_OUT")
    # 未知错误保留原文摘要，不丢信息
    assert "未知错误" in describe_browser_error("some unknown failure 未知错误")


# ── 未渲染检测 ──────────────────────────────────────────


def test_unrendered_detects_skeleton_page():
    """HTML 框架很大但正文极少 → 骨架页，该重试。

    实测万方检索页：失败时 193KB / 727 字符，成功时 481KB / 5720 字符。
    """
    from better_crawler import looks_unrendered

    html = "<html><body>" + "<div class='nav'>导航</div>" * 2000 + "</body></html>"
    assert len(html) > 30000
    assert looks_unrendered(html, "导航" * 100)


def test_unrendered_allows_rich_content():
    from better_crawler import looks_unrendered

    html = "<html><body>" + "<div>正文</div>" * 2000 + "</body></html>"
    assert not looks_unrendered(html, "正文内容" * 1000)


def test_unrendered_ignores_small_html():
    """HTML 本身就小 → 不是骨架页，避免误判轻量页面。"""
    from better_crawler import looks_unrendered

    html = "<html><body><p>短</p></body></html>"
    assert not looks_unrendered(html, "短")


def test_unrendered_handles_empty():
    from better_crawler import looks_unrendered

    assert not looks_unrendered("", "")
    assert not looks_unrendered("", "有内容" * 100)


# ── 重试决策 ────────────────────────────────────────────


def test_retry_only_for_transient_failures():
    """5xx/429 与骨架页值得重试；404/403 是确定性的，重试没意义。"""
    from better_crawler.fetcher import _worth_retrying

    assert _worth_retrying(502, "网关错误")
    assert _worth_retrying(503, "服务不可用")
    assert _worth_retrying(429, "限流")
    assert not _worth_retrying(404, "页面不存在")
    assert not _worth_retrying(403, "拒绝访问")
    # 无状态码时看原因描述
    assert _worth_retrying(None, "服务器返回 HTTP 200 但页面无有效正文")
    assert not _worth_retrying(None, "页面提示内容不存在或已被删除")
