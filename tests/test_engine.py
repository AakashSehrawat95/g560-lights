import threading
import time
import unittest

from g560_lights import config, engine


class FakeSpeakers:
    def __init__(self):
        self.is_open = False
        self.frames, self.breathes, self.cycles, self.offs = [], [], [], 0
        self.lock = threading.Lock()

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def invalidate(self):
        pass

    def set_static(self, colors):
        with self.lock:
            self.frames.append((time.monotonic(), list(colors)))

    def set_breathe(self, colors, rate, brightness=100):
        with self.lock:
            self.breathes.append((time.monotonic(), list(colors), rate, brightness))

    def set_cycle(self, rate, brightness=100):
        with self.lock:
            self.cycles.append((time.monotonic(), rate, brightness))

    def set_off(self):
        self.offs += 1


def run_engine(state, drive, seconds):
    dev = FakeSpeakers()
    eng = engine.Engine(device=dev)
    eng.start(state)
    start = time.monotonic()
    drive(eng, state)
    time.sleep(max(0, seconds - (time.monotonic() - start)))
    eng.stop()
    return dev


class EngineTests(unittest.TestCase):
    def test_software_frames_are_paced_even_when_the_ui_hammers_updates(self):
        state = config.State(effect="wave")

        def hammer(eng, st):
            end = time.monotonic() + 1.0
            while time.monotonic() < end:  # a slider dragged fast: ~500 updates a second
                st.brightness = 50 + int(time.monotonic() * 1000) % 50
                eng.update(st)
                time.sleep(0.002)

        dev = run_engine(state, hammer, 1.2)
        sent = len(dev.frames)
        self.assertLessEqual(sent, engine.FPS * 1.2 + 4, "frames sent: %d" % sent)
        self.assertGreaterEqual(sent, engine.FPS * 1.2 * 0.7)

    def test_preview_shows_the_frame_that_was_sent_to_the_speakers(self):
        state = config.State(effect="wave", speed=70)
        mismatches, checks = [], []

        def watch(eng, st):
            time.sleep(0.5)
            for _ in range(30):
                preview = eng.preview(st)
                recent = [f[1] for f in eng.device.frames[-3:]]  # the newest frames (another may land meanwhile)
                checks.append(preview in recent)
                time.sleep(0.037)

        dev = FakeSpeakers()
        eng = engine.Engine(device=dev)
        eng.start(state)
        watch(eng, state)
        eng.stop()
        self.assertTrue(all(checks), "the preview drifted from what the speakers were sent: %d/%d matched" % (sum(checks), len(checks)))

    def test_preview_of_a_stopped_or_unconnected_engine_still_animates(self):
        eng = engine.Engine(device=FakeSpeakers())  # never started: nothing has been sent
        st = config.State(effect="wave", speed=70)
        first = eng.preview(st)
        time.sleep(0.2)
        self.assertNotEqual(first, eng.preview(st))

    def test_breathe_is_not_restarted_on_every_slider_step(self):
        state = config.State(effect="breathe", zone_colors=[(1, 0, 0), (0, 2, 0), (0, 0, 3), (4, 4, 4)])

        def drag(eng, st):
            time.sleep(0.1)
            for v in range(1, 61):  # 60 brightness steps over ~0.9 s
                st.brightness = v
                eng.update(st)
                time.sleep(0.015)
            time.sleep(0.8)

        dev = run_engine(state, drag, 2.0)
        self.assertLessEqual(len(dev.breathes), 6, "re-sent %d times" % len(dev.breathes))
        self.assertEqual(dev.breathes[-1][3], 60, "the last value must always arrive")
        self.assertEqual(dev.breathes[-1][1], state.zone_colors, "per-light colors are sent")

    def test_spectrum_final_speed_arrives_after_a_drag(self):
        state = config.State(effect="cycle")

        def drag(eng, st):
            time.sleep(0.1)
            for v in range(10, 91, 4):
                st.speed = v
                eng.update(st)
                time.sleep(0.01)
            time.sleep(0.8)

        dev = run_engine(state, drag, 1.6)
        from g560_lights import effects
        self.assertLessEqual(len(dev.cycles), 4)
        self.assertEqual(dev.cycles[-1][1], effects.rate_ms(90))

    def test_static_applies_immediately(self):
        state = config.State(effect="static")

        def change(eng, st):
            time.sleep(0.15)
            st.zone_colors = [(9, 9, 9)] * 4
            eng.update(st)
            time.sleep(0.15)

        dev = run_engine(state, change, 0.5)
        self.assertEqual(dev.frames[-1][1], [(9, 9, 9)] * 4)

    def test_power_off_turns_lights_off(self):
        state = config.State(effect="wave")

        def off(eng, st):
            time.sleep(0.2)
            st.power = False
            eng.update(st)
            time.sleep(0.2)

        dev = run_engine(state, off, 0.6)
        self.assertGreaterEqual(dev.offs, 1)


if __name__ == "__main__":
    unittest.main()
