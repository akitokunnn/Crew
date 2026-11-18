#!/usr/bin/env bash
# README 配图：用无头 Chrome 按 2 倍分辨率截取 HTML，再转成 WebP 放到 docs/readme/。
# 用法：bash docs/readme/src/render.sh <页面名> <宽> <高> <输出名> [查询参数] [lossy]
#   页面名对应本目录的 <页面名>.html；查询参数原样拼到地址后（如 theme=dark&img=...）；末尾写 lossy 用有损压缩（适合界面截图）。
set -e
here="$(cd "$(dirname "$0")" && pwd -W)"
chrome="${CHROME:-/c/Program Files/Google/Chrome/Application/chrome.exe}"
page="$1"; width="$2"; height="$3"; name="$4"; query="${5:-theme=light}"; mode="${6:-lossless}"
png="$TEMP/crew-readme-$name.png"
"$chrome" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
  --default-background-color=00000000 --virtual-time-budget=5000 --window-size="$width,$height" \
  --user-data-dir="$TEMP/crew-readme-render" --screenshot="$png" "file:///$here/$page.html?$query" 2>/dev/null
python - "$png" "$here/../$name.webp" "$mode" <<'PY'
import sys
from PIL import Image
src, out, mode = sys.argv[1:]
image = Image.open(src)
if mode == 'lossy':
    image.save(out, 'WEBP', quality=90, method=6)
else:
    image.save(out, 'WEBP', lossless=True, method=6)
print(out, image.size)
PY
rm -f "$png"
