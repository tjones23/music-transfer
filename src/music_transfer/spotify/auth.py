"""Spotify OAuth 2.0 Authorization Code flow with PKCE (no client secret needed)."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests

from ..config import JsonStore, SpotifySettings, data_dir
from ..localserver import html_page, parse_host_port, run_browser_flow

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"

SCOPES = [
    "user-read-private",
    "user-read-email",
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-library-read",
    "user-follow-read",
    "user-top-read",
]


def _pkce_pair() -> tuple:
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class SpotifyAuth:
    def __init__(self, settings: SpotifySettings, store: Optional[JsonStore] = None):
        self.settings = settings
        self.store = store or JsonStore(data_dir() / "spotify_token.json")

    # -- public -----------------------------------------------------------
    def access_token(self) -> str:
        token = self.store.load()
        if token.get("client_id") != self.settings.client_id or not token.get("access_token"):
            token = self.login()
        elif token.get("expires_at", 0) - 60 < time.time():
            token = self._refresh(token) or self.login()
        return token["access_token"]

    def logout(self) -> None:
        self.store.save({})

    def login(self) -> Dict[str, Any]:
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)
        params = {
            "client_id": self.settings.client_id,
            "response_type": "code",
            "redirect_uri": self.settings.redirect_uri,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": state,
            "scope": " ".join(SCOPES),
        }
        url = f"{AUTH_URL}?{urlencode(params)}"
        host, port, cb_path = parse_host_port(self.settings.redirect_uri)
        result: Dict[str, str] = {}

        def handler(method, path, query, body):
            if path != cb_path:
                return 404, "text/plain", b"Not found", False
            if query.get("state") != state:
                return 400, "text/html", html_page("Login failed", "State mismatch; please retry."), False
            if "error" in query:
                result["error"] = query["error"]
                return 200, "text/html", html_page("Login cancelled", query["error"]), True
            result["code"] = query.get("code", "")
            return 200, "text/html", html_page(
                "Spotify connected", "You can close this tab and return to the terminal."
            ), True

        run_browser_flow(host, port, url, handler)
        if "error" in result or not result.get("code"):
            raise SystemExit(f"Spotify login failed: {result.get('error', 'no code returned')}")

        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": result["code"],
                "redirect_uri": self.settings.redirect_uri,
                "client_id": self.settings.client_id,
                "code_verifier": verifier,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise SystemExit(f"Spotify token exchange failed: {resp.status_code} {resp.text}")
        return self._persist(resp.json())

    # -- internal ---------------------------------------------------------
    def _refresh(self, token: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            return None
        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.settings.client_id,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        data.setdefault("refresh_token", refresh_token)
        return self._persist(data)

    def _persist(self, data: Dict[str, Any]) -> Dict[str, Any]:
        token = {
            "client_id": self.settings.client_id,
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "expires_at": time.time() + int(data.get("expires_in", 3600)),
            "scope": data.get("scope", ""),
        }
        self.store.save(token)
        return token
