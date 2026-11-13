"""Minimal Spotify Web API client with paging and rate-limit handling."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterator, Optional

import requests

API = "https://api.spotify.com/v1"


class SpotifyAPIError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Spotify API error {status}: {message}")
        self.status = status


class SpotifyClient:
    def __init__(self, token_provider: Callable[[], str], session: Optional[requests.Session] = None):
        self._token = token_provider
        self.session = session or requests.Session()

    def get(self, path_or_url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = path_or_url if path_or_url.startswith("http") else f"{API}{path_or_url}"
        for attempt in range(6):
            resp = self.session.get(
                url, params=params, headers={"Authorization": f"Bearer {self._token()}"}, timeout=30
            )
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "2")) + 1
                time.sleep(min(wait, 60))
                continue
            if resp.status_code >= 500 and attempt < 5:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code >= 400:
                try:
                    msg = resp.json().get("error", {}).get("message", resp.text)
                except ValueError:
                    msg = resp.text
                raise SpotifyAPIError(resp.status_code, msg)
            return resp.json() if resp.content else {}
        raise SpotifyAPIError(429, "rate limited too many times")

    def paginate(self, path: str, params: Optional[Dict[str, Any]] = None, key: Optional[str] = None) -> Iterator[Dict[str, Any]]:
        """Yield items across pages. `key` selects a wrapper object (e.g. 'artists' for cursor paging)."""
        page = self.get(path, params)
        while page:
            if key:
                page = page.get(key, {})
            for item in page.get("items", []) or []:
                yield item
            nxt = page.get("next")
            if not nxt:
                break
            page = self.get(nxt)
