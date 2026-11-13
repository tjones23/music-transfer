"""Service-agnostic data model for an exported music library."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1


@dataclass
class Track:
    name: str
    artists: List[str]
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    isrc: Optional[str] = None
    explicit: bool = False
    spotify_id: Optional[str] = None
    added_at: Optional[str] = None

    @property
    def primary_artist(self) -> str:
        return self.artists[0] if self.artists else ""

    @property
    def key(self) -> str:
        """Stable identifier used to cache match results."""
        if self.isrc:
            return f"isrc:{self.isrc.upper()}"
        if self.spotify_id:
            return f"spotify:{self.spotify_id}"
        return f"text:{self.primary_artist.lower()}|{self.name.lower()}"

    def describe(self) -> str:
        return f"{', '.join(self.artists)} - {self.name}"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Track":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Album:
    name: str
    artists: List[str]
    upc: Optional[str] = None
    release_date: Optional[str] = None
    total_tracks: Optional[int] = None
    spotify_id: Optional[str] = None
    added_at: Optional[str] = None

    @property
    def key(self) -> str:
        if self.upc:
            return f"upc:{self.upc}"
        if self.spotify_id:
            return f"spotify-album:{self.spotify_id}"
        return f"album:{(self.artists[0] if self.artists else '').lower()}|{self.name.lower()}"

    def describe(self) -> str:
        return f"{', '.join(self.artists)} - {self.name}"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Album":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Artist:
    name: str
    genres: List[str] = field(default_factory=list)
    spotify_id: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Artist":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Playlist:
    name: str
    description: str = ""
    tracks: List[Track] = field(default_factory=list)
    public: Optional[bool] = None
    collaborative: bool = False
    owned_by_user: bool = True
    owner: Optional[str] = None
    spotify_id: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Playlist":
        data = {k: d.get(k) for k in cls.__dataclass_fields__ if k in d and k != "tracks"}
        return cls(tracks=[Track.from_dict(t) for t in d.get("tracks", [])], **data)


@dataclass
class Library:
    """Everything exported from the source service."""

    source: str
    exported_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    profile: Dict[str, Any] = field(default_factory=dict)
    playlists: List[Playlist] = field(default_factory=list)
    liked_tracks: List[Track] = field(default_factory=list)
    saved_albums: List[Album] = field(default_factory=list)
    followed_artists: List[Artist] = field(default_factory=list)
    # Preferences/taste data: {"short_term": [...], "medium_term": [...], "long_term": [...]}
    top_tracks: Dict[str, List[Track]] = field(default_factory=dict)
    top_artists: Dict[str, List[Artist]] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: Path) -> "Library":
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return cls(
            source=d.get("source", "unknown"),
            exported_at=d.get("exported_at", ""),
            profile=d.get("profile", {}),
            playlists=[Playlist.from_dict(p) for p in d.get("playlists", [])],
            liked_tracks=[Track.from_dict(t) for t in d.get("liked_tracks", [])],
            saved_albums=[Album.from_dict(a) for a in d.get("saved_albums", [])],
            followed_artists=[Artist.from_dict(a) for a in d.get("followed_artists", [])],
            top_tracks={k: [Track.from_dict(t) for t in v] for k, v in d.get("top_tracks", {}).items()},
            top_artists={k: [Artist.from_dict(a) for a in v] for k, v in d.get("top_artists", {}).items()},
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )

    def summary(self) -> str:
        n_playlist_tracks = sum(len(p.tracks) for p in self.playlists)
        lines = [
            f"Source:           {self.source} (exported {self.exported_at})",
            f"Playlists:        {len(self.playlists)} ({n_playlist_tracks} tracks)",
            f"Liked songs:      {len(self.liked_tracks)}",
            f"Saved albums:     {len(self.saved_albums)}",
            f"Followed artists: {len(self.followed_artists)}",
        ]
        if self.top_tracks:
            lines.append(
                "Top tracks:       "
                + ", ".join(f"{k}={len(v)}" for k, v in self.top_tracks.items())
            )
        if self.top_artists:
            lines.append(
                "Top artists:      "
                + ", ".join(f"{k}={len(v)}" for k, v in self.top_artists.items())
            )
        return "\n".join(lines)
