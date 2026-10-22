"""Applies a State to the speakers from a background thread."""

import threading
import time

from . import effects
from .audio import AudioMonitor
from .device import DeviceError, G560

FPS = 20
RETRY_SECONDS = 2.0


class Engine:
    def __init__(self, device=None, on_status=None):
        self.device = device or G560()
        self.audio = AudioMonitor()
        self.on_status = on_status or (lambda ok, message: None)
        self._state = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._quit = threading.Event()
        self._dirty = True
        self._thread = None
        self._audio_on = False
        self._status = (None, "")
        self._t0 = time.monotonic()

    def start(self, state):
        self.update(state)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._quit.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._set_audio(False)
        self.device.close()

    def update(self, state):
        with self._lock:
            self._state = state.copy()
            self._dirty = True
        self._wake.set()

    def preview(self, state):
        """Colors for the on-screen preview of `state` right now."""
        t = time.monotonic() - self._t0
        return effects.render(state, t, self.audio.levels())

    def _set_audio(self, wanted):
        if wanted and not self._audio_on:
            self.audio.start()
        elif not wanted and self._audio_on:
            self.audio.stop()
        self._audio_on = wanted

    def _report(self, ok, message=""):
        if (ok, message) != self._status:
            self._status = (ok, message)
            self.on_status(ok, message)

    def _run(self):
        last_try = 0.0
        while not self._quit.is_set():
            with self._lock:
                state, dirty, self._dirty = self._state, self._dirty, False
            info = effects.BY_ID[state.effect]
            software = state.power and not info.hardware
            self._set_audio(software and state.effect == "audio")

            try:
                if not self.device.is_open:
                    if time.monotonic() - last_try < RETRY_SECONDS and not dirty:
                        raise_wait = True
                    else:
                        last_try = time.monotonic()
                        raise_wait = False
                        self.device.open()
                        dirty = True  # speakers (re)appeared: resend everything
                    if raise_wait:
                        self._wake.wait(0.25)
                        self._wake.clear()
                        continue
                if dirty:
                    self.device.invalidate()
                    self._send_hardware_or_off(state, info)
                if software:
                    self._send_frame(state)
                self._report(True)
            except DeviceError as e:
                self._report(False, str(e))
                self._wake.wait(0.25)
                self._wake.clear()
                continue

            timeout = 1.0 / FPS if software else 0.5
            self._wake.wait(timeout)
            self._wake.clear()

    def _send_hardware_or_off(self, state, info):
        if not state.power:
            self.device.set_off()
        elif info.id == "static":
            self._send_frame(state)
        elif info.id == "breathe":
            self.device.set_breathe(state.colors[0], effects.rate_ms(state.speed), state.brightness)
        elif info.id == "cycle":
            self.device.set_cycle(effects.rate_ms(state.speed), state.brightness)

    def _send_frame(self, state):
        t = time.monotonic() - self._t0
        self.device.set_static(effects.render(state, t, self.audio.levels()))
