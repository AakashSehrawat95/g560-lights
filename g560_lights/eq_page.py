"""The Equalizer tab."""

import math

import cairo
from gi.repository import Adw, GLib, Gtk, Pango

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
    def __init__(self, state, on_change, on_reconnect=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.state = state
        self.on_change = on_change
        self.on_reconnect = on_reconnect or (lambda: None)
        self._building = True
        self._timer = 0
        self._dirty = False
        self.set_margin_top(12)
        self.set_margin_bottom(24)

        # Something is wrong with the EQ right now: say so, and offer ways out.
        self.warning = Gtk.Label(wrap=True, xalign=0, hexpand=True)
        self.warning_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.warning_box.add_css_class("status-card")
        self.warning_box.append(self.warning)
        actions = Gtk.Box(spacing=8)
        self.reconnect_btn = Gtk.Button(label="Reconnect now")
        self.reconnect_btn.connect("clicked", lambda *_: self.on_reconnect())
        self.off_btn = Gtk.Button(label="Turn EQ off")
        self.off_btn.connect("clicked", lambda *_: self._turn_off())
        actions.append(self.reconnect_btn)
        actions.append(self.off_btn)
        self.warning_box.append(actions)
        self.warning_rev = Gtk.Revealer(child=self.warning_box)
        self.append(self.warning_rev)

        # A one-off message that stays until dismissed (e.g. the EQ was switched off for safety).
        self.notice = Gtk.Label(wrap=True, xalign=0, hexpand=True)
        self.notice_box = Gtk.Box(spacing=8)
        self.notice_box.add_css_class("status-card")
        self.notice_box.append(self.notice)
        dismiss = Gtk.Button(label="Dismiss", valign=Gtk.Align.CENTER)
        dismiss.connect("clicked", lambda *_: self.notice_rev.set_reveal_child(False))
        self.notice_box.append(dismiss)
        self.notice_rev = Gtk.Revealer(child=self.notice_box)
        self.append(self.notice_rev)

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
            "Boosts lower the level automatically so loud peaks cannot distort; use Pre to bring it back up "
            "(raise it too far and loud music can distort)."))
        note.add_css_class("dim-label")
        note.add_css_class("caption")
        self.append(note)

        self._building = False
        self.refresh()
        self.connect("map", lambda *_: self.check_conflicts())

    # ---- construction -----------------------------------------------------
    def _build_presets(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        head = Gtk.Box(spacing=8)
        title = Gtk.Label(label="Presets", xalign=0)
        title.add_css_class("heading")
        head.append(title)
        self.custom_label = Gtk.Label(label="Custom curve")  # shown when nothing matches
        self.custom_label.add_css_class("dim-label")
        self.custom_label.add_css_class("caption")
        head.append(self.custom_label)
        head.append(Gtk.Box(hexpand=True))
        self.delete_btn = Gtk.Button(label="Delete profile")
        self.delete_btn.add_css_class("flat")
        self.delete_btn.connect("clicked", lambda *_: self.delete_profile(self._active_profile))
        head.append(self.delete_btn)
        self.save_btn = Gtk.MenuButton(label="Save as profile\u2026")
        self.save_btn.add_css_class("flat")
        self.save_btn.set_popover(self._build_save_popover())
        head.append(self.save_btn)
        box.append(head)

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                                min_children_per_line=2, column_spacing=8, row_spacing=8, homogeneous=True)
        self.chips = {}
        self.profile_chips = {}
        self._active_profile = None
        self._none = Gtk.ToggleButton()  # never shown: lets "no preset" be a state of the group
        for name in eq.PRESETS:
            chip = Gtk.ToggleButton(label=name, tooltip_text=HINTS.get(name))
            chip.add_css_class("chip")
            chip.set_group(self._none)
            chip.connect("toggled", self._on_preset, name)
            self.chips[name] = chip
            self.flow.append(chip)
        box.append(self.flow)
        return box

    def _build_save_popover(self):
        pop = Gtk.Popover()
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(inner, "set_margin_" + side)(10)
        row = Gtk.Box(spacing=8)
        self.name_entry = Gtk.Entry(placeholder_text="Profile name", max_length=eq.MAX_PROFILE_NAME, width_chars=18)
        self.name_entry.connect("activate", lambda *_: self._on_save_clicked())
        self.name_entry.connect("changed", lambda *_: (self.name_entry.remove_css_class("error"), self.name_error.set_label("")))
        row.append(self.name_entry)
        ok = Gtk.Button(label="Save")
        ok.add_css_class("suggested-action")
        ok.connect("clicked", lambda *_: self._on_save_clicked())
        row.append(ok)
        inner.append(row)
        self.name_error = Gtk.Label(xalign=0)
        self.name_error.add_css_class("caption")
        self.name_error.add_css_class("error")
        inner.append(self.name_error)
        pop.set_child(inner)
        self.save_popover = pop
        pop.connect("show", lambda *_: (self.name_entry.set_text(""), self.name_entry.grab_focus()))
        return pop

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

        def column(label_text, limit, handler, tooltip):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            value = Gtk.Label(label="0")
            value.add_css_class("caption")
            value.add_css_class("dim-label")
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, -limit, limit, 0.5)
            scale.set_inverted(True)
            scale.set_draw_value(False)
            scale.set_size_request(-1, 200)
            scale.set_vexpand(True)
            scale.add_mark(0, Gtk.PositionType.RIGHT, None)
            scale.set_tooltip_text(tooltip)
            scale.connect("value-changed", handler)
            name = Gtk.Label(label=label_text)
            name.add_css_class("caption")
            for w in (value, scale, name):
                col.append(w)
            return col, scale, value

        row = Gtk.Box(spacing=8)
        pre, self.preamp, self.preamp_value = column(
            "Pre", eq.PREAMP_DB, self._on_preamp,
            "Preamp: raises or lowers the overall level. 0 keeps the automatic headroom.")
        pre.set_size_request(48, -1)
        row.append(pre)
        row.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        cols = Gtk.Box(spacing=4, homogeneous=True, hexpand=True)
        self.scales, self.values = [], []
        for i, label in enumerate(LABELS):
            hz = eq.UI_BANDS[i]
            col, scale, value = column(
                label, eq.MAX_DB, lambda s, i=i: self._on_slider(s, i),
                "%s Hz" % ("%gk" % (hz / 1000) if hz >= 1000 else hz))
            cols.append(col)
            self.scales.append(scale)
            self.values.append(value)
        row.append(cols)
        box.append(row)

        self.level = Gtk.Label(xalign=0)
        self.level.add_css_class("dim-label")
        self.level.add_css_class("caption")
        box.append(self.level)
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
        self.preamp.set_value(s.eq_preamp)
        self.preamp_value.set_label(self._fmt(s.eq_preamp))
        self._update_level()
        self._sync_profile_chips()
        self._select_current()
        self.curve.queue_draw()
        self._building = was

    def _sync_profile_chips(self):
        """Keep one chip per saved custom profile, after the built-in presets."""
        names = list(self.state.eq_profiles)
        if names == list(self.profile_chips):
            return
        for chip in self.profile_chips.values():
            self.flow.remove(chip)
        self.profile_chips = {}
        for name in names:
            chip = Gtk.ToggleButton(label=name, tooltip_text="Your profile: %s" % name)
            chip.add_css_class("chip")
            label = chip.get_child()
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(12)
            chip.set_group(self._none)
            chip.connect("toggled", self._on_profile, name)
            self.profile_chips[name] = chip
            self.flow.append(chip)

    def current_selection(self):
        """('profile', name), ('preset', name) or None for a custom curve."""
        s = self.state
        for name, p in s.eq_profiles.items():
            if eq.profile_matches(p, s.eq_gains, s.eq_preamp):
                return ("profile", name)
        preset = eq.matching_preset(s.eq_gains)
        return ("preset", preset) if preset else None

    def _select_current(self):
        cur = self.current_selection()
        if cur is None:
            widget = self._none
        elif cur[0] == "profile":
            widget = self.profile_chips[cur[1]]
        else:
            widget = self.chips[cur[1]]
        widget.set_active(True)
        self._active_profile = cur[1] if cur and cur[0] == "profile" else None
        self.custom_label.set_visible(cur is None)
        self.delete_btn.set_visible(self._active_profile is not None)

    def _update_level(self):
        s = self.state
        auto = eq.headroom(s.eq_gains)
        total = eq.master_gain(s.eq_gains, s.eq_preamp)
        self.level.set_label("Overall level: %+.1f dB  (automatic headroom %+.1f dB, preamp %+.1f dB)" % (
            total + 0.0, auto + 0.0, s.eq_preamp + 0.0))

    def show_status(self, ok, message):
        if ok:
            self.warning_rev.set_reveal_child(False)
            self.check_conflicts()
        else:
            self.warning.set_label(message)
            self.reconnect_btn.set_visible(self.state.eq_enabled)
            self.off_btn.set_visible(self.state.eq_enabled)
            self.warning_rev.set_reveal_child(True)

    def show_notice(self, message):
        self.notice.set_label(message)
        self.notice_rev.set_reveal_child(True)

    def _turn_off(self):
        self.state.eq_enabled = False
        self.warning_rev.set_reveal_child(False)
        self.refresh()
        self._changed()

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

    def _on_profile(self, chip, name):
        if self._building or not chip.get_active():
            return
        p = self.state.eq_profiles[name]
        self.state.eq_gains = [float(g) for g in p["gains"]]
        self.state.eq_preamp = float(p["preamp"])
        self._enable_if_needed()
        self.refresh()
        self._changed()

    def _on_save_clicked(self):
        name = self.name_entry.get_text().strip()
        ok, message = eq.valid_profile_name(name, self.state.eq_profiles)
        if not ok:
            self.name_entry.add_css_class("error")
            self.name_error.set_label(message)
            return
        self.save_popover.popdown()
        self.save_profile(name)

    def save_profile(self, name):
        """Keep the current curve and preamp under `name` (replacing a profile of that name)."""
        s = self.state
        s.eq_profiles[name] = {"gains": [float(g) for g in s.eq_gains], "preamp": float(s.eq_preamp)}
        self.refresh()
        self._changed()

    def delete_profile(self, name):
        if name in self.state.eq_profiles:
            del self.state.eq_profiles[name]
            self.refresh()
            self._changed()

    def _on_preamp(self, scale):
        if self._building:
            return
        v = round(scale.get_value() * 2) / 2.0
        self.state.eq_preamp = v
        self.preamp_value.set_label(self._fmt(v))
        self._update_level()
        was, self._building = self._building, True
        self._select_current()
        self._building = was
        self._enable_if_needed()
        self._changed()

    def _on_slider(self, scale, i):
        if self._building:
            return
        v = round(scale.get_value() * 2) / 2.0
        self.state.eq_gains[i] = v
        self.values[i].set_label(self._fmt(v))
        self._update_level()
        was, self._building = self._building, True
        self._select_current()
        self._building = was
        self._enable_if_needed()
        self._changed()
