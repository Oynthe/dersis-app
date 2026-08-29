"""The READMEs may only promise what the product actually does — ST-SEC-002.

Two promises were measurably false, and both were false in a way no runtime test
could ever notice, because they live in Markdown.

**1. The storage is not confidential, and the READMEs implied it was.**
``storage.py`` really does use AES-256-GCM, with a fresh 32-byte key from
``secrets.token_bytes``, a unique salt and nonce per file, and a SHA-256
checksum.  Every one of those sentences is true.  What was false was the
*framing*: the encryption bullet sat under a section headed **privacy** (
"Experience and privacy", "Deneyim ve gizlilik", "Bedienung und Datenschutz",
"Experiencia y privacidad"), directly under "no network calls of any kind" and
"the app itself never transmits anything".  A reader takes confidentiality from
that adjacency, and the English table called it, in the term of art,
*"Encryption at rest"* with *"OS keychain integration"* as the alternative.

The key is written to ``Documents/Dersis/keys/key.bin`` — the same tree as the
ciphertext in ``Documents/Dersis/saves/``.  A container parser written from
scratch, reading only files under that root, recovers a class name in about ten
lines.  So the property on offer is integrity plus opacity: a damaged or
hand-edited file is detected instead of loaded, and the saves are not readable
in a text editor.  It is not a lock, and ``tests/test_storage_roundtrip.py``
used to say in a docstring that it was.

The fix that was *not* taken, and why: wrapping ``key.bin`` with Windows DPAPI
buys nothing on a shared login (one profile is one principal) and near-nothing
on separate logins (``Documents`` already grants only the owner, SYSTEM and
Administrators), while a 282-byte DPAPI blob trips ``storage.py``'s
``len(key) == 32`` check and produces ST-DATA-001's "your saved timetables
cannot be opened" — a new, permanent, silent data-loss mode.  Measured: 3 of 42
storage tests fail under a naive wrapper.  Saying the true thing is cheaper and
does not put anyone's work at risk.

**2. The macOS download did not exist.**  Four READMEs told Mac users to fetch
``Dersis-<version>-mac-arm64.dmg`` or ``-mac-x64.dmg`` from ``releases/latest``.
Measured against the live repository: the latest release carries exactly one
asset, ``Dersis_Setup_v1.0.0.exe``, and **no macOS artifact has ever been
attached to any release**.  ``release.yml`` does build and upload them, but with
``fail_on_unmatched_files: false``, so it can silently publish Windows-only
again.  The macOS *build* is real and works — only the download promise was
false — so the build instructions stay and this module does not touch them.

Pure text.  No imports from ``scheduler_app``, no Qt, no filesystem beyond the
five README files.
"""
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The hub plus the four translations.  README.md carries no crypto strings (it
# is a language switcher and a download table), so it is exempt from the
# storage assertions and included only in the macOS one.
LANGUAGE_READMES = ("README-en.md", "README-tr.md", "README-de.md", "README-es.md")
ALL_READMES = ("README.md",) + LANGUAGE_READMES

# Words that would put the confidentiality claim back.  Matched as plain
# substrings against ``text.lower()``.
#
# Turkish note: ``"GİZLİ".lower()`` is ``"gi̇zli̇"`` (i + combining dot), which
# does *not* contain ``"gizli"``.  The patterns below are the lowercase forms the
# READMEs actually use; an all-caps Turkish reintroduction would slip past, which
# is a limit of this check, not a licence.
CONFIDENTIALITY_WORDS = {
    "README-en.md": ("private", "privacy", "confidential", "secure", "security",
                     "protected", "only you", "no one else"),
    "README-tr.md": ("gizli", "gizlilik", "güvenli", "güvenlik", "korumalı",
                     "yalnızca siz", "sadece siz"),
    "README-de.md": ("privat", "datenschutz", "vertraulich", "geheim", "sicher",
                     "sicherheit", "geschützt", "nur sie"),
    "README-es.md": ("privado", "privacidad", "confidencial", "seguro",
                     "seguridad", "protegido", "solo usted"),
}

# Where the key lives.  Every language README must say this next to its
# encryption bullet, in whatever words -- the path itself is the assertion.
KEY_LOCATION = "keys/key.bin"

# Asset names that were advertised and never published.
NEVER_PUBLISHED_ASSET = re.compile(r"mac-(?:arm64|x64)")

# A downloadable bundle named close to a link to the releases page.
DOWNLOADABLE_BUNDLE = re.compile(r"\.dmg|\.zip")
RELEASE_PAGE = "releases/latest"

# How far a "download this file" instruction can sit from its release link.
# The four false sections all put the filenames within four lines of it.
LINK_WINDOW_BEFORE = 2
LINK_WINDOW_AFTER = 8


def _read(name):
    with open(os.path.join(REPO_ROOT, name), encoding="utf-8") as fh:
        return fh.read()


_ATX = re.compile(r"^(#{1,6})\s+(.*)$")


def _sections(text):
    """Split Markdown into (level, heading, body).

    A section ends at the next heading of *any* level, so a ``###`` block never
    swallows the ``##`` chapters that follow it.
    """
    out = []
    level, heading, body = None, None, []
    for line in text.splitlines():
        match = _ATX.match(line)
        if match:
            if heading is not None:
                out.append((level, heading, "\n".join(body)))
            level, heading, body = len(match.group(1)), match.group(2).strip(), []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        out.append((level, heading, "\n".join(body)))
    return out


def _storage_section(name):
    """The one ``###`` section that advertises encrypted storage to a user.

    Identified by the AES-256-GCM string.  Two other places mention it -- the
    project-structure listing and the technology-stack table -- and both sit
    under ``##`` chapters with no ``###`` of their own, so the match is unique.
    If that ever stops being true the test says so rather than silently checking
    the wrong text.
    """
    text = _read(name)
    hits = [(h, b) for lvl, h, b in _sections(text)
            if lvl == 3 and "AES-256-GCM" in b]
    assert len(hits) == 1, (
        f"{name}: expected exactly one heading section advertising AES-256-GCM, "
        f"found {len(hits)}: {[h for h, _ in hits]}. Point this test at the right "
        f"section rather than deleting it.")
    return hits[0]


# ── 1. the storage section may not promise confidentiality ───────────────────

@pytest.mark.parametrize("name", LANGUAGE_READMES)
def test_the_encrypted_storage_section_does_not_promise_confidentiality(name):
    """ST-SEC-002: the encryption bullet may not sit under a privacy claim.

    A failure means the README is back to telling a school that its staff and
    student names are private, when ``keys/key.bin`` sits in the same folder as
    the ciphertext and a ten-line script reads them out.
    """
    heading, body = _storage_section(name)
    haystack = (heading + "\n" + body).lower()

    found = [w for w in CONFIDENTIALITY_WORDS[name] if w in haystack]
    assert not found, (
        f"{name}: the section '{heading}' describes the encrypted storage using "
        f"{found}. DERSİS stores the AES key at Documents/Dersis/keys/key.bin, "
        f"beside the saves it decrypts, so the files are opaque and tamper-"
        f"evident but not confidential against anyone who can open the folder. "
        f"Say that instead (ST-SEC-002).")


@pytest.mark.parametrize("name", LANGUAGE_READMES)
def test_the_encrypted_storage_section_says_where_the_key_lives(name):
    """ST-SEC-002: the fact that makes the claim honest must be on the page.

    A failure means the README mentions AES-256-GCM without mentioning that the
    key ships next to the data, which is the omission that made the original
    wording misleading.
    """
    heading, body = _storage_section(name)
    assert KEY_LOCATION in body, (
        f"{name}: the section '{heading}' claims AES-256-GCM without naming "
        f"{KEY_LOCATION}. The key's location is what turns 'encrypted' from a "
        f"confidentiality promise into an accurate one (ST-SEC-002).")


# ── 2. no README may advertise a download that does not exist ────────────────

@pytest.mark.parametrize("name", ALL_READMES)
def test_no_readme_names_a_macos_asset_that_was_never_published(name):
    """ST-SEC-002 cluster: ``-mac-arm64.dmg`` has never existed on a release.

    A failure means a Mac user is being sent to the releases page for a file
    that is not there. The placeholder forms used in the build instructions
    (``mac-<arch>``, ``mac-<mimari>``) are deliberately not matched: those
    describe what a local build writes into ``dist/``, which is true.
    """
    hits = [line.strip() for line in _read(name).splitlines()
            if NEVER_PUBLISHED_ASSET.search(line)]
    assert not hits, (
        f"{name} names a macOS release asset that has never been attached to a "
        f"DERSİS release (the latest carries one file, Dersis_Setup_v1.0.0.exe): "
        f"{hits}")


@pytest.mark.parametrize("name", ALL_READMES)
def test_no_readme_offers_a_bundle_download_beside_a_release_link(name):
    """ST-SEC-002 cluster: ``releases/latest`` may not sit beside a bundle name.

    This is the shape the false promise took in each of the three files that
    carried it: a link to the releases page, then within a few lines the name of
    a bundle to download from it. A failure means someone has put one back, or
    that a genuinely published macOS bundle now needs this test updated -- check
    the release assets before you touch it.
    """
    lines = _read(name).splitlines()
    offences = []
    for i, line in enumerate(lines):
        if RELEASE_PAGE not in line:
            continue
        window = lines[max(0, i - LINK_WINDOW_BEFORE): i + LINK_WINDOW_AFTER + 1]
        offences += [(i + 1, w.strip()) for w in window
                     if DOWNLOADABLE_BUNDLE.search(w)]
    assert not offences, (
        f"{name} advertises a downloadable bundle next to a releases/latest "
        f"link: {offences}. Only Dersis_Setup_v1.0.0.exe is published.")
