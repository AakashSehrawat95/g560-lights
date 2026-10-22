"""Runs the EQ against a real PulseAudio server, on a throwaway null sink.

The `caps` plugin must be installed (or found through LADSPA_PATH).

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


class PrivateServer(unittest.TestCase):
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
        for f in (100, 125, 1000, 8000):
            self.wavs[f] = os.path.join(self.tmp, "%d.wav" % f)
            tone(self.wavs[f], f)

    def tearDown(self):
        self.eq.remove()

    def levels(self, sink):
        return {f: measure(sink, w) for f, w in self.wavs.items()}


@unittest.skipUnless(ENABLED, "set G560_TEST_PULSE=1 (needs PulseAudio and the caps plugin)")
class PulseEqIntegration(PrivateServer):
    def test_flat_is_transparent_and_a_boost_shapes_the_sound(self):
        self.eq.apply(eq.PRESETS["Flat"])
        flat = self.levels(self.eq.active()[0][1])
        for f in flat:
            self.assertAlmostEqual(flat[f], flat[100], delta=0.6, msg="flat must treat every frequency alike")

        # +12 dB at 1 kHz only. The overall level drops 12 dB (automatic headroom). The bands overlap,
        # so the neighbours pull 1 kHz down a little: it must still stand clearly above the rest.
        gains = [0] * len(eq.UI_BANDS)
        gains[eq.UI_BANDS.index(1000)] = 12
        self.eq.apply(gains)
        self.assertEqual(len(self.eq.active()), 1, "old filter should be removed")
        boosted = self.levels(self.eq.active()[0][1])
        d = {f: round(boosted[f] - flat[f], 1) for f in flat}
        print("\nchange vs flat (dB):", d)
        self.assertGreater(d[1000] - d[100], 6)
        self.assertGreater(d[1000] - d[8000], 6)
        self.assertLess(d[100], -8)
        self.assertLess(d[8000], -8)

    def test_cut_reduces_that_band_and_leaves_the_far_ones(self):
        self.eq.apply(eq.PRESETS["Flat"])
        flat = self.levels(self.eq.active()[0][1])
        gains = [0] * len(eq.UI_BANDS)
        gains[eq.UI_BANDS.index(125)] = -10
        self.eq.apply(gains)
        cut = self.levels(self.eq.active()[0][1])
        d = {f: round(cut[f] - flat[f], 1) for f in flat}
        print("\nchange vs flat (dB):", d)
        # The bands are broad and overlap (as in most graphic EQs), so one band moved alone is softer
        # than its slider; presets, which move neighbours together, track much closer.
        self.assertLess(d[125], -2)
        self.assertLess(d[100], -2)
        self.assertAlmostEqual(d[1000], 0, delta=1.0)
        self.assertAlmostEqual(d[8000], 0, delta=1.0)

    def test_preamp_moves_the_whole_level_both_ways(self):
        self.eq.apply(eq.PRESETS["Flat"])
        flat = self.levels(self.eq.active()[0][1])
        self.eq.apply(eq.PRESETS["Flat"], preamp=6.0)
        up = self.levels(self.eq.active()[0][1])
        self.eq.apply(eq.PRESETS["Flat"], preamp=-6.0)
        down = self.levels(self.eq.active()[0][1])
        print("\npreamp +6 vs flat:", {f: round(up[f] - flat[f], 1) for f in flat},
              "| -6:", {f: round(down[f] - flat[f], 1) for f in flat})
        for f in flat:
            self.assertAlmostEqual(up[f] - flat[f], 6.0, delta=1.5)
            self.assertAlmostEqual(down[f] - flat[f], -6.0, delta=1.5)

    def test_remove_unloads_everything(self):
        self.eq.apply(eq.PRESETS["Loudness"])
        self.assertTrue(self.eq.is_active())
        self.eq.remove()
        self.assertFalse(self.eq.is_active())


G560_SINK = "alsa_output.usb-Logitech_G560_test.analog-stereo"


def pactl(*args):
    return subprocess.run(["pactl", *args], capture_output=True, text=True, check=True).stdout.strip()


def stream_sinks():
    """{stream id: sink name} for apps' streams (not the filter's own output)."""
    names = {row.split("\t")[0]: row.split("\t")[1] for row in pactl("list", "short", "sinks").splitlines()}
    out = {}
    for row in pactl("list", "short", "sink-inputs").splitlines():
        f = row.split("\t")
        if "module-ladspa-sink" not in f[3]:
            out[f[0]] = names.get(f[1], "?")
    return out


@unittest.skipUnless(ENABLED, "set G560_TEST_PULSE=1 (needs PulseAudio and the caps plugin)")
class Resilience(PrivateServer):
    """What the user hit: the speakers drop out, or the EQ is changed constantly. Sound must survive."""

    def setUp(self):
        super().setUp()
        self.mods = [pactl("load-module", "module-null-sink", "sink_name=" + G560_SINK),
                     pactl("load-module", "module-null-sink", "sink_name=hdmi_other")]
        pactl("set-default-sink", G560_SINK)
        wav = os.path.join(self.tmp, "long.wav")
        tone(wav, 440, seconds=60, amp=0.1)
        self.player = subprocess.Popen(["paplay", wav], stderr=subprocess.DEVNULL)
        time.sleep(0.5)

    def tearDown(self):
        self.player.terminate()
        self.player.wait()
        for m in self.mods:
            subprocess.run(["pactl", "unload-module", m], capture_output=True)
        super().tearDown()

    def wait_for(self, condition, seconds=10):
        end = time.time() + seconds
        while time.time() < end:
            if condition():
                return True
            time.sleep(0.2)
        return False

    def test_server_survives_many_random_curves_while_playing(self):
        import random
        e = eq.PulseEq()
        rnd = random.Random(3)
        for n in range(40):
            gains = [rnd.choice([-12, 12, rnd.uniform(-12, 12)]) for _ in range(10)]
            e.apply(gains, preamp=rnd.uniform(-12, 12))
            self.assertIsNone(self.server.poll(), "the audio server died at switch %d" % n)
            time.sleep(0.1)
        self.assertIsNone(self.player.poll(), "the playing stream was lost")
        e.remove()

    def test_speakers_vanish_and_return_and_the_service_restores_the_sound(self):
        svc = eq.EqService(eq=eq.PulseEq(), poll=0.3)
        svc.start(True, eq.PRESETS["Loudness"], 0.0)
        try:
            e = svc.eq
            self.assertTrue(self.wait_for(e.healthy), "EQ did not come up")
            self.assertEqual(set(stream_sinks().values()), {e.active()[0][1]}, "app should be on the EQ")

            pactl("unload-module", self.mods[0])  # the speakers vanish (USB drop)
            self.assertTrue(self.wait_for(lambda: not e.healthy(), 5))
            self.assertIsNone(self.server.poll(), "audio server died")
            self.assertTrue(set(stream_sinks().values()) <= {"eqtest", "hdmi_other"},
                            "the system rescues the app onto another output: %s" % stream_sinks())

            self.mods[0] = pactl("load-module", "module-null-sink", "sink_name=" + G560_SINK)  # they come back
            self.assertTrue(self.wait_for(e.healthy, 10), "EQ was not rebuilt by itself")
            self.assertTrue(self.wait_for(lambda: set(stream_sinks().values()) == {e.active()[0][1]}, 5),
                            "the app was not moved back onto the EQ: %s" % stream_sinks())
            self.assertIn("g560_eq", pactl("get-default-sink"))
        finally:
            svc.stop()

    def test_manual_reconnect_and_emergency_reset(self):
        svc = eq.EqService(eq=eq.PulseEq(), poll=30)  # slow polling: only a manual action can act
        svc.start(True, eq.PRESETS["Rock"], 0.0)
        try:
            e = svc.eq
            self.assertTrue(self.wait_for(e.healthy))
            before = e.active()[0][0]
            svc.reconnect()
            self.assertTrue(self.wait_for(lambda: e.active() and e.active()[0][0] != before, 5), "reconnect did nothing")
            self.assertTrue(e.healthy())
        finally:
            svc.stop()
        master, moved = eq.PulseEq().reset_audio()  # the emergency button
        self.assertEqual(master, G560_SINK)
        self.assertFalse(eq.PulseEq().is_active())
        self.assertEqual(pactl("get-default-sink"), G560_SINK)
        self.assertEqual(set(stream_sinks().values()), {G560_SINK})


if __name__ == "__main__":
    unittest.main()
