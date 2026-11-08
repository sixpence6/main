import pathlib

from PIL import Image, ImageDraw

out = pathlib.Path(r"D:\Desktop\新建文件夹\.tmp\a5\fixtures")
out.mkdir(parents=True, exist_ok=True)
w, h = 1600, 900
img = Image.new("RGB", (w, h), (246, 244, 239))
d = ImageDraw.Draw(img)
for y in range(h):
    t = y / h
    d.line([(0, y), (w, y)], fill=(int(246 - 18 * t), int(244 - 16 * t), int(239 - 14 * t)))
d.ellipse([560, 180, 1040, 720], fill=(229, 213, 214), outline=(198, 178, 180), width=4)
d.rectangle([640, 150, 960, 190], fill=(214, 198, 199))
d.text((660, 760), "BASELINE PLACEHOLDER 3840x2160", fill=(120, 120, 120))
img.save(out / "result-placeholder.png")
print("wrote", out / "result-placeholder.png")
