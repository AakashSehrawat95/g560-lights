"""Runs the EQ against a real PulseAudio server, on a throwaway null sink.

Skipped unless G560_TEST_PULSE=1. It starts its own private PulseAudio server
(the one you are using is never touched, and nothing is audible), so other
audio tools such as PulseEffects cannot interfere.
"""

import array
import math
import os
import subprocess
import tempfile
import time
import unittest
import wave

from g560_lights import eq

RATE = 44100
ENABLED = os.environ.get("G560_TEST_PULSE") == "1" and eq.plugin_available()


def tone(path, freq, seconds=1.8, amp=0.25):
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        frames = array.array("h")
        for i in range(int(RATE * seconds)):
            v = int(32767 * amp * math.sin(2 * math.pi * freq * i / RATE))
            frames.extend((v, v))
        w.writeframes(frames.tobytes())


def measure(sink, wav):
    """Play wav into `sink`, record the null sink's monitor, return RMS (dBFS)."""
    out = tempfile.NamedTemporaryFile(suffix=".raw")
    rec = subprocess.Popen(
        ["parec", "-d", "eqtest.monitor", "--raw", "--format=s16le", "--rate=%d" % RATE, "--channels=2", "--latency-msec=50"],
        stdout=out, stderr=subprocess.DEVNULL)  # to a file: a pipe would fill up and stall the recording
    time.sleep(0.2)
    subprocess.run(["paplay", "--device=" + sink, wav], check=True, timeout=15)
    time.sleep(0.2)
    rec.terminate()
    rec.wait()
    with open(out.name, "rb") as f:
        data = f.read()
    out.close()
    samples = array.array("h")
    samples.frombytes(data[: len(data) // 2 * 2])
    mid = samples[RATE:RATE * 2]  # skip start-up, keep ~0.5 s of steady tone (interleaved stereo)
    rms = math.sqrt(sum(x * x for x in mid) / max(1, len(mid))) / 32768.0
    return 20 * math.log10(max(rms, 1e-9))


@unittest.skipUnless(ENABLED, "set G560_TEST_PULSE=1 (needs PulseAudio and zam-plugins)")
class PulseEqIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="g560-pulse-")
        cls.saved = {k: os.environ.get(k) for k in ("PULSE_SERVER", "PULSE_RUNTIME_PATH")}
        sock = os.path.join(cls.dir, "native")
        env = dict(os.environ, PULSE_RUNTIME_PATH=cls.dir, PULSE_STATE_PATH=cls.dir, PULSE_CONFIG_PATH=cls.dir,
                   XDG_RUNTIME_DIR=cls.dir, DBUS_SESSION_BUS_ADDRESS="unix:path=%s/no-bus" % cls.dir)
        env.pop("PULSE_SERVER", None)
        cls.log = open(os.path.join(cls.dir, "server.log"), "w")
        cls.server = subprocess.Popen(
            ["pulseaudio", "-n", "--daemonize=no", "--exit-idle-time=-1", "--disable-shm", "--use-pid-file=no",
             "-L", "module-native-protocol-unix socket=%s auth-anonymous=1" % sock,
             "-L", "module-null-sink sink_name=eqtest"],
            env=env, stdout=cls.log, stderr=subprocess.STDOUT)
        os.environ["PULSE_SERVER"] = "unix:" + sock
        for _ in range(50):
            if subprocess.run(["pactl", "info"], capture_output=True).returncode == 0:
                return
            time.sleep(0.1)
        cls.tearDownClass()
        raise RuntimeError("private PulseAudio server did not start: " + open(cls.log.name).read())

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=5)
        cls.log.close()
        for k, v in cls.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        self.eq = eq.PulseEq(master="eqtest", manage_default=False)
        self.tmp = tempfile.mkdtemp()
        self.wavs = {}
        for f in (100, 1000, 8000):
            self.wavs[f] = os.path.join(self.tmp, "%d.wav" % f)
            tone(self.wavs[f], f)

    def tearDown(self):
        self.eq.remove()

    def levels(self, sink):
        return {f: measure(sink, w) for f, w in self.wavs.items()}

    def test_flat_is_transparent_and_boost_shapes_the_sound(self):
        self.eq.apply(eq.PRESETS["Flat"])
        sink = self.eq.active()[0][1]
        flat = self.levels(sink)

        # +12 dB at 1 kHz only. Master gain drops by 12 dB, so 1 kHz stays put
        # and everything away from it should be ~12 dB quieter.
        gains = [0] * len(eq.UI_BANDS)
        gains[eq.UI_BANDS.index(1000)] = 12
        self.eq.apply(gains)
        self.assertEqual(len(self.eq.active()), 1, "old filter should be removed")
        boosted = self.levels(self.eq.active()[0][1])

        d = {f: round(boosted[f] - flat[f], 1) for f in flat}
        print("\nchange vs flat (dB):", d)
        self.assertAlmostEqual(d[1000], 0, delta=1.5)
        self.assertAlmostEqual(d[100], -12, delta=2.0)
        self.assertAlmostEqual(d[8000], -12, delta=2.0)

    def test_cut_reduces_that_band_only(self):
        self.eq.apply(eq.PRESETS["Flat"])
        flat = self.levels(self.eq.active()[0][1])
        gains = [0] * len(eq.UI_BANDS)
        gains[eq.UI_BANDS.index(125)] = -10
        self.eq.apply(gains)
        cut = self.levels(self.eq.active()[0][1])
        d = {f: round(cut[f] - flat[f], 1) for f in flat}
        print("\nchange vs flat (dB):", d)
        self.assertLess(d[100], -6)
        self.assertAlmostEqual(d[1000], 0, delta=1.0)
        self.assertAlmostEqual(d[8000], 0, delta=1.0)

    def test_remove_unloads_everything(self):
        self.eq.apply(eq.PRESETS["Loudness"])
        self.assertTrue(self.eq.is_active())
        self.eq.remove()
        self.assertFalse(self.eq.is_active())


if __name__ == "__main__":
    unittest.main()
