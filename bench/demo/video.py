"""Render the demo as a side-by-side video: the native recording on the left,
a speedrunner one on the right, replayed in real time with a clock above
each, then a closing banner. Recordings from record.sh (asciinema casts).

Needs pyte, Pillow, ffmpeg and the DejaVu fonts.

Usage: python3 video.py <record_dir> <cold|warm> <out.mp4> [--fps 15] [--floor]
--floor: clocks truncated to the tenth, as nightly.sh printed its times
before it rounded them, so that they read what the recording's script says.
"""
import argparse
import json
import math
import os
import subprocess

import pyte
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
FONTS = "/usr/share/fonts/truetype/dejavu"
BG, PANE, EDGE = (20, 23, 20), (11, 13, 11), (44, 50, 44)
FG, MUTED, ACCENT, GOOD = (212, 219, 209), (139, 150, 138), (255, 178, 56), (124, 207, 107)
ANSI = {
    "black": (28, 31, 28), "red": (239, 107, 107), "green": (124, 207, 107), "brown": (231, 196, 94),
    "blue": (106, 169, 255), "magenta": (211, 139, 240), "cyan": (95, 207, 214), "white": (212, 219, 209),
    "brightblack": (107, 117, 105), "brightred": (255, 143, 143), "brightgreen": (159, 230, 142),
    "brightbrown": (255, 217, 122), "brightblue": (143, 192, 255), "brightmagenta": (228, 169, 255),
    "brightcyan": (134, 227, 232), "brightwhite": (255, 255, 255),
}
COLS, ROWS = 80, 32
CELL_W, CELL_H, SIZE = 11, 22, 18
PAD = 14
TERM_W, TERM_H = COLS * CELL_W + 2 * PAD, ROWS * CELL_H + 2 * PAD
TOP = 150
SHOWN = round  # how a clock shows a duration in tenths: round, or math.floor (--floor)


def tenths(t):
    return SHOWN(t * 10) / 10


def font(name, size):
    return ImageFont.truetype(os.path.join(FONTS, name), size)


MONO, MONO_B = font("DejaVuSansMono.ttf", SIZE), font("DejaVuSansMono-Bold.ttf", SIZE)
SYMBOL = font("DejaVuSans.ttf", SIZE)  # what the mono face lacks: ❯ ✔ ✨
SANS, SANS_B = font("DejaVuSans.ttf", 24), font("DejaVuSans-Bold.ttf", 30)
CLOCK = font("DejaVuSansMono-Bold.ttf", 46)
TITLE = font("DejaVuSans-Bold.ttf", 34)
BANNER, BANNER_S = font("DejaVuSans-Bold.ttf", 64), font("DejaVuSans.ttf", 30)


_GLYPHS = {}


def has_glyph(face, ch):
    """Whether face draws ch, rather than its box for a missing glyph."""
    def drawn(c):
        img = Image.new("L", (2 * SIZE, 2 * SIZE))
        ImageDraw.Draw(img).text((0, 0), c, font=face, fill=255)
        return img.tobytes()

    key = (id(face), ch)
    if key not in _GLYPHS:
        _GLYPHS[key] = drawn(ch) != drawn("\U0010fffd")
    return _GLYPHS[key]


def colour(value, default):
    if value == "default":
        return default
    if value in ANSI:
        return ANSI[value]
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


class Pane:
    """One recording on a pyte screen, rendered when it changes."""

    def __init__(self, cast, prompt):
        lines = open(cast).read().splitlines()
        self.events = [(e[0], e[2]) for e in map(json.loads, lines[1:]) if e[1] == "o"]
        self.duration = self.events[-1][0]
        self.screen = pyte.Screen(COLS, ROWS)
        self.stream = pyte.ByteStream(self.screen)
        self.stream.feed(prompt.encode())
        self.i = 0
        self.image = None

    def advance(self, t):
        changed = self.image is None
        while self.i < len(self.events) and self.events[self.i][0] <= t:
            self.stream.feed(self.events[self.i][1].encode())
            self.i += 1
            changed = True
        running = t < self.duration
        if changed or running != getattr(self, "running", None):
            self.running = running
            self.image = self.render(running)
        return self.image

    def render(self, cursor):
        img = Image.new("RGB", (TERM_W, TERM_H), PANE)
        d = ImageDraw.Draw(img)
        for y in range(ROWS):
            line = self.screen.buffer[y]
            for x in range(COLS):
                ch = line[x]
                if not ch.data or ch.data == " " and ch.bg == "default":
                    continue
                fg, bg = colour(ch.fg, FG), colour(ch.bg, PANE)
                if ch.reverse:
                    fg, bg = bg, fg
                px, py = PAD + x * CELL_W, PAD + y * CELL_H
                if bg != PANE:
                    d.rectangle([px, py, px + CELL_W - 1, py + CELL_H - 1], fill=bg)
                if ch.data == " ":
                    continue
                face = SYMBOL if ord(ch.data[0]) > 0x2500 else (MONO_B if ch.bold else MONO)
                if face is SYMBOL and not has_glyph(SYMBOL, ch.data):
                    continue  # black's emoji: no face here has them
                d.text((px, py + 1), ch.data, font=face, fill=fg)
        if cursor:
            cx, cy = self.screen.cursor.x, self.screen.cursor.y
            px, py = PAD + min(cx, COLS - 1) * CELL_W, PAD + cy * CELL_H
            d.rectangle([px, py + 2, px + CELL_W - 2, py + CELL_H - 2], fill=FG)
        return img


def frame(panes, labels, t, end, banner):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text(((W - 2 * TERM_W) // 3, 40), "nightly.sh  ·  the same bash script, run twice", font=TITLE, fill=FG)
    for k, (pane, (who, cmd)) in enumerate(zip(panes, labels)):
        gap = (W - 2 * TERM_W) // 3
        x0 = gap + k * (TERM_W + gap)
        tt = min(t, pane.duration)
        done = t >= pane.duration
        d.text((x0, 100), who, font=SANS_B, fill=ACCENT if k else FG)
        clock = f"{tenths(max(tt, 0)):5.1f} s"
        cw = d.textlength(clock, font=CLOCK)
        d.text((x0 + TERM_W - cw, 88), clock, font=CLOCK, fill=GOOD if done else (ACCENT if k else FG))
        d.rounded_rectangle([x0 - 1, TOP - 1, x0 + TERM_W, TOP + TERM_H], radius=10, outline=EDGE, width=2)
        img.paste(pane.advance(max(t, 0)), (x0, TOP))
        d.text((x0, TOP + TERM_H + 16), cmd, font=SANS, fill=MUTED)
    if t > end:
        n, f = tenths(panes[0].duration), tenths(panes[1].duration)
        text = f"{n:.1f} s  →  {f:.1f} s    x{n / f:.1f}"
        tw = d.textlength(text, font=BANNER)
        y = TOP + TERM_H // 2 - 70
        d.rounded_rectangle([W / 2 - tw / 2 - 50, y - 30, W / 2 + tw / 2 + 50, y + 150], radius=18, fill=(30, 34, 30),
                            outline=ACCENT, width=3)
        d.text((W / 2 - tw / 2, y), text, font=BANNER, fill=ACCENT)
        sw = d.textlength(banner, font=BANNER_S)
        d.text((W / 2 - sw / 2, y + 90), banner, font=BANNER_S, fill=FG)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("record_dir")
    ap.add_argument("mode", choices=("cold", "warm"))
    ap.add_argument("out")
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--floor", action="store_true")
    args = ap.parse_args()
    global SHOWN
    if args.floor:
        SHOWN = math.floor
    prompt = "\x1b[1;32m$\x1b[0m {}\r\n"
    panes = [Pane(os.path.join(args.record_dir, "native.cast"), prompt.format("./nightly.sh")),
             Pane(os.path.join(args.record_dir, f"{args.mode}.cast"), prompt.format("speedrunner exec ./nightly.sh"))]
    state = "daemons stopped at start" if args.mode == "cold" else "daemons already running"
    labels = [("native", "$ ./nightly.sh"), ("with speedrunner", f"$ speedrunner exec ./nightly.sh   ·   {state}")]
    banner = "same script, same output"
    lead, hold = 1.0, 4.0
    end = panes[0].duration + 0.8
    ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{W}x{H}", "-r", str(args.fps), "-i", "-", "-c:v", "libx264", "-preset", "medium",
                           "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", args.out],
                          stdin=subprocess.PIPE)
    n = int((lead + end + hold) * args.fps)
    for i in range(n):
        t = i / args.fps - lead
        ff.stdin.write(frame(panes, labels, t, end, banner).tobytes())
    ff.stdin.close()
    if ff.wait() != 0:
        raise SystemExit("ffmpeg failed")
    print(f"{args.out}: {n} frames, {n / args.fps:.1f} s")


if __name__ == "__main__":
    main()
