"""System-wide equalizer for the G560's audio output.

The speakers have no EQ of their own, so we put a PulseAudio LADSPA filter sink (the
`Eq10X2` stereo 10-band equalizer from the `caps` plugin suite) in front of the G560's
sink and make it the default. Streams are moved onto it, so running apps keep playing.
Changing a setting builds the new filter first and only then removes the old one, so
audio does not drop out.

Why `caps` and not a fancier plugin: the filter runs inside PulseAudio's real-time audio
thread, and if that thread runs too long the kernel kills the *whole audio server* (every
app loses its sound). The ZamGEQ31 plugin did that unpredictably; `caps` survived every
stress test. The service below also watches for the audio server restarting, rebuilds the
filter, moves apps back onto it, and gives up (switching the EQ off) if it keeps happening.
"""

import math
import os
import subprocess
import threading
import time

PLUGIN_FILE = "caps.so"
LABEL = "Eq10X2"
SINK_NAMES = ("g560_eq_a", "g560_eq_b")  # alternate, so the name stays stable
G560_MATCH = "Logitech_G560"
INSTALL_HINT = "sudo apt install caps"

# The 10 sliders, and the plugin's 10 bands: the same octave centres, so no interpolation.
UI_BANDS = (31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
MAX_DB = 12.0
PREAMP_DB = 12.0  # preamp range, +/-
PLUGIN_RANGE = (-48.0, 24.0)  # what each band control accepts

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


def curve(ui_gains, freqs):
    """dB at each of `freqs`, interpolating the sliders along log frequency (for drawing)."""
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


def headroom(ui_gains):
    """dB the whole signal is lowered by, so the largest boost cannot clip."""
    return -max(0.0, max(clamp_gains(ui_gains)))


def clamp_preamp(preamp):
    return max(-PREAMP_DB, min(PREAMP_DB, float(preamp)))


def master_gain(ui_gains, preamp=0.0):
    """Overall level change in dB: the automatic headroom plus the user's preamp."""
    return max(-24.0, min(24.0, headroom(ui_gains) + clamp_preamp(preamp)))


def controls(ui_gains, preamp=0.0):
    """The 10 band values sent to the plugin.

    The plugin has no master gain, so the overall level change (automatic headroom, then
    the preamp, which can go either way) is added to every band.
    """
    master = master_gain(ui_gains, preamp)
    lo, hi = PLUGIN_RANGE
    return [round(max(lo, min(hi, g + master)), 2) + 0.0 for g in clamp_gains(ui_gains)]


def control_arg(ui_gains, preamp=0.0):
    return ",".join("%.2f" % v for v in controls(ui_gains, preamp))


def matching_preset(gains):
    gains = tuple(round(float(g), 1) for g in gains)
    for name, values in PRESETS.items():
        if gains == tuple(float(v) for v in values):
            return name
    return None


MAX_PROFILES = 24
MAX_PROFILE_NAME = 24


def profile_matches(profile, gains, preamp):
    """True if a saved custom profile is exactly the current curve and preamp."""
    return (
        [round(float(g), 2) for g in profile["gains"]] == [round(float(g), 2) for g in gains]
        and round(float(profile["preamp"]), 2) == round(float(preamp), 2)
    )


def valid_profile_name(name, existing=()):
    """(ok, message) for a name the user typed. Existing custom names may be overwritten."""
    name = name.strip()
    if not name:
        return False, "Give the profile a name."
    if len(name) > MAX_PROFILE_NAME:
        return False, "Use %d characters or fewer." % MAX_PROFILE_NAME
    if name.lower() in {p.lower() for p in PRESETS}:
        return False, "That is a built-in preset name."
    if name not in existing and len(list(existing)) >= MAX_PROFILES:
        return False, "You can keep up to %d profiles." % MAX_PROFILES
    return True, ""


def find_plugin():
    """Absolute path of the caps LADSPA library, or None."""
    dirs = [d for d in os.environ.get("LADSPA_PATH", "").split(":") if d]
    dirs += ["/usr/lib/ladspa", "/usr/local/lib/ladspa", "/usr/lib/x86_64-linux-gnu/ladspa"]
    for d in dirs:
        path = os.path.join(d, PLUGIN_FILE)
        if os.path.exists(path):
            return path
    return None


def plugin_available():
    return find_plugin() is not None


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


def is_filter_stream(row):
    """True for the filter's own output stream (it must stay on the speakers)."""
    return len(row) > 3 and "module-ladspa-sink" in row[3]


class PulseEq:
    """The EQ filter sink. `run` is injectable so the command flow is testable."""

    def __init__(self, master=None, manage_default=True, run=_pactl):
        self.master = master
        self.manage_default = manage_default
        self.run = run
        self.routed = set()  # apps' streams last seen on the EQ: to bring back after a loss

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
        """Stream ids currently on one of `from_sinks` (sink names), excluding the filter's own."""
        index = self._sink_index()
        wanted = {index[s] for s in from_sinks if s in index}
        return [row[0] for row in self._rows("sink-inputs") if len(row) > 3 and row[1] in wanted and not is_filter_stream(row)]

    def healthy(self):
        """True if the filter is loaded, the speakers are there, and the two are connected."""
        try:
            if not self.active():
                return False
            sinks = self._sink_index()
            master = self.find_master()
            if master not in sinks or not any(n in sinks for _, n in self.active()):
                return False
            return any(is_filter_stream(r) and r[1] == sinks[master] for r in self._rows("sink-inputs"))
        except EqError:
            return False

    def note_routed(self):
        """Remember which apps are using the EQ, so they can be brought back if it is lost."""
        names = [n for _, n in self.active()]
        if names:
            self.routed = set(self._movable_inputs(names))

    def server_id(self):
        """Identifies the running audio server (its pid): changes when it restarts."""
        bases = [os.environ.get("PULSE_RUNTIME_PATH"), os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), "pulse")]
        for base in bases:
            if base:
                try:
                    with open(os.path.join(base, "pid")) as f:
                        return f.read().strip()
                except OSError:
                    continue
        return None

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
    def apply(self, ui_gains, preamp=0.0):
        """Turn the EQ on, or switch it to new settings, without a dropout."""
        plugin = find_plugin()
        if plugin is None:
            raise EqError("The EQ plugin is missing. Install it with: %s" % INSTALL_HINT)
        master = self.find_master()
        old = self.active()
        busy = {name for _, name in old}
        new_name = next((n for n in SINK_NAMES if n not in busy), SINK_NAMES[0])
        carry = self._volume_of(old[0][1]) if old else None

        try:
            self.run(
                "load-module", "module-ladspa-sink",
                "sink_name=" + new_name, "sink_master=" + master,
                "plugin=" + plugin, "label=" + LABEL,
                "control=" + control_arg(ui_gains, preamp),
                "sink_properties=\"device.description='G560 EQ'\"",  # PulseAudio quoting
            )
        except EqError as e:
            raise EqError("Could not start the equalizer: %s" % e)

        if carry:
            self.run("set-sink-volume", new_name, "%d%%" % carry[0])
            self.run("set-sink-mute", new_name, "1" if carry[1] else "0")
        if self.manage_default:
            self.run("set-default-sink", new_name)
        streams = set(self._movable_inputs([master] + [n for _, n in old]))
        if not old and self.routed:
            # The filter was lost (speakers unplugged, audio server restarted) and the system moved
            # the apps elsewhere: bring back the ones that were using the EQ.
            alive = {r[0] for r in self._rows("sink-inputs") if not is_filter_stream(r)}
            streams |= self.routed & alive
        for stream in sorted(streams, key=lambda x: int(x) if x.isdigit() else 0):
            try:
                self.run("move-sink-input", stream, new_name)
            except EqError:
                pass  # the stream ended in the meantime
        for module_id, _ in old:
            self.run("unload-module", module_id)
        self.routed = streams

    def remove(self):
        """Turn the EQ off and hand everything back to the speakers."""
        old = self.active()
        self.routed = set()
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

    def reset_audio(self):
        """Emergency: drop any filter, make the speakers the default, put every app on them."""
        master = self.find_master()
        for module_id, _ in self.active():
            try:
                self.run("unload-module", module_id)
            except EqError:
                pass
        self.routed = set()
        self.run("set-default-sink", master)
        moved = 0
        for row in self._rows("sink-inputs"):
            if not is_filter_stream(row):
                try:
                    self.run("move-sink-input", row[0], master)
                    moved += 1
                except EqError:
                    pass
        return master, moved


class NullEqService:
    """Does nothing; used in dry-run mode so a test build never touches the real audio setup."""

    def start(self, enabled, gains, preamp=0.0):
        pass

    def stop(self):
        pass

    def set(self, enabled, gains, preamp=0.0):
        pass

    def reconnect(self):
        pass


class EqService:
    """Background worker: latest settings win, and a lost filter is rebuilt by itself.

    The filter lives in the audio server, so it keeps working after the app closes. While the
    app runs it notices when the filter has gone (speakers unplugged or powered off, audio
    server restarted), rebuilds it, and moves the apps back onto it. If the audio server keeps
    restarting with the EQ on, it switches the EQ off rather than loop and cut your sound.
    """

    CRASH_WINDOW = 120.0  # seconds
    CRASH_LIMIT = 2  # audio-server restarts within the window before giving up

    def __init__(self, on_status=None, eq=None, on_crash_loop=None, poll=2.0, now=time.monotonic):
        self.eq = eq or PulseEq()
        self.on_status = on_status or (lambda ok, message: None)
        self.on_crash_loop = on_crash_loop or (lambda message: None)
        self.poll = poll
        self.now = now
        self._want = (False, tuple([0.0] * len(UI_BANDS)), 0.0)
        self._applied = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._quit = threading.Event()
        self._thread = None
        self._status = None
        self._force = False
        self._tripped = False
        self._restarts = []
        self._sid = None
        self._down = False

    def start(self, enabled, gains, preamp=0.0):
        self.set(enabled, gains, preamp)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._quit.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)

    def set(self, enabled, gains, preamp=0.0):
        with self._lock:
            if enabled and not self._want[0]:  # switched on by the user: a fresh start
                self._tripped = False
                self._restarts = []
            self._want = (bool(enabled), clamp_gains(gains), clamp_preamp(preamp))
        self._wake.set()

    def reconnect(self):
        """Manual: rebuild the filter now, even if it looks fine."""
        with self._lock:
            self._tripped = False
            self._restarts = []
            self._force = True
        self._wake.set()

    def _report(self, ok, message=""):
        if (ok, message) != self._status:
            self._status = (ok, message)
            self.on_status(ok, message)

    @staticmethod
    def friendly(message):
        low = message.lower()
        if "not found" in low and "g560" in low:
            return ("The G560's audio output is gone (USB cable or power?). "
                    "The equalizer comes back by itself when it returns.")
        if "connection" in low and ("fail" in low or "refused" in low or "terminated" in low):
            return "The audio server restarted. Reconnecting the equalizer\u2026"
        return message

    def _note_restart(self):
        """The audio server restarted while the EQ was on. Returns True if we should stop trying."""
        t = self.now()
        self._restarts = [x for x in self._restarts if t - x < self.CRASH_WINDOW] + [t]
        if len(self._restarts) >= self.CRASH_LIMIT:
            self._tripped = True
            self.on_crash_loop(
                "The audio server restarted %d times in a row with the equalizer on, so it was switched off "
                "to keep your sound working. Try a gentler curve; turn it on again whenever you like." % len(self._restarts))
            return True
        return False

    def _ensure(self, gains, preamp, force):
        if not plugin_available():
            raise EqError("The EQ plugin is missing. Install it with: %s" % INSTALL_HINT)
        sid = self.eq.server_id()
        restarted = False
        if sid is not None and self._sid is not None and sid != self._sid:
            restarted = True
        if self._down:  # the audio server was unreachable and is back
            restarted = True
        self._sid, self._down = sid, False
        if restarted and self._note_restart():
            return
        if force or self._applied != (gains, preamp) or not self.eq.healthy():
            self.eq.apply(gains, preamp)
            self._applied = (gains, preamp)
        self.eq.note_routed()

    def _run(self):
        while not self._quit.is_set():
            with self._lock:
                enabled, gains, preamp = self._want
                force, self._force = self._force, False
            try:
                if enabled:
                    if not self._tripped:
                        self._ensure(gains, preamp, force)
                elif self._applied is not None or self.eq.is_active():
                    self.eq.remove()
                    self._applied = None
                self._report(True)
            except EqError as e:
                self._applied = None
                if "connection" in str(e).lower():
                    self._down = True
                self._report(False, self.friendly(str(e)))
            self._wake.wait(self.poll if enabled else 60.0)
            self._wake.clear()
