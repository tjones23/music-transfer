# music-transfer

A cross-platform (Windows / macOS / Linux) Python command-line tool that copies your
**Spotify** library into **Apple Music**.

| From Spotify | Into Apple Music |
|---|---|
| Playlists (yours, collaborative, and followed) | Library playlists with the same name, description and track order |
| Liked Songs | Added to your library, marked **Favorite/Loved**, plus a `Liked Songs (Spotify)` playlist that keeps the order |
| Saved albums | Added to your library |
| Top tracks: last 4 weeks, last 6 months, all time | `Spotify Top Tracks – …` playlists |
| Followed artists and top artists | A CSV of Apple Music links you can open to favorite each artist (Apple's API can't follow artists) |
| Profile (name, country, plan) | Saved in the export file for reference |

Tracks are matched by **ISRC** first, the industry-standard recording ID, which gives exact matches.
If that fails, the tool searches the Apple Music catalog and scores the results on title, artist,
duration and explicit/clean version. Anything that can't be matched confidently goes into a report
instead of being guessed.

The work happens in two steps, so you can inspect your data in between:

1. `spotify-export` saves everything to `exports/spotify_library.json`. This is also a useful backup.
2. `apple-import` reads that file and writes to Apple Music.

Imports can be **resumed**: progress and matches are saved, so if a run is interrupted or fails,
run the same command again and it picks up where it stopped without duplicating anything.

---

## 1. Requirements

* Python **3.9+**. On Windows, install from [python.org](https://www.python.org/downloads/) and tick "Add python.exe to PATH". On macOS, use the python.org installer or `brew install python`.
* A **Spotify** account. Any plan works, but Spotify may require the app owner to have Premium for apps in Development Mode.
* An **Apple Music subscription**.
* An **Apple Developer Program membership** (US$99/year). Apple only issues Apple Music API keys
  to developer accounts, and there is no way around this for third-party tools. If you don't
  have one, you can still run `spotify-export` and `export-csv` to get your data out.

## 2. Install

macOS / Linux:

```bash
git clone https://github.com/tjones23/music-transfer.git
cd music-transfer
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Windows (PowerShell):

```powershell
git clone https://github.com/tjones23/music-transfer.git
cd music-transfer
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

> If PowerShell blocks the activate script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

You now have the `music-transfer` command. `python -m music_transfer` does the same thing.

Copy the settings template:

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

## 3. Set up Spotify access (about 2 minutes)

1. Go to <https://developer.spotify.com/dashboard> and click **Create app**.
2. Give it any name and description. Under **Redirect URIs**, add exactly:
   `http://127.0.0.1:8888/callback`
3. Tick **Web API**, then save.
4. Copy the **Client ID** into `.env` as `SPOTIFY_CLIENT_ID=...`.

No client secret is needed. The tool uses OAuth with PKCE and only asks for read-only permissions.

## 4. Set up Apple Music access (about 5 minutes)

1. In the [Apple Developer portal](https://developer.apple.com/account/resources/identifiers/list/musicId),
   go to **Identifiers → Media IDs** and register a Media ID with **MusicKit** enabled.
2. Go to **Keys**, click **+**, tick **Media Services (MusicKit, ShazamKit, Apple Music Feed)**,
   configure it with your Media ID, then continue and register.
3. **Download** the `AuthKey_XXXXXXXXXX.p8` file. Apple only lets you download it once. Keep it private.
4. Fill in `.env`:
   ```
   APPLE_TEAM_ID=ABCDE12345          # top-right of the developer portal / Membership page
   APPLE_KEY_ID=XXXXXXXXXX           # shown on the key's page
   APPLE_PRIVATE_KEY_PATH=C:\Users\me\keys\AuthKey_XXXXXXXXXX.p8   # or /Users/me/keys/...
   ```

When you first import, a browser page opens on `http://127.0.0.1:8889`. Click
**Sign in to Apple Music** and approve access with your Apple ID.

## 5. Run it

```bash
# 1) Export from Spotify (opens a browser for login the first time)
music-transfer spotify-export

# See what was exported
music-transfer summary

# 2) Preview the import: matches everything and writes reports, but changes nothing
music-transfer apple-import --dry-run

# 3) Do it for real
music-transfer apple-import
```

Or do both steps in one go with `music-transfer transfer`.

After an import, check `exports/`:

* `unmatched_tracks.csv` lists songs that couldn't be found in Apple Music's catalog in your country.
* `artists_to_favorite.csv` has Apple Music links for your followed artists.
* `transfer_report.json` has full stats and any errors.

### Useful options

```text
spotify-export
  --owned-only            only playlists you own or collaborate on
  --no-playlists / --no-liked / --no-albums / --no-artists / --no-top
  -o FILE                 output path (default exports/spotify_library.json)
  --relogin               force a fresh Spotify login

apple-import [FILE]
  --dry-run               match only; don't change your Apple Music library
  --only liked,playlists  just these sections (playlists, liked, albums, artists, top)
  --skip top              skip a section (repeatable)
  --playlist "Road Trip"  only this playlist (repeatable)
  --owned-only            skip playlists you only follow
  --prefix "Spotify - "   prefix for created playlist names
  --no-love               don't mark liked songs as Favorite
  --no-liked-playlist     don't create the "Liked Songs (Spotify)" playlist
  --relogin               force a fresh Apple Music sign-in

export-csv [FILE] -o DIR  write tracks.csv / albums.csv / artists.csv (for spreadsheets or other tools)
logout [--all]            delete saved logins (--all also clears match cache and resume progress)
```

## Where data is stored

Login tokens, the match cache and resume progress are kept outside the repo:

* Windows: `%APPDATA%\music-transfer\`
* macOS: `~/Library/Application Support/music-transfer/`
* Linux: `~/.config/music-transfer/`

Set `MUSIC_TRANSFER_HOME` to use a different folder. Exports and reports go into `./exports/`.
`.env`, `*.p8` and `exports/` are git-ignored.

## Limitations

* **Apple Music's API can't delete or reorder playlist tracks**, so the tool never modifies a playlist it didn't create. To redo a playlist, delete it in the Music app and run `music-transfer logout --all` to clear the progress records, then import again.
* **Following artists** isn't supported by Apple's API. Use the links in `artists_to_favorite.csv` instead.
* **Podcasts and local files** in playlists are skipped.
* **Catalog differences**: some songs exist on Spotify but not on Apple Music in your region. These end up in `unmatched_tracks.csv`.
* **Spotify API restrictions**: in Development Mode, Spotify may only return tracks for playlists you own or collaborate on, and may block Spotify-curated playlists. Those playlists are exported with a warning and no tracks.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Project layout:

```
src/music_transfer/
  cli.py              command-line interface
  models.py           service-neutral Library / Playlist / Track model (the export format)
  matching.py         ISRC + fuzzy matching with a persistent cache
  localserver.py      one-shot localhost server for browser logins
  spotify/            OAuth PKCE login, API client, library extraction
  apple/              developer-token/user-token auth, API client, importer, login page
```

The export format is service-neutral, so other destinations (YouTube Music, Tidal, …) or
sources can be added later as new modules.
