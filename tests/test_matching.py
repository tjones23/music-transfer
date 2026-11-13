from music_transfer.matching import normalize, score_album, score_song, similarity
from music_transfer.models import Album, Track


def song(name, artist, dur=200000, album="", rating=None, sid="1"):
    return {"id": sid, "attributes": {"name": name, "artistName": artist, "durationInMillis": dur,
                                      "albumName": album, "contentRating": rating}}


def test_normalize_strips_versions_and_accents():
    assert normalize("Hey Jude - Remastered 2015") == "hey jude"
    assert normalize("Señorita (feat. Someone)") == "senorita"
    assert normalize("Rock & Roll") == "rock and roll"


def test_similarity_identical_after_normalization():
    assert similarity("Song (Live)", "song") == 1.0


def test_score_prefers_correct_song():
    t = Track(name="Bohemian Rhapsody - Remastered 2011", artists=["Queen"], duration_ms=354000)
    good = song("Bohemian Rhapsody", "Queen", 354320)
    bad = song("Bohemian Rhapsody", "Some Cover Band", 300000)
    assert score_song(t, good) > 0.9
    assert score_song(t, good) > score_song(t, bad)


def test_score_artist_containment():
    t = Track(name="Under Pressure", artists=["Queen", "David Bowie"], duration_ms=248000)
    assert score_song(t, song("Under Pressure", "Queen & David Bowie", 248000)) > 0.9


def test_score_album():
    a = Album(name="Abbey Road (Remastered)", artists=["The Beatles"], total_tracks=17)
    cand = {"id": "a", "attributes": {"name": "Abbey Road", "artistName": "The Beatles", "trackCount": 17}}
    assert score_album(a, cand) > 0.95
