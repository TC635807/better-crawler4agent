"""正文提取。

思路：并行收集多个候选，再按质量挑最优，而不是"第一个够长就用"。

候选来源：
  1. trafilatura —— 对博客/文档站正文识别最好
  2. 站点专用选择器 —— 知乎 .RichText、CSDN #content_views 等
  3. 最大文本块 —— 最后的兜底

两个坑（都踩过）：
  - 知乎会插入字体指纹蜜罐节点，内容是 "mmmmlli" 之类重复串，
    长度能到上万字符，纯按长度挑就会选中它
  - 选择器只取第一个匹配会漏正文：知乎页面上 .RichText.ztext 有 3 个节点，
    第一个可能只是摘要预览
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 低于此长度视为提取失败，触发下一级
MIN_CONTENT_LENGTH = 200

_NOISE_TAGS = (
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "aside",
    "noscript",
    "iframe",
    "form",
    "svg",
)

# 站点专用正文容器
_SITE_SELECTORS = (
    # 知乎
    ".RichText.ztext",
    ".Post-RichText",
    ".RichContent-inner",
    # CSDN
    "#content_views",
    ".htmledit_views",
    # 微信公众号
    "#js_content",
    # 掘金 / 简书 / 博客园 / 少数派
    ".markdown-body",
    ".article-content",
    "#cnblogs_post_body",
    ".post-content",
    # 通用语义标签
    "article",
    "main",
    "[role=main]",
)


@dataclass
class Extracted:
    """一次提取的结果。"""
    content: str
    title: str
    method: str          # trafilatura | selector:<css> | largest-block
    truncated: bool = False


def _clean_text(text: str) -> str:
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def is_honeypot(text: str) -> bool:
    """识别字体指纹蜜罐：短 token 大量重复、几乎没有真实词汇。

    知乎会注入形如 "mmmmmmmmmmlli" 重复上万字符的隐藏节点。
    """
    if len(text) < 400:
        return False
    sample = text[:4000]
    # 按空白切词，看是否有超长重复单元
    tokens = sample.split()
    if len(tokens) < 20:
        # 没有空白分隔，看字符级重复度
        compact = re.sub(r"\s", "", sample)
        if not compact:
            return False
        # 统计最常见的 3~8 字符片段占比
        for size in (3, 5, 8):
            chunk = compact[:size]
            if chunk and compact.count(chunk) * size > len(compact) * 0.5:
                return True
        return False

    from collections import Counter

    counts = Counter(tokens)
    top_token, top_count = counts.most_common(1)[0]
    # 单个 token 占绝对多数且本身很短 → 蜜罐
    if top_count > len(tokens) * 0.5 and len(top_token) <= 12:
        return True
    # 去重后词种极少 → 蜜罐
    if len(set(tokens)) < max(5, len(tokens) * 0.05):
        return True
    return False


def _extract_title(html: str) -> str:
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001
        return ""
    for finder in (
        lambda: soup.find("meta", property="og:title"),
        lambda: soup.find("title"),
        lambda: soup.find("h1"),
    ):
        try:
            node = finder()
        except Exception:  # noqa: BLE001
            continue
        if node is None:
            continue
        raw = (
            node.get("content")
            if node.name == "meta"
            else node.get_text(" ", strip=True)
        )
        if raw:
            return _clean_text(str(raw))[:200]
    return ""


def _trafilatura_candidate(html: str) -> str:
    try:
        import trafilatura

        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_precision=False,
            no_fallback=False,
        )
        return _clean_text(text) if text else ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("[extract] trafilatura 失败: %s", str(exc)[:80])
        return ""


def _selector_candidates(soup) -> list[tuple[str, str]]:
    """返回 (选择器, 正文) 列表。每个选择器取其所有匹配中最长的那个。"""
    out: list[tuple[str, str]] = []
    for selector in _SITE_SELECTORS:
        try:
            nodes = soup.select(selector)
        except Exception:  # noqa: BLE001
            continue
        best = ""
        for node in nodes:
            text = _clean_text(node.get_text("\n", strip=True))
            if len(text) > len(best):
                best = text
        if best:
            out.append((selector, best))
    return out


def _largest_block(soup) -> str:
    best = ""
    for node in soup.find_all(["article", "main", "div", "section"]):
        text = _clean_text(node.get_text(" ", strip=True))
        if len(text) > len(best) and not is_honeypot(text):
            best = text
    return best


def extract_static(html: str) -> Extracted | None:
    """从 HTML 提取正文（不启动浏览器）。失败返回 None。"""
    if not html or len(html) < 200:
        return None

    title = _extract_title(html)

    traf = _trafilatura_candidate(html)
    if is_honeypot(traf):
        traf = ""

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        if traf and len(traf) >= MIN_CONTENT_LENGTH:
            return Extracted(traf, title, "trafilatura")
        return None

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001
        return None

    for tag in soup(_NOISE_TAGS):
        tag.decompose()

    selectors = [
        (sel, txt) for sel, txt in _selector_candidates(soup) if not is_honeypot(txt)
    ]
    best_sel, best_sel_text = ("", "")
    for sel, txt in selectors:
        if len(txt) > len(best_sel_text):
            best_sel, best_sel_text = sel, txt

    # trafilatura 通常最干净；但站点选择器明显更长时说明 trafilatura 漏了正文
    if traf and len(traf) >= MIN_CONTENT_LENGTH and len(traf) >= len(best_sel_text) * 0.5:
        return Extracted(traf, title, "trafilatura")

    if best_sel_text and len(best_sel_text) >= MIN_CONTENT_LENGTH:
        return Extracted(best_sel_text, title, f"selector:{best_sel}")

    if traf and len(traf) >= MIN_CONTENT_LENGTH:
        return Extracted(traf, title, "trafilatura")

    largest = _largest_block(soup)
    if len(largest) >= MIN_CONTENT_LENGTH:
        return Extracted(largest, title, "largest-block")

    return None


def detect_error_page(html: str, content: str) -> str | None:
    """识别"看起来成功、其实是错误页"的情况。

    返回错误说明，正常页面返回 None。反爬拦截和文章被删都可能返回
    200 + 一小段无关内容，只看长度会误判成成功。
    """
    if not html:
        return None
    low = html[:20000].lower()

    # 知乎的文章不存在/已删除会渲染一张沙漠插画
    if "liukanshan_desert" in low:
        return "页面提示文章不存在或已删除（知乎错误页）"
    if len(content) < MIN_CONTENT_LENGTH:
        if any(k in low for k in ("验证码", "captcha", "人机验证", "安全检查")):
            return "疑似被反爬拦截（要求验证码）"
        if any(k in low for k in ("404", "not found", "页面不存在", "已删除", "违规")):
            return "页面疑似不存在或已被删除"
        return "正文过短，可能未成功渲染"
    return None
