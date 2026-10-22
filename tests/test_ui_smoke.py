"""Drives the real widgets in a dry-run app (no speakers, no audio changes).

Opens a window briefly, so it only runs with G560_TEST_UI=1 and a display.
"""

import os
import tempfile
import unittest

ENABLED = os.environ.get("G560_TEST_UI") == "1" and bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


@unittest.skipUnless(ENABLED, "set G560_TEST_UI=1 (needs a display)")
class UiSmoke(unittest.TestCase):
    def run_app(self, script):
        os.environ["G560_LIGHTS_DRY_RUN"] = "1"
        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
        from gi.repository import GLib
        from g560_lights import app as appmod

        app = appmod.App(False)
        outcome = {}

        def go():
            try:
                script(app.win, outcome)
            except Exception as e:  # report inside the test, never hang the main loop
                outcome["error"] = repr(e)
                import traceback
                outcome["trace"] = traceback.format_exc()
            app.quit()
            return False

        app.connect("activate", lambda *_: GLib.timeout_add(700, go))
        app.run([])
        if "error" in outcome:
            self.fail(outcome["trace"])
        return outcome

    def test_per_light_colors_on_static(self):
        from g560_lights.device import ZONE_LEFT_FRONT

        def script(win, out):
            s = win.state
            win.chips["static"].set_active(True)
            win.zone_btns[ZONE_LEFT_FRONT].set_active(True)
            assert s.sync is False and win.zone == ZONE_LEFT_FRONT
            win._on_pick((1, 2, 3))
            out["one_light"] = list(s.zone_colors)
            win.zone_btns["all"].set_active(True)
            assert s.sync is True
            win._on_pick((9, 9, 9))
            out["all_lights"] = list(s.zone_colors)

        out = self.run_app(script)
        self.assertEqual(out["one_light"].count((1, 2, 3)), 1)
        self.assertEqual(out["one_light"][2], (1, 2, 3))
        self.assertEqual(out["all_lights"], [(9, 9, 9)] * 4)

    def test_clicking_a_light_in_the_preview_selects_it(self):
        def script(win, out):
            win.chips["strobe"].set_active(True)
            win.select_zone(0)
            out["zone"], out["sync"], out["editing"] = win.zone, win.state.sync, win.editing_zone()
            win._on_pick((7, 7, 7))
            out["colors"] = list(win.state.zone_colors)

        out = self.run_app(script)
        self.assertEqual((out["zone"], out["sync"], out["editing"]), (0, False, 0))
        self.assertEqual(out["colors"][0], (7, 7, 7))
        self.assertNotEqual(out["colors"][1], (7, 7, 7))

    def test_every_one_color_effect_offers_the_light_selector(self):
        def script(win, out):
            for eid in ("static", "breathe", "strobe", "candle", "audio", "fade", "cycle", "wave"):
                win.chips[eid].set_active(True)
                out[eid] = (win.color_box.get_visible(), win.zone_box.get_visible())

        out = self.run_app(script)
        for eid in ("static", "breathe", "strobe", "candle", "audio", "fade"):
            self.assertEqual(out[eid], (True, True), eid)
        for eid in ("cycle", "wave"):
            self.assertFalse(out[eid][0], eid)  # nothing to pick

    def test_music_has_a_pickable_color_and_optional_second_color(self):
        def script(win, out):
            s = win.state
            win.chips["audio"].set_active(True)
            out["blend_default"] = s.audio_blend
            out["slots_single"] = win.slot_box.get_visible()
            win._on_pick((10, 200, 30))
            out["picked"] = list(s.zone_colors)
            win.blend_switch.set_active(True)
            out["slots_blend"] = win.slot_box.get_visible()
            win.slot_btns[1].set_active(True)
            out["zones_hidden_on_loud_slot"] = not win.zone_box.get_visible()
            win._on_pick((250, 0, 200))
            out["loud"] = s.colors[1]

        out = self.run_app(script)
        self.assertFalse(out["blend_default"])
        self.assertFalse(out["slots_single"])
        self.assertEqual(out["picked"], [(10, 200, 30)] * 4)
        self.assertTrue(out["slots_blend"])
        self.assertTrue(out["zones_hidden_on_loud_slot"])
        self.assertEqual(out["loud"], (250, 0, 200))

    def test_window_grows_to_fit_a_taller_page(self):
        from gi.repository import GLib

        def script(win, out):
            win.chips["static"].set_active(True)
            out["before"] = win.get_height()
            win.chips["audio"].set_active(True)
            win.blend_switch.set_active(True)  # the tallest Lights layout

        app_out = {}
        os.environ["G560_LIGHTS_DRY_RUN"] = "1"
        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
        from g560_lights import app as appmod
        app = appmod.App(False)

        def first():
            script(app.win, app_out)
            GLib.timeout_add(800, second)
            return False

        def second():
            app_out["after"] = app.win.get_height()
            app_out["natural"] = app.win.get_content().measure(1, -1)[1]
            app.quit()
            return False

        app.connect("activate", lambda *_: GLib.timeout_add(700, first))
        app.run([])
        self.assertGreaterEqual(app_out["after"], min(app_out["natural"], 1000) - 4,
                                "window %(after)s px, content wants %(natural)s px" % app_out)
        self.assertGreaterEqual(app_out["after"], app_out["before"])


if __name__ == "__main__":
    unittest.main()
