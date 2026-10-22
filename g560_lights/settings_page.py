"""The Settings tab."""

from gi.repository import Adw, Gtk

from . import __version__, autostart


class SettingsPage(Gtk.Box):
    def __init__(self, on_about):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._building = True
        self.set_margin_top(12)
        self.set_margin_bottom(24)

        startup = Adw.PreferencesGroup(title="Startup")
        self.login_row = Adw.ActionRow(
            title="Start at login",
            subtitle="Opens hidden in the background when you log in, and restores your lights and "
                     "equalizer. It stays running so the equalizer can recover by itself.")
        self.login_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.login_switch.connect("notify::active", self._on_login)
        self.login_row.add_suffix(self.login_switch)
        self.login_row.set_activatable_widget(self.login_switch)
        startup.add(self.login_row)
        self.append(startup)

        self.error = Gtk.Label(wrap=True, xalign=0)
        self.error.add_css_class("error")
        self.error.set_visible(False)
        self.append(self.error)

        about = Adw.PreferencesGroup(title="About")
        row = Adw.ActionRow(title="G560 Lights", subtitle="Version %s" % __version__, activatable=True)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda *_: on_about())
        about.add(row)
        self.append(about)

        self._building = False
        self.refresh()
        self.connect("map", lambda *_: self.refresh())  # the file may have changed outside the app

    def refresh(self):
        was, self._building = self._building, True
        self.login_switch.set_active(autostart.is_enabled())
        self._building = was

    def _on_login(self, switch, _pspec):
        if self._building:
            return
        wanted = switch.get_active()
        try:
            autostart.set_enabled(wanted)
            self.error.set_visible(False)
        except OSError as e:
            self.error.set_label("Could not change the startup setting: %s" % e)
            self.error.set_visible(True)
            self.refresh()  # put the switch back to what is really on disk
