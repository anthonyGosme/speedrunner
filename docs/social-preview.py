"""The repository's social preview (1280x640, the card shown where its link is
shared): name, what it serves, two measured gains, its promises.

Run (Pillow and the DejaVu fonts; a Debian container has both at hand):
  docker run --rm -v "$PWD/docs:/d" python:3.12-slim sh -c \\
    'apt-get update -qq && apt-get install -y -qq fonts-dejavu-core >/dev/null && pip install -q pillow && python /d/social-preview.py /d/social-preview.png'
Then upload it: the repository's Settings, General, Social preview.
"""
import sys

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640
BG, FG, DIM, ACCENT, NATIVE, FAST = "#0d1117", "#e6edf3", "#8b949e", "#3fb950", "#6e7681", "#3fb950"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def font(path, size):
    return ImageFont.truetype(path, size)


img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)
x = 80

d.text((x, 62), "$ srr", font=font(MONO, 34), fill=DIM)
d.text((x + 128, 46), "speedrunner", font=font(MONO_BOLD, 64), fill=ACCENT)
d.text((x, 140), "Python, Ruby, PHP and Node command-line tools, preloaded:",
       font=font(MONO, 27), fill=FG)
d.text((x, 178), "each tool stays loaded in a daemon that forks it on every call.",
       font=font(MONO, 27), fill=FG)

# Two measured gains, as bars on a shared scale per row.
rows = [("a nightly-checks bash script, dozens of calls", 22.8, 7.3, "s"), ("eslint, on save", 328, 43, "ms")]
y = 240
bar_x, bar_w = x + 160, 760
for label, native, fast, unit in rows:
    d.text((x, y), label, font=font(MONO, 24), fill=FG)
    for i, (value, colour, who) in enumerate([(native, NATIVE, "native"), (fast, FAST, "speedrunner")]):
        top = y + 40 + i * 34
        length = max(6, int(bar_w * value / native))
        d.rectangle((bar_x, top, bar_x + length, top + 24), fill=colour)
        d.text((bar_x + length + 14, top - 2), f"{value:g} {unit}", font=font(MONO_BOLD if i else MONO, 23),
               fill=ACCENT if i else DIM)
        d.text((bar_x - 12, top + 1), who, font=font(MONO, 18), fill=DIM, anchor="ra")
    y += 130

d.text((x, 526), "zero config  ·  one static binary  ·  never runs a command twice",
       font=font(MONO, 25), fill=FG)
d.text((x, 572), "github.com/anthonyGosme/speedrunner", font=font(MONO, 25), fill=DIM)
img.save(sys.argv[1])
