from typing import Dict

from ..models import Access, State
from .. import service, state as state_store


def normalize_access(access: Dict[str, Access]) -> Dict[str, Access]:
    """Only read/write grants are stored; "no" simply removes the entry."""
    return {k: v for k, v in access.items() if v != Access.NONE}


def commit(new_state: State) -> dict:
    """Validate + install the Samba config, then persist state.

    Validation failures raise before anything is written, so neither the
    live Samba config nor state.json can end up broken.
    """
    warning = service.apply(new_state)
    state_store.save_state(new_state)
    return {"ok": True, "warning": warning}
