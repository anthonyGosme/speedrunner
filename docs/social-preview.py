"""The repository's social preview (1280x640, the card shown where its link is
shared): name, what it serves, the demo video's gain, its promises.

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

# The gain the demo video shows (bench/speedrunner.mp4): the same numbers
# wherever the project is presented.
label, native, fast = "nightly.sh, the same bash script run twice (the demo video)", 24.0, 5.3
y = 252
bar_x, bar_w = x + 230, 740
d.text((x, y), label, font=font(MONO, 24), fill=FG)
for i, (value, colour, who) in enumerate([(native, NATIVE, "native"), (fast, FAST, "speedrunner")]):
    top = y + 58 + i * 52
    length = max(6, int(bar_w * value / native))
    d.rectangle((bar_x, top, bar_x + length, top + 34), fill=colour)
    d.text((bar_x + length + 16, top + 1), f"{value:.1f} s", font=font(MONO_BOLD if i else MONO, 28),
           fill=ACCENT if i else DIM)
    d.text((bar_x - 14, top + 6), who, font=font(MONO, 22), fill=DIM, anchor="ra")
d.text((bar_x, y + 178), "x4.5, same output", font=font(MONO_BOLD, 30), fill=ACCENT)

d.text((x, 526), "zero config  ·  one static binary  ·  never runs a command twice",
       font=font(MONO, 25), fill=FG)
d.text((x, 572), "github.com/anthonyGosme/speedrunner", font=font(MONO, 25), fill=DIM)
img.save(sys.argv[1])
