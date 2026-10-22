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

AUTOSTART_FILE = os.path.expanduser("~/.config/autostart/g560-lights.desktop")
AUTOSTART_TEXT = """[Desktop Entry]
Type=Application
Name=G560 Lights
Exec=g560-lights --background
Icon=g560-lights
X-GNOME-Autostart-enabled=true
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


class SpeakerPreview(Gtk.DrawingArea):
    """The two speakers, drawn with the colors being sent right now."""

    def __init__(self, app_window):
        super().__init__()
        self.win = app_window
        self.set_content_height(150)
        self.set_hexpand(True)
        self.add_css_class("preview")
        self.set_draw_func(self._draw)
        self.hit = {}  # zone -> (x, y, r)
        click = Gtk.GestureClick()
        click.connect("released", self._clicked)
        self.add_controller(click)

    def _glow(self, cr, x, y, r, color, selected):
        cr.save()
        if max(color) > 8:
            grad = cairo.RadialGradient(x, y, r * 0.2, x, y, r * 2.4)
            grad.add_color_stop_rgba(0, *rgb01(color), 0.75)
            grad.add_color_stop_rgba(1, *rgb01(color), 0.0)
            cr.set_source(grad)
            cr.arc(x, y, r * 2.4, 0, 2 * math.pi)
            cr.fill()
        cr.arc(x, y, r, 0, 2 * math.pi)
        cr.set_source_rgb(*rgb01(color)) if max(color) > 8 else cr.set_source_rgb(0.16, 0.17, 0.2)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.9 if selected else 0.18)
        cr.set_line_width(2.5 if selected else 1)
        cr.stroke()
        cr.restore()

    def _bar(self, cr, x, y, bw, bh, color, selected):
        lit = max(color) > 8
        cr.save()
        if lit:
            grad = cairo.RadialGradient(x, y, 2, x, y, bw * 0.8)
            grad.add_color_stop_rgba(0, *rgb01(color), 0.55)
            grad.add_color_stop_rgba(1, *rgb01(color), 0.0)
            cr.set_source(grad)
            cr.arc(x, y, bw * 0.8, 0, 2 * math.pi)
            cr.fill()
        r = bh / 2.0
        cr.new_sub_path()
        cr.arc(x + bw / 2 - r, y, r, -math.pi / 2, math.pi / 2)
        cr.arc(x - bw / 2 + r, y, r, math.pi / 2, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgb(*rgb01(color)) if lit else cr.set_source_rgb(0.16, 0.17, 0.2)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.9 if selected else 0.18)
        cr.set_line_width(2.5 if selected else 1)
        cr.stroke()
        cr.restore()

    def _draw(self, _area, cr, w, h):
        colors = self.win.engine.preview(self.win.state)
        if not self.win.state.power:
            colors = [(0, 0, 0)] * 4
        editing = self.win.editing_zone()
        self.hit = {}
        sp_w, sp_h = min(150, w * 0.27), min(130, h - 28)
        centers = {"left": w * 0.28, "right": w * 0.72}
        top = (h - sp_h) / 2
        for side, cx in centers.items():
            x0 = cx - sp_w / 2
            cr.save()
            cr.new_sub_path()
            rad = 16
            cr.arc(x0 + sp_w - rad, top + rad, rad, -math.pi / 2, 0)
            cr.arc(x0 + sp_w - rad, top + sp_h - rad, rad, 0, math.pi / 2)
            cr.arc(x0 + rad, top + sp_h - rad, rad, math.pi / 2, math.pi)
            cr.arc(x0 + rad, top + rad, rad, math.pi, 3 * math.pi / 2)
            cr.close_path()
            cr.set_source_rgb(0.1, 0.105, 0.125)
            cr.fill_preserve()
            cr.set_source_rgba(1, 1, 1, 0.08)
            cr.stroke()
            cr.restore()
            front = ZONE_LEFT_FRONT if side == "left" else ZONE_RIGHT_FRONT
            rear = ZONE_LEFT_REAR if side == "left" else ZONE_RIGHT_REAR
            fx, fy, fr = cx, top + sp_h * 0.62, sp_h * 0.2
            rx, ry = cx, top + sp_h * 0.2
            self._glow(cr, fx, fy, fr, colors[front], editing == front)
            self._bar(cr, rx, ry, sp_w * 0.5, 9, colors[rear], editing == rear)
            self.hit[front] = (fx, fy, fr * 1.4)
            self.hit[rear] = (rx, ry, sp_w * 0.3)
        cr.set_source_rgba(1, 1, 1, 0.35)
        cr.select_font_face("sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(11)
        for text, cx in (("LEFT", centers["left"]), ("RIGHT", centers["right"])):
            ext = cr.text_extents(text)
            cr.move_to(cx - ext.width / 2, h - 6)
            cr.show_text(text)

    def _clicked(self, _gesture, _n, x, y):
        for zone, (cx, cy, r) in self.hit.items():
            if math.hypot(x - cx, y - cy) <= r:
                self.win.select_zone(zone)
                return


# --------------------------------------------------------------------------
# Window
# --------------------------------------------------------------------------
class MainWindow(Adw.ApplicationWindow):
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
        self.power_btn = Gtk.ToggleButton(icon_name="system-shutdown-symbolic", tooltip_text="Lights on / off")
        self.power_btn.connect("toggled", self._on_power)
        header.pack_start(self.power_btn)
        menu = Gio.Menu()
        menu.append("Start at login", "app.autostart")
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

        self.eq_page = EqPage(self.state, self._eq_changed)

        self.stack = Adw.ViewStack(vexpand=True)
        self.stack.add_titled(self._scrolled(page), "lights", "Lights").set_icon_name("display-brightness-symbolic")
        self.stack.add_titled(self._scrolled(self.eq_page), "eq", "Equalizer").set_icon_name("audio-volume-high-symbolic")
        self.stack.connect("notify::visible-child-name", self._on_tab)
        header.set_title_widget(Adw.ViewSwitcherTitle(stack=self.stack, title="G560"))

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
        if name in ("lights", "eq"):
            self.stack.set_visible_child_name(name)

    def _on_tab(self, *_):
        self.power_btn.set_visible(self.stack.get_visible_child_name() == "lights")

    def _eq_changed(self):
        self.app.eqsvc.set(self.state.eq_enabled, self.state.eq_gains)
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
        for key, label in (("all", "All"), (ZONE_LEFT_REAR, "Left rear"), (ZONE_RIGHT_REAR, "Right rear"),
                           (ZONE_LEFT_FRONT, "Left front"), (ZONE_RIGHT_FRONT, "Right front")):
            btn = Gtk.ToggleButton(label=label)
            if first is None:
                first = btn
            else:
                btn.set_group(first)
            btn.connect("toggled", self._on_zone_button, key)
            self.zone_btns[key] = btn
            zone_row.append(btn)
        self.zone_box.append(zone_row)
        hint = Gtk.Label(label="Set every light at once, or pick one (you can also click a light in the preview).")
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        self.zone_box.append(hint)
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
        self.zone_box.set_visible(info.per_zone and self.slot == 0)
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
        self.state.effect = eid
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
        self.eqsvc = NullEqService() if self.dry_run else EqService(on_status=self._on_eq_status)
        self.win = None
        self._last_status = (True, "")

    def _on_status(self, ok, message):
        self._last_status = (ok, message)
        GLib.idle_add(lambda: self.win and self.win.show_status(ok, message) and False)

    def _on_eq_status(self, ok, message):
        GLib.idle_add(self._show_eq_status, ok, message)

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
        auto = Gio.SimpleAction.new_stateful("autostart", None, GLib.Variant.new_boolean(os.path.exists(AUTOSTART_FILE)))
        auto.connect("change-state", self._toggle_autostart)
        self.add_action(auto)
        self.hold()

    def do_activate(self):
        if self.win is None:
            self.win = MainWindow(self)
            self.engine.start(self.win.state)
            self.eqsvc.start(self.win.state.eq_enabled, self.win.state.eq_gains)
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

    def _toggle_autostart(self, action, value):
        action.set_state(value)
        try:
            if value.get_boolean():
                os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
                with open(AUTOSTART_FILE, "w") as f:
                    f.write(AUTOSTART_TEXT)
            elif os.path.exists(AUTOSTART_FILE):
                os.unlink(AUTOSTART_FILE)
        except OSError as e:
            print("Could not change autostart: %s" % e, file=sys.stderr)

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
