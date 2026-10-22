import os
import tempfile
import unittest

from g560_lights import autostart


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp  # never touch the real login items

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self.saved

    def test_path_follows_xdg_config_home(self):
        self.assertEqual(autostart.path(), os.path.join(self.tmp, "autostart", "g560-lights.desktop"))

    def test_off_by_default(self):
        self.assertFalse(autostart.is_enabled())

    def test_turning_on_writes_a_valid_hidden_launch_entry(self):
        autostart.set_enabled(True)
        self.assertTrue(autostart.is_enabled())
        text = open(autostart.path()).read()
        self.assertIn("Exec=g560-lights --background", text)
        self.assertIn("X-GNOME-Autostart-enabled=true", text)
        self.assertTrue(text.startswith("[Desktop Entry]"))

    def test_turning_off_removes_it_and_is_safe_to_repeat(self):
        autostart.set_enabled(True)
        autostart.set_enabled(False)
        self.assertFalse(autostart.is_enabled())
        autostart.set_enabled(False)  # already off: no error

    def test_unwritable_folder_raises_oserror(self):
        os.environ["XDG_CONFIG_HOME"] = "/proc/nonexistent-dir"
        with self.assertRaises(OSError):
            autostart.set_enabled(True)


if __name__ == "__main__":
    unittest.main()
