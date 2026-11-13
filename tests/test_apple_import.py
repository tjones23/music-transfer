from music_transfer.apple.importer import AppleImporter, ImportOptions
from music_transfer.config import JsonStore
from music_transfer.matching import Matcher
from music_transfer.models import Album, Artist, Library, Playlist, Track


class FakeApple:
    storefront = "us"

    def __init__(self):
        self.playlists = {}
        self.library_songs, self.library_albums, self.loved = [], [], []
        self.searches = 0

    def songs_by_isrc(self, isrcs):
        return [{"id": f"am-{i}", "attributes": {"isrc": i, "name": f"Track {i[-1]}", "artistName": "Artist"}}
                for i in isrcs if i != "MISSING1"]

    def search(self, term, types="songs", limit=10):
        self.searches += 1
        if types == "songs" and "Findable" in term:
            return [{"id": "am-find", "attributes": {"name": "Findable", "artistName": "Artist"}}]
        if types == "albums":
            return [{"id": "am-album", "attributes": {"name": "Album", "artistName": "Artist"}}]
        if types == "artists":
            return [{"id": "ar", "attributes": {"name": "Artist", "url": "https://music.apple.com/us/artist/ar"}}]
        return []

    def albums_by_upc(self, upc):
        return []

    def create_playlist(self, name, description=""):
        pid = f"pl{len(self.playlists)}"
        self.playlists[pid] = {"name": name, "tracks": []}
        return pid

    def add_tracks_to_playlist(self, pid, ids):
        self.playlists[pid]["tracks"].extend(ids)

    def add_to_library(self, song_ids=(), album_ids=()):
        self.library_songs += list(song_ids)
        self.library_albums += list(album_ids)

    def love_song(self, sid):
        self.loved.append(sid)


def make_lib():
    t1 = Track(name="Track 1", artists=["Artist"], isrc="ISRC1")
    t2 = Track(name="Findable", artists=["Artist"])
    t3 = Track(name="Nope", artists=["Nobody"], isrc="MISSING1")
    return Library(
        source="spotify",
        playlists=[Playlist(name="Mix", tracks=[t1, t2, t3, t1])],
        liked_tracks=[t1, t2],
        saved_albums=[Album(name="Album", artists=["Artist"])],
        followed_artists=[Artist(name="Artist")],
        top_tracks={"short_term": [t1]},
    )


def run(tmp_path, client, **opts):
    matcher = Matcher(client, JsonStore(tmp_path / "cache.json"), log=lambda *_: None)
    imp = AppleImporter(client, matcher, JsonStore(tmp_path / "state.json"), ImportOptions(**opts), log=lambda *_: None)
    return imp.run(make_lib())


def test_full_import(tmp_path):
    client = FakeApple()
    report = run(tmp_path, client)
    names = {p["name"]: p["tracks"] for p in client.playlists.values()}
    assert names["Mix"] == ["am-ISRC1", "am-find"]  # unmatched dropped, dupes removed
    assert names["Liked Songs (Spotify)"] == ["am-ISRC1", "am-find"]
    assert "Spotify Top Tracks – Last 4 Weeks" in names
    assert client.library_songs == ["am-ISRC1", "am-find"]
    assert client.loved == ["am-ISRC1", "am-find"]
    assert client.library_albums == ["am-album"]
    assert report.artists[0]["apple_music_url"].startswith("https://")
    assert [u["title"] for u in report.unmatched_tracks] == ["Nope"]
    assert report.stats["liked"] == {"matched": 2, "total": 2}

    # Re-running is idempotent: nothing is created or added twice, and matches are cached.
    searches = client.searches
    run(tmp_path, client)
    assert len(client.playlists) == 3
    assert client.loved == ["am-ISRC1", "am-find"]
    assert client.library_albums == ["am-album"]
    assert client.searches == searches + 1  # only the artist lookup is repeated


def test_dry_run_changes_nothing(tmp_path):
    client = FakeApple()
    report = run(tmp_path, client, dry_run=True)
    assert client.playlists == {} and client.library_songs == [] and client.loved == []
    assert report.stats["playlist"]["matched"] == 3


def test_report_files(tmp_path):
    report = run(tmp_path, FakeApple(), dry_run=True)
    out = report.write(tmp_path / "reports")
    assert (out / "unmatched_tracks.csv").read_text(encoding="utf-8-sig").splitlines()[1].endswith("Nope,,MISSING1")
    assert (out / "artists_to_favorite.csv").exists()
