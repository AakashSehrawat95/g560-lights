"""Settings model and persistence (~/.config/g560-lights/config.json)."""

import dataclasses
import json
import os
import tempfile
from dataclasses import dataclass, field

from .effects import BY_ID, default_palette


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
    sync: bool = True  # editing all lights at once (the "All" button)
    audio_blend: bool = False  # Music: shift to the second color when loud
    brightness: int = 100
    speed: int = 50
    power: bool = True
    recent: list = field(default_factory=list)
    eq_enabled: bool = False
    eq_gains: list = field(default_factory=lambda: [0.0] * 10)
    eq_preamp: float = 0.0  # dB, on top of the automatic headroom
    eq_profiles: dict = field(default_factory=dict)  # name -> {"gains": [10], "preamp": dB}
    # Each effect remembers its own colors: effect id -> {"zone_colors", "second", "sync"}.
    # The live values for the current effect are zone_colors / colors / sync above.
    palettes: dict = field(default_factory=dict)

    def copy(self):
        return dataclasses.replace(
            self, colors=list(self.colors), zone_colors=list(self.zone_colors),
            recent=list(self.recent), eq_gains=list(self.eq_gains),
            palettes={k: dict(v, zone_colors=list(v["zone_colors"])) for k, v in self.palettes.items()},
            eq_profiles={k: {"gains": list(v["gains"]), "preamp": v["preamp"]} for k, v in self.eq_profiles.items()},
        )

    def _snapshot(self):
        return {"zone_colors": list(self.zone_colors), "second": self.colors[1], "sync": self.sync}

    def switch_effect(self, new_id):
        """Change effect, keeping each effect's own colors."""
        if BY_ID[self.effect].colors > 0:
            self.palettes[self.effect] = self._snapshot()
        self.effect = new_id
        if BY_ID[new_id].colors > 0:
            pal = self.palettes.get(new_id) or default_palette(new_id)
            self.zone_colors = list(pal["zone_colors"])
            self.colors = [self.zone_colors[0], pal["second"]]
            self.sync = pal["sync"]

    def to_dict(self):
        return {
            "effect": self.effect,
            "colors": [to_hex(c) for c in self.colors],
            "zone_colors": [to_hex(c) for c in self.zone_colors],
            "sync": self.sync,
            "audio_blend": self.audio_blend,
            "brightness": self.brightness,
            "speed": self.speed,
            "power": self.power,
            "recent": [to_hex(c) for c in self.recent],
            "eq": {"enabled": self.eq_enabled, "gains": list(self.eq_gains), "preamp": self.eq_preamp,
                   "profiles": {k: {"gains": list(v["gains"]), "preamp": v["preamp"]} for k, v in self.eq_profiles.items()}},
            "palettes": self._palettes_for_save(),
        }

    def _palettes_for_save(self):
        palettes = dict(self.palettes)
        if BY_ID[self.effect].colors > 0:
            palettes[self.effect] = self._snapshot()  # the live colors of the current effect
        return {
            eid: {"zone_colors": [to_hex(c) for c in p["zone_colors"]], "second": to_hex(p["second"]), "sync": p["sync"]}
            for eid, p in palettes.items()
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
        # Older files only had one shared first color: use it for every light.
        s.zone_colors = colors("zone_colors", 4) or [s.colors[0]] * 4
        s.audio_blend = bool(data.get("audio_blend", False))
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
            try:
                s.eq_preamp = max(-12.0, min(12.0, float(eq_data.get("preamp", 0.0))))
            except (TypeError, ValueError):
                pass
            profiles = eq_data.get("profiles")
            if isinstance(profiles, dict):
                for name, p in list(profiles.items())[:24]:
                    try:
                        gains = [max(-12.0, min(12.0, float(g))) for g in p["gains"]]
                        if isinstance(name, str) and name.strip() and len(name) <= 24 and len(gains) == 10:
                            s.eq_profiles[name] = {"gains": gains, "preamp": max(-12.0, min(12.0, float(p.get("preamp", 0.0))))}
                    except (KeyError, TypeError, ValueError, AttributeError):
                        continue
        saved = data.get("palettes")
        if isinstance(saved, dict):
            for eid, p in saved.items():
                try:
                    if BY_ID[eid].colors > 0:
                        zones = [from_hex(h) for h in p["zone_colors"]]
                        if len(zones) == 4:
                            s.palettes[eid] = {"zone_colors": zones, "second": from_hex(p["second"]), "sync": bool(p["sync"])}
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
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
