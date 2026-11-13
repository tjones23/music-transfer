"""Import an exported Library into Apple Music."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from ..config import JsonStore
from ..matching import Matcher
from ..models import Library, Playlist, Track
from .client import AppleAPIError, AppleMusicClient

TOP_RANGE_LABELS = {
    "short_term": "Last 4 Weeks",
    "medium_term": "Last 6 Months",
    "long_term": "All Time",
}

ALL_SECTIONS = ("playlists", "liked", "albums", "artists", "top")


@dataclass
class ImportOptions:
    sections: Set[str] = field(default_factory=lambda: set(ALL_SECTIONS))
    playlist_filter: List[str] = field(default_factory=list)  # names (case-insensitive) to include
    owned_only: bool = False
    name_prefix: str = ""
    liked_playlist: bool = True  # also create a "Liked Songs" playlist preserving order
    love_liked: bool = True
    dry_run: bool = False


@dataclass
class Report:
    unmatched_tracks: List[Dict[str, str]] = field(default_factory=list)
    unmatched_albums: List[Dict[str, str]] = field(default_factory=list)
    artists: List[Dict[str, str]] = field(default_factory=list)
    stats: Dict[str, Dict[str, int]] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def stat(self, section: str, matched: int, total: int) -> None:
        s = self.stats.setdefault(section, {"matched": 0, "total": 0})
        s["matched"] += matched
        s["total"] += total

    def write(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / "transfer_report.json", "w", encoding="utf-8") as f:
            json.dump(self.__dict__, f, indent=2, ensure_ascii=False)
        with open(directory / "unmatched_tracks.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["context", "artist", "title", "album", "isrc"])
            w.writeheader()
            w.writerows(self.unmatched_tracks)
        if self.artists:
            with open(directory / "artists_to_favorite.csv", "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=["artist", "apple_music_url"])
                w.writeheader()
                w.writerows(self.artists)
        return directory


class AppleImporter:
    def __init__(
        self,
        client: AppleMusicClient,
        matcher: Matcher,
        state: JsonStore,
        options: ImportOptions,
        log: Callable[[str], None] = print,
    ):
        self.client = client
        self.matcher = matcher
        self.state_store = state
        self.state = state.load()
        self.opts = options
        self.log = log
        self.report = Report()

    # -- orchestration ----------------------------------------------------
    def run(self, lib: Library) -> Report:
        s = self.opts.sections
        if "liked" in s and lib.liked_tracks:
            self.import_liked(lib.liked_tracks)
        if "playlists" in s:
            for p in self._selected_playlists(lib.playlists):
                self.import_playlist(p, state_key=f"playlist:{p.spotify_id or p.name}")
        if "albums" in s and lib.saved_albums:
            self.import_albums(lib)
        if "top" in s:
            for rng, tracks in lib.top_tracks.items():
                if tracks:
                    label = TOP_RANGE_LABELS.get(rng, rng)
                    pl = Playlist(
                        name=f"Spotify Top Tracks – {label}",
                        description=f"Your most played Spotify tracks ({label.lower()}), exported {lib.exported_at[:10]}.",
                        tracks=tracks,
                    )
                    self.import_playlist(pl, state_key=f"top:{rng}", context=f"top {rng}")
        if "artists" in s and lib.followed_artists:
            self.resolve_artists(lib)
        self.matcher.save()
        return self.report

    def _selected_playlists(self, playlists: List[Playlist]) -> List[Playlist]:
        wanted = {n.lower() for n in self.opts.playlist_filter}
        out = []
        for p in playlists:
            if wanted and p.name.lower() not in wanted:
                continue
            if self.opts.owned_only and not p.owned_by_user:
                continue
            out.append(p)
        return out

    # -- sections ---------------------------------------------------------
    def _match(self, tracks: List[Track], context: str, record: bool = True) -> List[str]:
        def progress(i, n):
            if i == n or i % 25 == 0:
                self.log(f"    searched {i}/{n}")

        mapping = self.matcher.match_tracks(tracks, progress)
        ids: List[str] = []
        for t in tracks:
            sid = mapping.get(t.key)
            if sid:
                ids.append(sid)
            elif record:
                self.report.unmatched_tracks.append(
                    {"context": context, "artist": ", ".join(t.artists), "title": t.name,
                     "album": t.album or "", "isrc": t.isrc or ""}
                )
        if record:
            self.report.stat(context.split(":")[0], len(ids), len(tracks))
        return ids

    def import_playlist(
        self, playlist: Playlist, state_key: str, context: Optional[str] = None, record: bool = True
    ) -> None:
        context = context or f"playlist: {playlist.name}"
        self.log(f"Playlist '{playlist.name}' ({len(playlist.tracks)} tracks)")
        entry = self.state.get(state_key, {})
        if entry.get("done"):
            self.log("  already transferred, skipping")
            return
        ids = list(dict.fromkeys(self._match(playlist.tracks, context, record)))  # de-dup, keep order
        self.log(f"  matched {len(ids)}/{len(playlist.tracks)}")
        if self.opts.dry_run:
            return
        try:
            if not entry.get("apple_id"):
                desc = playlist.description or ""
                suffix = "Transferred from Spotify" + (f" (by {playlist.owner})" if playlist.owner and not playlist.owned_by_user else "")
                entry["apple_id"] = self.client.create_playlist(
                    f"{self.opts.name_prefix}{playlist.name}", f"{desc}\n\n{suffix}".strip()
                )
                entry["added"] = 0
                self._save_state(state_key, entry)
            start = entry.get("added", 0)
            for i in range(start, len(ids), 100):
                self.client.add_tracks_to_playlist(entry["apple_id"], ids[i : i + 100])
                entry["added"] = min(i + 100, len(ids))
                self._save_state(state_key, entry)
            entry["done"] = True
            self._save_state(state_key, entry)
        except AppleAPIError as exc:
            msg = f"Failed to transfer playlist '{playlist.name}': {exc}"
            self.log(f"  ! {msg}")
            self.report.errors.append(msg)

    def import_liked(self, tracks: List[Track]) -> None:
        self.log(f"Liked songs ({len(tracks)})")
        ids = list(dict.fromkeys(self._match(tracks, "liked")))
        self.log(f"  matched {len(ids)}/{len(tracks)}")
        if self.opts.dry_run:
            return
        entry = self.state.get("liked", {})
        try:
            if not entry.get("library_done"):
                self.log("  adding to your Apple Music library...")
                self.client.add_to_library(song_ids=ids)
                entry["library_done"] = True
                self._save_state("liked", entry)
            if self.opts.love_liked:
                loved = set(entry.get("loved", []))
                todo = [i for i in ids if i not in loved]
                if todo:
                    self.log(f"  marking {len(todo)} songs as Loved/Favorite...")
                for n, sid in enumerate(todo, 1):
                    try:
                        self.client.love_song(sid)
                        loved.add(sid)
                    except AppleAPIError as exc:
                        self.report.errors.append(f"Could not love song {sid}: {exc}")
                    if n % 50 == 0 or n == len(todo):
                        entry["loved"] = sorted(loved)
                        self._save_state("liked", entry)
                        self.log(f"    {n}/{len(todo)}")
        except AppleAPIError as exc:
            msg = f"Failed to add liked songs: {exc}"
            self.log(f"  ! {msg}")
            self.report.errors.append(msg)
        if self.opts.liked_playlist:
            # Spotify keeps liked songs newest-first; preserve that order.
            pl = Playlist(name="Liked Songs (Spotify)", description="Your Spotify Liked Songs, newest first.", tracks=tracks)
            self.import_playlist(pl, state_key="playlist:__liked__", context="liked", record=False)

    def import_albums(self, lib: Library) -> None:
        self.log(f"Saved albums ({len(lib.saved_albums)})")
        ids = []
        for a in lib.saved_albums:
            aid = self.matcher.match_album(a)
            if aid:
                ids.append(aid)
            else:
                self.report.unmatched_albums.append({"artist": ", ".join(a.artists), "album": a.name, "upc": a.upc or ""})
        self.report.stat("albums", len(ids), len(lib.saved_albums))
        self.log(f"  matched {len(ids)}/{len(lib.saved_albums)}")
        if self.opts.dry_run or self.state.get("albums", {}).get("done"):
            return
        try:
            self.client.add_to_library(album_ids=ids)
            self._save_state("albums", {"done": True})
        except AppleAPIError as exc:
            msg = f"Failed to add albums: {exc}"
            self.log(f"  ! {msg}")
            self.report.errors.append(msg)

    def resolve_artists(self, lib: Library) -> None:
        # The Apple Music API has no endpoint for following/favoriting artists, so we
        # produce a list of links you can open to favorite them manually.
        self.log(f"Followed artists ({len(lib.followed_artists)}) – looking up Apple Music links")
        found = 0
        for a in lib.followed_artists:
            r = self.matcher.find_artist(a.name)
            url = ((r or {}).get("attributes") or {}).get("url", "")
            found += bool(url)
            self.report.artists.append({"artist": a.name, "apple_music_url": url})
        self.report.stat("artists", found, len(lib.followed_artists))
        self.log(f"  found {found}/{len(lib.followed_artists)} (see artists_to_favorite.csv)")

    def _save_state(self, key: str, entry: Dict) -> None:
        self.state[key] = entry
        self.state_store.save(self.state)
