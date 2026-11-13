"""Command line interface: `music-transfer <command>`."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import AppleSettings, JsonStore, SpotifySettings, data_dir, load_env
from .models import Library

DEFAULT_EXPORT = Path("exports") / "spotify_library.json"


def _fix_console_encoding() -> None:
    # Windows consoles may not be UTF-8; never crash on non-ASCII track names.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


# -- Spotify ---------------------------------------------------------------
def cmd_spotify_export(args) -> Path:
    from .spotify.auth import SpotifyAuth
    from .spotify.client import SpotifyClient
    from .spotify.extract import SpotifyExtractor

    auth = SpotifyAuth(SpotifySettings.from_env())
    if args.relogin:
        auth.logout()
    client = SpotifyClient(auth.access_token)
    lib = SpotifyExtractor(client).extract(
        include_playlists=not args.no_playlists,
        include_followed_playlists=not args.owned_only,
        include_liked=not args.no_liked,
        include_albums=not args.no_albums,
        include_artists=not args.no_artists,
        include_top=not args.no_top,
    )
    out = Path(args.output)
    lib.save(out)
    print("\n" + lib.summary())
    print(f"\nSaved Spotify export to {out.resolve()}")
    return out


# -- Apple Music -----------------------------------------------------------
def _apple_client(force_login: bool = False):
    from .apple.auth import AppleAuth
    from .apple.client import AppleAPIError, AppleMusicClient

    settings = AppleSettings.from_env()
    auth = AppleAuth(settings)
    if force_login:
        auth.clear_user_token()
    client = AppleMusicClient(auth.developer_token(), auth.user_token, settings.storefront)
    try:
        sf = client.storefront
    except AppleAPIError as exc:
        if exc.status in (401, 403):
            print("Apple Music session expired; please sign in again.")
            auth.clear_user_token()
            sf = client.storefront
        else:
            raise
    return client, sf


def cmd_apple_login(args) -> None:
    client, sf = _apple_client(force_login=True)
    print(f"Apple Music ready (storefront: {sf}).")


def cmd_apple_import(args) -> None:
    from .apple.importer import ALL_SECTIONS, AppleImporter, ImportOptions
    from .matching import Matcher

    path = Path(args.input)
    if not path.is_file():
        raise SystemExit(f"Export file not found: {path}. Run `music-transfer spotify-export` first.")
    lib = Library.load(path)
    print(lib.summary() + "\n")

    sections = set(ALL_SECTIONS)
    if args.only:
        sections = {s.strip() for s in args.only.split(",") if s.strip()}
        bad = sections - set(ALL_SECTIONS)
        if bad:
            raise SystemExit(f"Unknown section(s): {', '.join(sorted(bad))}. Choose from {', '.join(ALL_SECTIONS)}.")
    for s in args.skip or []:
        sections.discard(s)

    client, sf = _apple_client(force_login=args.relogin)
    print(f"Apple Music storefront: {sf}" + ("   [DRY RUN – nothing will be changed]" if args.dry_run else ""))

    matcher = Matcher(client, JsonStore(data_dir() / f"apple_match_cache_{sf}.json"))
    importer = AppleImporter(
        client,
        matcher,
        JsonStore(data_dir() / "apple_import_state.json"),
        ImportOptions(
            sections=sections,
            playlist_filter=args.playlist or [],
            owned_only=args.owned_only,
            name_prefix=args.prefix,
            liked_playlist=not args.no_liked_playlist,
            love_liked=not args.no_love,
            dry_run=args.dry_run,
        ),
    )
    report = importer.run(lib)
    out_dir = report.write(Path(args.report_dir))

    print("\n=== Transfer summary ===")
    for section, s in report.stats.items():
        pct = (100 * s["matched"] / s["total"]) if s["total"] else 100
        print(f"  {section:<14} {s['matched']:>5}/{s['total']:<5} matched ({pct:.0f}%)")
    if report.errors:
        print(f"  {len(report.errors)} error(s) – see transfer_report.json")
    print(f"\nReports written to {out_dir.resolve()}")


def cmd_transfer(args) -> None:
    args.input = str(cmd_spotify_export(args))
    print()
    cmd_apple_import(args)


# -- Utilities -------------------------------------------------------------
def cmd_summary(args) -> None:
    print(Library.load(Path(args.input)).summary())


def cmd_export_csv(args) -> None:
    lib = Library.load(Path(args.input))
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    fields = ["playlist", "position", "title", "artist", "album", "isrc", "duration_ms", "added_at"]
    rows = []
    for i, t in enumerate(lib.liked_tracks, 1):
        rows.append(["Liked Songs", i, t.name, ", ".join(t.artists), t.album, t.isrc, t.duration_ms, t.added_at])
    for p in lib.playlists:
        for i, t in enumerate(p.tracks, 1):
            rows.append([p.name, i, t.name, ", ".join(t.artists), t.album, t.isrc, t.duration_ms, t.added_at])
    with open(out / "tracks.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(fields)
        w.writerows(rows)
    with open(out / "albums.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["album", "artist", "upc", "release_date", "added_at"])
        w.writerows([[a.name, ", ".join(a.artists), a.upc, a.release_date, a.added_at] for a in lib.saved_albums])
    with open(out / "artists.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["artist", "genres"])
        w.writerows([[a.name, "; ".join(a.genres)] for a in lib.followed_artists])
    print(f"Wrote tracks.csv, albums.csv and artists.csv to {out.resolve()}")


def cmd_logout(args) -> None:
    removed = []
    for name in ("spotify_token.json", "apple_token.json"):
        p = data_dir() / name
        if p.exists():
            p.unlink()
            removed.append(name)
    if args.all:
        for p in data_dir().glob("apple_*.json"):
            p.unlink()
            removed.append(p.name)
    print("Removed: " + (", ".join(removed) if removed else "nothing"))
    print(f"Data directory: {data_dir()}")


# -- Parser ----------------------------------------------------------------
def _add_export_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("-o", "--output", default=str(DEFAULT_EXPORT), help="Where to save the export JSON")
    p.add_argument("--no-playlists", action="store_true", help="Skip playlists")
    p.add_argument("--no-liked", action="store_true", help="Skip liked songs")
    p.add_argument("--no-albums", action="store_true", help="Skip saved albums")
    p.add_argument("--no-artists", action="store_true", help="Skip followed artists")
    p.add_argument("--no-top", action="store_true", help="Skip top tracks/artists (listening preferences)")
    p.add_argument("--relogin", action="store_true", help="Force a fresh login")


def _add_import_args(p: argparse.ArgumentParser, with_input: bool = True) -> None:
    if with_input:
        p.add_argument("input", nargs="?", default=str(DEFAULT_EXPORT), help="Export JSON from spotify-export")
    p.add_argument("--dry-run", action="store_true", help="Match tracks and write reports, but change nothing")
    p.add_argument("--only", help="Comma-separated sections: playlists,liked,albums,artists,top")
    p.add_argument("--skip", action="append", choices=["playlists", "liked", "albums", "artists", "top"], help="Section to skip (repeatable)")
    p.add_argument("--playlist", action="append", metavar="NAME", help="Only transfer this playlist (repeatable)")
    p.add_argument("--owned-only", action="store_true", help="Only playlists you own/collaborate on")
    p.add_argument("--prefix", default="", help="Prefix for created playlist names, e.g. 'Spotify - '")
    p.add_argument("--no-love", action="store_true", help="Don't mark liked songs as Loved/Favorite")
    p.add_argument("--no-liked-playlist", action="store_true", help="Don't create a 'Liked Songs (Spotify)' playlist")
    p.add_argument("--report-dir", default="exports", help="Directory for reports (default: exports)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="music-transfer",
        description="Transfer playlists, liked songs, albums and preferences from Spotify to Apple Music.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--env-file", help="Path to a .env file (default: ./.env)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("spotify-export", help="Log in to Spotify and export your library to JSON")
    _add_export_args(p)
    p.add_argument("--owned-only", action="store_true", help="Only export playlists you own/collaborate on")
    p.set_defaults(func=cmd_spotify_export)

    p = sub.add_parser("apple-login", help="Sign in to Apple Music (stores a user token)")
    p.set_defaults(func=cmd_apple_login)

    p = sub.add_parser("apple-import", help="Import a Spotify export into Apple Music")
    _add_import_args(p)
    p.add_argument("--relogin", action="store_true", help="Force a fresh Apple Music sign-in")
    p.set_defaults(func=cmd_apple_import)

    p = sub.add_parser("transfer", help="Export from Spotify and import into Apple Music in one go")
    _add_export_args(p)
    _add_import_args(p, with_input=False)
    p.set_defaults(func=cmd_transfer)

    p = sub.add_parser("summary", help="Show what's in an export file")
    p.add_argument("input", nargs="?", default=str(DEFAULT_EXPORT))
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("export-csv", help="Convert an export JSON into CSV files")
    p.add_argument("input", nargs="?", default=str(DEFAULT_EXPORT))
    p.add_argument("-o", "--output", default="exports/csv")
    p.set_defaults(func=cmd_export_csv)

    p = sub.add_parser("logout", help="Delete stored login tokens")
    p.add_argument("--all", action="store_true", help="Also delete match cache and import progress")
    p.set_defaults(func=cmd_logout)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    _fix_console_encoding()
    args = build_parser().parse_args(argv)
    load_env(Path(args.env_file) if args.env_file else None)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted. Progress is saved; re-run the same command to resume.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
