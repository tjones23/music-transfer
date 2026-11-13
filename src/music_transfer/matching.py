"""Match tracks/albums from an exported library to Apple Music catalog items."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional

from .config import JsonStore
from .models import Album, Track

MATCH_THRESHOLD = 0.72

_PAREN = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_DASH_SUFFIX = re.compile(
    r"\s+-\s+.*\b(remaster(ed)?|live|version|edit|mix|mono|stereo|deluxe|anniversary|bonus|single)\b.*$",
    re.I,
)
_FEAT = re.compile(r"\b(feat\.?|ft\.?|featuring|with)\b.*$", re.I)
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize(text: str, strip_extras: bool = True) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("&", " and ")
    if strip_extras:
        text = _PAREN.sub(" ", text)
        text = _DASH_SUFFIX.sub("", text)
        text = _FEAT.sub("", text)
    return _NON_ALNUM.sub(" ", text).strip()


def similarity(a: str, b: str) -> float:
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def artist_similarity(source_artists: List[str], candidate_artist: str) -> float:
    cand = normalize(candidate_artist)
    if not source_artists:
        return 0.0
    best = max(similarity(a, candidate_artist) for a in source_artists)
    # Apple often joins artists ("A & B"); credit containment of the primary artist.
    primary = normalize(source_artists[0])
    if primary and primary in cand:
        best = max(best, 0.95)
    return best


def score_song(track: Track, candidate: Dict[str, Any]) -> float:
    attrs = candidate.get("attributes") or {}
    title = similarity(track.name, attrs.get("name", ""))
    artist = artist_similarity(track.artists, attrs.get("artistName", ""))
    score = 0.55 * title + 0.35 * artist
    cand_dur = attrs.get("durationInMillis")
    if track.duration_ms and cand_dur:
        diff = abs(track.duration_ms - cand_dur) / 1000.0
        score += 0.10 * (1.0 if diff <= 3 else 0.6 if diff <= 10 else 0.0 if diff > 30 else 0.3)
    else:
        score += 0.05
    # Prefer the same explicit/clean version.
    is_explicit = attrs.get("contentRating") == "explicit"
    if track.explicit != is_explicit:
        score -= 0.03
    if track.album and similarity(track.album, attrs.get("albumName", "")) > 0.85:
        score += 0.03
    return min(score, 1.0)


def score_album(album: Album, candidate: Dict[str, Any]) -> float:
    attrs = candidate.get("attributes") or {}
    title = similarity(album.name, attrs.get("name", ""))
    artist = artist_similarity(album.artists, attrs.get("artistName", ""))
    score = 0.6 * title + 0.4 * artist
    if album.total_tracks and attrs.get("trackCount") == album.total_tracks:
        score += 0.05
    return min(score, 1.0)


def best_candidate(candidates, scorer, item, threshold=MATCH_THRESHOLD):
    best, best_score = None, 0.0
    for c in candidates:
        s = scorer(item, c)
        if s > best_score:
            best, best_score = c, s
    return (best, best_score) if best is not None and best_score >= threshold else (None, best_score)


class Matcher:
    """Resolves Tracks to Apple Music catalog song IDs with a persistent cache."""

    def __init__(self, client, cache: JsonStore, log: Callable[[str], None] = print):
        self.client = client
        self.cache_store = cache
        self.cache: Dict[str, Optional[str]] = cache.load()
        self.log = log
        self._dirty = 0

    def save(self) -> None:
        self.cache_store.save(self.cache)
        self._dirty = 0

    def _remember(self, key: str, value: Optional[str]) -> None:
        self.cache[key] = value
        self._dirty += 1
        if self._dirty >= 50:
            self.save()

    def match_tracks(self, tracks: List[Track], progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, Optional[str]]:
        """Return {track.key: apple_song_id or None} for the given tracks."""
        pending = []
        seen = set()
        for t in tracks:
            if t.key not in self.cache and t.key not in seen:
                pending.append(t)
                seen.add(t.key)

        # 1) Batch ISRC lookups (fast and precise).
        with_isrc = [t for t in pending if t.isrc]
        if with_isrc:
            by_isrc: Dict[str, List[Dict[str, Any]]] = {}
            try:
                for song in self.client.songs_by_isrc(sorted({t.isrc.upper() for t in with_isrc})):
                    isrc = ((song.get("attributes") or {}).get("isrc") or "").upper()
                    by_isrc.setdefault(isrc, []).append(song)
            except Exception as exc:  # noqa: BLE001 - fall back to search
                self.log(f"  ! ISRC lookup failed ({exc}); falling back to search")
            for t in with_isrc:
                cand, _ = best_candidate(by_isrc.get(t.isrc.upper(), []), score_song, t, threshold=0.5)
                if cand:
                    self._remember(t.key, cand["id"])

        # 2) Text search for the rest.
        remaining = [t for t in pending if t.key not in self.cache]
        for i, t in enumerate(remaining, 1):
            self._remember(t.key, self._search_track(t))
            if progress:
                progress(i, len(remaining))
        self.save()
        return {t.key: self.cache.get(t.key) for t in tracks}

    def _search_track(self, t: Track) -> Optional[str]:
        queries = [
            f"{normalize(t.name)} {normalize(t.primary_artist)}",
            f"{t.name} {t.primary_artist}",
        ]
        for q in dict.fromkeys(queries):
            try:
                results = self.client.search(q, "songs", limit=10)
            except Exception as exc:  # noqa: BLE001
                self.log(f"  ! Search failed for {t.describe()}: {exc}")
                return None
            cand, _ = best_candidate(results, score_song, t)
            if cand:
                return cand["id"]
        return None

    def match_album(self, album: Album) -> Optional[str]:
        if album.key in self.cache:
            return self.cache[album.key]
        result = None
        try:
            if album.upc:
                cand, _ = best_candidate(self.client.albums_by_upc(album.upc), score_album, album, threshold=0.5)
                result = cand["id"] if cand else None
            if not result:
                q = f"{album.name} {album.artists[0] if album.artists else ''}"
                cand, _ = best_candidate(self.client.search(q, "albums", limit=10), score_album, album)
                result = cand["id"] if cand else None
        except Exception as exc:  # noqa: BLE001
            self.log(f"  ! Album lookup failed for {album.describe()}: {exc}")
            return None
        self._remember(album.key, result)
        return result

    def find_artist(self, name: str) -> Optional[Dict[str, Any]]:
        try:
            results = self.client.search(name, "artists", limit=5)
        except Exception:  # noqa: BLE001
            return None
        for r in results:
            if similarity(name, (r.get("attributes") or {}).get("name", "")) >= 0.9:
                return r
        return None
