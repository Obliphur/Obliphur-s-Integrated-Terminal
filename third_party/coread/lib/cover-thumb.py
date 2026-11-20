# 书架封面缩略图：python3 cover-thumb.py <原图> <缩略图.jpg>
# 只依赖 Pillow（pip install pillow）。没装或失败时服务端会退回发原图。
import os
import sys

from PIL import Image, ImageOps

src, dst = sys.argv[1], sys.argv[2]
img = ImageOps.exif_transpose(Image.open(src))
if img.mode in ('RGBA', 'LA', 'P'):
    img = img.convert('RGBA')
    bg = Image.new('RGB', img.size, (255, 255, 255))
    bg.paste(img, mask=img.split()[-1])
    img = bg
else:
    img = img.convert('RGB')
img.thumbnail((400, 600), Image.LANCZOS)
# 先写临时文件再改名：同一张图被并发请求时，读的一方不会读到写了一半的文件
tmp = f'{dst}.{os.getpid()}.tmp'
try:
    img.save(tmp, 'JPEG', quality=82, optimize=True)
    os.replace(tmp, dst)
finally:
    if os.path.exists(tmp):
        os.remove(tmp)
