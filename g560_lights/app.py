"""GTK 4 / libadwaita interface."""

import colorsys
import math
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import cairo  # noqa: E402
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, __version__, config, effects  # noqa: E402
from .device import ZONE_LEFT_FRONT, ZONE_LEFT_REAR, ZONE_NAMES, ZONE_RIGHT_FRONT, ZONE_RIGHT_REAR  # noqa: E402
from .engine import Engine, NullSpeakers  # noqa: E402
from .eq import EqService, NullEqService  # noqa: E402
from .eq_page import EqPage  # noqa: E402
from .settings_page import SettingsPage  # noqa: E402

PRESETS = [
    "ff0000", "ff6a00", "ffb000", "ffee00", "7cff00", "00ff4c", "00ffc8",
    "00e5ff", "0050ff", "7a1cff", "ff00d4", "ff4d94", "ffd9a0", "ffffff",
]

CSS = b"""
.preview { background: #15161a; border-radius: 14px; }
.chip { padding: 6px 14px; border-radius: 999px; min-height: 0; }
.chip:checked { background: @accent_bg_color; color: @accent_fg_color; }
.status-card { background: alpha(@warning_bg_color, 0.25); border-radius: 12px; padding: 10px 14px; }
.value-label { min-width: 34px; }
"""

def rgb01(color):
    return tuple(c / 255.0 for c in color)


# --------------------------------------------------------------------------
# Small custom widgets
# --------------------------------------------------------------------------
class ColorWheel(Gtk.DrawingArea):
    """Hue around the ring, saturation from centre to edge."""

    SIZE = 176

    def __init__(self, on_pick):
        super().__init__()
        self.on_pick = on_pick
        self.hue, self.sat = 0.5, 1.0
        self.set_content_width(self.SIZE)
        self.set_content_height(self.SIZE)
        self.set_halign(Gtk.Align.CENTER)
        self._surface = None
        self.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        self.add_controller(drag)
        self._origin = (0, 0)

    def set_color(self, color):
        h, s, _v = colorsys.rgb_to_hsv(*rgb01(color))
        if s > 0.01:  # grey/white keeps the previous hue
            self.hue = h
        self.sat = s
        self.queue_draw()

    def _build_surface(self):
        n = self.SIZE
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, n, n)
        buf = surface.get_data()
        r0 = n / 2.0
        for y in range(n):
            for x in range(n):
                dx, dy = x + 0.5 - r0, y + 0.5 - r0
                dist = math.hypot(dx, dy)
                edge = min(1.0, max(0.0, r0 - dist))
                if edge <= 0:
                    continue
                h = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
                r, g, b = colorsys.hsv_to_rgb(h, min(1.0, dist / (r0 - 1)), 1.0)
                i = (y * n + x) * 4
                buf[i : i + 4] = bytes((int(b * 255 * edge), int(g * 255 * edge), int(r * 255 * edge), int(255 * edge)))
        surface.mark_dirty()
        return surface

    def _draw(self, _area, cr, width, height):
        if self._surface is None:
            self._surface = self._build_surface()
        ox, oy = (width - self.SIZE) / 2, (height - self.SIZE) / 2
        cr.set_source_surface(self._surface, ox, oy)
        cr.paint()
        r0 = self.SIZE / 2.0
        angle = self.hue * 2 * math.pi
        px = ox + r0 + math.cos(angle) * self.sat * (r0 - 1)
        py = oy + r0 + math.sin(angle) * self.sat * (r0 - 1)
        cr.arc(px, py, 8, 0, 2 * math.pi)
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.set_line_width(4)
        cr.stroke_preserve()
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(2)
        cr.stroke()

    def _pick(self, x, y):
        w, h = self.get_width(), self.get_height()
        r0 = self.SIZE / 2.0
        dx, dy = x - w / 2.0, y - h / 2.0
        self.hue = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
        self.sat = min(1.0, math.hypot(dx, dy) / (r0 - 1))
        self.queue_draw()
        rgb = colorsys.hsv_to_rgb(self.hue, self.sat, 1.0)
        self.on_pick(tuple(int(round(c * 255)) for c in rgb))

    def _begin(self, _gesture, x, y):
        self._origin = (x, y)
        self._pick(x, y)

    def _update(self, _gesture, off_x, off_y):
        self._pick(self._origin[0] + off_x, self._origin[1] + off_y)


class Swatch(Gtk.DrawingArea):
    def __init__(self, color, on_click, size=26):
        super().__init__()
        self.color = color
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_draw_func(self._draw)
        self.set_cursor_from_name("pointer")
        self.set_tooltip_text("#" + config.to_hex(color))
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: on_click(self.color))
        self.add_controller(click)

    def _draw(self, _area, cr, w, h):
        r = min(w, h) / 2.0 - 1
        cr.arc(w / 2.0, h / 2.0, r, 0, 2 * math.pi)
        cr.set_source_rgb(*rgb01(self.color))
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.25)
        cr.set_line_width(1)
        cr.stroke()


def _fabric_tile():
    """A tiny dotted tile: the look of the speaker cloth."""
    tile = cairo.ImageSurface(cairo.FORMAT_ARGB32, 3, 3)
    c = cairo.Context(tile)
    c.arc(1.5, 1.5, 0.55, 0, 2 * math.pi)
    c.set_source_rgba(0, 0, 0, 0.55)
    c.fill()
    pattern = cairo.SurfacePattern(tile)
    pattern.set_extend(cairo.EXTEND_REPEAT)
    return pattern


class SpeakerPreview(Gtk.DrawingArea):
    """The two satellites, drawn like the real G560, in the colors being sent to the speakers.

    Each satellite is a round cloth grille. The REAR light is the big round glow and rim around the
    grille; the FRONT light is the small lobe growing out of it on the lower inner side.
    """

    # The small (front) light is a lobe that grows out of the big circle on its lower inner side. This
    # outline was drawn by hand (averaged from the user's sketch of both speakers) in units of the big
    # circle's radius, for the left speaker: +x is towards the middle, +y is down. It starts on the
    # circle about 11 degrees above 3 o'clock, swells to 1.37 radii at 34 degrees below it, and rejoins
    # the circle at the very bottom. The right speaker is its mirror image.
    LOBE = [
        (0.982, -0.190), (1.016, -0.109), (1.041, -0.025), (1.061, 0.061),
        (1.081, 0.146), (1.103, 0.232), (1.127, 0.316), (1.152, 0.400),
        (1.166, 0.487), (1.171, 0.575), (1.167, 0.662), (1.146, 0.747),
        (1.094, 0.817), (1.026, 0.873), (0.947, 0.912), (0.864, 0.939),
        (0.778, 0.960), (0.692, 0.976), (0.605, 0.986), (0.517, 0.993),
        (0.429, 0.999), (0.342, 1.008), (0.255, 1.017), (0.167, 1.018),
        (0.079, 1.013), (-0.007, 1.000),
    ]
    LABEL_GAP = 28  # px between the speakers' base and the LEFT / RIGHT labels

    def __init__(self, app_window):
        super().__init__()
        self.win = app_window
        self.set_content_height(196)
        self.set_hexpand(True)
        self.add_css_class("preview")
        self.set_draw_func(self._draw)
        self.hit = {}  # zone -> test(x, y) in widget coordinates
        self._fabric = _fabric_tile()
        click = Gtk.GestureClick()
        click.connect("released", self._clicked)
        self.add_controller(click)

    @classmethod
    def _lobe_path(cls, cr):
        pts = cls.LOBE
        n = len(pts)
        cr.move_to(*pts[0])
        for i in range(n - 1):  # Catmull-Rom spline as Bezier segments
            p0, p1, p2, p3 = pts[max(i - 1, 0)], pts[i], pts[i + 1], pts[min(i + 2, n - 1)]
            cr.curve_to(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6,
                        p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6, p2[0], p2[1])
        cr.close_path()  # the straight edge back is inside the big circle, hidden behind it

    def _satellite(self, cr, cx, base_y, r, mirror, ring, pod, ring_sel, pod_sel):
        lit_ring, lit_pod = max(ring) > 8, max(pod) > 8
        cr.save()
        cr.translate(cx, base_y - r)
        cr.scale(-r if mirror else r, r)
        px = 1.0 / r

        # ring light halo: round, and behind everything else (including the pod lens)
        if lit_ring:
            halo = cairo.RadialGradient(-0.1, -0.1, 0.8, -0.1, -0.1, 1.75)
            halo.add_color_stop_rgba(0, *rgb01(ring), 0.55)
            halo.add_color_stop_rgba(1, *rgb01(ring), 0.0)
            cr.set_source(halo)
            cr.arc(-0.1, -0.1, 1.75, 0, 2 * math.pi)
            cr.fill()

        # the small (front) light: a lobe growing out of the big circle on its lower inner side
        if lit_pod:
            cx0, cy0 = 0.95, 0.62
            glow = cairo.RadialGradient(cx0, cy0, 0.15, cx0, cy0, 1.0)
            glow.add_color_stop_rgba(0, *rgb01(pod), 0.6)
            glow.add_color_stop_rgba(1, *rgb01(pod), 0.0)
            cr.set_source(glow)
            cr.arc(cx0, cy0, 1.0, 0, 2 * math.pi)
            cr.fill()
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            for width, alpha in ((0.34, 0.07), (0.20, 0.12), (0.09, 0.22)):  # soft bloom around the edge
                self._lobe_path(cr)
                cr.set_source_rgba(*rgb01(pod), alpha)
                cr.set_line_width(width)
                cr.stroke()
        self._lobe_path(cr)
        cr.set_source_rgb(*rgb01(pod)) if lit_pod else cr.set_source_rgb(0.15, 0.16, 0.19)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.35)
        cr.set_line_width(1.6 * px)
        cr.stroke()
        if pod_sel:
            self._lobe_path(cr)
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.set_line_width(2.2 * px)
            cr.stroke()

        # the dark housing ring and the glowing rim around the grille
        cr.arc(0, 0, 1.07, 0, 2 * math.pi)  # the dark housing ring around the face
        cr.set_source_rgb(0.063, 0.067, 0.078)
        cr.fill()
        if lit_ring:
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            for width, alpha in ((0.34, 0.14), (0.18, 0.3), (0.075, 1.0)):  # bloom, then the bright core
                cr.arc(0, 0, 1.06, math.radians(90), math.radians(349))  # all but the lower inner quarter, where the small light's lobe grows out
                cr.set_source_rgba(*rgb01(ring), alpha)
                cr.set_line_width(width)
                cr.stroke()
            cr.arc(0, 0, 1.06, 0, 2 * math.pi)
            cr.set_source_rgba(*rgb01(ring), 0.35)
            cr.set_line_width(0.035)
            cr.stroke()
        else:
            cr.arc(0, 0, 1.06, 0, 2 * math.pi)
            cr.set_source_rgba(1, 1, 1, 0.08)
            cr.set_line_width(0.04)
            cr.stroke()
        if ring_sel:
            cr.arc(0, 0, 1.2, 0, 2 * math.pi)
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.set_line_width(2.2 * px)
            cr.stroke()

        # the cloth grille
        face = cairo.RadialGradient(-0.25, -0.35, 0.1, 0, 0, 1.0)
        face.add_color_stop_rgb(0, 0.26, 0.275, 0.31)
        face.add_color_stop_rgb(1, 0.085, 0.09, 0.11)
        cr.arc(0, 0, 1.0, 0, 2 * math.pi)
        cr.set_source(face)
        cr.fill_preserve()
        cr.save()
        cr.clip()
        cr.identity_matrix()  # device space for the cloth dots
        cr.set_source(self._fabric)
        cr.paint_with_alpha(0.55)
        cr.restore()
        cr.arc(0, 0, 0.985, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, 0.17)
        cr.set_line_width(1.6 * px)
        cr.stroke()
        cr.arc(0, 0, 0.76, math.radians(205), math.radians(262))
        cr.set_source_rgba(1, 1, 1, 0.16)
        cr.set_line_width(2.4 * px)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.stroke()
        cr.restore()

    def _draw(self, _area, cr, w, h):
        colors = self.win.engine.preview(self.win.state)
        if not self.win.state.power:
            colors = [(0, 0, 0)] * 4
        editing = self.win.editing_zone()
        self.hit = {}
        r = min(53.0, w * 0.1)
        label_space = 44
        cy = (h - label_space) / 2.0 + 4
        base_y = cy + r
        left_x, right_x = w * 0.3, w * 0.7

        for side, cx in (("left", left_x), ("right", right_x)):
            mirror = side == "right"
            ring_zone = ZONE_RIGHT_REAR if mirror else ZONE_LEFT_REAR  # the big light around the grille
            pod_zone = ZONE_RIGHT_FRONT if mirror else ZONE_LEFT_FRONT  # the small round light
            self._satellite(cr, cx, base_y, r, mirror, colors[ring_zone], colors[pod_zone],
                            editing == ring_zone, editing == pod_zone)
            sx = -1 if mirror else 1
            outline = [(cx + sx * x * r, cy + y * r) for x, y in self.LOBE]
            self.hit[pod_zone] = lambda x, y, o=outline, c=(cx, cy), rr=r: (
                math.hypot(x - c[0], y - c[1]) > 0.98 * rr and self._inside(o, x, y))
            self.hit[ring_zone] = lambda x, y, c=(cx, cy), rr=r: math.hypot(x - c[0], y - c[1]) <= 1.14 * rr

        cr.set_source_rgba(1, 1, 1, 0.42)
        cr.select_font_face("sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(11)
        for text, cx in (("LEFT", left_x), ("RIGHT", right_x)):
            ext = cr.text_extents(text)
            cr.move_to(cx - ext.width / 2, base_y + self.LABEL_GAP)
            cr.show_text(text)

    @staticmethod
    def _inside(polygon, x, y):
        """Point-in-polygon (ray casting)."""
        inside = False
        j = len(polygon) - 1
        for i, (xi, yi) in enumerate(polygon):
            xj, yj = polygon[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside

    def _clicked(self, _gesture, _n, x, y):
        # the small lights (lobes) first: they only overlap the big circle's area outside its edge
        ordered = sorted(self.hit.items(), key=lambda kv: kv[0] not in (ZONE_LEFT_FRONT, ZONE_RIGHT_FRONT))
        for zone, test in ordered:
            if test(x, y):
                self.win.select_zone(zone)
                return


# --------------------------------------------------------------------------
# Window
# --------------------------------------------------------------------------
class MainWindow(Adw.ApplicationWindow):
    ZONE_HINT = "Set every light at once, or pick one (you can also click a light in the preview)."
    ZONE_HINT_SHARED = "This second color is the same on every light."

    def __init__(self, app):
        super().__init__(application=app, title="G560 Lights")
        self.app = app
        self.state = config.load()
        self.engine = app.engine
        self.zone = ZONE_LEFT_FRONT  # zone being edited when not synced
        self.slot = 0  # which of the two effect colors is being edited
        self._building = True
        self._save_id = 0
        self._tick_id = 0
        self.set_default_size(560, -1)  # as tall as the content needs
        self.set_icon_name("g560-lights")

        self.connect("close-request", self._on_close)
        self.connect("notify::visible", lambda *_: self._sync_timer())
        self._build()
        self._building = False
        self._refresh()
        self.engine.update(self.state)

    # ---- construction -----------------------------------------------------
    def _build(self):
        header = Adw.HeaderBar()
        self.power_btn = Gtk.ToggleButton(icon_name="system-shutdown-symbolic", tooltip_text="Speaker lights on / off")
        self.power_btn.connect("toggled", self._on_power)
        header.pack_start(self.power_btn)
        menu = Gio.Menu()
        menu.append("About G560 Lights", "app.about")
        menu.append("Quit", "app.quit")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        page.set_margin_top(12)
        page.set_margin_bottom(24)

        self.status = Gtk.Label(wrap=True, xalign=0)
        self.status_box = Gtk.Box()
        self.status_box.add_css_class("status-card")
        self.status_box.append(self.status)
        self.status_rev = Gtk.Revealer(child=self.status_box, reveal_child=False)
        page.append(self.status_rev)

        self.preview = SpeakerPreview(self)
        page.append(self.preview)

        page.append(self._build_effects())
        page.append(self._build_sliders())
        page.append(self._build_colors())

        self.eq_page = EqPage(self.state, self._eq_changed, on_reconnect=self.app.eqsvc.reconnect)

        self.stack = Adw.ViewStack(vexpand=True)
        self.stack.add_titled(self._scrolled(page), "lights", "Lights").set_icon_name("display-brightness-symbolic")
        self.stack.add_titled(self._scrolled(self.eq_page), "eq", "Equalizer").set_icon_name("audio-volume-high-symbolic")
        self.settings_page = SettingsPage(on_about=lambda: self.app.activate_action("about", None))
        self.stack.add_titled(self._scrolled(self.settings_page), "settings", "Settings").set_icon_name("emblem-system-symbolic")
        self.switcher = Adw.ViewSwitcherTitle(stack=self.stack, title="G560")
        header.set_title_widget(self.switcher)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(header)
        outer.append(self.stack)
        self.set_content(outer)

    @staticmethod
    def _screen_limit():
        """Tallest the content area may grow before it scrolls: the screen minus bars."""
        try:
            monitors = Gdk.Display.get_default().get_monitors()
            return max(600, monitors.get_item(0).get_geometry().height - 160)
        except (AttributeError, TypeError):
            return 900

    def _scrolled(self, child):
        clamp = Adw.Clamp(maximum_size=560, child=child)
        clamp.set_margin_start(16)
        clamp.set_margin_end(16)
        return Gtk.ScrolledWindow(
            child=clamp, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True, max_content_height=self._screen_limit())

    def show_tab(self, name):
        if name in ("lights", "eq", "settings"):
            self.stack.set_visible_child_name(name)

    def _eq_changed(self):
        self.app.eqsvc.set(self.state.eq_enabled, self.state.eq_gains, self.state.eq_preamp)
        self._changed(push=False)

    def _build_effects(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        title = Gtk.Label(label="Effect", xalign=0)
        title.add_css_class("heading")
        box.append(title)
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                           min_children_per_line=2, column_spacing=8, row_spacing=8, homogeneous=True)
        self.chips = {}
        group = None
        for info in effects.EFFECTS:
            chip = Gtk.ToggleButton(label=info.label)
            chip.add_css_class("chip")
            if group is None:
                group = chip
            else:
                chip.set_group(group)
            chip.connect("toggled", self._on_effect, info.id)
            self.chips[info.id] = chip
            flow.append(chip)
        box.append(flow)
        self.blurb = Gtk.Label(wrap=True, xalign=0)
        self.blurb.add_css_class("dim-label")
        self.blurb.add_css_class("caption")
        box.append(self.blurb)
        return box

    def _build_colors(self):
        self.color_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.color_title = Gtk.Label(label="Color", xalign=0)
        self.color_title.add_css_class("heading")
        self.color_box.append(self.color_title)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("card")
        for side in ("top", "bottom", "start", "end"):
            getattr(card, "set_margin_" + side)(0)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ("top", "bottom", "start", "end"):
            getattr(inner, "set_margin_" + side)(14)
        card.append(inner)

        # slot selector (two-color effects)
        self.slot_box = Gtk.Box(spacing=0, halign=Gtk.Align.CENTER)
        self.slot_box.add_css_class("linked")
        self.slot_btns = []
        for i in (0, 1):
            b = Gtk.ToggleButton(label="Color %d" % (i + 1))
            if i:
                b.set_group(self.slot_btns[0])
            b.connect("toggled", self._on_slot, i)
            self.slot_btns.append(b)
            self.slot_box.append(b)
        inner.append(self.slot_box)

        # which light(s) the color goes to
        self.zone_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        zone_row = Gtk.Box(spacing=0, halign=Gtk.Align.CENTER)
        zone_row.add_css_class("linked")
        self.zone_btns = {}
        first = None
        for key, label in (("all", "All"), (ZONE_LEFT_FRONT, "Left front"), (ZONE_LEFT_REAR, "Left rear"),
                           (ZONE_RIGHT_FRONT, "Right front"), (ZONE_RIGHT_REAR, "Right rear")):
            btn = Gtk.ToggleButton(label=label)
            if first is None:
                first = btn
            else:
                btn.set_group(first)
            btn.connect("toggled", self._on_zone_button, key)
            self.zone_btns[key] = btn
            zone_row.append(btn)
        self.zone_box.append(zone_row)
        self.zone_hint = Gtk.Label(label=self.ZONE_HINT)
        self.zone_hint.add_css_class("dim-label")
        self.zone_hint.add_css_class("caption")
        self.zone_box.append(self.zone_hint)
        inner.append(self.zone_box)

        row = Gtk.Box(spacing=18)
        self.wheel = ColorWheel(self._on_pick)
        row.append(self.wheel)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, hexpand=True, valign=Gtk.Align.CENTER)
        self.hex = Gtk.Entry(max_length=7, placeholder_text="RRGGBB", width_chars=8)
        self.hex.connect("changed", self._on_hex)
        side.append(Gtk.Label(label="Hex", xalign=0))
        side.append(self.hex)
        self.blend_row = Gtk.Box(spacing=8)  # Music only
        self.blend_row.append(Gtk.Label(label="Shift to a second color when loud", xalign=0, hexpand=True, wrap=True))
        self.blend_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.blend_switch.connect("notify::active", self._on_blend)
        self.blend_row.append(self.blend_switch)
        side.append(self.blend_row)
        row.append(side)
        inner.append(row)

        inner.append(self._swatch_flow(PRESETS, "Presets", per_line=7))
        self.recent_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.append(self.recent_holder)

        self.color_box.append(card)
        return self.color_box

    def _swatch_flow(self, hexes, title, per_line=14):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        label = Gtk.Label(label=title, xalign=0)
        label.add_css_class("caption")
        label.add_css_class("dim-label")
        box.append(label)
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=per_line,
                           min_children_per_line=min(per_line, len(hexes)),
                           column_spacing=6, row_spacing=6, homogeneous=True)
        for h in hexes:
            flow.append(Swatch(config.from_hex(h), self._set_color))
        box.append(flow)
        return box

    def _build_sliders(self):
        self.slider_group = Adw.PreferencesGroup(title="Intensity")
        _, self.brightness, self.brightness_val = self._slider_row("Brightness", self._on_brightness)
        self.speed_row, self.speed, self.speed_val = self._slider_row("Speed", self._on_speed)
        return self.slider_group

    def _slider_row(self, title, handler):
        row = Adw.ActionRow(title=title)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 100, 1)
        scale.set_size_request(260, -1)
        scale.set_valign(Gtk.Align.CENTER)
        scale.set_draw_value(False)
        value = Gtk.Label(label="0")
        value.add_css_class("value-label")
        value.add_css_class("dim-label")
        scale.connect("value-changed", handler)
        box = Gtk.Box(spacing=8)
        box.append(scale)
        box.append(value)
        row.add_suffix(box)
        self.slider_group.add(row)
        return row, scale, value

    # ---- state -> widgets -------------------------------------------------
    def _two_colors(self):
        s = self.state
        return effects.BY_ID[s.effect].colors == 2 or (s.effect == "audio" and s.audio_blend)

    def editing_zone(self):
        """The single light being edited (highlighted in the preview), or None."""
        s = self.state
        if effects.BY_ID[s.effect].per_zone and not s.sync and self.slot == 0:
            return self.zone
        return None

    def _current_color(self):
        s = self.state
        if self.slot == 1 and self._two_colors():
            return s.colors[1]
        return s.zone_colors[0 if s.sync else self.zone]

    def _refresh(self):
        """Push self.state into every widget without triggering handlers."""
        was, self._building = self._building, True
        s = self.state
        info = effects.BY_ID[s.effect]
        two = self._two_colors()
        self.chips[s.effect].set_active(True)
        self.power_btn.set_active(s.power)
        self.blurb.set_label(info.blurb + ("" if info.hardware else "\nThis one is animated by the app, which keeps running in the background when you close the window."))

        self.color_box.set_visible(info.colors > 0)
        self.slot_box.set_visible(two)
        for i, b in enumerate(self.slot_btns):
            b.set_label(info.color_labels[i])
        if self.slot >= (2 if two else 1):
            self.slot = 0
        self.slot_btns[self.slot].set_active(True)
        # The light selector never hides, so switching between From and To (or Color and Loud color)
        # cannot change the height of the window; it is just greyed out for the shared second color.
        self.zone_box.set_visible(info.per_zone)
        self.zone_box.set_sensitive(self.slot == 0)
        self.zone_hint.set_label(self.ZONE_HINT if self.slot == 0 else self.ZONE_HINT_SHARED)
        self.zone_btns["all" if s.sync else self.zone].set_active(True)
        self.blend_row.set_visible(s.effect == "audio")
        self.blend_switch.set_active(s.audio_blend)
        self.color_title.set_label("Colors" if two else "Color")

        color = self._current_color()
        self.wheel.set_color(color)
        self.hex.set_text(config.to_hex(color))

        self.speed_row.set_visible(bool(info.speed_label))
        self.speed_row.set_title(info.speed_label or "Speed")
        self.speed.set_value(s.speed)
        self.speed_val.set_label(str(s.speed))
        self.brightness.set_value(s.brightness)
        self.brightness_val.set_label(str(s.brightness))
        self._refresh_recent()
        self._building = was
        GLib.idle_add(self._fit_height)

    def _fit_height(self):
        """Grow the window downward when the content needs more room (never shrinks it)."""
        try:
            needed = self.get_content().measure(Gtk.Orientation.VERTICAL, -1)[1]
            monitors = Gdk.Display.get_default().get_monitors()
            limit = monitors.get_item(0).get_geometry().height - 100
        except (AttributeError, TypeError):
            return False
        needed = min(needed, limit)
        if needed > self.get_height() + 2:
            self.set_default_size(self.get_width() or 560, needed)
        return False

    def _refresh_recent(self):
        child = self.recent_holder.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.recent_holder.remove(child)
            child = nxt
        if self.state.recent:
            self.recent_holder.append(self._swatch_flow([config.to_hex(c) for c in self.state.recent], "Recent", per_line=8))

    def _changed(self, push=True):
        if self._building:
            return
        if push:
            self.engine.update(self.state)
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(400, self._save_now)

    def _save_now(self):
        self._save_id = 0
        config.save(self.state)
        return False

    # ---- handlers ---------------------------------------------------------
    def _on_effect(self, chip, eid):
        if self._building or not chip.get_active():
            return
        self.state.switch_effect(eid)  # each effect keeps its own colors
        self._refresh()
        self._changed()
        self._sync_timer()

    def _on_power(self, btn):
        if self._building:
            return
        self.state.power = btn.get_active()
        self._changed()

    def _on_slot(self, btn, i):
        if self._building or not btn.get_active():
            return
        self.slot = i
        self._refresh()

    def select_zone(self, zone):
        """A light was clicked in the preview."""
        if not effects.BY_ID[self.state.effect].per_zone:
            return
        self.slot = 0
        self.state.sync = False
        self.zone = zone
        self._refresh()

    def _on_zone_button(self, btn, key):
        if self._building or not btn.get_active():
            return
        if key == "all":
            self.state.sync = True
        else:
            self.state.sync = False
            self.zone = key
        self._refresh()

    def _on_blend(self, switch, _pspec):
        if self._building:
            return
        self.state.audio_blend = switch.get_active()
        self._refresh()
        self._changed()

    def _store_color(self, color):
        s = self.state
        if self.slot == 1 and self._two_colors():
            s.colors[1] = color
        elif s.sync:
            s.zone_colors = [color] * 4
        else:
            s.zone_colors[self.zone] = color

    def _on_pick(self, color):
        self._store_color(color)
        was, self._building = self._building, True
        self.hex.set_text(config.to_hex(color))
        self._building = was
        self._changed()

    def _set_color(self, color):
        """Swatch click or hex entry: set and remember."""
        self._store_color(color)
        recent = [c for c in self.state.recent if c != color]
        self.state.recent = ([color] + recent)[:8]
        self._refresh()
        self._changed()

    def _on_hex(self, entry):
        if self._building:
            return
        try:
            color = config.from_hex(entry.get_text())
        except ValueError:
            return
        self._store_color(color)
        was, self._building = self._building, True
        self.wheel.set_color(color)
        self._building = was
        self._changed()

    def _on_brightness(self, scale):
        v = int(scale.get_value())
        self.brightness_val.set_label(str(v))
        if not self._building:
            self.state.brightness = v
            self._changed()

    def _on_speed(self, scale):
        v = int(scale.get_value())
        self.speed_val.set_label(str(v))
        if not self._building:
            self.state.speed = v
            self._changed()

    def show_status(self, ok, message):
        self.status.set_label(message)
        self.status_rev.set_reveal_child(not ok)

    # ---- preview timer / closing -----------------------------------------
    def _sync_timer(self):
        want = self.get_visible()
        if want and not self._tick_id:
            self._tick_id = GLib.timeout_add(40, self._tick)
        elif not want and self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0

    def _tick(self):
        self.preview.queue_draw()
        return True

    def _on_close(self, _win):
        info = effects.BY_ID[self.state.effect]
        keep_running = self.state.power and not info.hardware
        if keep_running:
            self.set_visible(False)  # keep animating in the background
            return True
        self._save_now()
        self.app.quit()
        return True


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------
class App(Adw.Application):
    def __init__(self, background, tab=None):
        # G560_LIGHTS_DRY_RUN=1: no speakers, no audio changes, and a separate app id so a
        # test build never hands off to (or disturbs) the copy you are really using.
        self.dry_run = os.environ.get("G560_LIGHTS_DRY_RUN") == "1"
        super().__init__(application_id=APP_ID + (".Dry" if self.dry_run else ""))
        self.background = background
        self.tab = tab
        self.engine = Engine(device=NullSpeakers() if self.dry_run else None, on_status=self._on_status)
        self.eqsvc = NullEqService() if self.dry_run else EqService(
            on_status=self._on_eq_status, on_crash_loop=self._on_eq_crash_loop)
        self.win = None
        self._last_status = (True, "")

    def _on_status(self, ok, message):
        self._last_status = (ok, message)
        GLib.idle_add(lambda: self.win and self.win.show_status(ok, message) and False)

    def _on_eq_status(self, ok, message):
        GLib.idle_add(self._show_eq_status, ok, message)

    def _on_eq_crash_loop(self, message):
        GLib.idle_add(self._eq_gave_up, message)

    def _eq_gave_up(self, message):
        """The audio server kept restarting with the EQ on: switch it off and say why."""
        if self.win:
            self.win.state.eq_enabled = False
            self.win.eq_page.refresh()
            self.win.eq_page.show_notice(message)
            self.win._changed(push=False)
        self.eqsvc.set(False, self.win.state.eq_gains if self.win else [0.0] * 10)
        return False

    def _show_eq_status(self, ok, message):
        if self.win:
            self.win.eq_page.show_status(ok, message)
        return False

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        try:
            provider.load_from_data(CSS)
        except TypeError:
            provider.load_from_data(CSS, -1)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        for name, handler in (("about", self._about), ("quit", lambda *_: self._quit())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
        self.hold()

    def do_activate(self):
        if self.win is None:
            self.win = MainWindow(self)
            self.engine.start(self.win.state)
            self.eqsvc.start(self.win.state.eq_enabled, self.win.state.eq_gains, self.win.state.eq_preamp)
            if self.tab:
                self.win.show_tab(self.tab)
            if self.background:
                self.background = False
                return
        self.win.show_status(*self._last_status)
        self.win.present()

    def _quit(self):
        if self.win:
            self.win._save_now()
        self.engine.stop()
        self.eqsvc.stop()
        self.quit()

    def do_shutdown(self):
        self.engine.stop()
        self.eqsvc.stop()
        Adw.Application.do_shutdown(self)

    def _about(self, *_):
        dialog = Gtk.AboutDialog(
            transient_for=self.win, modal=True, program_name="G560 Lights", version=__version__,
            comments="Lighting control for Logitech G560 speakers on Linux.",
            logo_icon_name="g560-lights", license_type=Gtk.License.MIT_X11,
        )
        dialog.present()


def run(background=False, tab=None):
    app = App(background, tab)
    return app.run([sys.argv[0]])
