"""URL 安全校验（SSRF 防护）。

只放行 http/https，并拒绝解析到私网、回环、链路本地等地址的主机。
默认拦截，可用 BETTER_CRAWLER_ALLOW_PRIVATE=1 放开（本地开发调试用）。
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = ("http", "https")

# 这些主机名无论解析结果如何都直接拒绝
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
    }
)

_ALLOW_PRIVATE = os.getenv("BETTER_CRAWLER_ALLOW_PRIVATE", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


class UnsafeURLError(ValueError):
    """URL 未通过安全校验。"""


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """判断单个 IP 是否属于不应访问的范围。"""
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return True
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    # IPv6 里映射过来的 IPv4（::ffff:127.0.0.1）也要按 IPv4 规则查一遍
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_blocked_ip(ip.ipv4_mapped)
    return False


def _resolve_ips(hostname: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """解析主机名的全部 A/AAAA 记录。"""
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"域名无法解析: {hostname}") from exc

    ips = []
    for info in infos:
        addr = info[4][0]
        # getaddrinfo 对 IPv6 会带 scope id（fe80::1%eth0），去掉后再解析
        addr = addr.split("%", 1)[0]
        try:
            ips.append(ipaddress.ip_address(addr))
        except ValueError:
            continue
    if not ips:
        raise UnsafeURLError(f"域名无法解析: {hostname}")
    return ips


def validate_url(url: str) -> str:
    """校验 URL 并返回规范化后的形式。

    Raises:
        UnsafeURLError: 协议不允许、缺少主机名，或主机解析到内网地址。
    """
    if not url or not isinstance(url, str):
        raise UnsafeURLError("URL 不能为空")

    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"仅支持 http/https，收到: {parsed.scheme or '(空)'}"
        )
    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError("URL 缺少主机名")

    if _ALLOW_PRIVATE:
        return url.strip()

    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise UnsafeURLError(f"拒绝访问本机地址: {hostname}")

    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    for ip in _resolve_ips(hostname, port):
        if _is_blocked_ip(ip):
            raise UnsafeURLError(
                f"拒绝访问内网地址: {hostname} -> {ip}"
            )
    return url.strip()
