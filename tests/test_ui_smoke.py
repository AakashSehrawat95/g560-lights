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
        from g560_lights.device import ZONE_LEFT_FRONT
        self.assertEqual(out["one_light"][ZONE_LEFT_FRONT], (1, 2, 3))  # exactly the light that was selected
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
            out["zones_greyed_not_hidden_on_loud_slot"] = win.zone_box.get_visible() and not win.zone_box.get_sensitive()
            win._on_pick((250, 0, 200))
            out["loud"] = s.colors[1]

        out = self.run_app(script)
        self.assertFalse(out["blend_default"])
        self.assertFalse(out["slots_single"])
        self.assertEqual(out["picked"], [(10, 200, 30)] * 4)
        self.assertTrue(out["slots_blend"])
        self.assertTrue(out["zones_greyed_not_hidden_on_loud_slot"])
        self.assertEqual(out["loud"], (250, 0, 200))

    def test_custom_eq_profiles_can_be_saved_selected_and_deleted(self):
        def script(win, out):
            page, s = win.eq_page, win.state
            page.chips["Rock"].set_active(True)
            s.eq_gains[0] = 9.0
            page.refresh()
            out["custom_label_shown"] = page.custom_label.get_visible()
            page.save_profile("My mix")
            out["chip_added"] = "My mix" in page.profile_chips
            out["selected"] = page.current_selection()
            out["delete_shown"] = page.delete_btn.get_visible()
            page.chips["Flat"].set_active(True)
            out["flat"] = list(s.eq_gains)
            page.profile_chips["My mix"].set_active(True)
            out["restored_first_band"] = s.eq_gains[0]
            page.delete_profile("My mix")
            out["gone"] = "My mix" not in page.profile_chips and "My mix" not in s.eq_profiles
            ok, msg = eq_module.valid_profile_name("Rock", s.eq_profiles)
            out["builtin_name_rejected"] = not ok

        from g560_lights import eq as eq_module
        out = self.run_app(script)
        self.assertTrue(out["custom_label_shown"])
        self.assertTrue(out["chip_added"])
        self.assertEqual(out["selected"], ("profile", "My mix"))
        self.assertTrue(out["delete_shown"])
        self.assertEqual(out["flat"], [0.0] * 10)
        self.assertEqual(out["restored_first_band"], 9.0)
        self.assertTrue(out["gone"])
        self.assertTrue(out["builtin_name_rejected"])

    def test_eq_problem_banner_offers_reconnect_and_turn_off(self):
        def script(win, out):
            page, s = win.eq_page, win.state
            s.eq_enabled = True
            page.refresh()
            page.show_status(False, "The audio server restarted.")
            out["buttons"] = (page.reconnect_btn.get_visible(), page.off_btn.get_visible())
            out["banner"] = page.warning_rev.get_reveal_child()
            clicked = []
            page.on_reconnect = lambda: clicked.append("reconnect")
            page.reconnect_btn.emit("clicked")
            out["reconnect_called"] = clicked
            page._turn_off()
            out["eq_off"] = s.eq_enabled is False
            page.show_notice("switched off for safety")
            out["notice"] = page.notice_rev.get_reveal_child()

        out = self.run_app(script)
        self.assertEqual(out["buttons"], (True, True))
        self.assertTrue(out["banner"])
        self.assertEqual(out["reconnect_called"], ["reconnect"])
        self.assertTrue(out["eq_off"])
        self.assertTrue(out["notice"])

    def test_start_at_login_is_a_toggle_on_the_settings_tab(self):
        from g560_lights import autostart

        def script(win, out):
            page = win.settings_page
            win.show_tab("settings")
            out["tab"] = win.stack.get_visible_child_name()
            out["starts_off"] = (not page.login_switch.get_active(), not autostart.is_enabled())
            page.login_switch.set_active(True)
            out["on"] = (autostart.is_enabled(), "--background" in open(autostart.path()).read())
            page.login_switch.set_active(False)
            out["off"] = not autostart.is_enabled()
            # changed outside the app (e.g. deleted by hand): the switch follows when the tab is shown again
            autostart.set_enabled(True)
            page.refresh()
            out["follows_disk"] = page.login_switch.get_active()
            autostart.set_enabled(False)
            out["old_menu_item_gone"] = win.app.lookup_action("autostart") is None

        out = self.run_app(script)
        self.assertEqual(out["tab"], "settings")
        self.assertEqual(out["starts_off"], (True, True))
        self.assertEqual(out["on"], (True, True))
        self.assertTrue(out["off"])
        self.assertTrue(out["follows_disk"])
        self.assertTrue(out["old_menu_item_gone"])

    def test_clicking_the_lobe_picks_the_small_light_and_the_grille_picks_the_big_one(self):
        from g560_lights import device
        from g560_lights.app import SpeakerPreview

        def script(win, out):
            prev = win.preview
            w, h = prev.get_width(), prev.get_height()
            r = min(53.0, w * 0.1)
            cy = (h - 44) / 2.0 + 4
            lx, rx = w * 0.27, w * 0.73
            spots = {"left lobe": (lx + 1.1 * r, cy + 0.35 * r), "left grille": (lx, cy),
                     "right lobe": (rx - 1.1 * r, cy + 0.35 * r), "right grille": (rx, cy),
                     "empty corner": (w * 0.05, cy - 0.9 * r), "gap beside the lobe": (lx + 1.3 * r, cy - 0.5 * r)}
            for name, (x, y) in spots.items():
                win.state.sync = True
                win.zone = -1
                prev._clicked(None, 1, x, y)
                out[name] = win.zone if not win.state.sync else None
            # the lobe outline really reaches 1.37 radii out and starts/ends on the circle
            import math
            radii = [math.hypot(x, y) for x, y in SpeakerPreview.SUPPORT]

            out["lobe_far"] = round(max(radii), 2)
            out["lobe_ends_on_circle"] = abs(radii[0] - 1.0) < 0.05 and abs(radii[-1] - 1.0) < 0.05

        out = self.run_app(script)
        from g560_lights import device
        self.assertEqual(out["left lobe"], device.ZONE_LEFT_FRONT)
        self.assertEqual(out["left grille"], device.ZONE_LEFT_REAR)
        self.assertEqual(out["right lobe"], device.ZONE_RIGHT_FRONT)
        self.assertEqual(out["right grille"], device.ZONE_RIGHT_REAR)
        self.assertIsNone(out["empty corner"])
        self.assertIsNone(out["gap beside the lobe"])
        self.assertAlmostEqual(out["lobe_far"], 1.35, delta=0.04)

        self.assertTrue(out["lobe_ends_on_circle"])

    def test_the_small_light_is_an_arch_of_even_thickness_with_no_circle_inside(self):
        def script(win, out):
            import math
            import cairo
            from g560_lights.app import SpeakerPreview as SP

            r, cx, base = 80.0, 150.0, 250.0  # draw one speaker big, on a black canvas, with only the small light lit
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 400)
            cr = cairo.Context(surf)
            cr.set_source_rgb(0, 0, 0)
            cr.paint()
            win.preview._satellite(cr, cx, base, r, False, (0, 0, 0), (0, 255, 0), False, False)
            surf.flush()
            data, stride = surf.get_data(), surf.get_stride()

            def bright_green(x, y):
                if not (0 <= x < 400 and 0 <= y < 400):
                    return False
                i = int(y) * stride + int(x) * 4  # BGRA
                return data[i + 1] > 200 and data[i] < 90 and data[i + 2] < 90

            p0, p1, p2, p3 = SP.SUPPORT_CURVE
            centre = (cx, base - r)
            widths = []
            for k in range(8):
                t = 0.30 + 0.07 * k  # the middle of the arch, clear of the big circle
                u = 1 - t
                x = u**3 * p0[0] + 3*u*u*t * p1[0] + 3*u*t*t * p2[0] + t**3 * p3[0]
                y = u**3 * p0[1] + 3*u*u*t * p1[1] + 3*u*t*t * p2[1] + t**3 * p3[1]
                dx = 3*u*u * (p1[0]-p0[0]) + 6*u*t * (p2[0]-p1[0]) + 3*t*t * (p3[0]-p2[0])
                dy = 3*u*u * (p1[1]-p0[1]) + 6*u*t * (p2[1]-p1[1]) + 3*t*t * (p3[1]-p2[1])
                n = math.hypot(dx, dy)
                nx, ny = -dy / n, dx / n
                px, py = centre[0] + x * r, centre[1] + y * r
                count = sum(1 for i in range(-160, 161) if bright_green(px + nx * i * 0.1, py + ny * i * 0.1))
                widths.append(count * 0.1)
            out["widths"] = widths
            out["expected"] = SP.ARCH_WIDTH * r
            lx, ly = centre[0] + 0.832 * r, centre[1] + 0.647 * r  # where the old small circle used to be
            out["old_circle_spot_is_lit"] = bright_green(lx, ly)
            out["lit_pixels_inside_the_arch"] = sum(1 for yy in range(int(ly - 12), int(ly + 12)) for xx in range(int(lx - 12), int(lx + 12)) if bright_green(xx, yy))

        out = self.run_app(script)
        widths, expected = out["widths"], out["expected"]
        self.assertGreater(min(widths), 0, "the arch was not found along the curve: %s" % widths)
        self.assertLess(max(widths) - min(widths), 1.2, "the arch changes thickness: %s" % [round(v, 1) for v in widths])
        self.assertAlmostEqual(sum(widths) / len(widths), expected, delta=0.2 * expected)
        self.assertFalse(out["old_circle_spot_is_lit"], "there is still a filled circle inside the arch")

    def test_from_and_to_do_not_resize_the_window(self):
        from gi.repository import Gtk

        def script(win, out):
            win.chips["fade"].set_active(True)
            measure = lambda: (win.get_height(), win.get_content().measure(Gtk.Orientation.VERTICAL, -1)[1],
                               win.color_box.get_allocated_height())
            win.slot_btns[0].set_active(True)
            out["from"] = measure()
            win.slot_btns[1].set_active(True)
            out["to_state"] = (win.zone_box.get_visible(), win.zone_box.get_sensitive(), win.zone_hint.get_label())
            out["to"] = measure()
            win.slot_btns[0].set_active(True)
            out["back"] = measure()

        out = self.run_app(script)
        self.assertEqual(out["from"], out["to"], "switching From -> To changed the layout size")
        self.assertEqual(out["from"], out["back"], "switching To -> From changed the layout size")
        self.assertEqual(out["to_state"][:2], (True, False))
        self.assertIn("same on every light", out["to_state"][2])

    def test_zone_buttons_use_the_real_names_in_order(self):
        def script(win, out):
            out["labels"] = [b.get_label() for b in win.zone_btns.values()]

        out = self.run_app(script)
        self.assertEqual(out["labels"], ["All", "Left front", "Left rear", "Right front", "Right rear"])

    def test_header_does_not_change_when_switching_tabs(self):
        from gi.repository import GLib

        app_out = {}
        os.environ["G560_LIGHTS_DRY_RUN"] = "1"
        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
        from g560_lights import app as appmod
        app = appmod.App(False)
        names = ["lights", "eq", "settings"]
        seen = {}

        def look(i):
            win = app.win
            win.show_tab(names[i])
            GLib.timeout_add(500, lambda: measure(i))
            return False

        def measure(i):
            win = app.win
            ok, bounds = win.switcher.compute_bounds(win)
            seen[names[i]] = (win.power_btn.get_visible(), round(bounds.get_x()), round(bounds.get_width()))
            if i + 1 < len(names):
                look(i + 1)
            else:
                app.quit()
            return False

        app.connect("activate", lambda *_: GLib.timeout_add(700, lambda: look(0)))
        app.run([])
        self.assertEqual(set(seen), set(names))
        for name, (power_visible, x, width) in seen.items():
            self.assertTrue(power_visible, "the power button must stay visible on the %s tab" % name)
        positions = {(x, w) for _, x, w in seen.values()}
        self.assertEqual(len(positions), 1, "the tabs moved between tabs: %s" % seen)

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
