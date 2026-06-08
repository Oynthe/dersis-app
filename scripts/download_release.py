#!/usr/bin/env python3
"""Fetch and download the latest DERSİS release installer from GitHub.

A small, dependency-free helper (Python 3.8+ standard library only). It asks the
GitHub API for the most recent published release of the project, resolves the
installer asset, downloads it, and verifies its SHA-256 checksum.

The installer file name embeds the version (e.g. ``Dersis_Setup_v1.0.0.exe``), so a
hard-coded download URL would break on every release. This script always resolves the
*current* asset instead, so it keeps working across versions.

Examples
--------
    # Download the latest Windows installer into the current directory
    python scripts/download_release.py

    # Show the latest release and its assets without downloading
    python scripts/download_release.py --list

    # Print the resolved download URL and exit (handy for CI / scripting)
    python scripts/download_release.py --url-only

    # Save to a specific path
    python scripts/download_release.py -o ~/Downloads/Dersis_Setup.exe

Set ``GITHUB_TOKEN`` (or ``GH_TOKEN``) to raise the GitHub API rate limit.
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_REPO = "Oynthe/dersis-app"
DEFAULT_ASSET_PATTERN = ".exe"
API_TEMPLATE = "https://api.github.com/repos/{repo}/releases/latest"
USER_AGENT = "dersis-download-release/1.0 (+https://github.com/Oynthe/dersis-app)"
CHUNK_SIZE = 1 << 16  # 64 KiB


def _open(url, accept="application/octet-stream"):
    """Open *url* with the headers GitHub expects, honouring a token if present."""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": accept}
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    return urllib.request.urlopen(request)  # nosec B310 - fixed https GitHub endpoints


def fetch_latest_release(repo):
    """Return the parsed JSON of the repository's latest published release."""
    url = API_TEMPLATE.format(repo=repo)
    try:
        with _open(url, accept="application/vnd.github+json") as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SystemExit(
                "No published release found for '%s'. Check the repository name "
                "or publish a release first." % repo
            )
        raise SystemExit(
            "GitHub API request failed (%s %s) for %s" % (exc.code, exc.reason, url)
        )
    except urllib.error.URLError as exc:
        raise SystemExit(
            "Could not reach GitHub (%s). Check your network connection." % exc.reason
        )


def pick_asset(release, pattern):
    """Choose the release asset matching *pattern* (suffix preferred, else substring)."""
    assets = release.get("assets") or []
    if not assets:
        raise SystemExit(
            "Release '%s' has no downloadable assets yet." % release.get("tag_name", "?")
        )
    pat = pattern.lower()
    by_suffix = [a for a in assets if a.get("name", "").lower().endswith(pat)]
    by_substring = [a for a in assets if pat in a.get("name", "").lower()]
    for candidate in by_suffix or by_substring:
        return candidate
    names = ", ".join(a.get("name", "?") for a in assets)
    raise SystemExit(
        "No asset matching '%s' in release '%s'. Available assets: %s"
        % (pattern, release.get("tag_name", "?"), names)
    )


def _expected_sha256(asset):
    """Return the lowercase hex SHA-256 the API advertises for *asset*, if any."""
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1].strip().lower()
    return None


def download_asset(asset, dest, verify=True):
    """Stream *asset* to *dest*, verifying its SHA-256 when the API advertises one."""
    url = asset["browser_download_url"]
    total = int(asset.get("size") or 0)
    expected = _expected_sha256(asset) if verify else None
    hasher = hashlib.sha256()
    done = 0
    print("Downloading %s (%s) -> %s" % (asset["name"], _human(total), dest))
    with _open(url) as response, open(dest, "wb") as out:
        while True:
            chunk = response.read(CHUNK_SIZE)
            if not chunk:
                break
            out.write(chunk)
            hasher.update(chunk)
            done += len(chunk)
            _progress(done, total)
    sys.stdout.write("\n")
    if expected:
        actual = hasher.hexdigest()
        if actual != expected:
            try:
                os.remove(dest)
            except OSError:
                pass
            raise SystemExit(
                "SHA-256 mismatch!\n  expected %s\n  actual   %s\n"
                "The download was deleted; please try again." % (expected, actual)
            )
        print("SHA-256 verified: %s" % actual)
    elif verify:
        print("Note: release advertised no SHA-256 digest; verification skipped.")
    return dest


def _progress(done, total):
    if total > 0:
        sys.stdout.write(
            "\r  %3d%%  %s / %s" % (done * 100 // total, _human(done), _human(total))
        )
    else:
        sys.stdout.write("\r  %s" % _human(done))
    sys.stdout.flush()


def _human(num):
    size = float(num)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return "%d B" % size if unit == "B" else "%.1f %s" % (size, unit)
        size /= 1024


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Download the latest DERSİS release installer from GitHub."
    )
    parser.add_argument(
        "--repo", default=DEFAULT_REPO, help="owner/name (default: %s)" % DEFAULT_REPO
    )
    parser.add_argument(
        "--asset",
        default=DEFAULT_ASSET_PATTERN,
        help="asset name suffix/substring to match (default: '%s')" % DEFAULT_ASSET_PATTERN,
    )
    parser.add_argument(
        "-o", "--output", help="destination file path (default: asset name in CWD)"
    )
    parser.add_argument(
        "--list", action="store_true", help="list the latest release and its assets, then exit"
    )
    parser.add_argument(
        "--url-only", action="store_true", help="print the resolved download URL, then exit"
    )
    parser.add_argument(
        "--no-verify", action="store_true", help="skip SHA-256 verification"
    )
    args = parser.parse_args(argv)

    release = fetch_latest_release(args.repo)
    tag = release.get("tag_name", "?")

    if args.list:
        print("Latest release: %s  (tag %s)" % (release.get("name") or tag, tag))
        print("Published:      %s" % release.get("published_at", "?"))
        print("Page:           %s" % release.get("html_url", "?"))
        print("Assets:")
        assets = release.get("assets") or []
        for a in assets:
            print(
                "  - %s  (%s)  %s"
                % (a.get("name"), _human(a.get("size") or 0), a.get("browser_download_url"))
            )
        if not assets:
            print("  (none)")
        return 0

    asset = pick_asset(release, args.asset)

    if args.url_only:
        print(asset["browser_download_url"])
        return 0

    print("Latest release: %s  (tag %s)" % (release.get("name") or tag, tag))
    dest = download_asset(asset, args.output or asset["name"], verify=not args.no_verify)
    print("Done: %s" % os.path.abspath(dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
