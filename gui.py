"""
gui.py — PyQt6 graphical interface for SunSync

Launch with:  sunsync --gui
              python3 gui.py
"""
import os
import sys
import shutil
from pathlib import Path
from typing import List, Tuple, Optional, Dict

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QCheckBox, QLabel, QDialog, QFormLayout, QMessageBox,
    QAbstractItemView, QSizePolicy, QGroupBox, QDialogButtonBox,
    QStackedWidget, QTabWidget, QFrame,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, pyqtSlot, QSize
from PyQt6.QtGui import QPixmap, QIcon, QPalette


def _bundled_scripts_dir() -> Path:
    """Locate the packaged virtual-monitor scripts.

    Works both from a source checkout (scripts/ next to this file) and from a
    system install (e.g. /usr/share/sunsync/scripts laid down by a package).
    """
    candidates = [
        Path(__file__).resolve().parent / "scripts",
        Path("/usr/share/sunsync/scripts"),
        Path("/usr/local/share/sunsync/scripts"),
    ]
    for candidate in candidates:
        if (candidate / "sunshine-start-vmon.sh").exists():
            return candidate
    raise FileNotFoundError(
        "Could not find bundled virtual-monitor scripts. "
        "Expected scripts/sunshine-start-vmon.sh alongside the application."
    )


# ---------------------------------------------------------------------------
# Launcher detection
# ---------------------------------------------------------------------------

def _get_available_launchers() -> dict:
    """Return {name: callable} for every detected launcher."""
    launchers: dict = {}

    try:
        from launchers.lutris import list_lutris_games, get_lutris_command
        if get_lutris_command():
            launchers["Lutris"] = lambda: [
                (gid, gname, "Lutris", "Lutris") for gid, gname in list_lutris_games()
            ]
    except Exception:
        pass

    try:
        from launchers.steam import detect_steam_installation, list_steam_games
        installed, _ = detect_steam_installation()
        if installed:
            launchers["Steam"] = lambda: [
                (gid, gname, "Steam", "Steam") for gid, gname in list_steam_games()
            ]
    except Exception:
        pass

    try:
        from launchers.heroic import list_heroic_games, get_heroic_command
        if get_heroic_command()[0]:
            launchers["Heroic"] = lambda: [
                (gid, gname, "Heroic", runner)
                for gid, gname, _, runner in list_heroic_games()
            ]
    except Exception:
        pass

    try:
        from launchers.bottles import detect_bottles_installation, list_bottles_games
        if detect_bottles_installation():
            launchers["Bottles"] = list_bottles_games
    except Exception:
        pass

    try:
        from launchers.faugus import detect_faugus_installation, list_faugus_games
        if detect_faugus_installation():
            launchers["Faugus"] = list_faugus_games
    except Exception:
        pass

    try:
        from launchers.ryubing import detect_ryubing_installation, list_ryubing_games
        if detect_ryubing_installation():
            launchers["Ryubing"] = lambda: [
                (gid, gname, "Ryubing", "Ryubing") for gid, gname in list_ryubing_games()
            ]
    except Exception:
        pass

    try:
        from launchers.retroarch import detect_retroarch_installation, list_retroarch_games
        if detect_retroarch_installation():
            launchers["RetroArch"] = lambda: [
                (gp, gn, "RetroArch", ci) for gp, gn, ci in list_retroarch_games()
            ]
    except Exception:
        pass

    try:
        from launchers.eden import detect_eden_installation, list_eden_games
        if detect_eden_installation():
            launchers["Eden"] = lambda: [
                (gid, gname, "Eden", "Eden") for gid, gname in list_eden_games()
            ]
    except Exception:
        pass

    return launchers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_active_server() -> Optional[str]:
    """Detect the installed/running backend and lock sunshine.py onto it.

    The GUI used to assume Sunshine unconditionally, which left Apollo users
    authenticating with the wrong method (Basic vs cookie) and reading the wrong
    config root. This mirrors the CLI's server selection so both front-ends behave
    the same. Best-effort: prefers a running server (Sunshine when both run, like
    the CLI), otherwise falls back to an installed one. Returns the chosen server
    name, or None when neither is installed (the GUI still opens so the user can
    read the status bar and open Settings).
    """
    from sunshine.sunshine import (
        detect_sunshine_installation, detect_apollo_installation,
        get_running_servers, set_installation_type, set_server_name,
    )
    sunshine_installed, sunshine_install_type = detect_sunshine_installation()
    apollo_installed = detect_apollo_installation()

    running = get_running_servers()
    if running:
        server = "sunshine" if "sunshine" in running else running[0]
    elif sunshine_installed:
        server = "sunshine"
    elif apollo_installed:
        server = "apollo"
    else:
        return None

    if server == "sunshine":
        set_installation_type(sunshine_install_type or "native")
    else:
        set_installation_type("native")
    set_server_name(server)
    return server


def _is_first_run() -> bool:
    """True if no connection has ever been saved for the active backend.

    Keyed on the active server so an Apollo user isn't re-prompted by a saved
    Sunshine connection (and vice versa), and so the flatpak config root is
    honoured instead of hard-coded paths.
    """
    from sunshine.sunshine import _load_api_connection_settings, SERVER_NAME
    return SERVER_NAME not in _load_api_connection_settings()


def _make_separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    return sep


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

class GameLoader(QObject):
    finished = pyqtSignal(list, list)   # (games, existing_names)
    error = pyqtSignal(str)

    def __init__(self, load_fn):
        super().__init__()
        self._load_fn = load_fn

    @pyqtSlot()
    def run(self):
        try:
            games = self._load_fn()
            existing: List[str] = []
            try:
                from sunshine.sunshine import get_existing_apps, ensure_authenticated
                if ensure_authenticated(allow_prompt=False):
                    existing = [app["name"] for app in get_existing_apps()]
            except Exception:
                pass
            self.finished.emit(sorted(games, key=lambda x: x[1].lower()), existing)
        except Exception as exc:
            self.error.emit(str(exc))


class CoverLoader(QObject):
    """Resolves local cover paths in a background thread; pixmaps are created on the main thread."""
    cover_ready = pyqtSignal(int, str)   # (list_index, image_path)
    finished = pyqtSignal()

    def __init__(self, games: list):
        super().__init__()
        self.games = games
        self._active = True

    def stop(self):
        self._active = False

    @pyqtSlot()
    def run(self):
        from utils.images import get_local_cover
        from display.manager import get_prefer_steamgriddb
        prefer_sgdb = get_prefer_steamgriddb()
        for i, (game_id, _, display_source, _) in enumerate(self.games):
            if not self._active:
                break
            if prefer_sgdb:
                continue
            try:
                path = get_local_cover(game_id, display_source)
                if path:
                    self.cover_ready.emit(i, path)
            except Exception:
                pass
        self.finished.emit()


class AddGamesWorker(QObject):
    progress = pyqtSignal(str)
    finished = pyqtSignal(int, int)   # added, total
    error = pyqtSignal(str)

    def __init__(self, games: List[Tuple], download_covers: bool, api_key: Optional[str]):
        super().__init__()
        self.games = games
        self.download_covers = download_covers
        self.api_key = api_key

    @pyqtSlot()
    def run(self):
        from sunshine.sunshine import (
            add_game_to_sunshine,
            prime_existing_apps_cache,
            clear_existing_apps_cache,
        )
        from config.constants import DEFAULT_IMAGE
        from display.manager import get_prefer_steamgriddb
        from utils.images import get_local_cover, prepare_sunshine_cover
        prefer_steamgriddb = get_prefer_steamgriddb()
        added = 0
        total = len(self.games)
        prime_existing_apps_cache()

        for game_id, game_name, display_source, runner in self.games:
            if display_source == "RetroArch":
                core_info = runner if isinstance(runner, dict) else {}
                core_path = (core_info.get("core_path", "") or "").strip()
                if not core_path or core_path.upper() == "DETECT":
                    self.error.emit(f"Skipped '{game_name}': RetroArch core not set.")
                    continue

            self.progress.emit(f"Adding {game_name}…")
            image_path = DEFAULT_IMAGE

            if not prefer_steamgriddb:
                try:
                    local = get_local_cover(game_id, display_source)
                    png = prepare_sunshine_cover(local, game_name) if local else None
                    if png:
                        image_path = png
                except Exception:
                    pass

            if image_path == DEFAULT_IMAGE and self.download_covers and self.api_key:
                try:
                    from utils.steamgriddb import download_image_from_steamgriddb
                    result = download_image_from_steamgriddb(game_name, self.api_key)
                    if result:
                        image_path = result
                except Exception:
                    pass

            try:
                add_game_to_sunshine(game_id, game_name, image_path, runner)
                added += 1
            except Exception as exc:
                self.error.emit(f"Error adding '{game_name}': {exc}")

        clear_existing_apps_cache()
        self.finished.emit(added, total)


class ManageAppsLoader(QObject):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    @pyqtSlot()
    def run(self):
        try:
            from sunshine.sunshine import get_existing_apps_full, ensure_authenticated
            if not ensure_authenticated(allow_prompt=False):
                self.finished.emit([])
                return
            apps = get_existing_apps_full()
            self.finished.emit(apps)
        except Exception as exc:
            self.error.emit(str(exc))


class UpdateCoversWorker(QObject):
    progress = pyqtSignal(str)
    finished = pyqtSignal(int, int)   # updated, total
    error = pyqtSignal(str)

    def __init__(self, apps: list):
        super().__init__()
        self.apps = apps

    @pyqtSlot()
    def run(self):
        from sunshine.sunshine import update_app_image
        from utils.images import get_cover_by_name, prepare_sunshine_cover
        updated = 0
        total = len(self.apps)
        for app in self.apps:
            name = app.get("name", "")
            idx = app.get("index", -1)
            self.progress.emit(f"Finding cover for {name}…")
            cover = prepare_sunshine_cover(get_cover_by_name(name), name)
            if cover:
                ok, err = update_app_image(idx, cover, app)
                if ok:
                    updated += 1
                else:
                    self.error.emit(f"Failed to update '{name}': {err}")
            else:
                self.error.emit(f"No local cover found for '{name}'")
        self.finished.emit(updated, total)


# ---------------------------------------------------------------------------
# Login dialog
# ---------------------------------------------------------------------------

class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        from sunshine.sunshine import get_server_display_name
        name = get_server_display_name()
        self.setWindowTitle(f"{name} login")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)

        info = QLabel(f"Enter your {name} credentials.")
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        self.username_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Username:", self.username_edit)
        form.addRow("Password:", self.password_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def credentials(self) -> Tuple[str, str]:
        return self.username_edit.text(), self.password_edit.text()


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        # Sunshine connection
        conn_group = QGroupBox("Sunshine connection")
        conn_layout = QFormLayout(conn_group)
        from sunshine.sunshine import get_api_connection
        host, port = get_api_connection()
        self.host_edit = QLineEdit(host or "localhost")
        self.port_edit = QLineEdit(str(port))
        self.port_edit.setMaximumWidth(80)
        conn_layout.addRow("Host:", self.host_edit)
        conn_layout.addRow("Port:", self.port_edit)
        layout.addWidget(conn_group)

        # Prep scripts
        prep_group = QGroupBox("Virtual monitor scripts (prep-cmd / undo)")
        prep_group.setToolTip(
            "Scripts that run before and after each game. Used to start and stop a virtual display."
        )
        prep_layout = QFormLayout(prep_group)
        from display.manager import get_external_prep_commands
        cmds = get_external_prep_commands()
        do_val = cmds[0]["do"] if cmds else ""
        undo_val = cmds[0]["undo"] if cmds else ""
        self.do_edit = QLineEdit(do_val)
        self.do_edit.setPlaceholderText("~/.local/bin/sunshine-start-vmon.sh")
        self.undo_edit = QLineEdit(undo_val)
        self.undo_edit.setPlaceholderText("~/.local/bin/sunshine-stop-vmon.sh")
        prep_layout.addRow("Do (pre-launch):", self.do_edit)
        prep_layout.addRow("Undo (post-launch):", self.undo_edit)
        layout.addWidget(prep_group)

        # SteamGridDB
        sgdb_group = QGroupBox("SteamGridDB")
        sgdb_layout = QFormLayout(sgdb_group)
        from sunshine.sunshine import get_api_key_path
        from display.manager import get_prefer_steamgriddb
        api_key_val = ""
        try:
            kp = get_api_key_path()
            if os.path.exists(kp):
                api_key_val = open(kp).read().strip()
        except Exception:
            pass
        self.api_key_edit = QLineEdit(api_key_val)
        self.api_key_edit.setPlaceholderText("Paste your SteamGridDB API key here")
        sgdb_layout.addRow("API Key:", self.api_key_edit)
        self.prefer_steamgriddb_check = QCheckBox("Use SteamGridDB instead of local covers")
        self.prefer_steamgriddb_check.setToolTip(
            "Use SteamGridDB instead of your local Lutris/Steam library for cover art."
        )
        self.prefer_steamgriddb_check.setChecked(get_prefer_steamgriddb())
        sgdb_layout.addRow("", self.prefer_steamgriddb_check)
        layout.addWidget(sgdb_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save_and_accept(self):
        from sunshine.sunshine import save_api_connection, set_api_connection, get_api_key_path
        from display.manager import (
            set_external_prep_scripts, clear_external_prep_scripts, set_prefer_steamgriddb,
        )

        host = self.host_edit.text().strip()
        try:
            port = int(self.port_edit.text().strip())
            if not 1 <= port <= 65535:
                raise ValueError()
        except ValueError:
            QMessageBox.warning(self, "Invalid port", "Port must be a number between 1 and 65535.")
            return

        set_api_connection(host=host, port=port)
        save_api_connection(host, port)

        do_script = os.path.expanduser(self.do_edit.text().strip())
        undo_script = os.path.expanduser(self.undo_edit.text().strip())
        if do_script or undo_script:
            set_external_prep_scripts(do_script, undo_script)
        else:
            clear_external_prep_scripts()

        api_key = self.api_key_edit.text().strip()
        try:
            kp = get_api_key_path()
            os.makedirs(os.path.dirname(kp), exist_ok=True)
            if api_key:
                fd = os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(api_key)
                os.chmod(kp, 0o600)
            elif os.path.exists(kp):
                os.remove(kp)
        except Exception:
            pass

        set_prefer_steamgriddb(self.prefer_steamgriddb_check.isChecked())
        self.accept()


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

class SetupWizardDialog(QDialog):
    """
    First-run setup wizard.

    Pages:
      0  Welcome
      1  Sunshine connection (host/port + test)
      2  Authentication (username/password + test)
      3  Virtual display — krfb-virtualmonitor scripts (detect / generate)
      4  Done / summary
    """

    _PAGES = [
        ("Welcome", ""),
        ("Sunshine Connection", "Where is Sunshine running?"),
        ("Authentication", "Sunshine credentials"),
        ("Virtual Display", "Start and stop a virtual monitor per game"),
        ("Done", ""),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SunSync Setup Wizard")
        self.setMinimumSize(640, 520)
        self._page = 0
        self._auth_ok = False
        self._conn_ok = False

        root = QVBoxLayout(self)
        root.setSpacing(6)

        self._title_lbl = QLabel()
        self._title_lbl.setStyleSheet("font-size: 16px; font-weight: bold; padding: 6px 0 2px;")
        self._sub_lbl = QLabel()
        self._sub_lbl.setWordWrap(True)
        root.addWidget(self._title_lbl)
        root.addWidget(self._sub_lbl)
        root.addWidget(_make_separator())

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("font-size: 13px;")
        root.addWidget(self._stack, stretch=1)

        nav = QHBoxLayout()
        self._skip_btn = QPushButton("Skip wizard")
        self._skip_btn.setFlat(True)
        self._skip_btn.clicked.connect(self.reject)
        self._back_btn = QPushButton("← Back")
        self._back_btn.setEnabled(False)
        self._back_btn.clicked.connect(self._go_back)
        self._next_btn = QPushButton("Start →")
        self._next_btn.setDefault(True)
        self._next_btn.setMinimumWidth(100)
        self._next_btn.clicked.connect(self._go_next)
        nav.addWidget(self._skip_btn)
        nav.addStretch()
        nav.addWidget(self._back_btn)
        nav.addWidget(self._next_btn)
        root.addLayout(nav)

        self._build_pages()
        self._show_page(0)

    # -----------------------------------------------------------------------
    # Page builders
    # -----------------------------------------------------------------------

    def _build_pages(self):
        for builder in [
            self._page_welcome,
            self._page_connection,
            self._page_auth,
            self._page_vdisplay,
            self._page_done,
        ]:
            self._stack.addWidget(builder())

    def _page_welcome(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(14)
        body = QLabel(
            "Welcome to SunSync.\n\n"
            "This wizard covers three steps:\n\n"
            "  1.  Connect to your Sunshine instance\n"
            "  2.  Set up authentication\n"
            "  3.  Configure a virtual display (optional)\n\n"
            "You can skip any step and change things later in Settings."
        )
        body.setWordWrap(True)
        layout.addWidget(body)
        layout.addStretch()
        return w

    def _page_connection(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)
        from sunshine.sunshine import get_api_connection
        host, port = get_api_connection()
        form = QFormLayout()
        # Prefill the real default rather than leaning on the placeholder — an
        # empty-looking field reads as "already set" and trips users up.
        self.wiz_host = QLineEdit(host or "localhost")
        self.wiz_host.setPlaceholderText("localhost")
        self.wiz_port = QLineEdit(str(port))
        self.wiz_port.setMaximumWidth(90)
        form.addRow("Host:", self.wiz_host)
        form.addRow("Port:", self.wiz_port)
        layout.addLayout(form)

        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self._test_connection)
        layout.addWidget(test_btn)

        self.wiz_conn_status = QLabel("")
        layout.addWidget(self.wiz_conn_status)

        note = QLabel("Default port is 47990.")
        layout.addWidget(note)
        layout.addStretch()
        return w

    def _page_auth(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)
        info = QLabel(
            "Enter the username and password you set up in Sunshine.\n"
            "SunSync uses them to talk to the Sunshine API."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        self.wiz_user = QLineEdit()
        self.wiz_user.setPlaceholderText("sunshine")
        self.wiz_pass = QLineEdit()
        self.wiz_pass.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Username:", self.wiz_user)
        form.addRow("Password:", self.wiz_pass)
        layout.addLayout(form)

        login_btn = QPushButton("Test Login")
        login_btn.clicked.connect(self._test_auth)
        layout.addWidget(login_btn)

        self.wiz_auth_status = QLabel("")
        layout.addWidget(self.wiz_auth_status)

        privacy_note = QLabel(
            "Your credentials are sent only to your local Sunshine instance. Nothing leaves your machine."
        )
        privacy_note.setWordWrap(True)
        layout.addWidget(privacy_note)

        layout.addStretch()
        return w

    def _page_vdisplay(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        krfb_ok = bool(shutil.which("krfb-virtualmonitor"))
        avail = QLabel(
            "✓  krfb-virtualmonitor found." if krfb_ok
            else (
                "✗  krfb-virtualmonitor not found.\n"
                "   Install it:  sudo pacman -S krfb   (Arch/CachyOS)\n"
                "                sudo apt install krfb  (Debian/Ubuntu)"
            )
        )
        avail.setWordWrap(True)
        layout.addWidget(avail)
        layout.addWidget(_make_separator())

        info = QLabel(
            "These scripts run before and after each game to start and stop the virtual display.\n"
            "Leave blank to skip this step."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        do_val, undo_val = self._detect_vmon_scripts()
        form = QFormLayout()
        self.wiz_do = QLineEdit(do_val)
        self.wiz_do.setPlaceholderText("~/.local/bin/sunshine-start-vmon.sh")
        self.wiz_undo = QLineEdit(undo_val)
        self.wiz_undo.setPlaceholderText("~/.local/bin/sunshine-stop-vmon.sh")
        form.addRow("Start script:", self.wiz_do)
        form.addRow("Stop script:", self.wiz_undo)
        layout.addLayout(form)

        gen_btn = QPushButton("Generate default scripts…")
        gen_btn.setEnabled(krfb_ok)
        gen_btn.setToolTip(
            "Creates start/stop scripts in ~/.local/bin/ that you can edit to change the display name or resolution."
        )
        gen_btn.clicked.connect(self._generate_vmon_scripts)
        layout.addWidget(gen_btn)

        self.wiz_vmon_status = QLabel("")
        layout.addWidget(self.wiz_vmon_status)
        layout.addStretch()
        return w

    def _page_done(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self._done_body = QLabel()
        self._done_body.setWordWrap(True)
        layout.addWidget(self._done_body)
        layout.addStretch()
        return w

    # -----------------------------------------------------------------------
    # Actions
    # -----------------------------------------------------------------------

    def _test_connection(self):
        from sunshine.sunshine import (
            set_api_connection, is_server_running, get_server_display_name,
        )
        try:
            port = int(self.wiz_port.text().strip())
        except ValueError:
            self.wiz_conn_status.setText("✗  Invalid port number.")
            return
        set_api_connection(host=self.wiz_host.text().strip(), port=port)
        name = get_server_display_name()
        if is_server_running():
            self.wiz_conn_status.setText(f"✓  {name} is running and reachable.")
            self._conn_ok = True
        else:
            self.wiz_conn_status.setText(
                f"Could not reach {name}. Check the host and port above."
            )

    def _test_auth(self):
        from sunshine.sunshine import authenticate_with_credentials
        user = self.wiz_user.text().strip()
        pwd = self.wiz_pass.text()
        if not user or not pwd:
            self.wiz_auth_status.setText("Enter a username and password.")
            return
        self.wiz_auth_status.setText("Testing…")
        QApplication.processEvents()
        if authenticate_with_credentials(user, pwd):
            self.wiz_auth_status.setText("✓  Login successful.")
            self._auth_ok = True
        else:
            self.wiz_auth_status.setText(
                "Login failed. Check your credentials and the host/port on the previous page."
            )

    def _detect_vmon_scripts(self) -> Tuple[str, str]:
        from display.manager import get_external_prep_commands
        cmds = get_external_prep_commands()
        if cmds:
            return cmds[0].get("do", ""), cmds[0].get("undo", "")
        for do_c, undo_c in [
            ("~/.local/bin/sunshine-start-vmon.sh", "~/.local/bin/sunshine-stop-vmon.sh"),
            ("~/bin/sunshine-start-vmon.sh", "~/bin/sunshine-stop-vmon.sh"),
            ("~/.config/sunshine/start-vmon.sh", "~/.config/sunshine/stop-vmon.sh"),
        ]:
            if Path(do_c).expanduser().exists():
                undo_found = undo_c if Path(undo_c).expanduser().exists() else ""
                return do_c, undo_found
        return "", ""

    def _generate_vmon_scripts(self):
        script_dir = Path("~/.local/bin").expanduser()
        start_path = script_dir / "sunshine-start-vmon.sh"
        stop_path = script_dir / "sunshine-stop-vmon.sh"

        try:
            src_dir = _bundled_scripts_dir()
            script_dir.mkdir(parents=True, exist_ok=True)
            for src_name, dst in (
                ("sunshine-start-vmon.sh", start_path),
                ("sunshine-stop-vmon.sh", stop_path),
            ):
                src = src_dir / src_name
                shutil.copyfile(src, dst)
                dst.chmod(0o755)
            self.wiz_do.setText(str(start_path))
            self.wiz_undo.setText(str(stop_path))
            self.wiz_vmon_status.setText(f"✓  Scripts written to {script_dir}")
        except Exception as exc:
            self.wiz_vmon_status.setText(f"✗  Error: {exc}")

    # -----------------------------------------------------------------------
    # Navigation
    # -----------------------------------------------------------------------

    def _show_page(self, idx: int):
        self._page = idx
        self._stack.setCurrentIndex(idx)
        title, subtitle = self._PAGES[idx]
        self._title_lbl.setText(title)
        self._sub_lbl.setText(subtitle)
        self._back_btn.setEnabled(idx > 0)
        last = self._stack.count() - 1
        if idx == 0:
            self._next_btn.setText("Start →")
        elif idx == last:
            self._next_btn.setText("Finish")
        else:
            self._next_btn.setText("Next →")

    def _go_next(self):
        last = self._stack.count() - 1
        if self._page == last:
            self._finish()
            return
        if self._page == 1:
            self._save_connection()
        elif self._page == 3:
            self._save_vdisplay()
        self._show_page(self._page + 1)
        if self._page == last:
            self._refresh_done_page()

    def _go_back(self):
        if self._page > 0:
            self._show_page(self._page - 1)

    def _save_connection(self):
        from sunshine.sunshine import set_api_connection, save_api_connection
        try:
            port = int(self.wiz_port.text().strip())
        except ValueError:
            return
        host = self.wiz_host.text().strip()
        set_api_connection(host=host, port=port)
        save_api_connection(host, port)

    def _save_vdisplay(self):
        from display.manager import set_external_prep_scripts, clear_external_prep_scripts
        do_s = os.path.expanduser(self.wiz_do.text().strip())
        undo_s = os.path.expanduser(self.wiz_undo.text().strip())
        if do_s or undo_s:
            set_external_prep_scripts(do_s, undo_s)
        else:
            clear_external_prep_scripts()

    def _refresh_done_page(self):
        from sunshine.sunshine import get_api_url
        from display.manager import get_external_prep_commands
        lines = [
            f"Sunshine URL:      {get_api_url()}",
            f"Authentication:    {'saved' if self._auth_ok else 'not tested yet (open Settings)'}",
        ]
        cmds = get_external_prep_commands()
        if cmds:
            lines.append("Virtual display:   scripts configured")
        else:
            lines.append("Virtual display:   not configured")
        lines += ["", "You can change all of this later in Settings."]
        self._done_body.setText("\n".join(lines))

    def _finish(self):
        self._save_connection()
        self._save_vdisplay()
        self.accept()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SunSync")
        self.setMinimumSize(720, 560)

        self._games: List[Tuple] = []
        self._existing_names: set = set()
        self._sunshine_apps: List[Dict] = []
        self._add_dirty: bool = False  # Manage changed Sunshine apps; refresh Add tab

        self._loader_thread: Optional[QThread] = None
        self._cover_loader: Optional[CoverLoader] = None
        self._cover_thread: Optional[QThread] = None
        self._adder_thread: Optional[QThread] = None
        self._manage_thread: Optional[QThread] = None
        self._update_thread: Optional[QThread] = None
        self._launchers: dict = {}
        self._server: Optional[str] = _init_active_server()

        self._setup_ui()
        self._detect_launchers()
        self._check_sunshine()

        if _is_first_run():
            self._open_wizard()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 4)
        outer.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_add_tab(), "Add Games")
        self._tabs.addTab(self._build_manage_tab(), "Manage")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(self._tabs)

        self._op_label = QLabel("")
        self.statusBar().addWidget(self._op_label)
        self._sun_lbl = QLabel("Checking…")
        self._sun_lbl.setContentsMargins(0, 0, 8, 0)
        self._sun_lbl.setStyleSheet("font-size: 11px;")
        self.statusBar().addPermanentWidget(self._sun_lbl)

    def _build_add_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 8)

        # Top bar: launcher, search, wizard, settings
        top = QHBoxLayout()
        top.addWidget(QLabel("Launcher:"))
        self.launcher_combo = QComboBox()
        self.launcher_combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.launcher_combo.currentTextChanged.connect(self._on_launcher_changed)
        top.addWidget(self.launcher_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filter_games)
        top.addWidget(self.search_edit)
        top.addStretch()

        wizard_btn = QPushButton("Setup Wizard")
        wizard_btn.setFlat(True)
        wizard_btn.setToolTip("Re-run the first-time setup wizard")
        wizard_btn.clicked.connect(self._open_wizard)
        top.addWidget(wizard_btn)

        settings_btn = QPushButton("⚙  Settings")
        settings_btn.setFlat(True)
        settings_btn.clicked.connect(self._open_settings)
        top.addWidget(settings_btn)

        layout.addLayout(top)

        self.count_label = QLabel("")
        self.count_label.setContentsMargins(2, 0, 0, 0)
        layout.addWidget(self.count_label)

        # Game list — shows cover thumbnails on the left
        self.game_list = QListWidget()
        self.game_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.game_list.setAlternatingRowColors(True)
        self.game_list.setIconSize(QSize(40, 56))
        layout.addWidget(self.game_list, stretch=1)

        # Options
        opts = QHBoxLayout()
        self.cover_checkbox = QCheckBox("Download missing covers from SteamGridDB")
        self.cover_checkbox.setToolTip(
            "Download cover art from SteamGridDB when no local cover is found. Requires a SteamGridDB API key (set in Settings)."
        )
        opts.addWidget(self.cover_checkbox)
        opts.addStretch()
        sel_all = QPushButton("Select all")
        sel_all.setFlat(True)
        sel_all.clicked.connect(self._select_all)
        desel_all = QPushButton("Deselect all")
        desel_all.setFlat(True)
        desel_all.clicked.connect(self._deselect_all)
        opts.addWidget(sel_all)
        opts.addWidget(desel_all)
        layout.addLayout(opts)

        # Add button
        self.add_btn = QPushButton("Add selected to Sunshine")
        self.add_btn.setEnabled(False)
        self.add_btn.setMinimumHeight(36)
        self.add_btn.clicked.connect(self._add_selected)
        layout.addWidget(self.add_btn)

        return w

    def _build_manage_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 8)

        info = QLabel("Games registered in Sunshine. Select entries to update covers or remove them.")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.manage_list = QListWidget()
        self.manage_list.setAlternatingRowColors(True)
        self.manage_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.manage_list.setIconSize(QSize(40, 56))
        self.manage_list.itemSelectionChanged.connect(self._on_manage_selection)
        layout.addWidget(self.manage_list, stretch=1)

        act = QHBoxLayout()
        refresh_btn = QPushButton("↺  Refresh")
        refresh_btn.setFlat(True)
        refresh_btn.clicked.connect(self._load_manage_apps)

        self.upd_sel_btn = QPushButton("Update Cover (selected)")
        self.upd_sel_btn.setEnabled(False)
        self.upd_sel_btn.setToolTip("Update cover from your local Lutris or Steam library")
        self.upd_sel_btn.clicked.connect(self._update_selected_covers)

        self.upd_all_btn = QPushButton("Update All Covers")
        self.upd_all_btn.setEnabled(False)
        self.upd_all_btn.setToolTip("Update covers for all apps from your local Lutris or Steam library")
        self.upd_all_btn.clicked.connect(self._update_all_covers)

        self.remove_btn = QPushButton("Remove selected")
        self.remove_btn.setEnabled(False)
        self.remove_btn.setToolTip("Remove selected entries from Sunshine")
        self.remove_btn.clicked.connect(self._remove_selected)

        act.addWidget(refresh_btn)
        act.addStretch()
        act.addWidget(self.upd_sel_btn)
        act.addWidget(self.upd_all_btn)
        act.addWidget(self.remove_btn)
        layout.addLayout(act)

        return w

    # -----------------------------------------------------------------------
    # Tab events
    # -----------------------------------------------------------------------

    def _on_tab_changed(self, idx: int):
        if idx == 0 and self._add_dirty:
            self._add_dirty = False
            if self.launcher_combo.currentText():
                self._load_games(self.launcher_combo.currentText())
        elif idx == 1 and not self._sunshine_apps:
            self._load_manage_apps()

    # -----------------------------------------------------------------------
    # Launcher detection & Sunshine status
    # -----------------------------------------------------------------------

    def _detect_launchers(self):
        self._op_label.setText("Detecting launchers…")
        self._launchers = _get_available_launchers()
        self.launcher_combo.blockSignals(True)
        self.launcher_combo.clear()
        for name in self._launchers:
            self.launcher_combo.addItem(name)
        self.launcher_combo.blockSignals(False)
        if self._launchers:
            self._load_games(self.launcher_combo.currentText())
        else:
            self._op_label.setText("No launchers detected.")

    def _check_sunshine(self):
        from sunshine.sunshine import (
            is_server_running, ensure_authenticated, get_server_display_name,
        )
        # Re-detect on each check so a server started after launch is picked up,
        # and the right backend (Sunshine/Apollo) stays locked in.
        self._server = _init_active_server()
        if self._server is None:
            self._sun_lbl.setText("⚠  No Sunshine or Apollo installed")
            self._sun_lbl.setStyleSheet("color: #cc7700; font-size: 11px;")
            return
        name = get_server_display_name()
        if not is_server_running(self._server):
            self._sun_lbl.setText(f"⚠  {name} not running")
            self._sun_lbl.setStyleSheet("color: #cc7700; font-size: 11px;")
            return
        if ensure_authenticated(allow_prompt=False):
            self._sun_lbl.setText("●  Connected")
            self._sun_lbl.setStyleSheet("color: #5aaa5a; font-size: 11px;")
        else:
            self._sun_lbl.setText("⚠  Not authenticated")
            self._sun_lbl.setStyleSheet("color: #cc7700; font-size: 11px;")

    # -----------------------------------------------------------------------
    # Add Games tab — game list
    # -----------------------------------------------------------------------

    def _on_launcher_changed(self, name: str):
        if name:
            self._load_games(name)

    def _load_games(self, launcher_name: str):
        if launcher_name not in self._launchers:
            return
        if self._loader_thread and self._loader_thread.isRunning():
            return

        self._stop_cover_loader()
        self.game_list.clear()
        self._games = []
        self.add_btn.setEnabled(False)
        self._op_label.setText(f"Loading {launcher_name} games…")
        self.count_label.setText("")

        self._loader = GameLoader(self._launchers[launcher_name])
        self._loader_thread = QThread()
        self._loader.moveToThread(self._loader_thread)
        self._loader_thread.started.connect(self._loader.run)
        self._loader.finished.connect(self._on_games_loaded)
        self._loader.error.connect(self._on_load_error)
        self._loader.finished.connect(self._loader_thread.quit)
        self._loader_thread.start()

    @pyqtSlot(list, list)
    def _on_games_loaded(self, games: list, existing: list):
        self._games = games
        self._existing_names = set(existing)
        self._populate_list(games)
        count = len(games)
        already = sum(1 for _, n, _, _ in games if n in self._existing_names)
        msg = f"{count} game{'s' if count != 1 else ''} found"
        if already:
            msg += f", {already} already in Sunshine"
        self.count_label.setText(msg)
        self._op_label.setText("")
        self.add_btn.setEnabled(bool(games))
        self._start_cover_loader(games)

    @pyqtSlot(str)
    def _on_load_error(self, error: str):
        self._op_label.setText(f"Error loading games: {error}")
        self.count_label.setText("")

    def _populate_list(self, games: list):
        self.game_list.clear()
        for game_id, game_name, display_source, runner in games:
            item = QListWidgetItem()
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            already = game_name in self._existing_names
            if already:
                item.setText(f"{game_name}  ✓")
                item.setForeground(
                    QApplication.palette().color(QPalette.ColorRole.PlaceholderText)
                )
                item.setToolTip(
                    f"{game_name} is already in Sunshine. Check it to update the cover and launch command."
                )
            else:
                item.setText(game_name)
            item.setData(Qt.ItemDataRole.UserRole, (game_id, game_name, display_source, runner))
            self.game_list.addItem(item)

    def _filter_games(self, query: str):
        q = query.lower()
        for i in range(self.game_list.count()):
            item = self.game_list.item(i)
            if item is None:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            name = data[1].lower() if data else ""
            item.setHidden(bool(q) and q not in name)

    def _select_all(self):
        for i in range(self.game_list.count()):
            item = self.game_list.item(i)
            if item is not None and not item.isHidden():
                item.setCheckState(Qt.CheckState.Checked)

    def _deselect_all(self):
        for i in range(self.game_list.count()):
            item = self.game_list.item(i)
            if item is not None:
                item.setCheckState(Qt.CheckState.Unchecked)

    def _get_checked_games(self) -> List[Tuple]:
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for i in range(self.game_list.count())
            if (item := self.game_list.item(i)) is not None
            and item.checkState() == Qt.CheckState.Checked
            and item.data(Qt.ItemDataRole.UserRole)
        ]

    # -----------------------------------------------------------------------
    # Cover thumbnails (async)
    # -----------------------------------------------------------------------

    def _stop_cover_loader(self):
        if self._cover_loader:
            self._cover_loader.stop()
        if self._cover_thread and self._cover_thread.isRunning():
            self._cover_thread.quit()
            self._cover_thread.wait(500)
        self._cover_loader = None
        self._cover_thread = None

    def _start_cover_loader(self, games: list):
        self._stop_cover_loader()
        self._cover_loader = CoverLoader(games)
        self._cover_thread = QThread()
        self._cover_loader.moveToThread(self._cover_thread)
        self._cover_thread.started.connect(self._cover_loader.run)
        self._cover_loader.cover_ready.connect(self._on_cover_ready)
        self._cover_loader.finished.connect(self._cover_thread.quit)
        self._cover_thread.start()

    @pyqtSlot(int, str)
    def _on_cover_ready(self, idx: int, path: str):
        if 0 <= idx < self.game_list.count():
            item = self.game_list.item(idx)
            if item:
                pm = QPixmap(path).scaled(
                    40, 56,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                if not pm.isNull():
                    item.setIcon(QIcon(pm))

    # -----------------------------------------------------------------------
    # Add games
    # -----------------------------------------------------------------------

    def _add_selected(self):
        from sunshine.sunshine import (
            ensure_authenticated, is_server_running, get_server_display_name,
        )

        name = get_server_display_name()
        if not (self._server and is_server_running(self._server)):
            QMessageBox.warning(self, f"{name} not running",
                                f"{name} is not running. Start it and try again.")
            return

        if not ensure_authenticated(allow_prompt=False):
            if not self._do_login():
                return

        games = self._get_checked_games()
        if not games:
            QMessageBox.information(self, "No selection", "Check at least one game first.")
            return

        api_key: Optional[str] = None
        if self.cover_checkbox.isChecked():
            from sunshine.sunshine import get_api_key_path
            try:
                kp = get_api_key_path()
                if os.path.exists(kp):
                    api_key = open(kp).read().strip() or None
            except Exception:
                pass
            if not api_key:
                QMessageBox.information(
                    self, "No SteamGridDB key",
                    "Set a SteamGridDB API key in ⚙ Settings to download covers.",
                )
                return

        self.add_btn.setEnabled(False)
        self._op_label.setText(f"Adding {len(games)} game(s)…")

        self._adder = AddGamesWorker(games, self.cover_checkbox.isChecked(), api_key)
        self._adder_thread = QThread()
        self._adder.moveToThread(self._adder_thread)
        self._adder_thread.started.connect(self._adder.run)
        self._adder.progress.connect(self._op_label.setText)
        self._adder.error.connect(lambda msg: self._op_label.setText(f"ℹ  {msg}"))
        self._adder.finished.connect(self._on_add_finished)
        self._adder.finished.connect(self._adder_thread.quit)
        self._adder_thread.start()

    @pyqtSlot(int, int)
    def _on_add_finished(self, added: int, total: int):
        self.add_btn.setEnabled(True)
        self._op_label.setText(f"Added {added} of {total} to Sunshine.")
        self._sunshine_apps = []  # invalidate manage cache
        self._load_games(self.launcher_combo.currentText())

    # -----------------------------------------------------------------------
    # Manage tab
    # -----------------------------------------------------------------------

    def _load_manage_apps(self):
        if self._manage_thread and self._manage_thread.isRunning():
            return

        self.manage_list.clear()
        self._sunshine_apps = []
        self.upd_all_btn.setEnabled(False)

        from sunshine.sunshine import (
            is_server_running, ensure_authenticated, get_server_display_name,
        )
        if not (self._server and is_server_running(self._server)):
            name = get_server_display_name()
            self.manage_list.addItem(QListWidgetItem(f"⚠  {name} is not running."))
            return
        if not ensure_authenticated(allow_prompt=False):
            if not self._do_login():
                self.manage_list.addItem(QListWidgetItem("⚠  Not authenticated."))
                return

        self.manage_list.addItem(QListWidgetItem("Loading…"))

        self._manage_loader = ManageAppsLoader()
        self._manage_thread = QThread()
        self._manage_loader.moveToThread(self._manage_thread)
        self._manage_thread.started.connect(self._manage_loader.run)
        self._manage_loader.finished.connect(self._on_manage_loaded)
        self._manage_loader.error.connect(self._on_manage_error)
        self._manage_loader.finished.connect(self._manage_thread.quit)
        self._manage_thread.start()

    @pyqtSlot(list)
    def _on_manage_loaded(self, apps: list):
        self._sunshine_apps = apps
        self.manage_list.clear()
        if not apps:
            self.manage_list.addItem(QListWidgetItem("No apps found in Sunshine."))
            return
        for app in sorted(apps, key=lambda a: a.get("name", "").lower()):
            item = QListWidgetItem(app.get("name", "(unnamed)"))
            img = app.get("image-path", "")
            if img and img != "default.png" and os.path.exists(img):
                pm = QPixmap(img).scaled(
                    40, 56,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                if not pm.isNull():
                    item.setIcon(QIcon(pm))
            item.setData(Qt.ItemDataRole.UserRole, app)
            self.manage_list.addItem(item)
        self.upd_all_btn.setEnabled(True)
        self._op_label.setText(f"{len(apps)} app(s) registered in Sunshine.")

    @pyqtSlot(str)
    def _on_manage_error(self, error: str):
        self.manage_list.clear()
        self.manage_list.addItem(QListWidgetItem(f"Error loading apps: {error}"))

    def _on_manage_selection(self):
        has = bool(self.manage_list.selectedItems())
        self.upd_sel_btn.setEnabled(has)
        self.remove_btn.setEnabled(has)

    def _get_selected_apps(self) -> List[Dict]:
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.manage_list.selectedItems()
            if item.data(Qt.ItemDataRole.UserRole)
        ]

    def _update_selected_covers(self):
        self._run_cover_update(self._get_selected_apps())

    def _update_all_covers(self):
        self._run_cover_update(self._sunshine_apps)

    def _run_cover_update(self, apps: List[Dict]):
        if not apps or (self._update_thread and self._update_thread.isRunning()):
            return

        self.upd_sel_btn.setEnabled(False)
        self.upd_all_btn.setEnabled(False)
        self._op_label.setText(f"Updating covers for {len(apps)} app(s)…")

        self._updater = UpdateCoversWorker(apps)
        self._update_thread = QThread()
        self._updater.moveToThread(self._update_thread)
        self._update_thread.started.connect(self._updater.run)
        self._updater.progress.connect(self._op_label.setText)
        self._updater.error.connect(lambda msg: self._op_label.setText(f"ℹ  {msg}"))
        self._updater.finished.connect(self._on_cover_update_done)
        self._updater.finished.connect(self._update_thread.quit)
        self._update_thread.start()

    @pyqtSlot(int, int)
    def _on_cover_update_done(self, updated: int, total: int):
        self._op_label.setText(f"Updated {updated} of {total} covers.")
        self._sunshine_apps = []
        self._load_manage_apps()

    def _remove_selected(self):
        apps = self._get_selected_apps()
        if not apps:
            return
        names = "\n".join(f"  • {a.get('name', '?')}" for a in apps)
        reply = QMessageBox.question(
            self, "Remove from Sunshine",
            f"Remove {len(apps)} app(s) from Sunshine?\n\n{names}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from sunshine.sunshine import delete_app
        removed = 0
        # Delete highest index first — Sunshine reindexes apps after each delete,
        # so ascending order would shift the remaining indices and remove the
        # wrong entries.
        for app in sorted(apps, key=lambda a: a.get("index", -1), reverse=True):
            ok, err = delete_app(app.get("index", -1))
            if ok:
                removed += 1
            else:
                self._op_label.setText(f"⚠  Failed to remove '{app.get('name')}': {err}")
        self._op_label.setText(f"Removed {removed}/{len(apps)} app(s).")
        self._sunshine_apps = []
        self._add_dirty = True
        self._load_manage_apps()

    # -----------------------------------------------------------------------
    # Auth & settings
    # -----------------------------------------------------------------------

    def _do_login(self) -> bool:
        from sunshine.sunshine import authenticate_with_credentials
        dialog = LoginDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        username, password = dialog.credentials()
        if not username or not password:
            return False
        if not authenticate_with_credentials(username, password):
            QMessageBox.warning(self, "Login failed",
                                "Could not authenticate. Check host, port, and credentials.")
            return False
        return True

    def _open_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._check_sunshine()

    def _open_wizard(self):
        dialog = SetupWizardDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._check_sunshine()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_gui():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("SunSync")
    app.setOrganizationName("SunSync")
    app.setWindowIcon(QIcon.fromTheme("weather-clear"))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_gui()
