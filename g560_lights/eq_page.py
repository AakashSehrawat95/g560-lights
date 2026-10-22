"""The Equalizer tab."""

import math

import cairo
from gi.repository import Adw, GLib, Gtk

from . import eq

LABELS = ("31", "63", "125", "250", "500", "1k", "2k", "4k", "8k", "16k")
HINTS = {
    "Flat": "No change to the sound",
    "Loudness": "More bass and treble: keeps music full at low volume",
    "Bass boost": "Heavier low end",
    "Less bass": "Tames boomy rooms and desk reflections",
    "Treble boost": "Brighter, more detail",
    "Vocal": "Clearer voices and podcasts",
    "Movie": "Deep effects with clear dialogue",
    "Gaming": "Brings out footsteps and cues",
    "Rock": "Punchy lows and bright highs",
    "Pop": "Forward mids for vocals",
    "Jazz": "Warm and open",
    "Classical": "Gentle, natural balance",
    "Electronic": "Strong bass and sparkle",
}
APPLY_INTERVAL_MS = 300  # while dragging, send changes at most this often
ACCENT_FALLBACK = (0.91, 0.33, 0.13)


class ResponseCurve(Gtk.DrawingArea):
    """The EQ curve, drawn from the same maths that goes to the audio server."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.set_content_height(130)
        self.add_css_class("preview")
        self.set_draw_func(self._draw)

    def _accent(self):
        ok, rgba = self.get_style_context().lookup_color("accent_bg_color")
        return (rgba.red, rgba.green, rgba.blue) if ok else ACCENT_FALLBACK

    def _draw(self, _area, cr, w, h):
        pad_x, pad_y = 14, 14
        lo, hi = math.log2(20), math.log2(20000)
        px = lambda f: pad_x + (math.log2(f) - lo) / (hi - lo) * (w - 2 * pad_x)
        py = lambda db: pad_y + (1 - (db + eq.MAX_DB) / (2 * eq.MAX_DB)) * (h - 2 * pad_y)

        cr.set_line_width(1)
        for db in (-12, -6, 6, 12):
            cr.set_source_rgba(1, 1, 1, 0.07)
            cr.move_to(pad_x, py(db) + 0.5)
            cr.line_to(w - pad_x, py(db) + 0.5)
            cr.stroke()
        for f in eq.UI_BANDS:
            cr.set_source_rgba(1, 1, 1, 0.05)
            cr.move_to(px(f) + 0.5, pad_y)
            cr.line_to(px(f) + 0.5, h - pad_y)
            cr.stroke()
        cr.set_source_rgba(1, 1, 1, 0.25)
        cr.move_to(pad_x, py(0) + 0.5)
        cr.line_to(w - pad_x, py(0) + 0.5)
        cr.stroke()

        gains = eq.clamp_gains(self.page.state.eq_gains)
        freqs = [20 * (1000.0 ** (i / 120.0)) for i in range(121)]
        db = eq.curve(gains, freqs)
        r, g, b = self._accent()
        alpha = 1.0 if self.page.state.eq_enabled else 0.35

        cr.move_to(px(freqs[0]), py(0))
        for f, v in zip(freqs, db):
            cr.line_to(px(f), py(v))
        cr.line_to(px(freqs[-1]), py(0))
        cr.close_path()
        cr.set_source_rgba(r, g, b, 0.22 * alpha)
        cr.fill()

        cr.move_to(px(freqs[0]), py(db[0]))
        for f, v in zip(freqs, db):
            cr.line_to(px(f), py(v))
        cr.set_source_rgba(r, g, b, alpha)
        cr.set_line_width(2.5)
        cr.stroke()

        for f, v in zip(eq.UI_BANDS, gains):
            cr.arc(px(f), py(v), 3.5, 0, 2 * math.pi)
            cr.set_source_rgba(1, 1, 1, 0.9 * alpha)
            cr.fill()


class EqPage(Gtk.Box):
    def __init__(self, state, on_change):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.state = state
        self.on_change = on_change
        self._building = True
        self._timer = 0
        self._dirty = False
        self.set_margin_top(12)
        self.set_margin_bottom(24)

        self.warning = Gtk.Label(wrap=True, xalign=0)
        self.warning_box = Gtk.Box()
        self.warning_box.add_css_class("status-card")
        self.warning_box.append(self.warning)
        self.warning_rev = Gtk.Revealer(child=self.warning_box)
        self.append(self.warning_rev)

        group = Adw.PreferencesGroup()
        self.row = Adw.ActionRow(title="Equalizer")
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch.connect("notify::active", self._on_switch)
        self.row.add_suffix(self.switch)
        self.row.set_activatable_widget(self.switch)
        group.add(self.row)
        self.append(group)

        self.curve = ResponseCurve(self)
        self.append(self.curve)

        self.append(self._build_presets())
        self.append(self._build_sliders())

        note = Gtk.Label(wrap=True, xalign=0, label=(
            "Applies to everything played through the G560, and keeps working after you close this app. "
            "Boosts lower the overall level a little so loud peaks cannot distort: raise the volume to compare."))
        note.add_css_class("dim-label")
        note.add_css_class("caption")
        self.append(note)

        self._building = False
        self.refresh()
        self.connect("map", lambda *_: self.check_conflicts())

    # ---- construction -----------------------------------------------------
    def _build_presets(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        title = Gtk.Label(label="Presets", xalign=0)
        title.add_css_class("heading")
        box.append(title)
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                           min_children_per_line=2, column_spacing=8, row_spacing=8, homogeneous=True)
        self.chips = {}
        self._none = Gtk.ToggleButton()  # never shown: lets "no preset" be a state of the group
        group = self._none
        for name in eq.PRESETS:
            chip = Gtk.ToggleButton(label=name, tooltip_text=HINTS.get(name))
            chip.add_css_class("chip")
            chip.set_group(group)
            chip.connect("toggled", self._on_preset, name)
            self.chips[name] = chip
            flow.append(chip)
        box.append(flow)
        return box

    def _build_sliders(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        head = Gtk.Box(spacing=8)
        title = Gtk.Label(label="Bands", xalign=0, hexpand=True)
        title.add_css_class("heading")
        head.append(title)
        unit = Gtk.Label(label="dB")
        unit.add_css_class("dim-label")
        unit.add_css_class("caption")
        head.append(unit)
        box.append(head)

        cols = Gtk.Box(spacing=4, homogeneous=True)
        self.scales, self.values = [], []
        for i, label in enumerate(LABELS):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            value = Gtk.Label(label="0")
            value.add_css_class("caption")
            value.add_css_class("dim-label")
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, -eq.MAX_DB, eq.MAX_DB, 0.5)
            scale.set_inverted(True)
            scale.set_draw_value(False)
            scale.set_size_request(-1, 200)
            scale.set_vexpand(True)
            scale.add_mark(0, Gtk.PositionType.RIGHT, None)
            scale.set_tooltip_text("%s Hz" % ("%gk" % (eq.UI_BANDS[i] / 1000) if eq.UI_BANDS[i] >= 1000 else eq.UI_BANDS[i]))
            scale.connect("value-changed", self._on_slider, i)
            name = Gtk.Label(label=label)
            name.add_css_class("caption")
            for w in (value, scale, name):
                col.append(w)
            cols.append(col)
            self.scales.append(scale)
            self.values.append(value)
        box.append(cols)
        return box

    # ---- state -> widgets -------------------------------------------------
    @staticmethod
    def _fmt(v):
        return ("%+g" % v) if v else "0"

    def refresh(self):
        was, self._building = self._building, True
        s = self.state
        self.switch.set_active(s.eq_enabled)
        self.row.set_subtitle("On: shaping the G560's sound" if s.eq_enabled
                              else "Off: your settings are kept but not applied")
        for i, (scale, value) in enumerate(zip(self.scales, self.values)):
            scale.set_value(s.eq_gains[i])
            value.set_label(self._fmt(s.eq_gains[i]))
        match = eq.matching_preset(s.eq_gains)
        (self.chips[match] if match else self._none).set_active(True)
        self.curve.queue_draw()
        self._building = was

    def show_status(self, ok, message):
        if ok:
            self.warning_rev.set_reveal_child(False)
            self.check_conflicts()
        else:
            self.warning.set_label(message)
            self.warning_rev.set_reveal_child(True)

    def check_conflicts(self):
        apps = eq.conflicting_apps()
        if apps and self.state.eq_enabled:
            self.warning.set_label(
                "%s is running and also takes over audio streams. Using both can fight over your sound: "
                "quit it, or leave this equalizer off." % " and ".join(a.capitalize() for a in apps))
            self.warning_rev.set_reveal_child(True)

    # ---- handlers ---------------------------------------------------------
    def _changed(self):
        """Settings changed: send to the audio server, at most every APPLY_INTERVAL_MS."""
        if self._building:
            return
        self.curve.queue_draw()
        self._dirty = True
        if not self._timer:
            self._flush()

    def _flush(self):
        self._timer = 0
        if not self._dirty:
            return False
        self._dirty = False
        self.on_change()
        self._timer = GLib.timeout_add(APPLY_INTERVAL_MS, self._flush)
        return False

    def _enable_if_needed(self):
        if not self.state.eq_enabled:
            self.state.eq_enabled = True
            self.refresh()

    def _on_switch(self, switch, _pspec):
        if self._building:
            return
        self.state.eq_enabled = switch.get_active()
        self.refresh()
        self._changed()

    def _on_preset(self, chip, name):
        if self._building or not chip.get_active():
            return
        self.state.eq_gains = [float(v) for v in eq.PRESETS[name]]
        self._enable_if_needed()
        self.refresh()
        self._changed()

    def _on_slider(self, scale, i):
        if self._building:
            return
        v = round(scale.get_value() * 2) / 2.0
        self.state.eq_gains[i] = v
        self.values[i].set_label(self._fmt(v))
        match = eq.matching_preset(self.state.eq_gains)
        was, self._building = self._building, True
        (self.chips[match] if match else self._none).set_active(True)
        self._building = was
        self._enable_if_needed()
        self._changed()
