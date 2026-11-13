from music_transfer.spotify.client import SpotifyAPIError
from music_transfer.spotify.extract import SpotifyExtractor


def strack(i, isrc=True):
    return {"type": "track", "id": f"t{i}", "name": f"Track {i}", "artists": [{"name": "Artist"}],
            "album": {"name": "Album"}, "duration_ms": 1000 * i, "explicit": False,
            "external_ids": {"isrc": f"ISRC{i}"} if isrc else {}}


class FakeSpotify:
    def __init__(self, legacy_playlist_endpoint=False):
        self.legacy = legacy_playlist_endpoint

    def get(self, path, params=None):
        if path == "/me":
            return {"id": "me", "display_name": "Me", "country": "US", "product": "premium"}
        if path == "/me/top/tracks":
            return {"items": [strack(9)]}
        if path == "/me/top/artists":
            return {"items": [{"id": "a1", "name": "Artist", "genres": ["pop"]}]}
        raise AssertionError(path)

    def paginate(self, path, params=None, key=None):
        if path == "/me/tracks":
            yield {"added_at": "2024-01-01T00:00:00Z", "track": strack(1)}
            yield {"added_at": "2024-01-02T00:00:00Z", "track": None}  # unavailable
        elif path == "/me/playlists":
            yield {"id": "p1", "name": "Mine", "owner": {"id": "me"}, "collaborative": False}
            yield {"id": "p2", "name": "Theirs", "owner": {"id": "other", "display_name": "Other"}}
        elif path.endswith("/items"):
            if self.legacy:
                raise SpotifyAPIError(404, "not found")
            yield {"item": strack(2)}
            yield {"item": {"type": "episode", "name": "Podcast"}}
        elif path.endswith("/tracks"):
            yield {"track": strack(3)}
        elif path == "/me/albums":
            yield {"album": {"id": "al", "name": "Album", "artists": [{"name": "Artist"}], "external_ids": {"upc": "999"}}}
        elif path == "/me/following":
            assert key == "artists"
            yield {"id": "a1", "name": "Artist", "genres": []}
        else:
            raise AssertionError(path)


def test_extract_everything():
    lib = SpotifyExtractor(FakeSpotify(), log=lambda *_: None).extract()
    assert [t.name for t in lib.liked_tracks] == ["Track 1"]
    assert lib.liked_tracks[0].isrc == "ISRC1"
    assert [p.name for p in lib.playlists] == ["Mine", "Theirs"]
    assert [t.name for t in lib.playlists[0].tracks] == ["Track 2"]  # episode skipped
    assert lib.playlists[1].owned_by_user is False
    assert lib.saved_albums[0].upc == "999"
    assert lib.followed_artists[0].name == "Artist"
    assert set(lib.top_tracks) == {"short_term", "medium_term", "long_term"}


def test_owned_only_and_legacy_endpoint():
    lib = SpotifyExtractor(FakeSpotify(legacy_playlist_endpoint=True), log=lambda *_: None).extract(
        include_followed_playlists=False, include_top=False
    )
    assert [p.name for p in lib.playlists] == ["Mine"]
    assert [t.name for t in lib.playlists[0].tracks] == ["Track 3"]
