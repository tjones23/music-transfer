"""Minimal Apple Music API client."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterable, List, Optional

import requests

API = "https://api.music.apple.com"


class AppleAPIError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Apple Music API error {status}: {message}")
        self.status = status


def chunks(items: List[Any], size: int) -> Iterable[List[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class AppleMusicClient:
    def __init__(
        self,
        developer_token: str,
        user_token_provider: Callable[[], str],
        storefront: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ):
        self.developer_token = developer_token
        self._user_token = user_token_provider
        self.session = session or requests.Session()
        self._storefront = storefront

    # -- low level --------------------------------------------------------
    def request(self, method: str, path: str, params=None, json=None, user: bool = True) -> Dict[str, Any]:
        url = path if path.startswith("http") else f"{API}{path}"
        headers = {"Authorization": f"Bearer {self.developer_token}"}
        if user:
            headers["Music-User-Token"] = self._user_token()
        for attempt in range(6):
            resp = self.session.request(method, url, params=params, json=json, headers=headers, timeout=30)
            if resp.status_code == 429 or (resp.status_code >= 500 and attempt < 5):
                wait = int(resp.headers.get("Retry-After", 0) or 2 ** attempt)
                time.sleep(min(wait, 60))
                continue
            if resp.status_code >= 400:
                raise AppleAPIError(resp.status_code, _error_message(resp))
            if not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError:
                return {}
        raise AppleAPIError(429, "rate limited too many times")

    def get(self, path, params=None, user=True):
        return self.request("GET", path, params=params, user=user)

    # -- account ----------------------------------------------------------
    @property
    def storefront(self) -> str:
        if not self._storefront:
            data = self.get("/v1/me/storefront")
            self._storefront = data["data"][0]["id"]
        return self._storefront

    # -- catalog ----------------------------------------------------------
    def songs_by_isrc(self, isrcs: List[str]) -> List[Dict[str, Any]]:
        """Look up catalog songs by ISRC (up to 25 per request)."""
        results: List[Dict[str, Any]] = []
        for batch in chunks(isrcs, 25):
            data = self.get(
                f"/v1/catalog/{self.storefront}/songs",
                {"filter[isrc]": ",".join(batch)},
                user=False,
            )
            results.extend(data.get("data", []))
        return results

    def albums_by_upc(self, upc: str) -> List[Dict[str, Any]]:
        data = self.get(f"/v1/catalog/{self.storefront}/albums", {"filter[upc]": upc}, user=False)
        return data.get("data", [])

    def search(self, term: str, types: str = "songs", limit: int = 10) -> List[Dict[str, Any]]:
        data = self.get(
            f"/v1/catalog/{self.storefront}/search",
            {"term": term[:200], "types": types, "limit": limit},
            user=False,
        )
        return ((data.get("results") or {}).get(types) or {}).get("data", [])

    # -- library ----------------------------------------------------------
    def library_playlists(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        path: Optional[str] = "/v1/me/library/playlists?limit=100"
        while path:
            data = self.get(path)
            out.extend(data.get("data", []))
            path = data.get("next")
        return out

    def create_playlist(self, name: str, description: str = "") -> str:
        body = {"attributes": {"name": name, "description": description}}
        data = self.request("POST", "/v1/me/library/playlists", json=body)
        return data["data"][0]["id"]

    def add_tracks_to_playlist(self, playlist_id: str, song_ids: List[str]) -> None:
        for batch in chunks(song_ids, 100):
            self.request(
                "POST",
                f"/v1/me/library/playlists/{playlist_id}/tracks",
                json={"data": [{"id": sid, "type": "songs"} for sid in batch]},
            )

    def add_to_library(self, song_ids: Iterable[str] = (), album_ids: Iterable[str] = ()) -> None:
        for kind, ids in (("songs", list(song_ids)), ("albums", list(album_ids))):
            for batch in chunks(ids, 50):
                self.request("POST", "/v1/me/library", params={f"ids[{kind}]": ",".join(batch)})

    def love_song(self, song_id: str) -> None:
        self.request(
            "PUT",
            f"/v1/me/ratings/songs/{song_id}",
            json={"type": "rating", "attributes": {"value": 1}},
        )


def _error_message(resp: requests.Response) -> str:
    try:
        errors = resp.json().get("errors") or []
        if errors:
            e = errors[0]
            return f"{e.get('title', '')}: {e.get('detail', '')}".strip(": ")
    except ValueError:
        pass
    return resp.text[:300]
