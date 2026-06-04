# Group map: `scheduler_app/assets/*.png` and `app_icon.ico`

## Overview

Binary application icons. Multiple resolutions are bundled for Windows + various Qt UI contexts.

| File | Format | Purpose |
|------|--------|---------|
| `scheduler_app/assets/app_icon.ico` | Windows ICO (multi-resolution) | Main Windows shortcut icon, Inno Setup. |
| `scheduler_app/assets/app_icon.png` | PNG | Default raster icon for Qt. |
| `scheduler_app/assets/app_icon_16.png` | PNG 16×16 | Toolbar / small UI. |
| `scheduler_app/assets/app_icon_32.png` | PNG 32×32 | Toolbar / dialog icons. |
| `scheduler_app/assets/app_icon_48.png` | PNG 48×48 | Larger icon contexts. |
| `scheduler_app/assets/app_icon_64.png` | PNG 64×64 | Login dialog logo size. |
| `scheduler_app/assets/app_icon_128.png` | PNG 128×128 | Splash / About. |
| `scheduler_app/assets/app_icon_256.png` | PNG 256×256 | High-DPI use, source for installer wizard images. |

## Usage

- Resolved via `scheduler_app/assets/__init__.py::asset_path(filename)`.
- The Inno Setup script (`installer.iss`) and Nuitka build (`build_nuitka.bat`) point at `app_icon.ico`.
- `installer/create_wizard_images.py` uses `app_icon_256.png` (or `dersis.png`) as the source for the installer wizard BMPs.

## Maintenance notes

- All icons share the same brand. Updating one resolution should be paired with regenerating the others (manually) and re-running `installer/create_wizard_images.py`.
- The ICO file is a multi-resolution container. Use ImageMagick or `iconvert.exe` to regenerate.

## Why these matter

Supporting. The app runs without them but UI quality drops sharply.
