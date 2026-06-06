# Contributing to SunSync

## Development setup

**Using uv (recommended):**
```bash
uv sync
uv run python sunsync.py --gui
```

**System Python (Arch / CachyOS):**
```bash
sudo pacman -S python python-pyqt6 python-requests python-pillow
python sunsync.py --gui
```

## Project structure

| Path | Purpose |
|------|---------|
| `sunsync.py` | CLI entrypoint |
| `gui.py` | PyQt6 GUI (wizard, Add Games, Manage) |
| `config/constants.py` | API defaults, terminal colour codes |
| `display/manager.py` | prep-cmd / undo state persistence |
| `launchers/` | Per-launcher integrations |
| `sunshine/sunshine.py` | Sunshine REST API helpers |
| `utils/images.py` | Local cover art lookup |
| `utils/steamgriddb.py` | SteamGridDB downloads |

## Platform target

KDE Plasma Wayland. Sunshine must use `capture=kwin`. Virtual display is `krfb-virtualmonitor`.

## Code style

- Python 3.11+, PEP 8, 4-space indents
- Type hints on all public functions
- Minimal comments — only when the *why* isn't obvious from the code
- No dead code, no backward-compat shims; delete unused code outright

## GUI

- Use the `QObject` + `QThread` pattern for background workers; never subclass `QThread` directly
- Workers emit signals with data (file paths, strings); never emit `QPixmap` objects — `QPixmap` must be created on the GUI thread

## Key behaviours to preserve

- Re-adding an existing Sunshine app **updates** it (uses existing index), never creates a duplicate — enforced in `_find_existing_app()` → `add_game_to_sunshine_api()`
- Cover priority: local Lutris / Steam cover → SteamGridDB fallback (unless `prefer_steamgriddb` is set)
- `get_external_prep_commands()` returns the prep-cmd list injected into every Sunshine app payload
- Config paths always use `os.path.expanduser`; never hardcode `/home/...`

## Testing

Run the unit tests:

```bash
python -m unittest discover -s tests
```

Then validate manually:

1. `sunsync --gui` — verify wizard, game list, cover thumbnails, add / update / remove
2. `sunsync` — interactive CLI
3. `sunsync display external-prep status` — show configured scripts

## Submitting a PR

- Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`
- Imperative subject line, scoped where helpful (`fix(display): ...`)
- One concern per PR
