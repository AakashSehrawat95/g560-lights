# G560 Lights

Lighting control for **Logitech G560 gaming speakers on Linux**. Logitech has no
Linux app for them, so this is one: a small GTK 4 / libadwaita window with a color
wheel, brightness and speed sliders and a set of effects.

## Effects

| Effect | Runs on | Notes |
|---|---|---|
| Static | speakers | One color, or a different color for each of the four lights |
| Breathe | speakers | Fades your colors in and out |
| Spectrum | speakers | Cycles through every color |
| Wave | app | A rainbow travelling across the speakers |
| Gradient | app | Blends from a color (per light) to a second color |
| Flash | app | The big and small lights flash in turn (limited to 6 flashes a second) |
| Candle | app | Warm, uneven flicker |
| Music | app | Pulses with what you are playing, in the color you pick; optionally shifts to a second color when loud (PulseAudio / PipeWire-Pulse) |

**Each effect remembers its own colors** (Static can stay cyan while Candle stays orange).

**Per-light colors.** Every effect that has a color (Static, Breathe, Gradient, Flash,
Candle, Music) can color the four lights separately: in the Color section pick **All** to set
everything at once, or one light (left or right, front or rear: the small round lights are the
*front* ones and the big glow around each grille is the *rear* one). Clicking a light in the speaker
preview selects it too.

"Runs on speakers" effects keep going after you close the app. The others are
animated by the app, which keeps running in the background when you close its
window (open it again from the app menu; **Quit** is in the ☰ menu). Settings are
saved in `~/.config/g560-lights/config.json`. The **Settings** tab has a **Start at login**
switch: the app then opens hidden at login and restores your lights and equalizer, since the
speakers forget their lights when powered off.

## Equalizer

The **Equalizer** tab is a 10-band graphic EQ (31 Hz to 16 kHz, ±12 dB) with a **Pre** (preamp)
slider that raises or lowers the overall level, 13 presets (Flat, Loudness, Bass boost, Less bass,
Treble boost, Vocal, Movie, Gaming, Rock, Pop, Jazz, Classical, Electronic) and your own
**profiles**: shape a curve, press *Save as profile…*, and it appears next to the presets
(select it to load, *Delete profile* to remove it).

The speakers have no EQ of their own, so the app puts a PulseAudio filter (`module-ladspa-sink`
running the `Eq10X2` plugin from the `caps` package) in front of the G560's output and makes it
the default sink. Running apps are moved across, so nothing needs restarting, and changes are
swapped in without a dropout. The filter lives in the audio server and keeps working after you
close the app. Boosts lower the overall level by the largest boost so peaks cannot clip; use
**Pre** to bring it back up (too far and loud music can distort). Like most graphic EQs the bands
are broad and overlap, so presets track their curve closely while one slider moved alone sounds
softer than its number.

**If the sound drops out.** While the app is open it checks the filter every couple of seconds. If
the speakers disappear (USB cable, power) or the audio server restarts, it rebuilds the filter by
itself and moves your apps back onto it. The Equalizer tab shows what happened with **Reconnect
now** and **Turn EQ off** buttons. If the audio server restarts twice within two minutes with the
EQ on, the app switches the EQ off instead of looping and tells you so. As a last resort, from any
terminal:

```
g560-lights eq-reset      # removes the filter and puts every app back on the speakers
```

Why `caps`: the filter runs inside PulseAudio's real-time audio thread, and if that thread runs
too long the kernel kills the whole audio server (every app loses sound). The ZamGEQ31 plugin did
this unpredictably (it died within a second of playback with most presets); `caps` survived every
stress test, including 40 random extreme curves while music was playing.

If PulseEffects or EasyEffects is running, it also grabs audio streams and the two will fight, so
use one. Needs PulseAudio (PipeWire's PulseAudio layer is untested) and the `caps` package
(`sudo apt install caps`; the `.deb` depends on it).

## Install (Ubuntu / Debian)

```bash
./packaging/build-deb.sh
sudo apt install ./dist/g560-lights_0.1.0_all.deb
```

The package installs a udev rule so your user can talk to the speakers without
root. If the app says "No permission", unplug and replug the speakers' USB cable.

Run from a checkout without installing: `python3 -m g560_lights` (still needs the
udev rule: `sudo cp data/70-g560-lights.rules /etc/udev/rules.d/`).

Dependencies: `python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 pulseaudio-utils caps`.

## Command line

Compatible with `g560-led`, no `sudo` needed:

```
g560-lights solid 00ffff
g560-lights breathe ff0000 [rate_ms [brightness]]
g560-lights cycle [rate_ms [brightness]]
g560-lights off
g560-lights --background     # start hidden and apply saved settings
g560-lights --tab eq         # open on a tab: lights, eq or settings
g560-lights eq-reset         # emergency: remove the equalizer, put all sound back on the speakers
```

## Develop

```bash
python3 -m unittest discover -s tests
G560_TEST_PULSE=1 python3 -m unittest tests.test_eq_integration   # real audio on a private PulseAudio server (needs caps)
G560_TEST_UI=1 python3 -m unittest tests.test_ui_smoke            # drives the real widgets (opens a window)

# Run a test build without touching the speakers or your audio setup, and without
# handing off to a copy of the app you have open:
G560_LIGHTS_DRY_RUN=1 python3 -m g560_lights
```

## Credits

The USB protocol comes from [g560-led](https://github.com/claudiosanches/g560-led)
by Claudio Sanches, itself based on g810-led. MIT licensed.
