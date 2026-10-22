"""Effect catalogue and the maths that turns settings + time into 4 colors.

Three effects run inside the speakers (static, breathe, cycle); the rest are
computed here and streamed as frames. render() is used for both: the live
preview always shows what render() says, and software effects send it too.
"""

import colorsys
import math
from dataclasses import dataclass

# Light positions along the speakers, rear-left to rear-right, for waves.
# Index = zone id (0 L-rear, 1 R-rear, 2 L-front, 3 R-front).
ZONE_POSITION = {0: 0.0, 2: 1.0, 3: 2.0, 1: 3.0}

MAX_FLASH_HZ = 6.0  # keep strobing well under the 15-25 Hz risky range


@dataclass(frozen=True)
class EffectInfo:
    id: str
    label: str
    hardware: bool
    colors: int  # how many colors the user picks (0, 1 or 2)
    speed_label: str  # "" = no speed slider
    blurb: str
    per_zone: bool = False
    color_labels: tuple = ("Color", "Color 2")


EFFECTS = [
    EffectInfo("static", "Static", True, 1, "", "One steady color, or a different one per light.", per_zone=True),
    EffectInfo("breathe", "Breathe", True, 1, "Speed", "Fades the color in and out. Runs on the speakers themselves."),
    EffectInfo("cycle", "Spectrum", True, 0, "Speed", "Cycles through every color. Runs on the speakers themselves."),
    EffectInfo("wave", "Wave", False, 0, "Speed", "A rainbow that travels across the speakers."),
    EffectInfo("fade", "Gradient", False, 2, "Speed", "Blends smoothly back and forth between two colors.", color_labels=("From", "To")),
    EffectInfo("strobe", "Flash", False, 1, "Speed", "Front and rear lights flash in turn. Flashing lights: take care if you are sensitive to them."),
    EffectInfo("candle", "Candle", False, 1, "Flicker", "A warm, uneven flame flicker."),
    EffectInfo("audio", "Music", False, 2, "Sensitivity", "Lights follow the sound you are playing.", color_labels=("Quiet", "Loud")),
]
BY_ID = {e.id: e for e in EFFECTS}
Color = tuple


def clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def lerp(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def hsv(h, s=1.0, v=1.0):
    return tuple(c * 255 for c in colorsys.hsv_to_rgb(h % 1.0, s, v))


def scale(color, k):
    return tuple(c * k for c in color)


def period_seconds(speed):
    """Speed 1..100 -> seconds per cycle (30 s ... 0.6 s, exponential)."""
    return 30.0 * (0.02 ** ((max(1, min(100, speed)) - 1) / 99.0))


def rate_ms(speed):
    """Speed 1..100 -> the speakers' rate value (milliseconds per cycle)."""
    return int(period_seconds(speed) * 1000)


def flash_period(speed):
    """Speed 1..100 -> seconds per flash, never faster than MAX_FLASH_HZ."""
    p = 1.2 * (0.07 ** ((max(1, min(100, speed)) - 1) / 99.0))
    return max(p, 1.0 / MAX_FLASH_HZ)


def render(state, t, audio=(0.0, 0.0, 0.0, 0.0)):
    """Return four (r, g, b) tuples (0-255 ints) for time t seconds.

    audio is (left, right, left_slow, right_slow) levels in 0..1.
    """
    eid = state.effect
    c1, c2 = state.colors[0], state.colors[1]
    speed = state.speed

    if eid == "static":
        out = list(state.zone_colors) if not state.sync else [c1] * 4
    elif eid == "breathe":
        level = 0.5 - 0.5 * math.cos(2 * math.pi * t / period_seconds(speed))
        out = [scale(c1, level)] * 4
    elif eid == "cycle":
        out = [hsv(t / period_seconds(speed))] * 4
    elif eid == "wave":
        p = period_seconds(speed)
        out = [hsv(t / p - ZONE_POSITION[z] * 0.12) for z in range(4)]
    elif eid == "fade":
        p = period_seconds(speed)
        out = [
            lerp(c1, c2, 0.5 - 0.5 * math.cos(2 * math.pi * (t / p - ZONE_POSITION[z] * 0.05)))
            for z in range(4)
        ]
    elif eid == "strobe":
        phase = (t / flash_period(speed)) % 1.0
        front_on = phase < 0.5
        out = [c1 if (front_on if z in (2, 3) else not front_on) else (0, 0, 0) for z in range(4)]
    elif eid == "candle":
        k = 0.4 + speed / 50.0
        out = []
        for z in range(4):
            v = (
                0.62
                + 0.20 * math.sin(k * (3.1 + z * 0.7) * t + z)
                + 0.12 * math.sin(k * (7.3 + z * 1.1) * t + 2 * z)
                + 0.06 * math.sin(k * (13.7 + z * 0.4) * t + 3 * z)
            )
            out.append(scale(c1, clamp01(v)))
    elif eid == "audio":
        left, right, left_slow, right_slow = audio
        gain = 0.5 + speed / 50.0
        levels = {2: left, 3: right, 0: left_slow, 1: right_slow}
        out = []
        for z in range(4):
            level = clamp01(levels[z] * gain)
            out.append(scale(lerp(c1, c2, level), 0.08 + 0.92 * level))
    else:
        out = [(0, 0, 0)] * 4

    k = state.brightness / 100.0
    return [tuple(int(round(max(0.0, min(255.0, ch * k)))) for ch in color) for color in out]
