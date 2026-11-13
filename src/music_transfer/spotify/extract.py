"""Extract a user's Spotify library into the service-agnostic Library model."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ..models import Album, Artist, Library, Playlist, Track
from .client import SpotifyAPIError, SpotifyClient

TIME_RANGES = ("short_term", "medium_term", "long_term")
Log = Callable[[str], None]


def parse_track(obj: Optional[Dict[str, Any]], added_at: Optional[str] = None) -> Optional[Track]:
    """Convert a Spotify track object into a Track (returns None for episodes/unavailable items)."""
    if not obj or obj.get("type", "track") != "track" or not obj.get("name"):
        return None
    return Track(
        name=obj["name"],
        artists=[a.get("name", "") for a in obj.get("artists", []) if a.get("name")],
        album=(obj.get("album") or {}).get("name"),
        duration_ms=obj.get("duration_ms"),
        isrc=(obj.get("external_ids") or {}).get("isrc"),
        explicit=bool(obj.get("explicit")),
        spotify_id=obj.get("id"),
        added_at=added_at,
    )


def parse_album(obj: Dict[str, Any], added_at: Optional[str] = None) -> Album:
    return Album(
        name=obj.get("name", ""),
        artists=[a.get("name", "") for a in obj.get("artists", []) if a.get("name")],
        upc=(obj.get("external_ids") or {}).get("upc"),
        release_date=obj.get("release_date"),
        total_tracks=obj.get("total_tracks"),
        spotify_id=obj.get("id"),
        added_at=added_at,
    )


def parse_artist(obj: Dict[str, Any]) -> Artist:
    return Artist(name=obj.get("name", ""), genres=list(obj.get("genres") or []), spotify_id=obj.get("id"))


def _playlist_item_track(item: Dict[str, Any]) -> Optional[Track]:
    # Newer API responses use "item"; older ones use "track".
    obj = item.get("item") or item.get("track")
    return parse_track(obj, item.get("added_at"))


class SpotifyExtractor:
    def __init__(self, client: SpotifyClient, log: Log = print):
        self.client = client
        self.log = log

    def extract(
        self,
        include_playlists: bool = True,
        include_followed_playlists: bool = True,
        include_liked: bool = True,
        include_albums: bool = True,
        include_artists: bool = True,
        include_top: bool = True,
    ) -> Library:
        profile = self.client.get("/me")
        lib = Library(
            source="spotify",
            profile={
                "id": profile.get("id"),
                "display_name": profile.get("display_name"),
                "country": profile.get("country"),
                "product": profile.get("product"),
            },
        )
        self.log(f"Logged in to Spotify as {profile.get('display_name') or profile.get('id')}")

        if include_liked:
            lib.liked_tracks = self.liked_tracks()
        if include_playlists:
            lib.playlists = self.playlists(profile.get("id"), include_followed_playlists)
        if include_albums:
            lib.saved_albums = self.saved_albums()
        if include_artists:
            lib.followed_artists = self.followed_artists()
        if include_top:
            lib.top_tracks, lib.top_artists = self.top_items()
        return lib

    def liked_tracks(self) -> List[Track]:
        self.log("Fetching liked songs...")
        tracks = []
        for item in self.client.paginate("/me/tracks", {"limit": 50}):
            t = parse_track(item.get("track"), item.get("added_at"))
            if t:
                tracks.append(t)
        self.log(f"  {len(tracks)} liked songs")
        return tracks

    def playlists(self, user_id: Optional[str], include_followed: bool) -> List[Playlist]:
        self.log("Fetching playlists...")
        result = []
        for p in self.client.paginate("/me/playlists", {"limit": 50}):
            if not p:
                continue
            owner = p.get("owner") or {}
            owned = owner.get("id") == user_id or bool(p.get("collaborative"))
            if not owned and not include_followed:
                continue
            playlist = Playlist(
                name=p.get("name") or "Untitled playlist",
                description=p.get("description") or "",
                public=p.get("public"),
                collaborative=bool(p.get("collaborative")),
                owned_by_user=owner.get("id") == user_id,
                owner=owner.get("display_name") or owner.get("id"),
                spotify_id=p.get("id"),
            )
            try:
                playlist.tracks = self._playlist_tracks(p["id"])
            except SpotifyAPIError as exc:
                self.log(f"  ! Skipping tracks of '{playlist.name}': {exc}")
            self.log(f"  {playlist.name}: {len(playlist.tracks)} tracks")
            result.append(playlist)
        return result

    def _playlist_tracks(self, playlist_id: str) -> List[Track]:
        tracks: List[Track] = []
        try:
            items = list(self.client.paginate(f"/playlists/{playlist_id}/items", {"limit": 50}))
        except SpotifyAPIError as exc:
            if exc.status not in (404, 410):
                raise
            # Fallback for API versions that only expose the legacy endpoint.
            items = list(self.client.paginate(f"/playlists/{playlist_id}/tracks", {"limit": 100}))
        for item in items:
            t = _playlist_item_track(item)
            if t:
                tracks.append(t)
        return tracks

    def saved_albums(self) -> List[Album]:
        self.log("Fetching saved albums...")
        albums = [
            parse_album(item["album"], item.get("added_at"))
            for item in self.client.paginate("/me/albums", {"limit": 50})
            if item.get("album")
        ]
        self.log(f"  {len(albums)} saved albums")
        return albums

    def followed_artists(self) -> List[Artist]:
        self.log("Fetching followed artists...")
        artists = [
            parse_artist(a)
            for a in self.client.paginate("/me/following", {"type": "artist", "limit": 50}, key="artists")
        ]
        self.log(f"  {len(artists)} followed artists")
        return artists

    def top_items(self):
        self.log("Fetching top tracks and artists (listening preferences)...")
        top_tracks: Dict[str, List[Track]] = {}
        top_artists: Dict[str, List[Artist]] = {}
        for rng in TIME_RANGES:
            try:
                top_tracks[rng] = [
                    t for t in (parse_track(o) for o in self.client.get("/me/top/tracks", {"limit": 50, "time_range": rng}).get("items", [])) if t
                ]
                top_artists[rng] = [
                    parse_artist(o) for o in self.client.get("/me/top/artists", {"limit": 50, "time_range": rng}).get("items", [])
                ]
            except SpotifyAPIError as exc:
                self.log(f"  ! Could not fetch top items for {rng}: {exc}")
        return top_tracks, top_artists
