"""Command-line entry point. Keeps the old `g560-led` commands working."""

import sys

from . import __version__, effects
from .device import DeviceError, G560

HELP = """G560 Lights %s: control the lights of Logitech G560 speakers

Usage:
  g560-lights                          Open the app
  g560-lights --background             Start hidden and apply saved settings
  g560-lights --tab lights|eq          Open on the Lights or Equalizer tab
  g560-lights solid RRGGBB             One steady color
  g560-lights breathe RRGGBB [rate [brightness]]
  g560-lights cycle [rate [brightness]]
  g560-lights off                      Lights off
  g560-lights --version | --help

Arguments:
  RRGGBB      Hex color, e.g. 00ffff
  rate        100-60000 milliseconds per cycle (default 10000)
  brightness  1-100 percent (default 100)

The command-line modes set the speakers directly. If the app is running a
software effect (Wave, Flash, ...) it will keep drawing over them.
"""


def _color(text):
    from .config import from_hex

    try:
        return from_hex(text or "")
    except ValueError:
        raise SystemExit("Error: color must be RRGGBB, e.g. 00ffff")


def _number(text, default, low, high, name):
    if text is None:
        return default
    try:
        return max(low, min(high, int(text)))
    except ValueError:
        raise SystemExit("Error: %s must be a number" % name)


def run_command(args):
    mode, rest = args[0], args[1:] + [None] * 3
    dev = G560()
    try:
        if mode == "solid":
            dev.set_static([_color(rest[0])] * 4)
        elif mode == "off":
            dev.set_off()
        elif mode == "breathe":
            dev.set_breathe(_color(rest[0]), _number(rest[1], 10000, 100, 60000, "rate"),
                            _number(rest[2], 100, 1, 100, "brightness"))
        elif mode == "cycle":
            dev.set_cycle(_number(rest[0], 10000, 100, 60000, "rate"),
                          _number(rest[1], 100, 1, 100, "brightness"))
        else:
            raise SystemExit("Error: unknown mode %r\n\n%s" % (mode, HELP % __version__))
    except DeviceError as e:
        raise SystemExit("Error: %s" % e)
    finally:
        dev.close()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help", "help"):
        print(HELP % __version__)
        return 0
    if args and args[0] in ("-V", "--version"):
        print(__version__)
        return 0
    if args and args[0] in ("solid", "off", "breathe", "cycle"):
        run_command(args)
        return 0

    try:
        from .app import run
    except (ImportError, ValueError) as e:
        print("Error: the GTK 4 / libadwaita Python bindings are missing (%s).\n"
              "Install: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1" % e, file=sys.stderr)
        return 1
    tab = None
    if "--tab" in args and args.index("--tab") + 1 < len(args):
        tab = args[args.index("--tab") + 1]
    return run(background="--background" in args, tab=tab)
