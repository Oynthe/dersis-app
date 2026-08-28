"""A README's correction is only as true as the page it hands the reader to.

ST-SEC-002 cluster. Phase 7 deleted "download ``Dersis-<version>-mac-arm64.dmg``
from the releases page" from the five READMEs — and, in the very passage that
deleted it, added a link to ``docs/MACOS.md``, which carried the identical
promise one click away under the heading "For users — what to download": a
``releases/latest`` link, then a two-row table of ``.dmg`` filenames, then "A
``.zip`` of the same app is also published", then "Installing: 1. Open the
``.dmg``". Measured against the live repository, the latest release carries one
asset, ``Dersis_Setup_v1.0.0.exe``, and no macOS artifact has ever been attached
to any release.

``tests/test_readme_claims.py`` could not see it: both of its macOS
parametrisations iterate ``ALL_READMES``, the five README files themselves.
So the correction moved the falsehood rather than removing it.

The property here is the one that was missing — **a document a README sends the
reader to must not offer a download that does not exist** — stated over the
links as they are today, so the next page linked from a README is covered on the
day it is linked rather than on the day someone remembers to add it here.

Placeholder forms (``mac-<arch>``, ``mac-<mimari>``) are deliberately not
matched anywhere in this module: they describe what ``./build_mac.sh`` writes
into ``dist/`` on the machine you run it on, which is true.

Pure text. No imports from ``scheduler_app``, no Qt, no network.
"""
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

READMES = ("README.md", "README-en.md", "README-tr.md",
           "README-de.md", "README-es.md")

# Asset names that were advertised and never published.
NEVER_PUBLISHED_ASSET = re.compile(r"mac-(?:arm64|x64)")

# A downloadable bundle named close to a link to the releases page.
DOWNLOADABLE_BUNDLE = re.compile(r"\.dmg|\.zip")
RELEASE_PAGE = "releases/latest"

# How far a "download this file" instruction can sit from its release link.
# The section this module exists for put the filenames three lines below it.
LINK_WINDOW_BEFORE = 2
LINK_WINDOW_AFTER = 8

# ``[text](target)`` — target only, no whitespace, so reference-style link
# definitions and image links inside HTML are both handled the same way.
MD_LINK = re.compile(r"\]\(([^)\s]+)\)")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _linked_markdown_docs():
    """In-repo Markdown files the READMEs link to, excluding the READMEs.

    Relative targets only: an ``https://`` link is somebody else's page, and a
    non-Markdown target (a script, an image) carries no prose to check.
    """
    found = set()
    for name in READMES:
        for target in MD_LINK.findall(_read(os.path.join(REPO_ROOT, name))):
            target = target.split("#")[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            path = os.path.normpath(os.path.join(REPO_ROOT, target))
            rel = os.path.relpath(path, REPO_ROOT)
            if rel in READMES or not rel.lower().endswith(".md"):
                continue
            if os.path.isfile(path):
                found.add(rel)
    return sorted(found)


LINKED_DOCS = _linked_markdown_docs()


def test_the_link_walk_still_reaches_the_macos_guide():
    """Guard on the guard: an empty walk would make everything below vacuous.

    ``docs/MACOS.md`` is the page this module was written for, and the READMEs
    link to it from their macOS sections. If it stops being discovered, either
    the links changed or ``MD_LINK`` stopped matching them — and the two tests
    below would pass by checking nothing.
    """
    assert LINKED_DOCS, "no README links to any in-repo Markdown document"
    assert os.path.join("docs", "MACOS.md") in LINKED_DOCS, (
        "the READMEs no longer link to docs/MACOS.md, or the walk stopped "
        "finding it: %r" % LINKED_DOCS)


@pytest.mark.parametrize("name", LINKED_DOCS)
def test_no_readme_linked_doc_offers_a_bundle_beside_a_release_link(name):
    """The exact shape the false promise took, one link out from the README.

    A failure means a reader who followed a README's link is being sent to the
    releases page for a ``.dmg`` or ``.zip`` that is not attached to it. If a
    macOS bundle is genuinely published one day, check the release assets before
    touching this test.
    """
    lines = _read(os.path.join(REPO_ROOT, name)).splitlines()
    offences = []
    for i, line in enumerate(lines):
        if RELEASE_PAGE not in line:
            continue
        window = lines[max(0, i - LINK_WINDOW_BEFORE): i + LINK_WINDOW_AFTER + 1]
        offences += [(i + 1, w.strip()) for w in window
                     if DOWNLOADABLE_BUNDLE.search(w)]
    assert not offences, (
        f"{name} is linked from a README and advertises a downloadable bundle "
        f"next to a releases/latest link: {offences}. Only "
        f"Dersis_Setup_v1.0.0.exe is published.")


def test_the_macos_guide_points_at_a_local_build_not_a_release_asset():
    """``docs/MACOS.md`` is where the READMEs send every Mac user.

    Two things must hold together: no concrete asset name that was never
    published anywhere on the page, and a "what to download" section that names
    the thing that actually produces the bundle. Either one alone can be
    satisfied by a page that still leaves the reader empty-handed.
    """
    text = _read(os.path.join(REPO_ROOT, "docs", "MACOS.md"))
    hits = [line.strip() for line in text.splitlines()
            if NEVER_PUBLISHED_ASSET.search(line)]
    assert not hits, (
        "docs/MACOS.md names a macOS release asset that has never been attached "
        "to a DERSİS release: %r. Use the mac-<arch> placeholder, which "
        "describes what ./build_mac.sh writes into dist/." % hits)

    section = next((body for _lvl, heading, body in _sections(text)
                    if "download" in heading.lower()), None)
    assert section is not None, (
        "docs/MACOS.md no longer has a section telling a user what to download; "
        "the READMEs link here for exactly that")
    assert "build_mac.sh" in section and "dist/" in section, (
        "docs/MACOS.md's download section does not say the bundle comes from "
        "./build_mac.sh into dist/, so it leaves the reader looking for a file "
        "nobody publishes")
    assert RELEASE_PAGE not in section, (
        "docs/MACOS.md's download section links to the releases page again; "
        "no macOS artifact has ever been attached to a release")


_ATX = re.compile(r"^(#{1,6})\s+(.*)$")


def _sections(text):
    """Split Markdown into (level, heading, body); a section ends at any heading."""
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
