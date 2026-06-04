# File: `installer/create_wizard_images.py`

## 1. File Role
Generates the two BMP wizard images for Inno Setup from the Dersis logo. Uses Pillow.

## 2. Why this file matters
Supporting (build pipeline asset).

## 3. Imports and Dependencies
- stdlib: `os`, `sys`.
- Third-party: `PIL.{Image, ImageDraw}` (Pillow).

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `SCRIPT_DIR`, `REPO_ROOT` | Path constants. |
| `PRIMARY_PURPLE`, `SECONDARY_NAVY` | Brand colours `(110, 79, 158)` and `(30, 32, 88)`. |
| `LOGO_CANDIDATES` | Source images in preference order: `docs/dersis.png`, `app_icon_256.png`, `app_icon.png`. |
| `find_logo()` | First existing candidate. |
| `main()` | Generates `wizard_image.bmp` (164×314) and `wizard_small_image.bmp` (55×55). |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–13 | docstring + import guards | Aborts if Pillow missing. |
| 16–22 | path + colour constants | |
| 25–34 | `LOGO_CANDIDATES` | |
| 37–~50 | `find_logo` | |
| ~50–110 | drawing + main | composes the two BMPs with brand background. |

## 6. Runtime Behavior
Run once before building the installer.

## 7. Data Flow
- In: logo PNG.
- Out: two BMP files written into `installer/`.

## 8. UI Flow
Not applicable.

## 9. Error Handling and Edge Cases
- No Pillow → abort with message.
- No logo found → abort.

## 10. Integration Points
Output consumed by `installer.iss`.

## 11. Risks and Maintenance Notes
- Brand colours hardcoded; if the brand changes, update this script (and any other place that hardcodes the brand colour).

## 12. Mini Summary
Pillow script to produce Inno Setup wizard BMPs. Run once before the installer build.
