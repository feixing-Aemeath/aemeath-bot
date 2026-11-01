"""Pure URL validation: never resolve short links or inspect a browser."""

import re
from urllib.parse import parse_qs, urlsplit

DOMAINS = [
    "bilibili.com",
    "www.bilibili.com",
    "m.bilibili.com",
    "b23.tv",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
]


def normalize(url, domains=DOMAINS):
    if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() for c in url):
        raise ValueError("视频链接无效")
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError as exc:
        raise ValueError("视频链接无效") from exc
    if (
        parts.scheme != "https"
        or host not in domains
        or port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
    ):
        raise ValueError("只接受白名单域名的 HTTPS 公开视频")
    query, path = parse_qs(parts.query, keep_blank_values=True), parts.path.rstrip("/")
    if any(k in query for k in ("list", "playlist", "series_id", "sid")):
        raise ValueError("只接受单个视频，不接受播放列表或频道")
    if host == "b23.tv":
        if not re.fullmatch(r"/[A-Za-z0-9]{1,64}", path) or parts.query:
            raise ValueError("B站短链接无效")
        return "bilibili", "short-" + path[1:], "https://b23.tv" + path
    if host in {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}:
        match = re.fullmatch(r"/video/(BV[A-Za-z0-9]{10}|av[0-9]{1,20})", path)
        if not match or ("p" in query and query["p"] != ["1"]):
            raise ValueError("只接受B站单个视频")
        identity = match[1]
        return "bilibili", identity, f"https://www.bilibili.com/video/{identity}"
    if host == "youtu.be":
        identity = path[1:]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if path == "/watch" and len(query.get("v", [])) == 1:
            identity = query["v"][0]
        else:
            match = re.fullmatch(r"/(?:shorts|embed)/([A-Za-z0-9_-]{11})", path)
            identity = match[1] if match else ""
    else:
        # Config can narrow hosts, but does not enable arbitrary yt-dlp extractors.
        raise ValueError("不支持这个视频平台")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", identity):
        raise ValueError("只接受 YouTube 单个视频")
    return "youtube", identity, f"https://www.youtube.com/watch?v={identity}"


def timestamp_link(platform, identity, seconds):
    if platform == "bilibili":
        return f"https://www.bilibili.com/video/{identity}?t={int(seconds)}"
    return f"https://www.youtube.com/watch?v={identity}&t={int(seconds)}s"
