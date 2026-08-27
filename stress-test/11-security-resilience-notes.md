# 11 — Security & Resilience Notes

Part of the [DERSİS stress-test audit](00-README.md). Findings registered as
`ST-SEC-*` in the [findings register](12-findings-register.md).

**Threat model.** DERSİS is a *local, offline, single-user desktop application*
with no server, accounts, or network API. Severities are calibrated to that
reality: there is no remote attack surface, so most issues are Medium/Low. The
genuinely elevated items are the **CI auto-publish of unvetted "latest" builds**
(High — it reaches real users) and the **local data-loss / privilege-escalation**
paths. Every claim below was verified by probe or by reading the exact code/CI
file; OBSERVED vs INFERRED is marked inline.

## Crypto reality-check: obfuscation, not protection

**Container format (correct parts).** `.egu` files use a well-formed binary container: `EGU1` magic + `uint16` version + 16-byte salt + 12-byte AES-GCM nonce + `uint32` payload length + ciphertext(+16-byte GCM tag) + trailing SHA-256 (`storage.py:216-239`). Parsing validates size, magic (accepting legacy `UVA1`), a hard `version == 1` check, the length header, and the checksum before decrypting (`storage.py:242-296`). **Nonce hygiene is correct** — I saved identical data twice and inspected the raw bytes: both the 16-byte salt and the 12-byte GCM nonce differed on every save (`iv1 8880e3e4… vs iv2 16724ddd…`), so there is no catastrophic GCM nonce reuse.

**Why it is obfuscation, not confidentiality.** The 32-byte AES-256 master key is written as raw plaintext to `keys/key.bin` inside the very tree it protects, `~/Documents/Dersis/keys/` (`storage.py:186-196`; probe: `key.bin` is exactly 32 bytes and lives under the data root). Anyone who can read the `.egu` files can read the key sitting beside them. The one hardening attempt, `os.chmod(kp, 0o600)` (`storage.py:197-200`), is a silent no-op on Windows. The per-file key is `sha256(master || salt)`, commented as an 'HKDF-like construction' (`storage.py:225-226`) but is not HKDF, and the SHA-256 trailer merely duplicates the GCM authentication tag. Net effect: the scheme defends against **casual inspection and file corruption/tampering only** — a fair and useful goal — but provides **no protection against a local attacker, another user of the same PC, or a stolen/lost device**.

**Silent key regeneration → permanent data loss (reproduced).** `_load_or_create_key` regenerates the key whenever `key.bin` exists with a length other than 32, moving the old one to `backups/` — and this fires on a *load*, not just first-run (`storage.py:183-201`). Sandbox repro: saved `data.egu`, truncated `key.bin` to 3 bytes, cleared the cache to mimic a fresh process, then `load_encrypted(data.egu)` raised `EguFileError: Decryption failed… encrypted with a different key`, `key.bin` was silently regenerated, and the original was only recoverable by hand from `backups/key.bin`. Because the entire timetable, settings, and learning data share this one key, a single length-changing corruption (partial write, AV quarantine, sync truncation) is total, silent, unrecoverable loss. A same-length bit-flip is worse: length stays 32, the wrong key is kept, and nothing self-heals.

**Claims mismatch.** `README-en.md:152` ('Encrypted local storage'), `README-en.md:304` ('Encryption at rest'), `docs/APP_OVERVIEW.md:109`, and `docs/CONTEXT.md:61` ('Encrypted Everything') would lead a school administrator to believe schedules are protected if the laptop is stolen or shared. They are not. `docs/CONTEXT.md:63` is the one honest line ('protecting institutional data from tampering'). Recommend aligning the user-facing wording to 'tamper/corruption detection', or upgrading to a passphrase-derived key (Argon2id/scrypt) or OS keystore (DPAPI/Keychain) so the key is not recoverable from disk alone.

## Supply chain & release integrity

**Auto-publish of unvetted 'latest' on every push to main.** `build-release.yml` triggers on `push: branches: [main]` (`build-release.yml:27-31`) and publishes a full, non-prerelease GitHub Release via `softprops/action-gh-release` with `draft:false, prerelease:false` (`build-release.yml:159-170`), tag `v<ver>-build.<run_number>`. The newest such release becomes the repo's **`latest`**. Confirmed live (read-only API + `scripts/download_release.py --list`): `releases/latest` = `DERSIS v1.0.0-build.10` (2026-06-19), whose only asset is `Dersis_Setup_v1.0.0.exe` (113.4 MiB) — **no `.sha256`, no macOS `.dmg/.zip`**. `README.md:13/50`, `docs/MACOS.md`, and `scripts/download_release.py` all send users to `releases/latest`, so users get untested dev builds, macOS users get nothing, and the installer they receive has no companion checksum. No curated `vX.Y.Z` release has ever run, so the tag-driven pipelines are unexercised.

**Unpinned, unverified build inputs.** Each publishing build fetches third-party binaries over HTTPS with no hash/signature check: the Python 3.11.9 embeddable zip (pinned version, unhashed), `get-pip.py` (always latest), and Inno Setup `is.exe` fetched as **latest** — not even version-pinned — via `Invoke-WebRequest https://jrsoftware.org/download.php/is.exe` (`build-release.yml:123`, `build-installer.yml:327`, and `release.yml`). Any upstream compromise or silent update flows straight into an **unsigned** `Dersis_Setup_v*.exe` shipped to schools, and installer bytes vary per build date (no reproducibility). Dependency pinning is also inconsistent — Windows installs `requirements-lock.txt` while macOS (`build_mac.sh:83`) and `ci.yml` install unpinned `>=` specs — and the lock file is dirty (phantom `urllib3`/`idna`/`certifi`, missing `tzdata`), so same-tag Windows/macOS releases can ship different library versions.

**Installer ACL → binary planting / LPE.** `installer.iss:108-109` sets `Permissions: users-modify` on `{app}`. `PrivilegesRequired=lowest` (`installer.iss:58`) makes the default install per-user (low impact), but an elevated install lands under `Program Files` with that permissive ACL, letting any standard user overwrite `Dersis.exe`/`pythonw.exe` or plant a DLL (the embed dist bundles a full `python.exe`+`pip` and many Qt/Python DLLs in that writable dir, `installer.iss:98`). The next launch by another user — including an admin — runs planted code. Binaries are unsigned, so nothing flags the swap.

**Placeholder identity.** `AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}}` (`installer.iss:40`) is a copy-paste placeholder GUID (collision/upgrade-detection risk), and neither Windows nor macOS binaries are properly signed/notarized (`docs/MACOS.md`).

## Runtime network egress from an 'offline' app

DERSIS advertises 'no network calls of any kind' (`README-en.md:151`, `docs/CONTEXT.md:63`), but three UI paths shell out to `pip install` from PyPI:

- PDF export with missing reportlab → `subprocess.check_call([sys.executable,'-m','pip','install','reportlab'], timeout=120)` (`app.py:3906-3915`).
- Excel import/export with missing pandas/openpyxl (`app.py:4460-4468`, duplicated in `dialogs.py:308-318`).

A single click on the confirmation dialog produces real outbound network traffic, and if PyPI or any resolved transitive dependency were compromised it would fetch and execute attacker-controlled wheels into the app's environment. In a frozen Nuitka/PyInstaller build `sys.executable` is `Dersis.exe`, so the call is also simply broken (spawns the GUI with `-m pip install …` as bogus args) and blocks the GUI thread up to 120 s. A correctly built embed dist already bundles these libraries (`requirements-lock.txt` + `verify_deps.py`), so the branch is dead where it 'works' and harmful where it does not. Recommend deleting the fallbacks and failing with a clear message instead.

## Local robustness: no single-instance lock (lost-update reproduced)

A repo-wide grep for `QLockFile|QSharedMemory|CreateMutex|single-instance|lockfile` returns **nothing** — there is no single-instance mechanism. The whole timetable is embedded under `data['state']` in `settings/app_settings.egu`, and `_auto_save` performs a full read-decrypt-modify-encrypt-write of that container after essentially every user action (`app.py:1835-1851`).

Sandbox demonstration of the resulting lost-update: two 'instances' both `load_encrypted(app_settings.egu)` from the same starting file; instance 1 appended a class and set `language='de'` and saved; instance 2 (stale snapshot) appended a different class and saved **last**. Final file classes: `['A','B','D_from_inst2']`, language `'tr'` — instance 1's added class **and** its language change were both silently discarded. `os.replace` prevents torn files but not lost updates, so a user who simply opens DERSIS twice loses whichever window they edited first, with no warning. Recommend a startup `QLockFile`/`QSharedMemory` guard that focuses the existing window.

## Warnings-panel HTML/markup injection (UI spoofing only)

`WarningLog.log` composes rich text by interpolating the message straight into HTML with no escaping — `lines.append(f'<span style="color:{c}">{msg}</span>')` then `self._log_area.setHtml(...)` (`widgets.py:232-236`) — and also feeds it to a `QLabel` under default AutoText via `_latest_label.setText(message)` (`widgets.py:225`). The message text is user-influenced: it is built from year/branch/class names in `_refresh_warnings` (`app.py:2992-2997`, `f"{yr}/{br}: …"`) and `_run_auto_negotiation` (`app.py:3028`, `f"{cls['name']}: …"`).

Headless probe (`QT_QPA_PLATFORM=offscreen`) driving the exact sink:

- Class name `Math<b>PWNED</b>101` → `toPlainText()` returns `MathPWNED101` (the `<b>` was interpreted as markup and stripped, i.e. rendered bold).
- `Room<img src="http://evil.example/x.png">A` → parsed into an embedded object: `toPlainText()` contains the U+FFFC object-replacement char and `toHtml()` contains an `img` node.
- `QLabel.setText('Math<b>PWNED</b>101')` under AutoText → `mightBeRichText` = True (rendered as rich text).

**Impact is limited to UI spoofing/defacement and possible layout DoS** inside the diagnostics panel. Qt rich text does **not** execute JavaScript, and a plain `QTextEdit` does not fetch remote `<img>` URLs by default, so there is no code execution and no automatic network egress from the `<img>` payload. It remains a correctness defect worth fixing with `html.escape()` on interpolated names, or by forcing plain-text format on both widgets.

## download_release.py: checksum & token handling

`scripts/download_release.py` is a stdlib-only, user-side CLI (the offline app has no updater). TLS uses urllib's default verifying context — good. SHA-256 is verified **only** against the GitHub API asset `digest` field (`download_release.py:95-101,121-132`). I confirmed the live asset advertises `sha256:6e524c84…639f`, so verification currently runs; but the digest travels in the same `api.github.com` response as the download URL, making it a TLS-integrity assumption rather than an independent signature. When the digest is **absent**, the tool prints `verification skipped` and exits success (`download_release.py:133-134`) — fail-open. The workflow-generated `.sha256` release asset is never consulted (and, per the release finding, is not even attached to `latest`).

Token handling: when `GITHUB_TOKEN`/`GH_TOKEN` is set, an `Authorization: Bearer` header is added (`download_release.py:49-52`); urllib's default redirect handler re-sends request headers on redirect, so the token is forwarded when `browser_download_url` redirects from `api.github.com`/`github.com` to `objects.githubusercontent.com` (INFERRED from documented urllib redirect behavior). Recommend fail-closed verification (require a digest or the published `.sha256`) and stripping credentials on cross-host redirects.

## Privacy: crash logs, mailto content, learning data

Crash tracebacks are written in plaintext to `logs/crash_log.txt` (`scheduler_gui.py:113-128`). The crash/bug reporter composes a `mailto:dersis.app@gmail.com` body containing the app version, an OS string (`platform.system()/release()/machine()`), and up to 4000 characters of traceback (`bug_report.py:354-383,507-525`). Tracebacks and exception messages routinely embed absolute paths; storage references `~/Documents/Dersis`, which expands to `C:\Users\<username>\Documents\Dersis\…`, so a report the user is prompted to send can disclose their Windows account name and folder layout. **Nothing is transmitted automatically** — `_open_mailto` only opens the default mail client, with a clipboard fallback (`bug_report.py:161-189`) — so exposure requires the user to actually send the email, keeping this Low. The feedback/learning logs store class and teacher names (institutional data) but stay local under the obfuscation-only `.egu` encryption. Recommend redacting the user-profile path prefix (replace with `~`) before writing the crash log and composing the mail body, and telling the user in the dialog what the report will contain.
---

## Cross-references

- Silent key regeneration ([ST-DATA-001](12-findings-register.md#st-data-001))
  and corrupt-container replacement ([ST-DATA-014](12-findings-register.md)) are
  the data-loss twins of the crypto section here
  ([ST-SEC-002](12-findings-register.md#st-sec-002)).
- The warnings-panel HTML injection is also tracked as
  [ST-UI-007](12-findings-register.md) (UI correctness) and pairs with the CSV
  formula-injection finding [ST-UI-008](12-findings-register.md).
- No single-instance lock is [ST-DATA-012](12-findings-register.md); it is a
  data-integrity risk as much as a robustness one.
- Release/CI issues connect to [ST-ARCH-002](12-findings-register.md) (dead CI
  trigger) in the [architecture audit](10-code-architecture-audit.md).
