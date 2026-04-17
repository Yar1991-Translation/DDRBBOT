from __future__ import annotations

from urllib.parse import urlparse


def validate_rsshub_feed_url(
    url: str,
    *,
    host_markers: frozenset[str],
    extra_hosts: frozenset[str],
) -> str:
    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("feed_url 须为 http 或 https")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("feed_url 缺少有效主机名")
    if host in extra_hosts:
        return cleaned
    if any(marker in host for marker in host_markers if marker):
        return cleaned
    raise ValueError(
        "当前 feed 主机不在 RSSHub 允许列表内；请在环境变量 RSSHUB_EXTRA_HOSTS 中加入该主机，"
        "或确保主机名包含 RSSHUB_HOST_MARKERS 中的任一段（默认含 rsshub）。"
    )
