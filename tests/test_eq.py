import unittest

from g560_lights import config, eq

G560 = "alsa_output.usb-Logitech_G560_Gaming_Speaker-00.analog-stereo"
OTHER = "alsa_output.pci-hdmi"


class FakePulse:
    """Just enough of `pactl` to follow what PulseEq does, including speakers vanishing."""

    def __init__(self, g560=True):
        self.sinks = []  # (index, name)
        self.modules = []  # (id, name, args)
        self.inputs = []  # (id, sink index, driver)
        self.next = 10
        self.volume = {}
        self.calls = []
        self.default = ""
        self.add_sink(OTHER)
        if g560:
            self.add_sink(G560)
            self.default = G560
        self.inputs = [("100", self.idx(G560 if g560 else OTHER), "app"), ("101", self.idx(G560 if g560 else OTHER), "app")]

    def add_sink(self, name):
        self.sinks.append((str(self.next), name))
        self.next += 1

    def idx(self, name):
        return dict((n, i) for i, n in self.sinks)[name]

    def has(self, name):
        return name in dict((n, i) for i, n in self.sinks)

    # -- scenarios
    def vanish_speakers(self):
        """USB drop: the speakers and anything built on them disappear; apps are rescued elsewhere."""
        gone = {self.idx(G560)}
        for mod in list(self.modules):
            name = self._sink_name_of(mod)
            if self.has(name):
                gone.add(self.idx(name))
            self.modules.remove(mod)
        self.sinks = [s for s in self.sinks if s[0] not in gone]
        self.inputs = [(i, self.idx(OTHER) if s in gone else s, d) for i, s, d in self.inputs if "ladspa" not in d]
        self.default = OTHER

    def return_speakers(self):
        self.add_sink(G560)

    @staticmethod
    def _sink_name_of(mod):
        return next(a.split("=", 1)[1] for a in mod[2].split() if a.startswith("sink_name="))

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
            master = next(a.split("=", 1)[1] for a in args if a.startswith("sink_master="))
            if not self.has(master):
                raise eq.EqError("Failure: Module initialization failed")
            mid = str(self.next); self.next += 1
            self.modules.append((mid, args[1], " ".join(args[2:])))
            self.add_sink(name)
            self.inputs.append((str(self.next), self.idx(master), "module-ladspa-sink.c"))  # the filter's own output
            self.next += 1
            return mid
        if cmd == "unload-module":
            mod = next(m for m in self.modules if m[0] == args[1])
            name = self._sink_name_of(mod)
            self.modules.remove(mod)
            self.sinks = [s for s in self.sinks if s[1] != name]
            return ""
        if cmd == "set-default-sink":
            self.default = args[1]
            return ""
        if cmd == "move-sink-input":
            if args[1] not in {i for i, _, _ in self.inputs}:
                raise eq.EqError("Failure: No such entity")
            idx = self.idx(args[2])
            self.inputs = [(i, idx if i == args[1] else s, d) for i, s, d in self.inputs]
            return ""
        if cmd == "get-sink-volume":
            v = self.volume.get(args[1], 100)
            return "Volume: front-left: 26000 /  %d%% / -12 dB,   front-right: 26000 /  %d%% / -12 dB\n" % (v, v)
        if cmd == "get-sink-mute":
            return "Mute: no\n"
        if cmd in ("set-sink-volume", "set-sink-mute"):
            if cmd == "set-sink-volume":
                self.volume[args[1]] = int(args[2].rstrip("%"))
            return ""
        raise AssertionError("unexpected pactl call %r" % (args,))

    def on(self, sink_name):
        idx = self.idx(sink_name)
        return sorted(i for i, s, d in self.inputs if s == idx and "ladspa" not in d)


class WithFakePlugin(unittest.TestCase):
    def setUp(self):
        self._find = eq.find_plugin
        eq.find_plugin = lambda: "/fake/ladspa/caps.so"

    def tearDown(self):
        eq.find_plugin = self._find


class CurveTests(unittest.TestCase):
    def test_flat_is_all_zero(self):
        self.assertEqual(eq.controls(eq.PRESETS["Flat"]), [0.0] * 10)

    def test_ten_bands_shifted_by_the_automatic_headroom(self):
        for name, gains in eq.PRESETS.items():
            c = eq.controls(gains)
            self.assertEqual(len(c), 10, name)
            master = eq.master_gain(gains)
            self.assertAlmostEqual(master, -max(0, max(gains)), places=6, msg=name)
            for want, have in zip(gains, c):
                self.assertAlmostEqual(want + master, have, places=2, msg=name)
            self.assertLessEqual(max(c), 0.0 + 1e-9, "no preset may boost past 0 dB overall: " + name)

    def test_cuts_do_not_lower_the_level(self):
        self.assertEqual(eq.master_gain(eq.PRESETS["Less bass"]), 0.0)

    def test_values_stay_inside_what_the_plugin_accepts(self):
        lo, hi = eq.PLUGIN_RANGE
        for gains in ([12] * 10, [-12] * 10, [12, -12] * 5):
            for pre in (-12, 0, 12):
                for v in eq.controls(gains, pre):
                    self.assertTrue(lo <= v <= hi, (gains, pre, v))

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

    def test_the_unsafe_plugin_is_gone(self):
        self.assertEqual(eq.LABEL, "Eq10X2")
        self.assertFalse(hasattr(eq, "PLUGIN_BANDS"))


class PulseEqFlowTests(WithFakePlugin):
    def make(self, **kw):
        fake = FakePulse(**kw)
        return fake, eq.PulseEq(run=fake)

    def test_apply_loads_filter_and_moves_streams(self):
        fake, e = self.make()
        e.apply(eq.PRESETS["Loudness"])
        load = next(c for c in fake.calls if c[0] == "load-module")
        self.assertIn("sink_master=" + G560, load)
        self.assertIn("plugin=/fake/ladspa/caps.so", load)
        self.assertIn("label=Eq10X2", load)
        control = next(a for a in load if a.startswith("control="))
        self.assertEqual(len(control[8:].split(",")), 10)
        self.assertEqual(fake.default, "g560_eq_a")
        self.assertEqual(fake.on("g560_eq_a"), ["100", "101"])
        own = next(i for i in fake.inputs if "ladspa" in i[2])
        self.assertEqual(own[1], fake.idx(G560))  # the filter's own output stays on the speakers

    def test_update_swaps_filters_and_keeps_volume(self):
        fake, e = self.make()
        e.apply(eq.PRESETS["Flat"])
        fake.volume["g560_eq_a"] = 40
        e.apply(eq.PRESETS["Rock"])
        self.assertEqual([n for _, n in e.active()], ["g560_eq_b"])
        self.assertEqual(fake.default, "g560_eq_b")
        self.assertEqual(fake.volume["g560_eq_b"], 40)
        self.assertEqual(fake.on("g560_eq_b"), ["100", "101"])
        order = [c[0] for c in fake.calls if c[0] in ("load-module", "unload-module")]
        self.assertEqual(order, ["load-module", "load-module", "unload-module"])  # new before old is removed

    def test_remove_restores_speakers(self):
        fake, e = self.make()
        e.apply(eq.PRESETS["Flat"])
        e.remove()
        self.assertFalse(e.is_active())
        self.assertEqual(fake.default, G560)
        self.assertEqual(fake.on(G560), ["100", "101"])

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
        eq.PulseEq(manage_default=False, run=fake).apply(eq.PRESETS["Flat"])
        self.assertEqual(fake.default, G560)

    def test_missing_plugin_says_how_to_install_it(self):
        eq.find_plugin = lambda: None
        fake, e = self.make()
        with self.assertRaises(eq.EqError) as cm:
            e.apply(eq.PRESETS["Flat"])
        self.assertIn("sudo apt install caps", str(cm.exception))
        self.assertEqual([c for c in fake.calls if c[0] == "load-module"], [])


class RecoveryTests(WithFakePlugin):
    """The speakers vanish (USB drop / power) and come back: sound must come back with them."""

    def setUp(self):
        super().setUp()
        self.fake = FakePulse()
        self.e = eq.PulseEq(run=self.fake)

    def test_healthy_when_connected(self):
        self.assertFalse(self.e.healthy())  # nothing loaded yet
        self.e.apply(eq.PRESETS["Loudness"])
        self.assertTrue(self.e.healthy())

    def test_not_healthy_when_the_speakers_disappear(self):
        self.e.apply(eq.PRESETS["Loudness"])
        self.fake.vanish_speakers()
        self.assertFalse(self.e.healthy())

    def test_apps_are_brought_back_onto_the_eq_after_the_speakers_return(self):
        self.e.apply(eq.PRESETS["Loudness"])
        self.e.note_routed()
        self.assertEqual(self.e.routed, {"100", "101"})
        self.fake.vanish_speakers()
        self.assertEqual(self.fake.on(OTHER), ["100", "101"])  # the system rescued them to another output
        with self.assertRaises(eq.EqError):  # while the speakers are gone there is nothing to build on
            self.e.apply(eq.PRESETS["Loudness"])
        self.fake.return_speakers()
        self.e.apply(eq.PRESETS["Loudness"])
        self.assertTrue(self.e.healthy())
        self.assertEqual(self.fake.default, "g560_eq_a")
        self.assertEqual(self.fake.on("g560_eq_a"), ["100", "101"])  # sound is back on the speakers

    def test_streams_the_user_moved_away_are_not_dragged_back_on_a_normal_update(self):
        self.e.apply(eq.PRESETS["Flat"])
        self.e.note_routed()
        self.fake(*("move-sink-input", "101", OTHER))  # the user sent one app to HDMI on purpose
        self.e.apply(eq.PRESETS["Rock"])  # an ordinary settings change, nothing was lost
        self.assertEqual(self.fake.on(OTHER), ["101"])

    def test_reset_audio_puts_everything_back_on_the_speakers(self):
        self.e.apply(eq.PRESETS["Loudness"])
        self.fake(*("move-sink-input", "101", OTHER))
        master, moved = self.e.reset_audio()
        self.assertEqual(master, G560)
        self.assertFalse(self.e.is_active())
        self.assertEqual(self.fake.default, G560)
        self.assertEqual(self.fake.on(G560), ["100", "101"])

    def test_reset_audio_works_with_a_dead_filter_left_behind(self):
        self.e.apply(eq.PRESETS["Loudness"])
        self.fake.vanish_speakers()
        self.fake.return_speakers()
        master, _ = self.e.reset_audio()
        self.assertEqual(self.fake.default, master)


class FakeEq:
    """A scripted stand-in for PulseEq, to test EqService decisions."""

    def __init__(self):
        self.sid = "100"
        self.is_healthy = False
        self.applied = []
        self.removed = 0
        self.active = False
        self.fail = None

    def server_id(self):
        return self.sid

    def healthy(self):
        return self.is_healthy

    def is_active(self):
        return self.active

    def note_routed(self):
        pass

    def apply(self, gains, preamp=0.0):
        if self.fail:
            raise eq.EqError(self.fail)
        self.applied.append((tuple(gains), preamp))
        self.is_healthy = self.active = True

    def remove(self):
        self.removed += 1
        self.is_healthy = self.active = False


class ServiceLogicTests(WithFakePlugin):
    GAINS = tuple(eq.PRESETS["Loudness"])

    def make(self):
        self.fake = FakeEq()
        self.statuses, self.gave_up = [], []
        self.t = 0.0
        svc = eq.EqService(on_status=lambda ok, m: self.statuses.append((ok, m)), eq=self.fake,
                           on_crash_loop=self.gave_up.append, now=lambda: self.t)
        svc.set(True, self.GAINS, 0.0)
        return svc

    def cycle(self, svc):
        """One pass of the worker loop, without the thread or the wait."""
        with svc._lock:
            enabled, gains, preamp = svc._want
            force, svc._force = svc._force, False
        try:
            if enabled:
                if not svc._tripped:
                    svc._ensure(gains, preamp, force)
            elif svc._applied is not None or svc.eq.is_active():
                svc.eq.remove()
                svc._applied = None
            svc._report(True)
        except eq.EqError as e:
            svc._applied = None
            if "connection" in str(e).lower():
                svc._down = True
            svc._report(False, svc.friendly(str(e)))

    def test_applies_once_then_leaves_a_healthy_filter_alone(self):
        svc = self.make()
        self.cycle(svc)
        self.cycle(svc)
        self.cycle(svc)
        self.assertEqual(len(self.fake.applied), 1)

    def test_rebuilds_a_filter_that_went_missing(self):
        svc = self.make()
        self.cycle(svc)
        self.fake.is_healthy = False  # e.g. the speakers came back after a USB drop
        self.cycle(svc)
        self.assertEqual(len(self.fake.applied), 2)

    def test_manual_reconnect_rebuilds_even_a_healthy_filter(self):
        svc = self.make()
        self.cycle(svc)
        svc.reconnect()
        self.cycle(svc)
        self.assertEqual(len(self.fake.applied), 2)

    def test_changed_settings_are_applied(self):
        svc = self.make()
        self.cycle(svc)
        svc.set(True, eq.PRESETS["Rock"], 2.0)
        self.cycle(svc)
        self.assertEqual(self.fake.applied[-1], (tuple(float(g) for g in eq.PRESETS["Rock"]), 2.0))

    def test_one_audio_server_restart_is_recovered_from(self):
        svc = self.make()
        self.cycle(svc)
        self.fake.sid, self.fake.is_healthy = "200", False  # the server restarted: new pid, filter gone
        self.cycle(svc)
        self.assertEqual(len(self.fake.applied), 2)
        self.assertEqual(self.gave_up, [])

    def test_repeated_restarts_switch_the_eq_off_instead_of_looping(self):
        svc = self.make()
        self.cycle(svc)
        for sid in ("200", "300"):  # the EQ keeps taking the audio server down
            self.t += 5
            self.fake.sid, self.fake.is_healthy = sid, False
            self.cycle(svc)
        self.assertEqual(len(self.gave_up), 1)
        self.assertIn("switched off", self.gave_up[0])
        applied = len(self.fake.applied)
        self.t += 5
        self.fake.sid = "400"
        self.cycle(svc)
        self.assertEqual(len(self.fake.applied), applied, "must not rebuild again after giving up")

    def test_restarts_far_apart_do_not_count_as_a_loop(self):
        svc = self.make()
        self.cycle(svc)
        for sid in ("200", "300", "400"):
            self.t += 600  # ten minutes apart
            self.fake.sid, self.fake.is_healthy = sid, False
            self.cycle(svc)
        self.assertEqual(self.gave_up, [])

    def test_turning_it_on_again_clears_the_give_up_state(self):
        svc = self.make()
        self.cycle(svc)
        for sid in ("200", "300"):
            self.t += 5
            self.fake.sid, self.fake.is_healthy = sid, False
            self.cycle(svc)
        svc.set(False, self.GAINS)
        self.cycle(svc)
        svc.set(True, self.GAINS)
        self.cycle(svc)
        self.assertTrue(self.fake.is_healthy)

    def test_a_dropped_connection_counts_as_a_restart_when_it_returns(self):
        svc = self.make()
        self.fake.sid = None  # no pid file to read: detect by the connection dropping
        self.cycle(svc)
        self.fake.fail = "Connection failure: Connection terminated"
        self.fake.is_healthy = False
        self.cycle(svc)
        self.assertEqual(self.statuses[-1][0], False)
        self.assertIn("restarted", self.statuses[-1][1])  # a friendly message, not the raw error
        self.fake.fail = None
        self.t += 5
        self.cycle(svc)
        self.assertTrue(self.statuses[-1][0])
        self.assertEqual(len(svc._restarts), 1)

    def test_speaker_loss_message_is_friendly_and_says_it_will_recover(self):
        msg = eq.EqService.friendly("G560 audio output not found. Is the USB cable plugged in?")
        self.assertIn("comes back by itself", msg)

    def test_disabling_removes_the_filter(self):
        svc = self.make()
        self.cycle(svc)
        svc.set(False, self.GAINS)
        self.cycle(svc)
        self.assertEqual(self.fake.removed, 1)


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


class PreampTests(WithFakePlugin):
    def test_default_preamp_is_zero_and_changes_nothing(self):
        for name, gains in eq.PRESETS.items():
            self.assertEqual(eq.controls(gains), eq.controls(gains, 0.0), name)

    def test_preamp_adds_to_the_automatic_headroom_both_ways(self):
        gains = eq.PRESETS["Bass boost"]
        base = eq.master_gain(gains)
        self.assertAlmostEqual(eq.master_gain(gains, 4.0), base + 4.0)
        self.assertAlmostEqual(eq.master_gain(gains, -6.5), base - 6.5)
        shifted = [b - a for a, b in zip(eq.controls(gains), eq.controls(gains, 5.0))]
        self.assertTrue(all(abs(d - 5.0) < 0.011 for d in shifted))  # every band moves together

    def test_preamp_can_cancel_the_headroom(self):
        gains = eq.PRESETS["Loudness"]
        self.assertAlmostEqual(eq.master_gain(gains, -eq.headroom(gains)), 0.0)
        for want, have in zip(gains, eq.controls(gains, -eq.headroom(gains))):
            self.assertAlmostEqual(want, have, places=2)

    def test_preamp_is_clamped(self):
        self.assertAlmostEqual(eq.master_gain(eq.PRESETS["Flat"], 99), eq.PREAMP_DB)
        self.assertAlmostEqual(eq.master_gain(eq.PRESETS["Flat"], -99), -eq.PREAMP_DB)

    def test_preamp_reaches_the_audio_server(self):
        fake = FakePulse()
        eq.PulseEq(run=fake).apply(eq.PRESETS["Flat"], preamp=-3.0)
        load = next(c for c in fake.calls if c[0] == "load-module")
        control = next(a for a in load if a.startswith("control="))[8:].split(",")
        self.assertEqual(control, ["-3.00"] * 10)

    def test_preamp_roundtrips_and_bad_values_are_ignored(self):
        s = config.State(eq_preamp=-4.5)
        self.assertEqual(config.State.from_dict(s.to_dict()).eq_preamp, -4.5)
        for bad in ("loud", None, 500, -500):
            got = config.State.from_dict({"eq": {"preamp": bad}}).eq_preamp
            self.assertTrue(-12 <= got <= 12)


class PerEffectColorTests(unittest.TestCase):
    def test_each_effect_remembers_its_own_colors(self):
        s = config.State()  # static, cyan
        s.zone_colors = [(10, 20, 30)] * 4
        s.switch_effect("candle")
        self.assertEqual(s.zone_colors, [(255, 110, 20)] * 4)  # candle's own default, not static's color
        s.zone_colors = [(200, 0, 0)] * 4
        s.switch_effect("static")
        self.assertEqual(s.zone_colors, [(10, 20, 30)] * 4)  # static kept its color
        s.switch_effect("candle")
        self.assertEqual(s.zone_colors, [(200, 0, 0)] * 4)

    def test_per_light_choice_and_second_color_are_remembered_per_effect(self):
        s = config.State()
        s.sync = False
        s.zone_colors = [(1, 0, 0), (2, 0, 0), (3, 0, 0), (4, 0, 0)]
        s.switch_effect("fade")
        self.assertTrue(s.sync)  # a fresh effect starts on "All"
        s.colors[1] = (9, 9, 9)
        s.switch_effect("static")
        self.assertFalse(s.sync)
        self.assertEqual(s.zone_colors[3], (4, 0, 0))
        s.switch_effect("fade")
        self.assertEqual(s.colors[1], (9, 9, 9))

    def test_colorless_effects_do_not_disturb_the_colors(self):
        s = config.State()
        s.zone_colors = [(5, 6, 7)] * 4
        s.switch_effect("wave")
        s.switch_effect("cycle")
        s.switch_effect("static")
        self.assertEqual(s.zone_colors, [(5, 6, 7)] * 4)

    def test_survives_saving_and_loading(self):
        s = config.State()
        s.zone_colors = [(10, 20, 30)] * 4
        s.switch_effect("audio")
        s.zone_colors = [(50, 60, 70)] * 4
        s.audio_blend = True
        back = config.State.from_dict(s.to_dict())
        self.assertEqual(back.effect, "audio")
        self.assertEqual(back.zone_colors, [(50, 60, 70)] * 4)
        back.switch_effect("static")
        self.assertEqual(back.zone_colors, [(10, 20, 30)] * 4)

    def test_copy_does_not_share_palettes(self):
        s = config.State()
        s.switch_effect("candle")
        s.switch_effect("static")
        c = s.copy()
        c.palettes["candle"]["zone_colors"][0] = (0, 0, 0)
        self.assertEqual(s.palettes["candle"]["zone_colors"][0], (255, 110, 20))

    def test_old_config_without_palettes_keeps_current_colors(self):
        s = config.State.from_dict({"effect": "static", "zone_colors": ["112233"] * 4, "colors": ["112233", "445566"]})
        self.assertEqual(s.zone_colors, [(0x11, 0x22, 0x33)] * 4)
        s.switch_effect("candle")
        self.assertEqual(s.zone_colors, [(255, 110, 20)] * 4)

    def test_garbage_palettes_are_ignored(self):
        for bad in ("x", {"static": "y"}, {"nope": {}}, {"static": {"zone_colors": ["zz"] * 4, "second": "000000", "sync": True}}):
            s = config.State.from_dict({"palettes": bad})
            self.assertEqual(s.effect, "static")


class CustomProfileTests(unittest.TestCase):
    def test_roundtrip_through_the_config_file(self):
        s = config.State(eq_profiles={"Night": {"gains": [3.0] * 10, "preamp": -2.5}})
        back = config.State.from_dict(s.to_dict())
        self.assertEqual(back.eq_profiles, {"Night": {"gains": [3.0] * 10, "preamp": -2.5}})

    def test_garbage_profiles_are_dropped_and_values_clamped(self):
        data = {"eq": {"profiles": {
            "ok": {"gains": [50] * 10, "preamp": 99},
            "short": {"gains": [1, 2], "preamp": 0},
            "": {"gains": [0] * 10, "preamp": 0},
            "x" * 40: {"gains": [0] * 10, "preamp": 0},
            "nogains": {"preamp": 1},
            "str": "nope",
        }}}
        s = config.State.from_dict(data)
        self.assertEqual(list(s.eq_profiles), ["ok"])
        self.assertEqual(s.eq_profiles["ok"], {"gains": [12.0] * 10, "preamp": 12.0})
        self.assertEqual(config.State.from_dict({"eq": {"profiles": "x"}}).eq_profiles, {})

    def test_copy_does_not_share_profiles(self):
        s = config.State(eq_profiles={"A": {"gains": [1.0] * 10, "preamp": 0.0}})
        c = s.copy()
        c.eq_profiles["A"]["gains"][0] = 9.0
        self.assertEqual(s.eq_profiles["A"]["gains"][0], 1.0)

    def test_matching_includes_the_preamp(self):
        p = {"gains": [1.0] * 10, "preamp": 2.0}
        self.assertTrue(eq.profile_matches(p, [1.0] * 10, 2.0))
        self.assertFalse(eq.profile_matches(p, [1.0] * 10, 0.0))
        self.assertFalse(eq.profile_matches(p, [1.5] + [1.0] * 9, 2.0))

    def test_name_rules(self):
        self.assertFalse(eq.valid_profile_name("   ")[0])
        self.assertFalse(eq.valid_profile_name("x" * 25)[0])
        self.assertFalse(eq.valid_profile_name("rock")[0])  # built-in name, any case
        self.assertTrue(eq.valid_profile_name("  My mix ")[0])
        full = {"p%d" % i: {} for i in range(eq.MAX_PROFILES)}
        self.assertFalse(eq.valid_profile_name("one more", full)[0])
        self.assertTrue(eq.valid_profile_name("p3", full)[0])  # overwriting is fine
