"""Explicit user OAuth setup; tokens stay in the local ignored secret directory."""

import argparse
import json
import os
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from dotenv import load_dotenv

from core.config import Settings
from plugins.feishu.plugin import OfficialFeishu, TokenStore


def main():
    load_dotenv(override=False)
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--redirect-uri", required=True)
    finish = commands.add_parser("finish")
    finish.add_argument("--callback-url", required=True)
    args = parser.parse_args()
    settings = Settings()
    folder = settings.data_dir / "secrets"
    state_store = TokenStore(folder / "feishu-oauth-state.json")
    if args.command == "start":
        state = secrets.token_urlsafe(32)
        state_store.write(
            {"state": state, "redirect_uri": args.redirect_uri, "expires_at": time.time() + 600}
        )
        print(
            "https://accounts.feishu.cn/open-apis/authen/v1/authorize?"
            + urlencode(
                {
                    "client_id": os.environ["FEISHU_APP_ID"],
                    "redirect_uri": args.redirect_uri,
                    "state": state,
                    "response_type": "code",
                    "scope": "offline_access im:message:readonly",
                }
            )
        )
        return
    stored = state_store.read()
    callback = urlsplit(args.callback_url)
    redirect = urlsplit(stored["redirect_uri"])
    query = parse_qs(callback.query)
    if (callback.scheme, callback.netloc, callback.path) != (
        redirect.scheme,
        redirect.netloc,
        redirect.path,
    ):
        raise SystemExit("OAuth callback URI mismatch")
    if time.time() > stored["expires_at"] or not secrets.compare_digest(
        query.get("state", [""])[0], stored["state"]
    ):
        raise SystemExit("OAuth state invalid or expired")
    client = OfficialFeishu(TokenStore(folder / "feishu.json"))
    client.oauth(
        {
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": stored["redirect_uri"],
        }
    )
    state_store.write(
        {"state": "consumed", "expires_at": 0, "redirect_uri": stored["redirect_uri"]}
    )
    print(json.dumps({"authorized": True, "tokens": "saved locally; hidden"}))


if __name__ == "__main__":
    main()
