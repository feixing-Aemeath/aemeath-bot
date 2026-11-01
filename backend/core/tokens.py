"""Device tokens: one token per device, each with its own scopes, revocable on its own.

`ASSISTANT_TOKEN` from the environment stays the Mac's own token with every scope; the local
Agents' stdio bridge uses it. Other devices (iPhone, a school computer, a Shortcuts automation)
each get a device token from `python -m scripts.tokens create`.

The database keeps only the SHA-256 of each token. Tokens are 256-bit random strings, so the
hash needs no salt: there is nothing to guess, and a copy of the database or of a backup
cannot be used to call the backend. The token itself is shown once, when it is created.
"""

import hashlib
import secrets
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

PREFIX = "a24_"
SCOPES = {
    "app": "App 的 HTTP 接口（/api/v1/…、/openapi.json）",
    "mcp": "MCP：远程 /mcp 和 stdio 桥接（/internal/tools）",
    "health_upload": "只能上传健康数据（POST /api/v1/health/samples）",
    "dav": "只能访问 WebDAV 备份（/dav/）",
}
ALL_SCOPES = frozenset(SCOPES)
NAME_LENGTH = 40
MAX_DAYS = 3650
# last_used_at is written at most this often per token, not on every request.
TOUCH_SECONDS = 60


@dataclass(frozen=True)
class Grant:
    """Who is calling: a device token, or ASSISTANT_TOKEN (token_id None)."""

    token_id: str | None
    name: str
    scopes: frozenset[str]
    reader: str | None = None
    kind: str | None = None
    area: str | None = None
    harness: str | None = None


MAIN = Grant(None, "ASSISTANT_TOKEN", ALL_SCOPES)
current_grant = ContextVar("memory_grant", default=MAIN)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def required_scopes(method: str, path: str) -> set[str]:
    """Scopes that may call a route; any one of them is enough. Everything that is not MCP
    counts as App API, so a route added later is never open to an MCP-only token."""
    if path == "/dav" or path.startswith("/dav/"):
        return {"dav", "app"}
    if path == "/mcp" or path.startswith(("/mcp/", "/internal/")):
        return {"mcp"}
    if method == "POST" and path == "/api/v1/health/samples":
        return {"app", "health_upload"}
    return {"app"}


def clean_name(name: str) -> str:
    name = name.strip()
    if not 1 <= len(name) <= NAME_LENGTH:
        raise ValueError(f"名称要 1 到 {NAME_LENGTH} 个字符")
    if any(unicodedata.category(c).startswith("C") for c in name):
        raise ValueError("名称里不能有控制字符")
    if name.startswith("dev_"):
        raise ValueError("名称不能以 dev_ 开头（那是令牌 ID 的格式）")
    return name


def clean_scopes(scopes) -> list[str]:
    chosen = sorted({s.strip() for s in scopes if s.strip()})
    unknown = set(chosen) - ALL_SCOPES
    if unknown or not chosen:
        raise ValueError(f"权限只能是 {', '.join(SCOPES)} 中的一个或几个")
    return chosen


class DeviceTokens:
    def __init__(self, db, clock=None):
        self.db = db
        self.clock = clock or (lambda: datetime.now(UTC))
        self._touched = {}
        self._temporary = {}

    def temporary(self, grant, seconds):
        """Run credentials never enter SQLite; expiry also bounds abandoned runs."""
        token = PREFIX + secrets.token_urlsafe(32)
        self._temporary[digest(token)] = (grant, self.clock() + timedelta(seconds=seconds))
        return token

    def discard(self, token):
        self._temporary.pop(digest(token), None)

    def create(self, name, scopes, days=None, reader=None):
        """Returns (token, view). The token is not stored and cannot be shown again."""
        name, scopes = clean_name(name), clean_scopes(scopes)
        if reader is not None:
            agents = self.db.get("knowledge_settings", {}).get("agents", ["Claude Code", "Codex"])
            if reader not in agents:
                raise ValueError("reader 必须是当前 Agent 读者之一")
        if days is not None and not 1 <= days <= MAX_DAYS:
            raise ValueError(f"有效期要在 1 到 {MAX_DAYS} 天之间")
        if self._active(name):
            raise ValueError(f"已经有一个有效的令牌叫「{name}」，先作废它或换个名字")
        token = PREFIX + secrets.token_urlsafe(32)
        created = self.clock()
        row = {
            "id": "dev_" + secrets.token_hex(6),
            "name": name,
            "token_hash": digest(token),
            # Enough to tell tokens apart in a list; the rest stays secret.
            "hint": token[: len(PREFIX) + 4],
            "scopes": ",".join(scopes),
            "created_at": created.isoformat(),
            "expires_at": (created + timedelta(days=days)).isoformat() if days else None,
            "last_used_at": None,
            "revoked_at": None,
            "reader": reader,
        }
        self.db.execute(
            f"INSERT INTO device_tokens({','.join(row)}) VALUES({','.join('?' * len(row))})",
            tuple(row.values()),
        )
        return token, self.view(row)

    def list(self, include_revoked=False):
        sql = "SELECT * FROM device_tokens"
        if not include_revoked:
            sql += " WHERE revoked_at IS NULL"
        return [self.view(row) for row in self.db.rows(sql + " ORDER BY created_at, id")]

    def revoke(self, ref):
        """By ID, or by the name of a token that is still active. Takes effect on the next
        request: tokens are checked against the database every time."""
        ref = ref.strip()
        rows = self.db.rows("SELECT * FROM device_tokens WHERE id=?", (ref,)) or self._active(ref)
        if not rows:
            raise KeyError(ref)
        row = rows[0]
        if row["revoked_at"] is None:
            row["revoked_at"] = self.clock().isoformat()
            self.db.execute(
                "UPDATE device_tokens SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (row["revoked_at"], row["id"]),
            )
        return self.view(row)

    def check(self, token):
        """The grant for a device token, or None when unknown, revoked or expired."""
        if not token.startswith(PREFIX):
            return None
        key = digest(token)
        if key in self._temporary:
            grant, expires = self._temporary[key]
            if expires > self.clock():
                return grant
            self._temporary.pop(key, None)
            return None
        rows = self.db.rows(
            "SELECT * FROM device_tokens WHERE token_hash=? AND revoked_at IS NULL",
            (digest(token),),
        )
        if not rows:
            return None
        row, current = rows[0], self.clock()
        if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) <= current:
            return None
        last = self._touched.get(row["id"])
        if last is None or (current - last).total_seconds() >= TOUCH_SECONDS:
            self._touched[row["id"]] = current
            self.db.execute(
                "UPDATE device_tokens SET last_used_at=? WHERE id=?",
                (current.isoformat(), row["id"]),
            )
        scopes = frozenset(row["scopes"].split(",")) & ALL_SCOPES
        return Grant(
            row["id"],
            row["name"],
            scopes,
            row["reader"],
            "agent" if row["reader"] is not None else None,
        )

    def _active(self, name):
        return self.db.rows(
            "SELECT * FROM device_tokens WHERE name=? AND revoked_at IS NULL", (name,)
        )

    def view(self, row):
        """Everything about a token except the token and its hash."""
        if row["revoked_at"]:
            status = "revoked"
        elif row["expires_at"] and datetime.fromisoformat(row["expires_at"]) <= self.clock():
            status = "expired"
        else:
            status = "active"
        return {
            "id": row["id"],
            "name": row["name"],
            "scopes": row["scopes"].split(","),
            "hint": row["hint"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "last_used_at": row["last_used_at"],
            "revoked_at": row["revoked_at"],
            "status": status,
            "reader": row["reader"],
        }
