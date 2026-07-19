import json
import os
import threading
from pathlib import Path

from .models import State

lock = threading.Lock()


def state_dir() -> Path:
    return Path(os.environ.get("PSG_STATE_DIR", "/etc/proxmox-samba-gui"))


def state_path() -> Path:
    return state_dir() / "state.json"


def load_state() -> State:
    path = state_path()
    if not path.exists():
        return State()
    return State.model_validate(json.loads(path.read_text()))


def save_state(state: State) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(state.model_dump_json(indent=2))
    os.replace(tmp, path)
