"""Apple Music authentication.

Apple Music needs two tokens:
  * a *developer token* - a JWT signed with your MusicKit private key (.p8), and
  * a *Music User Token* - obtained when you sign in with your Apple ID through MusicKit JS.

The developer token is generated locally. The user token is obtained by serving a small
page on 127.0.0.1 that loads Apple's MusicKit JS, asks you to sign in, and posts the
resulting token back to this program.
"""

from __future__ import annotations

import json
import time
from importlib import resources
from typing import Any, Dict, Optional

import jwt

from ..config import AppleSettings, JsonStore, data_dir
from ..localserver import html_page, run_browser_flow

DEV_TOKEN_LIFETIME = 60 * 60 * 24 * 150  # Apple allows up to ~6 months
AUTH_HOST = "127.0.0.1"
AUTH_PORT = 8889


def make_developer_token(settings: AppleSettings, lifetime: int = DEV_TOKEN_LIFETIME) -> Dict[str, Any]:
    private_key = settings.private_key_path.read_text(encoding="utf-8")
    now = int(time.time())
    token = jwt.encode(
        {"iss": settings.team_id, "iat": now, "exp": now + lifetime},
        private_key,
        algorithm="ES256",
        headers={"kid": settings.key_id},
    )
    if isinstance(token, bytes):  # PyJWT < 2
        token = token.decode()
    return {"token": token, "expires_at": now + lifetime}


class AppleAuth:
    def __init__(self, settings: AppleSettings, store: Optional[JsonStore] = None):
        self.settings = settings
        self.store = store or JsonStore(data_dir() / "apple_token.json")

    def developer_token(self) -> str:
        data = self.store.load()
        dev = data.get("developer")
        if (
            not dev
            or dev.get("key_id") != self.settings.key_id
            or dev.get("team_id") != self.settings.team_id
            or dev.get("expires_at", 0) - 86400 < time.time()
        ):
            dev = make_developer_token(self.settings)
            dev.update(key_id=self.settings.key_id, team_id=self.settings.team_id)
            data["developer"] = dev
            data.pop("user_token", None)  # user tokens are bound to the developer token
            self.store.save(data)
        return dev["token"]

    def user_token(self, force_login: bool = False) -> str:
        dev_token = self.developer_token()
        data = self.store.load()
        if data.get("user_token") and not force_login:
            return data["user_token"]
        token = self._browser_login(dev_token)
        data = self.store.load()
        data["user_token"] = token
        data["user_token_obtained_at"] = int(time.time())
        self.store.save(data)
        return token

    def clear_user_token(self) -> None:
        data = self.store.load()
        data.pop("user_token", None)
        self.store.save(data)

    def logout(self) -> None:
        self.store.save({})

    def _browser_login(self, dev_token: str) -> str:
        template = resources.files("music_transfer.apple").joinpath("login.html").read_text(encoding="utf-8")
        page = template.replace("__DEVELOPER_TOKEN__", json.dumps(dev_token)).encode("utf-8")
        result: Dict[str, str] = {}

        def handler(method, path, query, body):
            if method == "GET" and path in ("/", "/index.html"):
                return 200, "text/html; charset=utf-8", page, False
            if method == "POST" and path == "/token":
                try:
                    payload = json.loads(body.decode("utf-8") or "{}")
                except ValueError:
                    payload = {}
                if payload.get("error"):
                    result["error"] = str(payload["error"])
                elif payload.get("userToken"):
                    result["token"] = payload["userToken"]
                else:
                    result["error"] = "No token received"
                return 200, "application/json", b'{"ok":true}', True
            return 404, "text/html", html_page("Not found", "Unknown path."), False

        print("Sign in with your Apple ID in the browser window to authorize Apple Music access.")
        run_browser_flow(AUTH_HOST, AUTH_PORT, f"http://{AUTH_HOST}:{AUTH_PORT}/", handler, timeout=600)
        if "token" not in result:
            raise SystemExit(f"Apple Music login failed: {result.get('error', 'unknown error')}")
        print("Apple Music authorized.")
        return result["token"]
