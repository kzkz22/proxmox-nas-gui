from ..models import State
from . import accounts, service


def state_view(st: State) -> dict:
    """The Samba half of the GET /api/state payload.

    Accounts are reported alongside the stored description because state.json
    only records that the GUI created a user; whether the POSIX account still
    exists, and who is in a group, has to be read from the host.
    """
    return {
        "settings": st.settings,
        "shares": st.shares,
        "users": {
            name: {"description": info.description, "system": accounts.user_exists(name)}
            for name, info in st.users.items()
        },
        "groups": {
            name: {
                "description": info.description,
                "members": accounts.group_members(name),
            }
            for name, info in st.groups.items()
        },
        "service": service.status(),
    }
