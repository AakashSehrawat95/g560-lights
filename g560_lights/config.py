"""Settings model and persistence (~/.config/g560-lights/config.json)."""

import json
import os
import tempfile
from dataclasses import dataclass, field

from .effects import BY_ID


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "g560-lights", "config.json")


def to_hex(color):
    return "%02x%02x%02x" % tuple(int(c) for c in color)


def from_hex(text):
    text = text.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError("expected RRGGBB")
    return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))


def _clamp(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


@dataclass
class State:
    effect: str = "static"
    colors: list = field(default_factory=lambda: [(0, 255, 255), (255, 0, 200)])
    zone_colors: list = field(default_factory=lambda: [(0, 255, 255)] * 4)
    sync: bool = True
    brightness: int = 100
    speed: int = 50
    power: bool = True
    recent: list = field(default_factory=list)
    eq_enabled: bool = False
    eq_gains: list = field(default_factory=lambda: [0.0] * 10)

    def copy(self):
        return State(
            self.effect, list(self.colors), list(self.zone_colors), self.sync,
            self.brightness, self.speed, self.power, list(self.recent),
            self.eq_enabled, list(self.eq_gains),
        )

    def to_dict(self):
        return {
            "effect": self.effect,
            "colors": [to_hex(c) for c in self.colors],
            "zone_colors": [to_hex(c) for c in self.zone_colors],
            "sync": self.sync,
            "brightness": self.brightness,
            "speed": self.speed,
            "power": self.power,
            "recent": [to_hex(c) for c in self.recent],
            "eq": {"enabled": self.eq_enabled, "gains": list(self.eq_gains)},
        }

    @classmethod
    def from_dict(cls, data):
        """Build a State from untrusted JSON, falling back to defaults."""
        s = cls()
        if not isinstance(data, dict):
            return s

        def colors(key, count):
            try:
                found = [from_hex(h) for h in data[key]]
            except (KeyError, TypeError, ValueError):
                return None
            return found[:count] if len(found) >= count else None

        if data.get("effect") in BY_ID:
            s.effect = data["effect"]
        s.colors = colors("colors", 2) or s.colors
        s.zone_colors = colors("zone_colors", 4) or s.zone_colors
        s.sync = bool(data.get("sync", s.sync))
        s.power = bool(data.get("power", s.power))
        s.brightness = _clamp(data.get("brightness"), 1, 100, s.brightness)
        s.speed = _clamp(data.get("speed"), 1, 100, s.speed)
        try:
            s.recent = [from_hex(h) for h in data.get("recent", [])][:8]
        except (TypeError, ValueError):
            s.recent = []
        eq_data = data.get("eq")
        if isinstance(eq_data, dict):
            s.eq_enabled = bool(eq_data.get("enabled", False))
            try:
                gains = [max(-12.0, min(12.0, float(g))) for g in eq_data["gains"]]
                if len(gains) == 10:
                    s.eq_gains = gains
            except (KeyError, TypeError, ValueError):
                pass
        return s


def load(path=None):
    path = path or config_path()
    try:
        with open(path) as f:
            return State.from_dict(json.load(f))
    except (OSError, ValueError):
        return State()


def save(state, path=None):
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state.to_dict(), f, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
