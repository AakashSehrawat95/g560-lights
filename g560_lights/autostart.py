"""Start at login: a desktop entry in the autostart folder that opens the app hidden."""

import os

ENTRY = """[Desktop Entry]
Type=Application
Name=G560 Lights
Exec=g560-lights --background
Icon=g560-lights
X-GNOME-Autostart-enabled=true
"""


def path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "autostart", "g560-lights.desktop")


def is_enabled():
    return os.path.exists(path())


def set_enabled(enabled):
    """Create or remove the entry. Raises OSError if the folder cannot be written."""
    target = path()
    if enabled:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            f.write(ENTRY)
    elif os.path.exists(target):
        os.unlink(target)
