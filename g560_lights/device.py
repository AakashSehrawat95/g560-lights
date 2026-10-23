"""Low-level access to the Logitech G560 speakers over hidraw.

The speakers take 20-byte HID output reports (report id 0x11), one per light
zone. The protocol is the one used by g560-led and G HUB; we write it to the
hidraw node, so no USB driver detaching or root is needed once the udev rule
is installed.
"""

import errno
import glob
import os
import threading
import time

VENDOR_ID = 0x046D
PRODUCT_ID = 0x0A78

# Zone ids as the speakers number them.
# Each speaker has a small round light (the FRONT light, on the lower inner side) and a big
# light that rings the grille (the REAR light).
ZONE_LEFT_FRONT, ZONE_RIGHT_FRONT, ZONE_LEFT_REAR, ZONE_RIGHT_REAR = 0, 1, 2, 3
ZONES = (ZONE_LEFT_FRONT, ZONE_RIGHT_FRONT, ZONE_LEFT_REAR, ZONE_RIGHT_REAR)
ZONE_NAMES = {
    ZONE_LEFT_FRONT: "Left front",
    ZONE_RIGHT_FRONT: "Right front",
    ZONE_LEFT_REAR: "Left rear",
    ZONE_RIGHT_REAR: "Right rear",
}

MODE_STATIC, MODE_CYCLE, MODE_BREATHE = 0x01, 0x02, 0x04
REPORT_LEN = 20
# The speakers need a moment between reports: back-to-back writes fail with
# EPROTO. Measured on a G560 (gap after each write completes): 0-2 ms always
# fail, 3-5 ms fail 3-8% of the time under sustained load, 6 ms ~0.2%, so 7 ms.
WRITE_GAP = 0.007
WRITE_RETRIES = 2
MIN_RATE_MS, MAX_RATE_MS = 100, 60000

RGB = tuple


class DeviceError(Exception):
    """The speakers could not be reached."""


class NoDevice(DeviceError):
    """No G560 is plugged in."""


class NoPermission(DeviceError):
    """A G560 is present but we may not open it (udev rule missing)."""


def find_hidraw():
    """Return the /dev/hidraw path of the G560 control interface, or None."""
    wanted = "HID_ID=0003:%08X:%08X" % (VENDOR_ID, PRODUCT_ID)
    for uevent in sorted(glob.glob("/sys/class/hidraw/hidraw*/device/uevent")):
        try:
            with open(uevent) as f:
                if wanted in f.read().upper():
                    return "/dev/" + uevent.split("/")[4]
        except OSError:
            continue
    return None


def _identity(path):
    """Which physical connection `path` is: a new HID device every time the speakers re-enumerate."""
    return os.path.realpath("/sys/class/hidraw/%s/device" % os.path.basename(path))


def _clamp(value, low, high):
    return max(low, min(high, int(value)))


def _rgb(color):
    return bytes(_clamp(c, 0, 255) for c in color)


def build_report(zone, mode, payload):
    head = bytes([0x11, 0xFF, 0x04, 0x3A, zone, mode])
    return (head + payload).ljust(REPORT_LEN, b"\0")


def static_payload(color):
    return _rgb(color)


def cycle_payload(rate_ms, brightness):
    rate = _clamp(rate_ms, MIN_RATE_MS, MAX_RATE_MS)
    return bytes(5) + rate.to_bytes(2, "big") + bytes([_clamp(brightness, 1, 100)])


def breathe_payload(color, rate_ms, brightness):
    rate = _clamp(rate_ms, MIN_RATE_MS, MAX_RATE_MS)
    return _rgb(color) + rate.to_bytes(2, "big") + b"\0" + bytes([_clamp(brightness, 1, 100)]) + b"\0"


class G560:
    """A connection to the speakers. Safe to call from several threads."""

    def __init__(self):
        self._fd = None
        self._identity = None
        self._lock = threading.Lock()
        self._last = {}
        self._last_write = 0.0
        self.write_gap = WRITE_GAP

    @property
    def is_open(self):
        return self._fd is not None

    def open(self):
        with self._lock:
            self._open_locked()

    def _open_locked(self):
        if self._fd is not None:
            return
        path = find_hidraw()
        if path is None:
            raise NoDevice("No G560 speakers found. Is the USB cable plugged in?")
        try:
            self._fd = os.open(path, os.O_WRONLY)
        except PermissionError:
            raise NoPermission(
                "No permission to use the speakers. Install the udev rule "
                "(the .deb does this) and replug the USB cable."
            )
        except OSError as e:
            raise DeviceError("Could not open %s: %s" % (path, e))
        self._identity = _identity(path)
        self._last = {}

    def was_replugged(self):
        """True if the speakers we have open are gone or came back as a new device.

        The speakers re-enumerate when they reconnect or wake from standby; our file
        descriptor then points at the dead old device, and (for effects that run on the
        speakers) nothing is written that would reveal it.
        """
        with self._lock:
            if self._fd is None:
                return False
            path = find_hidraw()
            return path is None or _identity(path) != self._identity

    def close(self):
        with self._lock:
            self._close_locked()

    def _close_locked(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
        self._fd = None

    def invalidate(self):
        """Forget what was sent, so the next frame is written even if equal."""
        with self._lock:
            self._last = {}

    def _send(self, reports):
        """Write {zone: report}, skipping zones whose report is unchanged."""
        with self._lock:
            self._open_locked()
            for zone, report in reports.items():
                if self._last.get(zone) == report:
                    continue
                self._write_locked(report)
                self._last[zone] = report

    def _write_locked(self, report):
        for attempt in range(WRITE_RETRIES + 1):
            wait = self._last_write + self.write_gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                os.write(self._fd, report)
                self._last_write = time.monotonic()
                return
            except OSError as e:
                self._last_write = time.monotonic()
                if e.errno == errno.EPROTO and attempt < WRITE_RETRIES:
                    time.sleep(self.write_gap * 2)  # still busy: give it longer, then retry
                    continue
                self._close_locked()
                if e.errno in (errno.ENODEV, errno.EIO, errno.EPIPE, errno.EBADF):
                    raise NoDevice("The speakers were disconnected.")
                raise DeviceError("Write failed: %s" % e)

    def set_static(self, colors):
        """colors: four RGB tuples, indexed by zone id."""
        self._send({z: build_report(z, MODE_STATIC, static_payload(colors[z])) for z in ZONES})

    def set_breathe(self, colors, rate_ms, brightness=100):
        """colors: one RGB tuple for every light, or four (one per zone)."""
        if len(colors) == 3 and not hasattr(colors[0], "__len__"):
            colors = [colors] * 4
        self._send({z: build_report(z, MODE_BREATHE, breathe_payload(colors[z], rate_ms, brightness)) for z in ZONES})

    def set_cycle(self, rate_ms, brightness=100):
        payload = cycle_payload(rate_ms, brightness)
        self._send({z: build_report(z, MODE_CYCLE, payload) for z in ZONES})

    def set_off(self):
        self.set_static([(0, 0, 0)] * 4)
