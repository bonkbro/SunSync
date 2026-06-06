import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG_ROOT = Path("~/.config/sunsync").expanduser()
DISPLAY_ROOT = CONFIG_ROOT / "display"
DISPLAY_STATE_PATH = DISPLAY_ROOT / "display.json"
LEGACY_DISPLAY_ROOT = CONFIG_ROOT / "virtualdisplay"
LEGACY_STATE_PATH = LEGACY_DISPLAY_ROOT / "virtualdisplay.json"

_PCI_IDS_PATHS = [
    Path("/usr/share/hwdata/pci.ids"),
    Path("/usr/share/pci.ids"),
]


def _safe_string(value: Any) -> str:
    return str(value or "").strip()


def detect_sunshine_config_root() -> Path:
    for candidate in [
        Path("~/.config/sunshine").expanduser(),
        Path("~/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine").expanduser(),
    ]:
        if candidate.exists():
            return candidate
    return Path("~/.config/sunshine").expanduser()


def _default_state() -> Dict[str, Any]:
    return {
        "external_prep_do": "",
        "external_prep_undo": "",
        "prefer_steamgriddb": False,
    }


def load_state() -> Dict[str, Any]:
    state_path = DISPLAY_STATE_PATH if DISPLAY_STATE_PATH.exists() else LEGACY_STATE_PATH
    if not state_path.exists():
        return _default_state()
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state()
    state = _default_state()
    state.update(data)
    return state


def save_state(state: Dict[str, Any]) -> None:
    DISPLAY_ROOT.mkdir(parents=True, exist_ok=True)
    DISPLAY_STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def get_external_prep_commands() -> List[Dict[str, str]]:
    state = load_state()
    do_script = _safe_string(state.get("external_prep_do"))
    undo_script = _safe_string(state.get("external_prep_undo"))
    if not do_script and not undo_script:
        return []
    return [{"do": do_script, "undo": undo_script}]


def has_external_prep_scripts() -> bool:
    state = load_state()
    return bool(
        _safe_string(state.get("external_prep_do")) or _safe_string(state.get("external_prep_undo"))
    )


def set_external_prep_scripts(do_script: str, undo_script: str) -> None:
    state = load_state()
    state["external_prep_do"] = _safe_string(do_script)
    state["external_prep_undo"] = _safe_string(undo_script)
    save_state(state)


def get_prefer_steamgriddb() -> bool:
    return bool(load_state().get("prefer_steamgriddb", False))


def set_prefer_steamgriddb(value: bool) -> None:
    state = load_state()
    state["prefer_steamgriddb"] = bool(value)
    save_state(state)


def clear_external_prep_scripts() -> None:
    state = load_state()
    state.pop("external_prep_do", None)
    state.pop("external_prep_undo", None)
    save_state(state)


def _pci_device_name(vendor_hex: str, device_hex: str) -> str:
    pci_ids_file = None
    for candidate in _PCI_IDS_PATHS:
        if candidate.exists():
            pci_ids_file = candidate
            break
    if pci_ids_file is None:
        return ""
    vendor_prefix = vendor_hex.replace("0x", "").lower()
    device_prefix = device_hex.replace("0x", "").lower()
    if not vendor_prefix or not device_prefix:
        return ""
    try:
        in_vendor = False
        with open(pci_ids_file, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not in_vendor:
                    if line.rstrip() and not line[0].isspace() and line.split()[0].lower() == vendor_prefix:
                        in_vendor = True
                    continue
                if line.strip() and not line[0].isspace() and not line.startswith("#"):
                    break
                if line[0] == "\t" and (len(line) < 2 or line[1] != "\t"):
                    parts = line.lstrip("\t").split(None, 1)
                    if parts and parts[0].lower() == device_prefix:
                        return parts[1].strip() if len(parts) > 1 else ""
        return ""
    except OSError:
        return ""


def detect_available_gpus() -> List[Dict[str, str]]:
    gpus = []
    for entry in sorted(Path("/sys/class/drm").glob("card*")):
        if not re.match(r"^card\d+$", entry.name):
            continue
        try:
            device_link = entry / "device"
            if not device_link.is_symlink():
                continue
            pci_addr = os.path.basename(os.path.realpath(str(device_link)))
            if not pci_addr.startswith("0000:"):
                continue
            vendor_file = entry / "device" / "vendor"
            device_file = entry / "device" / "device"
            if not vendor_file.exists():
                continue
            vendor_id = vendor_file.read_text().strip()
            device_id = device_file.read_text().strip() if device_file.exists() else ""
        except OSError:
            continue
        vendor = "Unknown"
        gpu_type = "Unknown"
        if vendor_id == "0x8086":
            vendor = "Intel"
            gpu_type = "Integrated" if pci_addr == "0000:00:02.0" else "Discrete"
        elif vendor_id == "0x1002":
            vendor = "AMD"
            hwmon_dir = entry / "device" / "hwmon"
            try:
                has_voltage = any(hwmon_dir.glob("hwmon*/in1_input"))
            except OSError:
                has_voltage = False
            gpu_type = "Integrated" if has_voltage else "Discrete"
        elif vendor_id == "0x10de":
            vendor = "NVIDIA"
            gpu_type = "Discrete"
        model = _pci_device_name(vendor_id, device_id)
        label = f"{vendor} {model}" if model else f"{vendor} ({gpu_type}) [{pci_addr}]"
        gpus.append({
            "pci_addr": pci_addr,
            "vendor": vendor,
            "model": model,
            "label": label,
            "render_path": f"/dev/dri/by-path/pci-{pci_addr}-render",
        })
    return gpus
