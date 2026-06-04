# File: `scheduler_app/ui/translations.py`

## 1. File Role
The translation table for the entire UI plus the `tr()` / `get_language()` / `set_language()` / `is_rtl()` API. Holds 22 language blocks (~21,790 lines total — by far the largest file in the project).

## 2. Why this file matters
**Critical.** Every visible string passes through here.

## 3. Imports and Dependencies
None (deliberately). Self-contained.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `TRANSLATIONS` (dict) | `{lang_code: {key: str, …}}`. Languages: `en`, `tr`, `de`, `fr`, `es`, `zh`, `ru`, `ar`, `fa`, `it`, `pt_BR`, `pt_PT`, `nl`, `sv`, `da`, `pl`, `az`, `hi`, `id`, `af`, `ja`, `ko`. |
| `_current_lang` | Module-level current code. Default `"en"`. |
| `tr(key, **kwargs)` | Translation + `str.format(**kwargs)` substitution; falls back to English then to the key itself. Never raises. |
| `get_language()`, `set_language(lang)` | Module-level accessor + setter (only sets if `lang in TRANSLATIONS`). |
| `RTL_LANGUAGES` (frozenset) | `{"ar", "he", "fa", "ur"}`. |
| `is_rtl(lang=None)` | True if current/given language is RTL. |

## 5. Block-by-block code map (logical sections — line-by-line is impractical)

| Line range (approx.) | Block | Notes |
|----------------------|-------|-------|
| 1–2 | docstring | "Multi-language translation support." |
| 3–1045 | `'en': { … }` | English block, ~1,000 keys. Source of truth for fall-back. |
| 1046–~2089 | `'tr': { … }` | Turkish. |
| 2090–~3071 | `'de': { … }` | German. |
| 3072–~4053 | `'fr': { … }` | French. |
| 4054–~5035 | `'es': { … }` | Spanish. |
| ~5036–~6017 | `'zh': { … }` | Chinese. |
| 6018–~6999 | `'ru': { … }` | Russian. |
| ~7000–~7981 | `'ar': { … }` | Arabic (RTL). |
| ~7982–~8963 | `'fa': { … }` | Persian (RTL). |
| 8964–~9945 | `'it': { … }` | Italian. |
| ~9946–~10927 | `'pt_BR': { … }` | Portuguese (Brazil). |
| ~10928–~11909 | `'pt_PT': { … }` | Portuguese (Portugal). |
| 11910–~12891 | `'nl': { … }` | Dutch. |
| ~12892–~13873 | `'sv': { … }` | Swedish. |
| ~13874–~14855 | `'da': { … }` | Danish. |
| 14856–~15837 | `'pl': { … }` | Polish. |
| 15838–~16819 | `'az': { … }` | Azerbaijani. |
| ~16820–~17801 | `'hi': { … }` | Hindi. |
| 17802–~18783 | `'id': { … }` | Indonesian. |
| ~18784–~19765 | `'af': { … }` | Afrikaans. |
| ~19766–~20747 | `'ja': { … }` | Japanese. |
| ~20748–~21729 | `'ko': { … }` | Korean. |
| 21730–21743 | dict close + `_current_lang = "en"` | |
| 21745–21766 | `tr(key, **kwargs)` | Translation with safe `format`. |
| 21767–21776 | `get_language` | |
| 21778–21785 | `set_language` | |
| 21787 | `RTL_LANGUAGES` | |
| 21789–21790 | `is_rtl` | |

Key naming conventions and prefix groups are detailed in `09_SETTINGS_LOCALIZATION_AND_PERSISTENCE_MAP.md`.

## 6. Runtime Behavior
- Loaded at import time (parsing this 22k-line dict is the slowest import in the project).
- `tr()` is called on every UI label, every error message, and every analytics insight — extremely hot path. Performance: dict lookup × 2 + optional `str.format`.

## 7. Data Flow
- In: key + kwargs.
- Out: translated string.

## 8. UI Flow
Every visible string in every dialog/menu/toast/tooltip comes from here.

## 9. Error Handling and Edge Cases
- Missing key → fallback to English → fallback to key itself.
- Bad `format` placeholder (KeyError/IndexError/ValueError) → return unformatted text (silent failure).
- `set_language` ignores unknown codes (silent no-op).
- `RTL_LANGUAGES` is `{"ar", "he", "fa", "ur"}` — note that `he` and `ur` are NOT among the supported translations; harmless because `is_rtl` only checks the membership.

## 10. Integration Points
Imported by virtually every UI module and many core modules (for translated error strings). `ui/tier_translations.py` extends the dict on import (adds `upgrade.*` keys).

## 11. Risks and Maintenance Notes
- Adding a key: add to ALL 22 language blocks; if any are missing, `tr()` falls back silently.
- Bad format placeholders silently produce literal `{name}` text.
- No parity test — recommend writing one (see `11_TESTING_AND_QA_MAP.md`).
- Editing the file requires care; consider scripted edits to maintain consistency.

## 12. Mini Summary
The 22-language translation table plus `tr()`/`set_language`/`is_rtl`. ~21,790 lines. Touch with discipline; falling back silently means typos won't crash anything but will display the wrong text.
