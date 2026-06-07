import os
import sqlite3
from typing import Optional

from PIL import Image

from sunshine.sunshine import get_covers_path


_LUTRIS_DB_PATHS = [
    "~/.local/share/lutris/pga.db",
    "~/.config/lutris/pga.db",
]
_LUTRIS_COVERART_DIRS = [
    "~/.local/share/lutris/coverart",
    "~/.cache/lutris/coverart",
]
_IMAGE_EXTS = ("jpg", "jpeg", "png", "webp")


def _lutris_db_path() -> Optional[str]:
    for candidate in _LUTRIS_DB_PATHS:
        path = os.path.expanduser(candidate)
        if os.path.exists(path):
            return path
    return None


def get_lutris_cover(game_id: str) -> Optional[str]:
    """Return absolute path to Lutris local cover art, or None."""
    db = _lutris_db_path()
    if not db:
        return None
    try:
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT slug FROM games WHERE id = ?", (int(game_id),)
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    slug = row[0]
    for d in _LUTRIS_COVERART_DIRS:
        for ext in _IMAGE_EXTS:
            path = os.path.join(os.path.expanduser(d), f"{slug}.{ext}")
            if os.path.exists(path):
                return path
    return None


_STEAM_ROOTS = [
    "~/.local/share/Steam",
    "~/.steam/steam",
    "~/.var/app/com.valvesoftware.Steam/.steam/steam",
    "~/.var/app/com.valvesoftware.Steam/data/Steam",
]


def get_steam_cover(game_id: str) -> Optional[str]:
    """Return absolute path to Steam local cover art, or None.

    Checks both native and Flatpak Steam roots, since the install type the
    cover lives under may differ from the one detected for launching.
    """
    for root in _STEAM_ROOTS:
        steam_root = os.path.expanduser(root)
        if not os.path.isdir(steam_root):
            continue

        userdata = os.path.join(steam_root, "userdata")
        if os.path.isdir(userdata):
            for user_dir in os.listdir(userdata):
                grid = os.path.join(userdata, user_dir, "config", "grid")
                if not os.path.isdir(grid):
                    continue
                for ext in _IMAGE_EXTS:
                    for name in (f"{game_id}p.{ext}", f"{game_id}.{ext}"):
                        path = os.path.join(grid, name)
                        if os.path.exists(path):
                            return path

        cache = os.path.join(steam_root, "appcache", "librarycache")
        if os.path.isdir(cache):
            for suffix in ("library_600x900", "library_hero", ""):
                for ext in _IMAGE_EXTS:
                    name = f"{game_id}_{suffix}.{ext}" if suffix else f"{game_id}.{ext}"
                    path = os.path.join(cache, name)
                    if os.path.exists(path):
                        return path

    return None


def get_local_cover(game_id: str, launcher: str) -> Optional[str]:
    """Try to find a local cover image for the given launcher game. Returns path or None."""
    if launcher == "Lutris":
        return get_lutris_cover(game_id)
    if launcher == "Steam":
        return get_steam_cover(game_id)
    return None


def prepare_sunshine_cover(src_path: Optional[str], game_name: str) -> Optional[str]:
    """Convert a local cover into a PNG inside Sunshine's covers directory.

    Sunshine only serves PNG box art to Moonlight, so a referenced .jpg/.webp
    (e.g. straight from the Lutris cover cache) shows in this app but not in
    Moonlight. Returns the PNG path, or None if conversion fails.
    """
    if not src_path or not os.path.exists(src_path):
        return None
    covers_dir = get_covers_path()
    dest = os.path.join(covers_dir, f"{game_name.lower().replace(' ', '-')}.png")
    if os.path.abspath(src_path) == os.path.abspath(dest):
        return dest
    try:
        os.makedirs(covers_dir, exist_ok=True)
        with Image.open(src_path) as img:
            out = img.convert("RGBA") if img.mode not in ("RGB", "RGBA") else img
            out.save(dest, "PNG", optimize=True)
        return dest
    except Exception:
        return None


def get_cover_by_name(game_name: str) -> Optional[str]:
    """Search Lutris DB by name and return a cover path. Used in the Manage tab."""
    db = _lutris_db_path()
    if not db:
        return None
    try:
        with sqlite3.connect(db) as conn:
            for query, arg in [
                ("SELECT slug FROM games WHERE name = ?", game_name),
                ("SELECT slug FROM games WHERE lower(name) = lower(?)", game_name),
                ("SELECT slug FROM games WHERE lower(name) LIKE lower(?)", f"%{game_name}%"),
            ]:
                row = conn.execute(query, (arg,)).fetchone()
                if row:
                    slug = row[0]
                    for d in _LUTRIS_COVERART_DIRS:
                        for ext in _IMAGE_EXTS:
                            path = os.path.join(os.path.expanduser(d), f"{slug}.{ext}")
                            if os.path.exists(path):
                                return path
    except Exception:
        pass
    return None
