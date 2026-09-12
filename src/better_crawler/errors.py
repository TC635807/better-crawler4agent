"""把底层错误翻译成可操作的说明。

抓取失败的原因五花八门，但统一报成"提取不到有效正文"会让调用方无法判断
该重试、该换 URL，还是该放弃。这里把 HTTP 状态码和 Chromium 网络错误
映射成人能看懂的说明。

区分"页面有问题"和"抓取被拦"很重要：
  - 404 / 410  → 换 URL
  - 403 / 429  → 反爬或限流，可重试
  - 5xx        → 服务端异常，可稍后重试
  - DNS / 连接 → 网络环境问题
"""

from __future__ import annotations

_STATUS_HINTS: dict[int, str] = {
    400: "请求无效",
    401: "需要登录（页面要求身份验证）",
    403: "服务器拒绝访问（可能触发反爬）",
    404: "页面不存在",
    405: "请求方法不被允许",
    406: "服务器拒绝该请求",
    407: "需要代理认证",
    408: "请求超时",
    410: "页面已永久移除",
    418: "服务器拒绝处理该请求",
    429: "请求过于频繁（被限流）",
    451: "因法律原因不可访问",
    500: "服务器内部错误",
    502: "网关错误（服务端异常）",
    503: "服务暂不可用",
    504: "网关超时",
    507: "服务器存储不足",
    521: "源站不可达（Cloudflare 报错）",
    522: "连接源站超时（Cloudflare 报错）",
}

# Chromium 的网络错误码 → 说明。顺序有意义，先匹配具体项
_NET_ERROR_HINTS: tuple[tuple[str, str], ...] = (
    ("ERR_NAME_NOT_RESOLVED", "域名无法解析（DNS 问题）"),
    ("ERR_NAME_RESOLUTION_FAILED", "域名无法解析（DNS 问题）"),
    ("ERR_INTERNET_DISCONNECTED", "本机网络已断开"),
    ("ERR_ADDRESS_UNREACHABLE", "地址不可达"),
    ("ERR_CONNECTION_REFUSED", "连接被拒绝"),
    ("ERR_CONNECTION_TIMED_OUT", "连接超时"),
    ("ERR_CONNECTION_RESET", "连接被重置"),
    ("ERR_CONNECTION_CLOSED", "连接被关闭"),
    ("ERR_EMPTY_RESPONSE", "服务器返回空响应"),
    ("ERR_TIMED_OUT", "请求超时"),
    ("ERR_ABORTED", "请求被中断"),
    ("ERR_CERT", "HTTPS 证书错误"),
    ("ERR_SSL", "SSL 握手失败"),
    ("ERR_PROXY", "代理连接失败"),
    ("ERR_TOO_MANY_REDIRECTS", "重定向次数过多"),
)


def describe_status(status: int | None) -> str:
    """把 HTTP 状态码翻译成说明。

    只对 >= 400 的状态码返回说明：2xx/3xx 表示请求本身是成功的，
    此时提取不到正文属于内容问题，用状态码解释反而误导。
    """
    if status is None or status < 400:
        return ""
    hint = _STATUS_HINTS.get(status)
    if hint:
        return f"服务器返回 HTTP {status}（{hint}）"
    if status >= 500:
        return f"服务器返回 HTTP {status}（服务端错误）"
    return f"服务器返回 HTTP {status}（客户端错误）"


def describe_net_error(message: str) -> str | None:
    """从浏览器异常信息里识别网络错误。识别不出返回 None。"""
    if not message:
        return None
    for marker, hint in _NET_ERROR_HINTS:
        if marker in message:
            return hint
    return None


def describe_browser_error(message: str) -> str:
    """浏览器层异常的完整说明。

    先看是不是已知网络错误，否则回退到截断后的原始信息——
    保留原文总比丢掉好，但截断避免把整段堆栈塞进返回值。
    """
    net = describe_net_error(message)
    if net:
        return net
    short = " ".join(str(message).split())[:150]
    return f"浏览器抓取失败: {short}" if short else "浏览器抓取失败"
