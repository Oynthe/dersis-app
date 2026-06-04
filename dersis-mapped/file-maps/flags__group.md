# Group map: `flags/*.png` (22 country flag images)

## Overview

22 PNG flag icons used by `ui/first_run.LanguageSelectorDialog` to render the language picker. Each PNG is loaded via `ui/icons._png_flag_icon(filename)`.

## File list

| Flag PNG | Loader helper |
|----------|---------------|
| `azerbaijan-206711.png` | `flag_az` |
| `brazil-206597.png` | `flag_br` |
| `china-206818.png` | `flag_cn` |
| `denmark-206678.png` | `flag_dk` |
| `france-206657.png` | `flag_fr` |
| `germany-206690.png` | `flag_de` |
| `india-206606.png` | `flag_in` |
| `indonesia-206643.png` | `flag_id` |
| `iran-206716.png` | `flag_ir` |
| `italy-206839.png` | `flag_it` |
| `japan-206789.png` | `flag_jp` |
| `netherlands-206615.png` | `flag_nl` |
| `poland-206641.png` | `flag_pl` |
| `portugal-206628.png` | `flag_pt` |
| `russia-206604.png` | `flag_ru` |
| `saudi-arabia-206719.png` | `flag_sa` |
| `south-africa-206652.png` | `flag_za` |
| `south-korea-206758.png` | `flag_kr` |
| `spain-206724.png` | `flag_es` |
| `sweden-206668.png` | `flag_se` |
| `turkey-206634.png` | `flag_tr` |
| `united-kingdom-206592.png` | `flag_gb` |

## How they're discovered

`ui/icons.py` computes `_FLAGS_DIR` from `__file__` (three levels up from `ui/`). After Nuitka build, the directory is copied via `--include-data-dir=flags/=flags`.

## Why grouped

Binary PNGs with no executable content. The names are tied to a stock-icon set's IDs (206XXX). Mapping each individually would be noise.

## Risks and maintenance notes

- Renaming a PNG breaks the corresponding `flag_*` helper in `ui/icons.py`. Treat the filenames as a stable API.
- Adding a language requires (a) the PNG, (b) a helper in `icons.py`, (c) a button in `first_run.LanguageSelectorDialog`, (d) a translation block in `ui/translations.py`.
