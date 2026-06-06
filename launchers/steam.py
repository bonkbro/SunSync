import os
import re
from functools import lru_cache
from typing import Tuple, List, Optional
from utils.utils import run_command

STEAM_FLATPAK_ID = "com.valvesoftware.Steam"

@lru_cache(maxsize=None)
def detect_steam_installation() -> Tuple[bool, str]:
    if run_command(f"flatpak list | grep {STEAM_FLATPAK_ID}").returncode == 0:
        return True, "flatpak"
    elif run_command("which steam").returncode == 0:
        return True, "native"
    else:
        return False, ""

def get_steam_root(installation_type: str) -> str:
    if installation_type == "flatpak":
        return os.path.expanduser("~/.var/app/com.valvesoftware.Steam/.steam/steam")
    else:
        return os.path.expanduser("~/.steam/steam")

def parse_vdf_value(line: str) -> Optional[str]:
    match = re.match(r'^\s*"[^"]*"\s*"([^"]*)"', line)
    return match.group(1) if match else None

def parse_libraryfolders(vdf_path: str) -> List[str]:
    if not os.path.exists(vdf_path):
        return []

    paths = []
    with open(vdf_path, 'r') as f:
        content = f.read()

    for match in re.finditer(r'"path"\s*"([^"]*)"', content):
        paths.append(match.group(1))

    return paths

def parse_appmanifest(manifest_path: str) -> Optional[Tuple[str, str]]:
    if not os.path.exists(manifest_path):
        return None

    appid = None
    name = None
    with open(manifest_path, 'r') as f:
        for line in f:
            if '"appid"' in line:
                appid = parse_vdf_value(line)
            elif '"name"' in line:
                name = parse_vdf_value(line)
            if appid and name:
                break

    if appid and name:
        return appid, name
    return None

def list_steam_games() -> List[Tuple[str, str]]:
    installed, installation_type = detect_steam_installation()
    if not installed:
        return []

    steam_root = get_steam_root(installation_type)
    libraryfolders_path = os.path.join(steam_root, "config", "libraryfolders.vdf")

    library_paths = parse_libraryfolders(libraryfolders_path)
    if not library_paths:
        library_paths = [os.path.join(steam_root, "steamapps")]

    games = []
    exclude_patterns = ["proton", "steam linux runtime", "steamworks common", "steamvr"]
    for lib_path in library_paths:
        steamapps_path = os.path.join(lib_path, "steamapps")
        if os.path.exists(steamapps_path):
            for filename in os.listdir(steamapps_path):
                if filename.startswith("appmanifest_") and filename.endswith(".acf"):
                    manifest_path = os.path.join(steamapps_path, filename)
                    result = parse_appmanifest(manifest_path)
                    if result:
                        appid, name = result
                        if not any(name.lower().startswith(pattern) for pattern in exclude_patterns):
                            games.append((appid, name))

    return games

def get_steam_command() -> str:
    installed, installation_type = detect_steam_installation()
    if not installed:
        return ""
    if installation_type == "flatpak":
        return f"flatpak run {STEAM_FLATPAK_ID}"
    else:
        return "steam"
