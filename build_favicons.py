import os
import subprocess
from PIL import Image

def build():
    edge_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    base_dir = os.path.dirname(os.path.abspath(__file__))
    static_images = os.path.join(base_dir, 'static', 'images')
    os.makedirs(static_images, exist_ok=True)
    
    svg_path = os.path.join(static_images, 'favicon.svg')
    temp_html = os.path.join(base_dir, 'temp_favicon.html')
    png_512 = os.path.join(static_images, 'favicon-512x512.png')

    # Create HTML container with no margins and transparent background
    html_content = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body {{
    margin: 0;
    padding: 0;
    width: 512px;
    height: 512px;
    overflow: hidden;
    background: transparent;
  }}
  img {{
    width: 512px;
    height: 512px;
    display: block;
  }}
</style>
</head>
<body>
  <img src="static/images/favicon.svg" />
</body>
</html>'''
    with open(temp_html, 'w', encoding='utf-8') as f:
        f.write(html_content)

    # Render via Headless Chromium in Edge
    cmd = [
        edge_path,
        "--headless=new",
        "--disable-gpu",
        "--force-device-scale-factor=1",
        "--window-size=512,512",
        "--default-background-color=00000000",
        f"--screenshot={png_512}",
        temp_html
    ]
    print("Rendering SVG to 512x512 PNG via Headless Edge...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    print("Edge returncode:", res.returncode)

    if os.path.exists(temp_html):
        os.remove(temp_html)

    if not os.path.exists(png_512):
        print("ERROR: Failed to render PNG!")
        return

    print("Success! Created favicon-512x512.png")
    
    # Open base 512x512 image
    base_img = Image.open(png_512).convert("RGBA")

    # Generate all standard favicon sizes
    sizes = {
        'favicon-192x192.png': (192, 192),
        'apple-touch-icon.png': (180, 180),
        'favicon-48x48.png': (48, 48),
        'favicon-32x32.png': (32, 32),
        'favicon-16x16.png': (16, 16),
    }

    for fname, size in sizes.items():
        resized = base_img.resize(size, Image.Resampling.LANCZOS)
        out_path = os.path.join(static_images, fname)
        resized.save(out_path, format="PNG")
        print(f"Generated {fname} ({size[0]}x{size[1]})")

    # Also save static/favicon.ico (multi-frame ICO at root of static/ and static/images/)
    ico_img16 = base_img.resize((16, 16), Image.Resampling.LANCZOS)
    ico_img32 = base_img.resize((32, 32), Image.Resampling.LANCZOS)
    ico_img48 = base_img.resize((48, 48), Image.Resampling.LANCZOS)

    ico_path_static = os.path.join(base_dir, 'static', 'favicon.ico')
    ico_path_images = os.path.join(static_images, 'favicon.ico')
    
    ico_img48.save(
        ico_path_static,
        format='ICO',
        sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=[ico_img16, ico_img32]
    )
    ico_img48.save(
        ico_path_images,
        format='ICO',
        sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=[ico_img16, ico_img32]
    )
    print(f"Generated multi-frame favicon.ico at {ico_path_static} and {ico_path_images}")

if __name__ == '__main__':
    build()
