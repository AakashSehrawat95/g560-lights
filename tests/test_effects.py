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


class ConfigTests(unittest.TestCase):
    def test_roundtrip(self):
        s = config.State(effect="fade", colors=[(1, 2, 3), (4, 5, 6)], brightness=42, speed=7, sync=False)
        back = config.State.from_dict(s.to_dict())
        self.assertEqual(back.to_dict(), s.to_dict())

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
