from music_transfer.models import Album, Artist, Library, Playlist, Track


def test_library_roundtrip(tmp_path):
    t = Track(name="Song", artists=["A", "B"], album="Alb", duration_ms=1000, isrc="usabc1234567", spotify_id="x")
    lib = Library(
        source="spotify",
        playlists=[Playlist(name="P", tracks=[t])],
        liked_tracks=[t],
        saved_albums=[Album(name="Alb", artists=["A"], upc="123")],
        followed_artists=[Artist(name="A", genres=["rock"])],
        top_tracks={"short_term": [t]},
        top_artists={"short_term": [Artist(name="A")]},
    )
    path = tmp_path / "out" / "lib.json"
    lib.save(path)
    loaded = Library.load(path)
    assert loaded.to_dict() == lib.to_dict()
    assert loaded.playlists[0].tracks[0].key == "isrc:USABC1234567"
    assert "Playlists:        1 (1 tracks)" in loaded.summary()


def test_track_key_fallbacks():
    assert Track(name="X", artists=["Y"], spotify_id="id1").key == "spotify:id1"
    assert Track(name="X", artists=["Y"]).key == "text:y|x"
