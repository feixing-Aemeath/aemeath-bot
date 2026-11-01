import re
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
HOSTNAME = re.compile(rf"^{LABEL}(?:\.{LABEL})*(?::\d{{1,5}})?$")


def safe_bind(host: str) -> str:
    address = ip_address(host)
    if not (
        address.is_loopback
        or address in ip_network("100.64.0.0/10")
        or address in ip_network("fd7a:115c:a1e0::/48")
    ):
        raise ValueError("bind must be a loopback or Tailscale IP")
    return host


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASSISTANT_", env_file=".env", extra="ignore")
    token: SecretStr
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)
    data_dir: Path = Path(".state")
    vault_dir: Path | None = None
    apple_mode: Literal["fake", "real"] = "fake"
    apple_binary: Path | None = None
    scheduler_enabled: bool = True
    # Extra Host header values accepted by the Streamable HTTP MCP endpoint (/mcp), for Agents
    # on other devices, e.g. the Tailscale name "mac-mini.tail1234.ts.net". Comma-separated in
    # the environment. Without a port any port is accepted. The Bearer token is still required.
    mcp_allowed_hosts: Annotated[list[str], NoDecode] = []

    _bind = field_validator("host")(safe_bind)

    @field_validator("mcp_allowed_hosts", mode="before")
    @classmethod
    def split_hosts(cls, value):
        if isinstance(value, str):
            return value.replace(",", " ").split()
        return value

    @field_validator("mcp_allowed_hosts")
    @classmethod
    def plain_hosts(cls, hosts: list[str]) -> list[str]:
        normalized = []
        for host in hosts:
            host = host.strip().lower()
            # Names only: no scheme, path, user info or wildcard that would switch the DNS
            # rebinding protection off for everything.
            if not HOSTNAME.match(host):
                raise ValueError(f"ASSISTANT_MCP_ALLOWED_HOSTS: invalid host name {host!r}")
            normalized.append(host)
        return normalized

    @field_validator("token")
    @classmethod
    def strong_token(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("ASSISTANT_TOKEN must contain at least 32 characters")
        return value

    @property
    def base_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"
