"""Configuration and per-user storage locations (Windows, macOS, Linux)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

APP_NAME = "music-transfer"


def data_dir() -> Path:
    """Directory where tokens and match caches are stored."""
    override = os.environ.get("MUSIC_TRANSFER_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def load_env(env_file: Optional[Path] = None) -> None:
    """Load settings from a .env file (current directory by default)."""
    load_dotenv(env_file if env_file else Path.cwd() / ".env", override=False)


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(
            f"Missing setting {name}. Add it to your .env file or environment "
            f"(see .env.example and README.md)."
        )
    return value


@dataclass
class SpotifySettings:
    client_id: str
    redirect_uri: str

    @classmethod
    def from_env(cls) -> "SpotifySettings":
        return cls(
            client_id=require("SPOTIFY_CLIENT_ID"),
            redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", "").strip()
            or "http://127.0.0.1:8888/callback",
        )


@dataclass
class AppleSettings:
    team_id: str
    key_id: str
    private_key_path: Path
    storefront: Optional[str] = None

    @classmethod
    def from_env(cls) -> "AppleSettings":
        key_path = Path(require("APPLE_PRIVATE_KEY_PATH")).expanduser()
        if not key_path.is_file():
            raise SystemExit(f"APPLE_PRIVATE_KEY_PATH does not point to a file: {key_path}")
        return cls(
            team_id=require("APPLE_TEAM_ID"),
            key_id=require("APPLE_KEY_ID"),
            private_key_path=key_path,
            storefront=os.environ.get("APPLE_STOREFRONT", "").strip().lower() or None,
        )


class JsonStore:
    """Tiny JSON file store used for tokens and caches."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)
        if sys.platform != "win32":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
