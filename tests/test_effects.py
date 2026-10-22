import math
import unittest

from g560_lights import config, effects


class EffectTests(unittest.TestCase):
    def test_every_effect_returns_four_valid_colors(self):
        for info in effects.EFFECTS:
            for t in (0.0, 0.37, 5.0, 123.4):
                frame = effects.render(config.State(effect=info.id), t, (0.5, 0.9, 0.2, 0.4))
                self.assertEqual(len(frame), 4, info.id)
                for color in frame:
                    self.assertEqual(len(color), 3)
                    self.assertTrue(all(isinstance(c, int) and 0 <= c <= 255 for c in color), (info.id, color))

    def test_brightness_scales_output(self):
        full = effects.render(config.State(effect="static", brightness=100), 0)[0]
        half = effects.render(config.State(effect="static", brightness=50), 0)[0]
        self.assertEqual(full, (0, 255, 255))
        self.assertEqual(half, (0, 128, 128))

    def test_static_per_zone(self):
        zc = [(1, 0, 0), (0, 2, 0), (0, 0, 3), (4, 4, 4)]
        s = config.State(effect="static", sync=False, zone_colors=zc)
        self.assertEqual(effects.render(s, 0), zc)

    def test_speed_mapping_is_monotonic_and_bounded(self):
        periods = [effects.period_seconds(s) for s in range(1, 101)]
        self.assertEqual(periods, sorted(periods, reverse=True))
        self.assertAlmostEqual(periods[0], 30.0)
        self.assertAlmostEqual(periods[-1], 0.6, places=2)
        self.assertGreaterEqual(effects.rate_ms(100), 100)

    def test_flash_never_exceeds_safe_rate(self):
        for speed in range(1, 101):
            self.assertGreaterEqual(effects.flash_period(speed), 1.0 / effects.MAX_FLASH_HZ - 1e-9)

    def test_strobe_alternates_front_and_rear(self):
        s = config.State(effect="strobe", speed=1)
        frame = effects.render(s, 0.0)
        self.assertNotEqual(frame[2], (0, 0, 0))   # front on
        self.assertEqual(frame[0], (0, 0, 0))      # rear off
        later = effects.render(s, effects.flash_period(1) * 0.75)
        self.assertEqual(later[2], (0, 0, 0))
        self.assertNotEqual(later[0], (0, 0, 0))

    def test_audio_follows_level(self):
        s = config.State(effect="audio")
        quiet = effects.render(s, 0, (0, 0, 0, 0))
        loud = effects.render(s, 0, (1, 1, 1, 1))
        self.assertLess(sum(quiet[2]), sum(loud[2]))

    def test_breathe_is_dark_at_start_and_full_at_half_period(self):
        s = config.State(effect="breathe", speed=50)
        p = effects.period_seconds(50)
        self.assertEqual(effects.render(s, 0)[0], (0, 0, 0))
        self.assertEqual(effects.render(s, p / 2)[0], (0, 255, 255))


class PerLightAndPhaseTests(unittest.TestCase):
    ZONES = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]

    def state(self, effect, **kw):
        return config.State(effect=effect, zone_colors=list(self.ZONES), **kw)

    def test_every_one_color_effect_honours_per_light_colors(self):
        for eid in ("static", "breathe", "strobe", "candle", "audio", "fade"):
            self.assertTrue(effects.BY_ID[eid].per_zone, eid)
        # Use a moment when the effect is bright enough that the hue is clear.
        loud = (1, 1, 1, 1)
        for eid, t in (("static", 0), ("breathe", effects.period_seconds(50) / 2), ("candle", 0.0), ("audio", 0)):
            frame = effects.render(self.state(eid), t, loud)
            hues = [max(range(3), key=lambda i: c[i]) for c in frame]
            self.assertEqual(hues[:3], [0, 1, 2], (eid, frame))  # red, green, blue stay distinct

    def test_music_uses_the_picked_color_unless_blending(self):
        s = self.state("audio", brightness=100)
        s.zone_colors = [(10, 200, 30)] * 4
        s.colors = [(0, 0, 0), (255, 0, 200)]
        loud = effects.render(s, 0, (1, 1, 1, 1))
        self.assertEqual(loud[2], (10, 200, 30))  # fully loud, still the picked color, not pink
        s.audio_blend = True
        blended = effects.render(s, 0, (1, 1, 1, 1))
        self.assertEqual(blended[2], (255, 0, 200))  # now shifts to the loud color

    def test_changing_speed_does_not_make_the_animation_jump(self):
        s = self.state("wave", speed=30)
        clock = effects.Clock()
        for step in range(100):
            clock.advance(step * 0.04, 30)
        before = effects.render(s, clock)
        s.speed = 60  # slider dragged from 30 to 60
        clock.advance(100 * 0.04, 60)  # the very next frame
        after = effects.render(s, clock)
        smooth = max(max(abs(x - y) for x, y in zip(a, b)) for a, b in zip(before, after))
        self.assertLess(smooth, 40)  # a small step, not a teleport
        # the old time / period maths lands on a completely different color
        old = effects.render(s, 100 * 0.04)
        jump = max(max(abs(x - y) for x, y in zip(a, b)) for a, b in zip(before, old))
        self.assertGreater(jump, 50)
        self.assertGreater(jump, 2.5 * smooth)

    def test_speed_up_means_faster_phase(self):
        slow, fast = effects.Clock(), effects.Clock()
        for step in range(50):
            slow.advance(step * 0.04, 20)
            fast.advance(step * 0.04, 80)
        self.assertGreater(fast.cycles, slow.cycles * 3)

    def test_a_long_stall_does_not_fast_forward(self):
        clock = effects.Clock()
        clock.advance(0.0, 50)
        clock.advance(30.0, 50)  # app was suspended for 30 s
        self.assertLessEqual(clock.t, 0.25)


class ConfigTests(unittest.TestCase):
    def test_roundtrip(self):
        s = config.State(effect="fade", colors=[(1, 2, 3), (4, 5, 6)], brightness=42, speed=7, sync=False)
        back = config.State.from_dict(s.to_dict())
        self.assertEqual(back.to_dict(), s.to_dict())

    def test_copy_keeps_every_field_and_is_independent(self):
        s = config.State(effect="audio", colors=[(1, 2, 3), (4, 5, 6)], zone_colors=[(9, 9, 9)] * 4, sync=False,
                         audio_blend=True, brightness=42, speed=7, power=False, recent=[(1, 1, 1)],
                         eq_enabled=True, eq_gains=[1.0] * 10)
        c = s.copy()
        self.assertEqual(c.to_dict(), s.to_dict())
        c.zone_colors[0] = (0, 0, 0)
        c.eq_gains[0] = 5.0
        self.assertEqual(s.zone_colors[0], (9, 9, 9))
        self.assertEqual(s.eq_gains[0], 1.0)

    def test_old_config_with_one_shared_color_fills_every_light(self):
        s = config.State.from_dict({"effect": "static", "colors": ["ff0000", "00ff00"]})
        self.assertEqual(s.zone_colors, [(255, 0, 0)] * 4)

    def test_garbage_falls_back_to_defaults(self):
        for bad in (None, [], "x", {"effect": "nope", "colors": "zz", "brightness": "high", "speed": None}):
            s = config.State.from_dict(bad)
            self.assertEqual(s.effect, "static")
            self.assertEqual(len(s.colors), 2)
            self.assertTrue(1 <= s.brightness <= 100)

    def test_values_are_clamped(self):
        s = config.State.from_dict({"brightness": 5000, "speed": -3})
        self.assertEqual((s.brightness, s.speed), (100, 1))

    def test_hex(self):
        self.assertEqual(config.from_hex("#00FfFF"), (0, 255, 255))
        with self.assertRaises(ValueError):
            config.from_hex("12345")

    def test_save_and_load(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "config.json")
            config.save(config.State(effect="wave", speed=77), path)
            self.assertEqual(config.load(path).speed, 77)
            self.assertEqual(config.load(os.path.join(d, "missing.json")).effect, "static")


if __name__ == "__main__":
    unittest.main()
