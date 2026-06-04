#!/usr/bin/env python3
"""Generate Inno Setup wizard BMP images from the Dersis logo.

Run this once before building the installer:
    python installer/create_wizard_images.py

Requires: Pillow (pip install Pillow)

Produces:
    installer/wizard_image.bmp       — 164x314 left panel image
    installer/wizard_small_image.bmp — 55x55 top-right icon
"""

import os
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("ERROR: Pillow is required. Install with: pip install Pillow")
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

# Dersis brand colors
PRIMARY_PURPLE = (110, 79, 158)    # #6e4f9e
SECONDARY_NAVY = (30, 32, 88)     # #1e2058

# Source logo — prefer the high-res one from docs/
LOGO_CANDIDATES = [
    os.path.join(REPO_ROOT, "docs", "dersis.png"),
    os.path.join(REPO_ROOT, "scheduler_app", "assets", "app_icon_256.png"),
    os.path.join(REPO_ROOT, "scheduler_app", "assets", "app_icon.png"),
]


def find_logo():
    for path in LOGO_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def create_gradient(width, height, color_top, color_bottom):
    """Create a vertical gradient image."""
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        r = int(color_top[0] + (color_bottom[0] - color_top[0]) * ratio)
        g = int(color_top[1] + (color_bottom[1] - color_top[1]) * ratio)
        b = int(color_top[2] + (color_bottom[2] - color_top[2]) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
    return img


def create_wizard_image(logo_path):
    """Create the 164x314 left-panel wizard image."""
    width, height = 164, 314
    img = create_gradient(width, height, SECONDARY_NAVY, PRIMARY_PURPLE)

    if logo_path:
        logo = Image.open(logo_path).convert("RGBA")
        # Scale logo to fit width with padding
        max_logo_size = width - 24
        logo.thumbnail((max_logo_size, max_logo_size), Image.LANCZOS)
        # Center horizontally, position in upper third
        x = (width - logo.width) // 2
        y = 40
        # Composite with alpha
        img.paste(logo, (x, y), logo)

    out_path = os.path.join(SCRIPT_DIR, "wizard_image.bmp")
    img.save(out_path, "BMP")
    print(f"  Created: {out_path} ({width}x{height})")


def create_wizard_small_image(logo_path):
    """Create the 55x55 top-right wizard icon."""
    size = 55
    img = Image.new("RGB", (size, size), PRIMARY_PURPLE)

    if logo_path:
        logo = Image.open(logo_path).convert("RGBA")
        logo.thumbnail((size - 6, size - 6), Image.LANCZOS)
        x = (size - logo.width) // 2
        y = (size - logo.height) // 2
        img.paste(logo, (x, y), logo)

    out_path = os.path.join(SCRIPT_DIR, "wizard_small_image.bmp")
    img.save(out_path, "BMP")
    print(f"  Created: {out_path} ({size}x{size})")


def main():
    print("Generating Inno Setup wizard images...")
    logo_path = find_logo()
    if logo_path:
        print(f"  Using logo: {logo_path}")
    else:
        print("  WARNING: No logo found, using solid gradient background")

    create_wizard_image(logo_path)
    create_wizard_small_image(logo_path)
    print("Done.")


if __name__ == "__main__":
    main()
