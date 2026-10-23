"""Applies a State to the speakers from a background thread."""

import threading
import time

from . import effects
from .audio import AudioMonitor
from .device import DeviceError, G560

# The speakers cannot take much more than ~25 four-light frames a second (each
# report needs a few ms to settle), so this is close to their ceiling.
FPS = 25
RETRY_SECONDS = 2.0
# How often to check that the speakers we have open are still the same device.
REPLUG_CHECK_SECONDS = 1.0
# Breathe and Spectrum run on the speakers and restart from the beginning every
# time they are re-sent, so dragging a slider would make them blink. Send at
# most this often while a slider moves; the final value always gets through.
HARDWARE_RESTART_INTERVAL = 0.4
ANIMATED_ON_SPEAKERS = ("breathe", "cycle")


class NullSpeakers:
    """Stands in for the speakers when G560_LIGHTS_DRY_RUN=1 (development, screenshots)."""

    is_open = True

    def open(self):
        pass

    def close(self):
        pass

    def invalidate(self):
        pass

    def was_replugged(self):
        return False

    def set_static(self, colors):
        pass

    def set_breathe(self, colors, rate_ms, brightness=100):
        pass

    def set_cycle(self, rate_ms, brightness=100):
        pass

    def set_off(self):
        pass


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
        self._clock = effects.Clock()  # drives the speakers
        self._preview_clock = effects.Clock()  # drives the preview of effects that run on the speakers
        self._last_hardware_send = 0.0
        self._frame = None  # (colors, time, effect) of the newest frame written to the speakers

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
        """Colors for the on-screen preview of `state` right now.

        For effects the app animates, this is the very frame last sent to the speakers, so the
        preview and the lights cannot drift apart (they used to run on separate clocks). Effects
        that run on the speakers themselves, or nothing sent yet, fall back to a local clock.
        """
        frame = self._frame
        if (frame and frame[2] == state.effect and state.power
                and not effects.BY_ID[state.effect].hardware and time.monotonic() - frame[1] < 0.5):
            return list(frame[0])
        self._preview_clock.advance(time.monotonic(), state.speed)
        return effects.render(state, self._preview_clock, self.audio.levels())

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
        last_try = last_check = 0.0
        while not self._quit.is_set():
            frame_start = time.monotonic()
            with self._lock:
                state, dirty, self._dirty = self._state, self._dirty, False
            info = effects.BY_ID[state.effect]
            software = state.power and not info.hardware
            self._set_audio(software and state.effect == "audio")

            hold = 0.0
            if dirty and state.power and state.effect in ANIMATED_ON_SPEAKERS:
                wait = self._last_hardware_send + HARDWARE_RESTART_INTERVAL - frame_start
                if wait > 0:  # still being dragged: come back with the latest value shortly
                    with self._lock:
                        self._dirty = True
                    dirty, hold = False, wait

            try:
                if self.device.is_open and frame_start - last_check >= REPLUG_CHECK_SECONDS:
                    last_check = frame_start
                    if self.device.was_replugged():
                        self.device.close()  # reopened below, and everything resent
                        last_try = 0.0
                if not self.device.is_open:
                    if frame_start - last_try < RETRY_SECONDS and not dirty:
                        self._wake.wait(0.25)
                        self._wake.clear()
                        continue
                    last_try = frame_start
                    self.device.open()
                    dirty = True  # speakers (re)appeared: resend everything
                if dirty and not software:
                    self.device.invalidate()
                    self._send_hardware_or_off(state, info)
                    if state.effect in ANIMATED_ON_SPEAKERS:
                        self._last_hardware_send = time.monotonic()
                        # The speakers restart these from the beginning when re-sent: so does the preview.
                        self._preview_clock = effects.Clock()
                if software:
                    self._send_frame(state, frame_start)
                self._report(True)
            except DeviceError as e:
                self._report(False, str(e))
                self._wake.wait(0.25)
                self._wake.clear()
                continue

            if hold:
                self._quit.wait(hold)  # not cut short by slider events
            elif software:
                # Fixed pacing: UI events must not trigger extra frames, which
                # would push the speakers past what they can take.
                self._quit.wait(max(0.0, 1.0 / FPS - (time.monotonic() - frame_start)))
            else:
                self._wake.wait(0.5)
            self._wake.clear()

    def _send_hardware_or_off(self, state, info):
        if not state.power:
            self.device.set_off()
        elif info.id == "static":
            self._send_frame(state, time.monotonic())
        elif info.id == "breathe":
            self.device.set_breathe(state.zone_colors, effects.rate_ms(state.speed), state.brightness)
        elif info.id == "cycle":
            self.device.set_cycle(effects.rate_ms(state.speed), state.brightness)

    def _send_frame(self, state, now):
        self._clock.advance(now, state.speed)
        colors = effects.render(state, self._clock, self.audio.levels())
        self.device.set_static(colors)
        self._frame = (colors, time.monotonic(), state.effect)
