import unittest

from g560_lights import config, eq


class FakePulse:
    """Just enough of `pactl` to follow what PulseEq does."""

    def __init__(self, g560=True):
        self.sinks = [("0", "alsa_output.usb-Logitech_G560_Gaming_Speaker-00.analog-stereo")] if g560 else []
        self.modules = []
        self.inputs = [("100", "0", "app"), ("101", "0", "app")]  # (id, sink index, driver)
        self.next = 10
        self.default = self.sinks[0][1] if g560 else ""
        self.volume = {}
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        cmd = args[0]
        if args[:3] == ("list", "short", "sinks"):
            return "".join("%s\t%s\tmodule\ts16le 2ch 44100Hz\tRUNNING\n" % s for s in self.sinks)
        if args[:3] == ("list", "short", "modules"):
            return "".join("%s\t%s\t%s\n" % m for m in self.modules)
        if args[:3] == ("list", "short", "sink-inputs"):
            return "".join("%s\t%s\t7\t%s\tfloat32le 2ch 44100Hz\n" % i for i in self.inputs)
        if cmd == "load-module":
            name = next(a.split("=", 1)[1] for a in args if a.startswith("sink_name="))
            mid = str(self.next); self.next += 1
            self.modules.append((mid, args[1], " ".join(args[2:])))
            idx = str(self.next); self.next += 1
            self.sinks.append((idx, name))
            self.inputs.append((str(self.next), self.sinks[0][0], "module-ladspa-sink.c"))  # the filter's own output
            self.next += 1
            return mid
        if cmd == "unload-module":
            mod = next(m for m in self.modules if m[0] == args[1])
            name = next(a.split("=", 1)[1] for a in mod[2].split() if a.startswith("sink_name="))
            self.modules.remove(mod)
            self.sinks = [s for s in self.sinks if s[1] != name]
            return ""
        if cmd == "set-default-sink":
            self.default = args[1]
            return ""
        if cmd == "move-sink-input":
            idx = dict((n, i) for i, n in self.sinks)[args[2]]
            self.inputs = [(i, idx if i == args[1] else s, d) for i, s, d in self.inputs]
            return ""
        if cmd == "get-sink-volume":
            return "Volume: front-left: 26000 /  %d%% / -12 dB,   front-right: 26000 /  %d%% / -12 dB\n" % (
                self.volume.get(args[1], 100), self.volume.get(args[1], 100))
        if cmd == "get-sink-mute":
            return "Mute: no\n"
        if cmd in ("set-sink-volume", "set-sink-mute"):
            if cmd == "set-sink-volume":
                self.volume[args[1]] = int(args[2].rstrip("%"))
            return ""
        raise AssertionError("unexpected pactl call %r" % (args,))

    def on(self, sink_name):
        idx = dict((n, i) for i, n in self.sinks)[sink_name]
        return sorted(i for i, s, d in self.inputs if s == idx and "ladspa" not in d)


class CurveTests(unittest.TestCase):
    def test_flat_is_all_zero(self):
        self.assertEqual(eq.controls(eq.PRESETS["Flat"]), [0.0] * 30)

    def test_controls_shape_and_headroom(self):
        c = eq.controls(eq.PRESETS["Bass boost"])
        self.assertEqual(len(c), 30)  # master + 29 bands
        self.assertAlmostEqual(c[0], -max(c[1:]), places=6)
        self.assertLessEqual(c[0], 0)
        self.assertTrue(all(-12 <= v <= 12 for v in c[1:]))

    def test_cuts_do_not_lower_master(self):
        self.assertEqual(eq.controls(eq.PRESETS["Less bass"])[0], -0.0)

    def test_curve_passes_through_the_sliders(self):
        gains = [0, 0, 6, 0, 0, -4, 0, 0, 0, 0]
        got = eq.curve(gains, freqs=eq.UI_BANDS)
        for want, have in zip(gains, got):
            self.assertAlmostEqual(want, have, places=6)

    def test_every_preset_is_valid(self):
        for name, gains in eq.PRESETS.items():
            self.assertEqual(len(gains), len(eq.UI_BANDS), name)
            self.assertTrue(all(-eq.MAX_DB <= g <= eq.MAX_DB for g in gains), name)
        self.assertEqual(eq.matching_preset(eq.PRESETS["Loudness"]), "Loudness")
        self.assertIsNone(eq.matching_preset([1] * 10))

    def test_loudness_boosts_both_ends(self):
        g = eq.PRESETS["Loudness"]
        self.assertGreater(g[0], g[5])
        self.assertGreater(g[-1], g[5])

    def test_out_of_range_gains_are_clamped(self):
        self.assertEqual(eq.clamp_gains([50, -50]), (12.0, -12.0))


class PulseEqFlowTests(unittest.TestCase):
    def make(self, **kw):
        fake = FakePulse(**kw)
        return fake, eq.PulseEq(run=fake)

    def test_apply_loads_filter_and_moves_streams(self):
        fake, e = self.make()
        e.apply(eq.PRESETS["Loudness"])
        load = next(c for c in fake.calls if c[0] == "load-module")
        self.assertIn("sink_master=alsa_output.usb-Logitech_G560_Gaming_Speaker-00.analog-stereo", load)
        self.assertIn("plugin=ZamGEQ31-ladspa", load)
        control = next(a for a in load if a.startswith("control="))
        self.assertEqual(len(control[8:].split(",")), 30)
        self.assertEqual(fake.default, "g560_eq_a")
        self.assertEqual(fake.on("g560_eq_a"), ["100", "101"])  # app streams moved
        # the filter's own output stream must stay on the speakers, not loop into itself
        own = next(i for i in fake.inputs if "ladspa" in i[2])
        self.assertEqual(own[1], "0")

    def test_update_swaps_filters_and_keeps_volume(self):
        fake, e = self.make()
        e.apply(eq.PRESETS["Flat"])
        fake.volume["g560_eq_a"] = 40
        e.apply(eq.PRESETS["Rock"])
        self.assertEqual([n for _, n in e.active()], ["g560_eq_b"])
        self.assertEqual(fake.default, "g560_eq_b")
        self.assertEqual(fake.volume["g560_eq_b"], 40)
        self.assertEqual(fake.on("g560_eq_b"), ["100", "101"])
        # new filter was created before the old one was removed
        order = [c[0] for c in fake.calls if c[0] in ("load-module", "unload-module")]
        self.assertEqual(order, ["load-module", "load-module", "unload-module"])

    def test_remove_restores_speakers(self):
        fake, e = self.make()
        e.apply(eq.PRESETS["Flat"])
        e.remove()
        self.assertFalse(e.is_active())
        self.assertEqual(fake.default, "alsa_output.usb-Logitech_G560_Gaming_Speaker-00.analog-stereo")
        self.assertEqual(fake.on(fake.default), ["100", "101"])

    def test_remove_when_inactive_does_nothing(self):
        fake, e = self.make()
        e.remove()
        self.assertEqual([c for c in fake.calls if c[0] not in ("list",)], [])

    def test_no_speakers(self):
        fake, e = self.make(g560=False)
        with self.assertRaises(eq.EqError):
            e.apply(eq.PRESETS["Flat"])

    def test_default_sink_untouched_when_not_managed(self):
        fake = FakePulse()
        e = eq.PulseEq(manage_default=False, run=fake)
        e.apply(eq.PRESETS["Flat"])
        self.assertEqual(fake.default, "alsa_output.usb-Logitech_G560_Gaming_Speaker-00.analog-stereo")


class ServiceTests(unittest.TestCase):
    def test_config_roundtrip(self):
        s = config.State(eq_enabled=True, eq_gains=[1.5] * 10)
        back = config.State.from_dict(s.to_dict())
        self.assertTrue(back.eq_enabled)
        self.assertEqual(back.eq_gains, [1.5] * 10)

    def test_bad_eq_config_falls_back(self):
        for bad in ({"eq": "x"}, {"eq": {"gains": [1, 2]}}, {"eq": {"gains": "abc"}}, {"eq": {"enabled": 1, "gains": [99] * 10}}):
            s = config.State.from_dict(bad)
            self.assertEqual(len(s.eq_gains), 10)
            self.assertTrue(all(-12 <= g <= 12 for g in s.eq_gains))


if __name__ == "__main__":
    unittest.main()
