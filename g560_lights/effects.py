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
    per_zone: bool = False  # the first color can be set per light
    color_labels: tuple = ("Color", "Color 2")


EFFECTS = [
    EffectInfo("static", "Static", True, 1, "", "One steady color, or a different one for each light.", per_zone=True),
    EffectInfo("breathe", "Breathe", True, 1, "Speed", "Fades the colors in and out. Runs on the speakers themselves.", per_zone=True),
    EffectInfo("cycle", "Spectrum", True, 0, "Speed", "Cycles through every color. Runs on the speakers themselves."),
    EffectInfo("wave", "Wave", False, 0, "Speed", "A rainbow that travels across the speakers."),
    EffectInfo("fade", "Gradient", False, 2, "Speed", "Blends smoothly back and forth between two colors.", per_zone=True, color_labels=("From", "To")),
    EffectInfo("strobe", "Flash", False, 1, "Speed", "Front and rear lights flash in turn. Flashing lights: take care if you are sensitive to them.", per_zone=True),
    EffectInfo("candle", "Candle", False, 1, "Flicker", "An uneven flame flicker in your color.", per_zone=True),
    EffectInfo("audio", "Music", False, 1, "Sensitivity", "The lights pulse with the sound you are playing, in the color you pick.", per_zone=True,
               color_labels=("Color", "Loud color")),
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


class Clock:
    """Animation phase that keeps moving smoothly when the speed changes.

    Computing phase as time / period makes every color jump the moment the
    period changes (dragging the Speed slider). Instead the phase is the
    running total of dt / period, so a speed change only changes the pace.
    """

    def __init__(self):
        self.last = None
        self.t = 0.0  # seconds
        self.cycles = 0.0  # full cycles of the speed-set period
        self.flashes = 0.0  # flash periods
        self.flicker = 0.0  # candle time, scaled by the flicker setting

    def advance(self, now, speed):
        if self.last is not None:
            dt = max(0.0, min(now - self.last, 0.25))  # a long stall must not fast-forward
            self.t += dt
            self.cycles += dt / period_seconds(speed)
            self.flashes += dt / flash_period(speed)
            self.flicker += dt * (0.4 + speed / 50.0)
        self.last = now
        return self

    @classmethod
    def at(cls, t, speed):
        """The clock as if `speed` had been constant since t = 0 (tests, previews)."""
        clock = cls()
        clock.t = t
        clock.cycles = t / period_seconds(speed)
        clock.flashes = t / flash_period(speed)
        clock.flicker = t * (0.4 + speed / 50.0)
        return clock


def render(state, clock, audio=(0.0, 0.0, 0.0, 0.0)):
    """Return four (r, g, b) tuples (0-255 ints) for the moment `clock` shows.

    `clock` is a Clock, or a number of seconds. The first color of every
    effect is per light (state.zone_colors); the second (gradient end, loud
    color) is shared (state.colors[1]).

    audio is (left, right, left_slow, right_slow) levels in 0..1.
    """
    if not isinstance(clock, Clock):
        clock = Clock.at(float(clock), state.speed)
    eid = state.effect
    mine = state.zone_colors  # first color, per light
    c2 = state.colors[1]
    speed = state.speed

    if eid == "static":
        out = list(mine)
    elif eid == "breathe":
        level = 0.5 - 0.5 * math.cos(2 * math.pi * clock.cycles)
        out = [scale(c, level) for c in mine]
    elif eid == "cycle":
        out = [hsv(clock.cycles)] * 4
    elif eid == "wave":
        out = [hsv(clock.cycles - ZONE_POSITION[z] * 0.12) for z in range(4)]
    elif eid == "fade":
        out = [
            lerp(mine[z], c2, 0.5 - 0.5 * math.cos(2 * math.pi * (clock.cycles - ZONE_POSITION[z] * 0.05)))
            for z in range(4)
        ]
    elif eid == "strobe":
        front_on = (clock.flashes % 1.0) < 0.5
        out = [mine[z] if (front_on if z in (2, 3) else not front_on) else (0, 0, 0) for z in range(4)]
    elif eid == "candle":
        out = []
        for z in range(4):
            f = clock.flicker
            v = (
                0.62
                + 0.20 * math.sin((3.1 + z * 0.7) * f + z)
                + 0.12 * math.sin((7.3 + z * 1.1) * f + 2 * z)
                + 0.06 * math.sin((13.7 + z * 0.4) * f + 3 * z)
            )
            out.append(scale(mine[z], clamp01(v)))
    elif eid == "audio":
        left, right, left_slow, right_slow = audio
        gain = 0.5 + speed / 50.0
        levels = {2: left, 3: right, 0: left_slow, 1: right_slow}
        out = []
        for z in range(4):
            level = clamp01(levels[z] * gain)
            color = lerp(mine[z], c2, level) if state.audio_blend else mine[z]
            out.append(scale(color, 0.08 + 0.92 * level))
    else:
        out = [(0, 0, 0)] * 4

    k = state.brightness / 100.0
    return [tuple(int(round(max(0.0, min(255.0, ch * k)))) for ch in color) for color in out]
