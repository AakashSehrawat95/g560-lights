"""System-wide equalizer for the G560's audio output.

The speakers have no EQ of their own, so we put a PulseAudio LADSPA filter
sink (the ZamGEQ31 graphic EQ from zam-plugins) in front of the G560's sink
and make it the default. Streams are moved onto it, so running apps keep
playing. Changing a setting builds the new filter first and only then removes
the old one, so audio does not drop out.
"""

import math
import os
import subprocess
import threading
import time

PLUGIN = "ZamGEQ31-ladspa"
LABEL = "ZamGEQ31"
SINK_NAMES = ("g560_eq_a", "g560_eq_b")  # alternate, so the name stays stable
G560_MATCH = "Logitech_G560"

# Sliders shown in the UI (Hz), and the plugin's own 29 bands (Hz).
UI_BANDS = (31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
PLUGIN_BANDS = (
    32, 40, 50, 63, 79, 100, 126, 158, 200, 251, 316, 398, 501, 631, 794,
    999, 1257, 1584, 1997, 2514, 3165, 3986, 5017, 6318, 7963, 10032,
    12662, 16081, 20801,
)
MAX_DB = 12.0

# dB at 31, 63, 125, 250, 500, 1k, 2k, 4k, 8k, 16k
PRESETS = {
    "Flat": (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    "Loudness": (7, 5, 2, 0, -1, -1, 0, 2, 4, 6),
    "Bass boost": (8, 7, 5, 3, 1, 0, 0, 0, 0, 0),
    "Less bass": (-6, -5, -3, -1, 0, 0, 0, 0, 0, 0),
    "Treble boost": (0, 0, 0, 0, 0, 1, 2, 4, 6, 7),
    "Vocal": (-4, -3, -2, 0, 2, 4, 5, 3, 1, 0),
    "Movie": (5, 4, 2, 0, 0, 2, 3, 2, 2, 1),
    "Gaming": (-2, -2, -1, -1, 0, 2, 4, 5, 3, 1),
    "Rock": (5, 4, 3, 1, -1, -1, 1, 3, 4, 5),
    "Pop": (-2, -1, 1, 3, 4, 3, 1, -1, -2, -2),
    "Jazz": (3, 2, 1, 2, -1, -1, 0, 1, 2, 3),
    "Classical": (4, 3, 2, 1, 0, 0, 0, -1, -2, -3),
    "Electronic": (6, 5, 2, 0, -2, 2, 1, 2, 5, 6),
}


class EqError(Exception):
    pass


# ---------------------------------------------------------------- the maths
def clamp_gains(gains):
    return tuple(max(-MAX_DB, min(MAX_DB, float(g))) for g in gains)


def curve(ui_gains, freqs=PLUGIN_BANDS):
    """dB at each of `freqs`, interpolating the UI sliders along log frequency."""
    xs = [math.log2(f) for f in UI_BANDS]
    out = []
    for f in freqs:
        x = math.log2(f)
        if x <= xs[0]:
            out.append(ui_gains[0])
        elif x >= xs[-1]:
            out.append(ui_gains[-1])
        else:
            i = max(j for j in range(len(xs) - 1) if xs[j] <= x)
            k = (x - xs[i]) / (xs[i + 1] - xs[i])
            k = 0.5 - 0.5 * math.cos(math.pi * k)  # smooth, no corners at the sliders
            out.append(ui_gains[i] + (ui_gains[i + 1] - ui_gains[i]) * k)
    return out


def controls(ui_gains):
    """LADSPA control values: master gain, then the 29 bands.

    The master gain is lowered by the largest boost so boosted presets cannot
    clip.
    """
    bands = curve(clamp_gains(ui_gains))
    headroom = -max(0.0, max(bands))
    return [headroom] + bands


def control_arg(ui_gains):
    return ",".join("%.2f" % v for v in controls(ui_gains))


def matching_preset(gains):
    gains = tuple(round(float(g), 1) for g in gains)
    for name, values in PRESETS.items():
        if gains == tuple(float(v) for v in values):
            return name
    return None


def plugin_available():
    dirs = [d for d in os.environ.get("LADSPA_PATH", "").split(":") if d]
    dirs += ["/usr/lib/ladspa", "/usr/local/lib/ladspa", "/usr/lib/x86_64-linux-gnu/ladspa"]
    return any(os.path.exists(os.path.join(d, PLUGIN + ".so")) for d in dirs)


def conflicting_apps():
    """Other audio-effects apps that also grab streams (they fight over them)."""
    found = []
    for name in ("pulseeffects", "easyeffects"):
        try:
            if subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0:
                found.append(name)
        except OSError:
            pass
    return found


# ------------------------------------------------------- talking to PulseAudio
def _pactl(*args):
    try:
        result = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        raise EqError("pactl not found (install pulseaudio-utils)")
    except subprocess.TimeoutExpired:
        raise EqError("The audio server did not answer")
    if result.returncode != 0:
        raise EqError((result.stderr or result.stdout).strip() or "pactl failed")
    return result.stdout


class PulseEq:
    """The EQ filter sink. `run` is injectable so the command flow is testable."""

    def __init__(self, master=None, manage_default=True, run=_pactl):
        self.master = master
        self.manage_default = manage_default
        self.run = run

    # -- queries
    def _rows(self, what):
        return [line.split("\t") for line in self.run("list", "short", what).splitlines() if line.strip()]

    def find_master(self):
        if self.master:
            return self.master
        for row in self._rows("sinks"):
            if len(row) > 1 and G560_MATCH in row[1] and not row[1].startswith("g560_eq"):
                return row[1]
        raise EqError("G560 audio output not found. Is the USB cable plugged in?")

    def active(self):
        """[(module_id, sink_name)] for the filter sinks we have loaded."""
        found = []
        for row in self._rows("modules"):
            if len(row) > 2 and row[1] == "module-ladspa-sink":
                for name in SINK_NAMES:
                    if "sink_name=" + name in row[2].split():
                        found.append((row[0], name))
        return found

    def is_active(self):
        return bool(self.active())

    def _sink_index(self):
        return {row[1]: row[0] for row in self._rows("sinks") if len(row) > 1}

    def _movable_inputs(self, from_sinks):
        """Stream ids currently on one of `from_sinks` (sink names), excluding
        the filter's own output stream."""
        index = self._sink_index()
        wanted = {index[s] for s in from_sinks if s in index}
        ids = []
        for row in self._rows("sink-inputs"):
            if len(row) > 3 and row[1] in wanted and "module-ladspa-sink" not in row[3]:
                ids.append(row[0])
        return ids

    def _volume_of(self, sink):
        """(percent, muted) of a sink, or None."""
        try:
            vol = self.run("get-sink-volume", sink)
            mute = self.run("get-sink-mute", sink)
        except EqError:
            return None
        for token in vol.replace("/", " ").split():
            if token.endswith("%") and token[:-1].isdigit():
                return int(token[:-1]), "yes" in mute
        return None

    # -- actions
    def apply(self, ui_gains):
        """Turn the EQ on, or switch it to new settings, without a dropout."""
        master = self.find_master()
        old = self.active()
        busy = {name for _, name in old}
        new_name = next((n for n in SINK_NAMES if n not in busy), SINK_NAMES[0])
        carry = self._volume_of(old[0][1]) if old else None

        try:
            self.run(
                "load-module", "module-ladspa-sink",
                "sink_name=" + new_name, "sink_master=" + master,
                "plugin=" + PLUGIN, "label=" + LABEL,
                "control=" + control_arg(ui_gains),
                "sink_properties=\"device.description='G560 EQ'\"",  # PulseAudio quoting
            )
        except EqError as e:
            raise EqError("Could not start the equalizer: %s" % e)

        if carry:
            self.run("set-sink-volume", new_name, "%d%%" % carry[0])
            self.run("set-sink-mute", new_name, "1" if carry[1] else "0")
        if self.manage_default:
            self.run("set-default-sink", new_name)
        for stream in self._movable_inputs([master] + [n for _, n in old]):
            try:
                self.run("move-sink-input", stream, new_name)
            except EqError:
                pass  # the stream ended in the meantime
        for module_id, _ in old:
            self.run("unload-module", module_id)

    def remove(self):
        """Turn the EQ off and hand everything back to the speakers."""
        old = self.active()
        if not old:
            return
        master = self.find_master()
        names = [n for _, n in old]
        if self.manage_default:
            self.run("set-default-sink", master)
        for stream in self._movable_inputs(names):
            try:
                self.run("move-sink-input", stream, master)
            except EqError:
                pass
        for module_id, _ in old:
            self.run("unload-module", module_id)


class EqService:
    """Background worker: latest settings win, and a lost filter is rebuilt.

    The filter lives in the audio server, so it keeps working after the app
    closes. While the app runs it re-creates the filter if the speakers are
    replugged.
    """

    def __init__(self, on_status=None, eq=None):
        self.eq = eq or PulseEq()
        self.on_status = on_status or (lambda ok, message: None)
        self._want = (False, tuple([0.0] * len(UI_BANDS)))
        self._applied = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._quit = threading.Event()
        self._thread = None
        self._status = None

    def start(self, enabled, gains):
        self.set(enabled, gains)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._quit.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)

    def set(self, enabled, gains):
        with self._lock:
            self._want = (bool(enabled), clamp_gains(gains))
        self._wake.set()

    def _report(self, ok, message=""):
        if (ok, message) != self._status:
            self._status = (ok, message)
            self.on_status(ok, message)

    def _run(self):
        while not self._quit.is_set():
            with self._lock:
                enabled, gains = self._want
            try:
                if enabled:
                    if not plugin_available():
                        raise EqError("The EQ plugin is missing. Install it with: sudo apt install zam-plugins")
                    # Rebuild when settings changed, or when the filter vanished.
                    if self._applied != gains or not self.eq.is_active():
                        self.eq.apply(gains)
                        self._applied = gains
                elif self._applied is not None or self.eq.is_active():
                    self.eq.remove()
                    self._applied = None
                self._report(True)
            except EqError as e:
                self._applied = None
                self._report(False, str(e))
            self._wake.wait(3.0 if enabled else 60.0)
            self._wake.clear()
