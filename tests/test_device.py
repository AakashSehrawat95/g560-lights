import unittest

from g560_lights import device


class ReportTests(unittest.TestCase):
    def test_static_matches_g560_led(self):
        # Byte-for-byte what g560-led sends for `solid 00ffff` to light 0 (the left small / front light).
        expected = bytes.fromhex("11ff043a" "00" "01" "00ffff" "0000000000" "000000000000")
        got = device.build_report(device.ZONE_LEFT_FRONT, device.MODE_STATIC, device.static_payload((0, 255, 255)))
        self.assertEqual(got, expected)

    def test_breathe_matches_g560_led(self):
        # `breathe ff0000 10000 100`: color, rate 0x2710, 00, brightness 0x64, 00
        expected = bytes.fromhex("11ff043a" "03" "04" "ff0000" "2710" "00" "64" "00" "000000000000")
        got = device.build_report(device.ZONE_RIGHT_REAR, device.MODE_BREATHE,
                                  device.breathe_payload((255, 0, 0), 10000, 100))
        self.assertEqual(got, expected)

    def test_cycle_matches_g560_led(self):
        expected = bytes.fromhex("11ff043a" "02" "02" "0000000000" "1388" "32" "000000000000")
        got = device.build_report(device.ZONE_LEFT_REAR, device.MODE_CYCLE, device.cycle_payload(5000, 50))
        self.assertEqual(got, expected)

    def test_zone_ids_and_names_match_the_hardware(self):
        # ids as the speakers number them: 0, 1 = the small round lights (front), 2, 3 = the big ones (rear)
        self.assertEqual((device.ZONE_LEFT_FRONT, device.ZONE_RIGHT_FRONT, device.ZONE_LEFT_REAR, device.ZONE_RIGHT_REAR), (0, 1, 2, 3))
        self.assertEqual([device.ZONE_NAMES[z] for z in device.ZONES], ["Left front", "Right front", "Left rear", "Right rear"])

    def test_reports_are_always_20_bytes(self):
        for payload in (device.static_payload((1, 2, 3)), device.cycle_payload(1, 1),
                        device.breathe_payload((0, 0, 0), 99999, 500)):
            self.assertEqual(len(device.build_report(0, 1, payload)), device.REPORT_LEN)

    def test_values_are_clamped(self):
        self.assertEqual(device.static_payload((-5, 300, 7)), bytes([0, 255, 7]))
        self.assertEqual(device.cycle_payload(1, 0)[5:], (100).to_bytes(2, "big") + bytes([1]))


class FakeFd:
    """Records what would be written, by monkeypatching os.write/os.open."""


class G560WriteTests(unittest.TestCase):
    def setUp(self):
        self.writes = []
        self._orig = (device.os.write, device.os.open, device.os.close, device.find_hidraw)
        device.os.write = lambda fd, data: self.writes.append(data) or len(data)
        device.os.open = lambda path, flags: 99
        device.os.close = lambda fd: None
        device.find_hidraw = lambda: "/dev/hidraw-test"

    def tearDown(self):
        device.os.write, device.os.open, device.os.close, device.find_hidraw = self._orig

    def test_unchanged_frames_are_not_resent(self):
        dev = device.G560()
        colors = [(1, 2, 3)] * 4
        dev.set_static(colors)
        dev.set_static(colors)
        self.assertEqual(len(self.writes), 4)
        dev.set_static([(9, 9, 9)] + colors[1:])
        self.assertEqual(len(self.writes), 5)  # only the changed zone

    def test_invalidate_forces_resend(self):
        dev = device.G560()
        dev.set_off()
        dev.invalidate()
        dev.set_off()
        self.assertEqual(len(self.writes), 8)

    def test_replug_is_detected_when_the_speakers_come_back_as_a_new_device(self):
        ident = {"now": "/sys/devices/usb/0003:046D:0A78.0047"}
        orig = device._identity
        device._identity = lambda path: ident["now"]
        try:
            dev = device.G560()
            self.assertFalse(dev.was_replugged())  # not open: nothing to compare
            dev.open()
            self.assertFalse(dev.was_replugged())
            ident["now"] = "/sys/devices/usb/0003:046D:0A78.0049"  # re-enumerated, same /dev/hidraw name
            self.assertTrue(dev.was_replugged())
            device.find_hidraw = lambda: None  # gone altogether
            self.assertTrue(dev.was_replugged())
        finally:
            device._identity = orig

    def test_writes_are_spaced(self):
        stamps = []
        device.os.write = lambda fd, data: stamps.append(device.time.monotonic()) or len(data)
        dev = device.G560()
        dev.write_gap = 0.01
        dev.set_static([(1, 1, 1)] * 4)
        self.assertEqual(len(stamps), 4)
        for a, b in zip(stamps, stamps[1:]):
            self.assertGreaterEqual(b - a, 0.0095)

    def test_breathe_accepts_one_color_or_one_per_light(self):
        dev = device.G560()
        dev.write_gap = 0
        dev.set_breathe((1, 2, 3), 3000, 50)
        same = list(self.writes)
        self.assertEqual(len(same), 4)
        self.assertTrue(all(w[6:9] == bytes([1, 2, 3]) for w in same))
        self.writes.clear()
        dev.invalidate()
        dev.set_breathe([(1, 0, 0), (0, 2, 0), (0, 0, 3), (4, 4, 4)], 3000, 50)
        self.assertEqual([w[6:9] for w in self.writes], [b"\x01\x00\x00", b"\x00\x02\x00", b"\x00\x00\x03", b"\x04\x04\x04"])

    def test_busy_speakers_are_retried(self):
        import errno
        calls = []

        def flaky(fd, data):
            calls.append(data)
            if len(calls) == 2:
                raise OSError(errno.EPROTO, "Protocol error")
            return len(data)

        device.os.write = flaky
        dev = device.G560()
        dev.write_gap = 0
        dev.set_static([(1, 1, 1)] * 4)  # must not raise
        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[1], calls[2])  # same report sent again

    def test_persistent_protocol_error_is_reported(self):
        import errno

        def broken(fd, data):
            raise OSError(errno.EPROTO, "Protocol error")

        device.os.write = broken
        dev = device.G560()
        dev.write_gap = 0
        with self.assertRaises(device.DeviceError):
            dev.set_off()

    def test_missing_device(self):
        device.find_hidraw = lambda: None
        with self.assertRaises(device.NoDevice):
            device.G560().set_off()

    def test_permission_error(self):
        def deny(path, flags):
            raise PermissionError()
        device.os.open = deny
        with self.assertRaises(device.NoPermission):
            device.G560().set_off()


if __name__ == "__main__":
    unittest.main()
