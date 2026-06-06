import os
import json
import base64
import requests
import getpass
import urllib3
import subprocess
import glob
import shlex
from typing import Tuple, Optional, Dict, List
from requests.utils import dict_from_cookiejar, cookiejar_from_dict
from config.constants import (
    DEFAULT_IMAGE,
    DEFAULT_SUNSHINE_HOST,
    DEFAULT_SUNSHINE_PORT,
)
from utils.utils import run_command
from launchers.lutris import get_lutris_command
from launchers.heroic import get_heroic_command
from launchers.faugus import get_faugus_command
from launchers.steam import get_steam_command
from launchers.retroarch import get_retroarch_command
from launchers.eden import get_eden_command
from display.manager import get_external_prep_commands, has_external_prep_scripts

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

INSTALLATION_TYPE = None
SERVER_NAME = "sunshine"
AUTH_SESSION: Optional[requests.Session] = None
AUTH_TOKEN: Optional[str] = None
API_HOST_OVERRIDE: Optional[str] = None
API_PORT_OVERRIDE: Optional[int] = None


def set_installation_type(type_: str):
    global INSTALLATION_TYPE
    INSTALLATION_TYPE = type_


def set_server_name(name: str):
    global SERVER_NAME
    SERVER_NAME = name


def _normalize_api_host(host: Optional[str]) -> str:
    normalized = (host or "").strip()
    return normalized or DEFAULT_SUNSHINE_HOST


def _normalize_api_port(port: Optional[object]) -> int:
    try:
        normalized = int(port)
    except (TypeError, ValueError):
        raise ValueError("Port must be an integer.")
    if not 1 <= normalized <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return normalized


def _api_connection_file_path() -> str:
    return os.path.join(_get_config_root(), "server_connection.json")


def _load_api_connection_settings() -> Dict[str, Dict[str, object]]:
    path = _api_connection_file_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as file:
            payload = json.load(file)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_api_connection_settings(settings: Dict[str, Dict[str, object]]) -> None:
    os.makedirs(_get_config_root(), exist_ok=True)
    with open(_api_connection_file_path(), "w") as file:
        json.dump(settings, file, indent=2)


def set_api_connection(host: Optional[str] = None, port: Optional[object] = None) -> None:
    global API_HOST_OVERRIDE, API_PORT_OVERRIDE, AUTH_SESSION, AUTH_TOKEN
    API_HOST_OVERRIDE = _normalize_api_host(host) if host is not None else None
    API_PORT_OVERRIDE = _normalize_api_port(port) if port is not None else None
    AUTH_SESSION = None
    AUTH_TOKEN = None


def save_api_connection(host: Optional[str], port: Optional[object], server_name: Optional[str] = None) -> None:
    target_server = (server_name or SERVER_NAME or "sunshine").strip().lower()
    current_host, current_port = get_api_connection(server_name=target_server)
    settings = _load_api_connection_settings()
    settings[target_server] = {
        "host": _normalize_api_host(host if host is not None else current_host),
        "port": _normalize_api_port(port if port is not None else current_port),
    }
    _save_api_connection_settings(settings)


def _get_environment_api_host(server_name: str) -> Optional[str]:
    keys = [
        f"SUNSYNC_{server_name.upper()}_HOST",
        "SUNSYNC_API_HOST",
    ]
    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None


def _get_environment_api_port(server_name: str) -> Optional[int]:
    keys = [
        f"SUNSYNC_{server_name.upper()}_PORT",
        "SUNSYNC_API_PORT",
    ]
    for key in keys:
        value = os.environ.get(key, "").strip()
        if not value:
            continue
        try:
            return _normalize_api_port(value)
        except ValueError:
            continue
    return None


def get_api_connection(server_name: Optional[str] = None) -> Tuple[str, int]:
    target_server = (server_name or SERVER_NAME or "sunshine").strip().lower()
    settings = _load_api_connection_settings()
    saved = settings.get(target_server, {}) if isinstance(settings.get(target_server, {}), dict) else {}

    host = (
        API_HOST_OVERRIDE
        or _get_environment_api_host(target_server)
        or _normalize_api_host(saved.get("host"))
    )

    saved_port = saved.get("port")
    try:
        normalized_saved_port = _normalize_api_port(saved_port) if saved_port is not None else DEFAULT_SUNSHINE_PORT
    except ValueError:
        normalized_saved_port = DEFAULT_SUNSHINE_PORT

    port = (
        API_PORT_OVERRIDE
        or _get_environment_api_port(target_server)
        or normalized_saved_port
    )
    return host, port


def get_api_url(server_name: Optional[str] = None) -> str:
    host, port = get_api_connection(server_name=server_name)
    return f"https://{host}:{port}"


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
_INSECURE_REMOTE_WARNED = False


def _warn_if_insecure_remote() -> None:
    """Warn once when talking to a non-local host.

    Sunshine/Apollo serve their web UI with a self-signed certificate, so TLS
    verification cannot be enabled without breaking every connection. That is
    fine over loopback, but for a remote host it means credentials travel over
    an unverified channel — flag it so the user keeps that traffic on a trusted
    network (LAN, VPN or SSH tunnel).
    """
    global _INSECURE_REMOTE_WARNED
    if _INSECURE_REMOTE_WARNED:
        return
    host, _ = get_api_connection()
    if host.strip().lower() not in _LOCAL_HOSTS:
        print(
            f"Warning: connecting to {get_server_display_name()} at a remote host "
            f"({host}) over an unverified TLS connection. Only do this on a network "
            "you trust (LAN/VPN/SSH tunnel)."
        )
        _INSECURE_REMOTE_WARNED = True


def _server_supports_token_auth() -> bool:
    return SERVER_NAME != "apollo"


def get_server_display_name() -> str:
    return "Apollo" if SERVER_NAME == "apollo" else "Sunshine"


def _get_apollo_process_config_root() -> Optional[str]:
    try:
        output = subprocess.check_output(["pgrep", "-x", "apollo-bin"], stderr=subprocess.STDOUT).decode()
    except subprocess.CalledProcessError:
        return None

    for pid in output.split():
        cmdline_path = f"/proc/{pid}/cmdline"
        try:
            with open(cmdline_path, "rb") as cmdline_file:
                args = [part.decode() for part in cmdline_file.read().split(b"\0") if part]
        except (OSError, UnicodeDecodeError):
            continue

        for arg in reversed(args[1:]):
            expanded = os.path.expanduser(arg)
            if expanded.endswith(".conf"):
                return os.path.dirname(os.path.realpath(expanded))

    return None


def _get_apollo_config_root() -> str:
    process_root = _get_apollo_process_config_root()
    if process_root:
        return process_root

    apollo_root = os.path.expanduser("~/.config/apollo")
    if os.path.isdir(apollo_root):
        return apollo_root

    sunshine_root = os.path.expanduser("~/.config/sunshine")
    if os.path.isdir(sunshine_root):
        return sunshine_root

    return apollo_root


def _get_config_root() -> str:
    if INSTALLATION_TYPE == "flatpak":
        return os.path.expanduser("~/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine")
    if SERVER_NAME == "apollo":
        return _get_apollo_config_root()
    return os.path.expanduser("~/.config/sunshine")


def get_covers_path():
    return os.path.join(_get_config_root(), "covers")


def get_api_key_path():
    return os.path.join(_get_config_root(), "steamgriddb_api_key.txt")


def get_credentials_path():
    return os.path.join(_get_config_root(), "credentials")


def detect_sunshine_installation() -> Tuple[bool, str]:
    if run_command("flatpak list | grep dev.lizardbyte.app.Sunshine").returncode == 0:
        return True, "flatpak"
    elif run_command("which sunshine").returncode == 0:
        return True, "native"
    else:
        appimage_paths = (
            glob.glob(os.path.expanduser("~/sunshine.AppImage")) +
            glob.glob(os.path.expanduser("~/.local/share/applications/sunshine.AppImage")) +
            glob.glob(os.path.expanduser("~/AppImages/sunshine.AppImage")) +
            glob.glob(os.path.expanduser("~/bin/sunshine.AppImage")) +
            glob.glob(os.path.expanduser("~/Downloads/sunshine.AppImage"))
        )
        if appimage_paths:
            return True, "appimage"
        return False, ""


def detect_apollo_installation() -> bool:
    return run_command("which apollo").returncode == 0


def _process_running(names: Tuple[str, ...]) -> bool:
    """True if a process with one of these exact names is running.

    Uses `pgrep -x` (exact comm match) instead of substring-scanning `ps -A`,
    which would false-positive on the virtual-monitor helper scripts named
    `sunshine-*`.
    """
    for name in names:
        try:
            result = subprocess.run(
                ["pgrep", "-x", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return False
        if result.returncode == 0:
            return True
    return False


def get_running_servers() -> List[str]:
    running = []
    if _process_running(("sunshine", "sunshine-bin", "Sunshine")):
        running.append("sunshine")
    if _process_running(("apollo", "apollo-bin")):
        running.append("apollo")
    return running


def is_server_running(name: Optional[str] = None) -> bool:
    running = get_running_servers()
    if name is None:
        return bool(running)
    return name in running


def _find_existing_app(game_name: str) -> Optional[Dict]:
    for app in get_existing_apps():
        if app["name"] == game_name:
            return app
    return None


def add_game_to_sunshine_api(
    game_name: str,
    cmd: str,
    image_path: str,
    prep_cmd: Optional[List[Dict[str, str]]] = None,
    detached: Optional[List[str]] = None,
) -> None:
    existing = _find_existing_app(game_name)
    index = existing["index"] if existing else -1
    payload = {
        "name": game_name,
        "output": "",
        "cmd": cmd,
        "index": index,
        "exclude-global-prep-cmd": False,
        "elevated": False,
        "auto-detach": True,
        "wait-all": True,
        "exit-timeout": 5,
        "prep-cmd": prep_cmd or [],
        "detached": detached or [],
        "image-path": image_path,
    }
    _, error = sunshine_api_request("POST", "/api/apps", json=payload)
    if error:
        print(f"Error {'updating' if existing else 'adding'} {game_name} in {get_server_display_name()} via API: {error}")
    else:
        print(f"{'Updated' if existing else 'Added'} {game_name} in {get_server_display_name()}.")


def get_sunshine_credentials() -> Tuple[str, str]:
    username = input("Enter your Sunshine/Apollo username: ")
    password = getpass.getpass("Enter your Sunshine/Apollo password: ")
    return username, password


def is_sunshine_running() -> bool:
    return is_server_running()


def _cookies_file_path():
    return os.path.join(get_credentials_path(), "cookies.json")


def _token_file_path() -> str:
    return os.path.join(get_credentials_path(), "auth_token.txt")


def _ensure_credentials_dir() -> None:
    path = get_credentials_path()
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _write_private_file(path: str, content: str) -> None:
    """Write a secret to disk readable only by the owner (mode 0600).

    Opening with the mode set avoids the brief window in which a default-umask
    file would be world-readable before a follow-up chmod.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    os.chmod(path, 0o600)


def _save_auth_token(token: str) -> None:
    _ensure_credentials_dir()
    _write_private_file(_token_file_path(), token)


def _save_session_cookies(session: requests.Session):
    _ensure_credentials_dir()
    cookies_dict = dict_from_cookiejar(session.cookies)
    try:
        _write_private_file(_cookies_file_path(), json.dumps(cookies_dict))
    except Exception as e:
        print(f"Warning: Failed to save cookies: {e}")


def _load_session_from_cookies() -> requests.Session:
    session = requests.Session()
    cookie_file = _cookies_file_path()
    if os.path.exists(cookie_file):
        try:
            with open(cookie_file, "r") as f:
                cookies_dict = json.load(f)
            session.cookies = cookiejar_from_dict(cookies_dict)
        except Exception:
            try:
                os.remove(cookie_file)
            except Exception:
                pass
    return session


def _validate_session(session: requests.Session) -> bool:
    try:
        resp = session.get(f"{get_api_url()}/api/apps", verify=False, timeout=10)
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def _validate_token(token: str) -> bool:
    if not _server_supports_token_auth():
        return False
    try:
        resp = requests.get(
            f"{get_api_url()}/api/apps",
            headers={"Authorization": token},
            verify=False,
            timeout=10,
        )
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def _load_cached_auth_token() -> Optional[str]:
    token_path = _token_file_path()
    if not os.path.exists(token_path):
        return None
    try:
        with open(token_path, "r") as file:
            token = file.read().strip()
    except OSError:
        return None
    return token or None


def _basic_auth_login(username: str, password: str) -> Optional[requests.Session]:
    """Authenticate Sunshine via HTTP Basic auth, caching the token on success."""
    global AUTH_TOKEN
    session = requests.Session()
    session.auth = (username, password)
    if not _validate_session(session):
        return None
    token = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    _save_auth_token(token)
    AUTH_TOKEN = token
    return session


def _cookie_login(username: str, password: str) -> Optional[requests.Session]:
    """Authenticate Apollo via its form-login endpoint, caching cookies."""
    session = requests.Session()
    credentials = {"username": username, "password": password}
    for endpoint in ("/api/login", "/login", "/auth/login", "/api/auth/login"):
        url = f"{get_api_url()}{endpoint}"
        for kwargs in ({"json": credentials}, {"data": credentials}):
            try:
                session.post(url, verify=False, timeout=10, **kwargs)
            except requests.exceptions.RequestException:
                continue
            if _validate_session(session):
                _save_session_cookies(session)
                return session
    return None


def _attempt_login(username: str, password: str) -> Optional[requests.Session]:
    """Obtain a validated session for the given credentials, or None.

    Sunshine authenticates with HTTP Basic auth; Apollo uses a cookie-based
    form login. Picking the right method up front avoids probing endpoints that
    don't exist on the target server.
    """
    if _server_supports_token_auth():
        return _basic_auth_login(username, password)
    return _cookie_login(username, password)


def get_auth_session(allow_prompt: bool = True) -> Optional[requests.Session]:
    global AUTH_SESSION
    if AUTH_SESSION and _validate_session(AUTH_SESSION):
        return AUTH_SESSION

    session = _load_session_from_cookies()
    if _validate_session(session):
        AUTH_SESSION = session
        return session

    if not allow_prompt:
        return None

    if not is_sunshine_running():
        print("Error: Sunshine or Apollo is not running. Please start it and try again.")
        return None

    username, password = get_sunshine_credentials()
    if not username or not password:
        return None

    session = _attempt_login(username, password)
    if session is not None:
        AUTH_SESSION = session
        return session

    print("Error: Authentication failed. Could not obtain a valid session.")
    return None


def authenticate_with_credentials(username: str, password: str) -> bool:
    """Attempt authentication with given credentials without prompting. For GUI use."""
    global AUTH_SESSION
    session = _attempt_login(username, password)
    if session is not None:
        AUTH_SESSION = session
        return True
    return False


def ensure_authenticated(allow_prompt: bool = True) -> bool:
    global AUTH_TOKEN

    session = get_auth_session(allow_prompt=False)
    if session is not None:
        return True

    if _server_supports_token_auth():
        token = AUTH_TOKEN or _load_cached_auth_token()
        if token and _validate_token(token):
            AUTH_TOKEN = token
            return True

    if not allow_prompt:
        return False

    session = get_auth_session(allow_prompt=True)
    if session is not None:
        return True

    if _server_supports_token_auth():
        return get_auth_token() is not None

    return False


def get_auth_token() -> Optional[str]:
    global AUTH_TOKEN
    token_path = _token_file_path()

    if not _server_supports_token_auth():
        return None

    if not is_sunshine_running():
        print("Error: Sunshine or Apollo is not running. Please start it and try again.")
        return None

    if os.path.exists(token_path):
        with open(token_path, "r") as f:
            token = f.read().strip()

        if not _validate_token(token):
            print("Error: Existing token is invalid. Please re-enter your credentials.")
            os.remove(token_path)
        else:
            AUTH_TOKEN = token
            return token

    username, password = get_sunshine_credentials()
    if not username or not password:
        return None

    auth_header = f"{username}:{password}"
    encoded_auth = base64.b64encode(auth_header.encode()).decode()
    token = f"Basic {encoded_auth}"

    if not _validate_token(token):
        print("Error: Authentication failed. Please check your credentials.")
        return None

    _save_auth_token(token)
    AUTH_TOKEN = token
    return token


def build_game_command(game_id: str, runner) -> Optional[str]:
    if runner == "Lutris":
        lutris_cmd = get_lutris_command()
        return f"{lutris_cmd} {shlex.quote(f'lutris:rungameid/{game_id}')}"
    if runner in ["legendary", "gog", "nile", "sideload"]:
        heroic_cmd, _ = get_heroic_command()
        uri = shlex.quote(f"heroic://launch/{runner}/{game_id}")
        return f"{heroic_cmd} {uri} --no-gui --no-sandbox"
    if runner == "Steam":
        steam_cmd = get_steam_command()
        return f"{steam_cmd} {shlex.quote(f'steam://run/{game_id}')}"
    if runner == "Ryubing":
        return f"flatpak run io.github.ryubing.Ryujinx {shlex.quote(game_id)}"
    if runner == "Eden":
        eden_cmd = get_eden_command()
        if not eden_cmd:
            return None
        return f"{eden_cmd} -f -g {shlex.quote(game_id)}"
    if isinstance(runner, dict) and runner.get("type") == "Faugus":
        faugus_cmd = get_faugus_command()
        return f"{faugus_cmd} --game {shlex.quote(game_id)}"
    if isinstance(runner, dict) and runner.get("type") == "RetroArch":
        core_path = runner.get("core_path", "")
        core_path = os.path.expanduser(core_path) if core_path else core_path
        retroarch_cmd = get_retroarch_command()
        if not retroarch_cmd or not core_path:
            return None
        return f"{retroarch_cmd} -L {shlex.quote(core_path)} {shlex.quote(game_id)}"
    return (
        "flatpak run --command=bottles-cli com.usebottles.bottles run "
        f"-b {shlex.quote(str(runner))} -p {shlex.quote(game_id)}"
    )


def add_game_to_sunshine(game_id: str, game_name: str, image_path: str, runner) -> None:
    cmd = build_game_command(game_id, runner)
    if not cmd:
        print(f"Warning: Unable to determine launch command for {game_name}. Skipping.")
        return

    if INSTALLATION_TYPE == "flatpak":
        cmd = f"flatpak-spawn --host {cmd}"

    prep_cmd = get_external_prep_commands() if has_external_prep_scripts() else []
    add_game_to_sunshine_api(game_name, cmd, image_path, prep_cmd=prep_cmd, detached=[])


def get_existing_apps() -> List[Dict]:
    data, error = sunshine_api_request("GET", "/api/apps")
    if error:
        print(f"Error retrieving existing apps from {get_server_display_name()} API: {error}")
        return []

    existing_apps = []
    apps_list = []
    if data is not None:
        apps_list = data.get("apps", [])
    else:
        print(f"Warning: No data received from {get_server_display_name()} API.")

    if isinstance(apps_list, list):
        for pos, app_data in enumerate(apps_list):
            if isinstance(app_data, dict) and "name" in app_data:
                existing_apps.append({
                    "name": app_data["name"],
                    "index": app_data.get("index", pos),
                })
    else:
        print("Warning: Unexpected data structure in API response.")

    return existing_apps


def get_existing_apps_full() -> List[Dict]:
    """Return complete app data for all Sunshine apps (for the Manage tab)."""
    data, error = sunshine_api_request("GET", "/api/apps")
    if error:
        return []
    apps_list = data.get("apps", []) if data else []
    result = []
    for pos, a in enumerate(apps_list):
        if isinstance(a, dict) and "name" in a:
            entry = dict(a)
            if "index" not in entry:
                entry["index"] = pos
            result.append(entry)
    return result


def update_app_image(app_index: int, image_path: str, app_data: Dict) -> Tuple[bool, str]:
    """Update the cover image of an existing Sunshine app, preserving all other fields."""
    payload = {
        "name": app_data.get("name", ""),
        "output": app_data.get("output", ""),
        "cmd": app_data.get("cmd", ""),
        "index": app_index,
        "exclude-global-prep-cmd": app_data.get("exclude-global-prep-cmd", False),
        "elevated": app_data.get("elevated", False),
        "auto-detach": app_data.get("auto-detach", True),
        "wait-all": app_data.get("wait-all", True),
        "exit-timeout": app_data.get("exit-timeout", 5),
        "prep-cmd": app_data.get("prep-cmd", []),
        "detached": app_data.get("detached", []),
        "image-path": image_path,
    }
    _, error = sunshine_api_request("POST", "/api/apps", json=payload)
    if error:
        return False, error
    return True, ""


def delete_app(app_index: int) -> Tuple[bool, str]:
    """Delete a Sunshine app by its numeric index."""
    _, error = sunshine_api_request("DELETE", f"/api/apps/{app_index}")
    if error:
        return False, error
    return True, ""


def sunshine_api_request(method, endpoint, **kwargs):
    _warn_if_insecure_remote()
    url = f"{get_api_url()}{endpoint}"
    session = kwargs.pop("session", None) or AUTH_SESSION
    token = kwargs.pop("token", None) or AUTH_TOKEN
    headers = kwargs.pop("headers", {})

    if session is None:
        session = get_auth_session(allow_prompt=False)

    if session is None and token is None:
        if not ensure_authenticated(allow_prompt=True):
            return None, "Error: Could not obtain authentication token or session."
        session = AUTH_SESSION
        token = AUTH_TOKEN

    if session:
        try:
            response = session.request(method, url, headers=headers, verify=False, **kwargs)
            response.raise_for_status()
            try:
                return response.json(), None
            except ValueError:
                return {"text": response.text}, None
        except requests.exceptions.RequestException as e:
            return None, str(e)

    if token is None:
        token = get_auth_token()

    if not token:
        return None, "Error: Could not obtain authentication token or session."

    headers = {**headers, "Authorization": token}
    try:
        response = requests.request(method, url, headers=headers, verify=False, **kwargs)
        response.raise_for_status()
        try:
            return response.json(), None
        except ValueError:
            return {"text": response.text}, None
    except requests.exceptions.RequestException as e:
        return None, str(e)
