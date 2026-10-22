"""Loudness of whatever is playing, read from the PulseAudio monitor source."""

import math
import subprocess
import threading
from array import array

RATE = 16000
CHUNK_FRAMES = 400  # 25 ms


def default_monitor():
    """Name of the monitor source of the default output, or None."""
    for cmd, parse in (
        (["pactl", "get-default-sink"], lambda out: out.strip()),
        (["pactl", "info"], lambda out: next(
            (l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("Default Sink:")), "")),
    ):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        sink = parse(out)
        if sink:
            return sink + ".monitor"
    return None


class AudioMonitor:
    """Reference-counted: start() / stop() can be called by several users."""

    def __init__(self):
        self._lock = threading.Lock()
        self._users = 0
        self._proc = None
        self._thread = None
        self._levels = (0.0, 0.0, 0.0, 0.0)
        self.error = ""

    def start(self):
        with self._lock:
            self._users += 1
            if self._proc is not None:
                return
            source = default_monitor()
            if source is None:
                self.error = "No audio output found (is PulseAudio running?)"
                return
            try:
                self._proc = subprocess.Popen(
                    ["parec", "-d", source, "--format=s16le", "--rate=%d" % RATE,
                     "--channels=2", "--latency-msec=40"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                )
            except OSError:
                self.error = "parec not found (install pulseaudio-utils)"
                return
            self.error = ""
            self._thread = threading.Thread(target=self._run, args=(self._proc,), daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            self._users = max(0, self._users - 1)
            if self._users or self._proc is None:
                return
            proc, self._proc = self._proc, None
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        self._levels = (0.0, 0.0, 0.0, 0.0)

    def levels(self):
        """(left, right, left_slow, right_slow), each 0..1."""
        return self._levels

    def _run(self, proc):
        size = CHUNK_FRAMES * 4
        peak = [0.02, 0.02]
        fast = [0.0, 0.0]
        slow = [0.0, 0.0]
        while True:
            data = proc.stdout.read(size)
            if len(data) < size:
                break
            samples = array("h")
            samples.frombytes(data)
            for ch in (0, 1):
                chan = samples[ch::2]
                rms = math.sqrt(sum(x * x for x in chan) / len(chan)) / 32768.0
                # Auto-gain: follow the loudest recent level, forget it slowly.
                peak[ch] = max(peak[ch] * 0.9993, rms, 0.01)
                level = min(1.0, (rms / peak[ch]) ** 1.6)
                fast[ch] = level if level > fast[ch] else fast[ch] * 0.80 + level * 0.20
                slow[ch] = slow[ch] * 0.90 + fast[ch] * 0.10
            self._levels = (fast[0], fast[1], slow[0], slow[1])
        self._levels = (0.0, 0.0, 0.0, 0.0)
