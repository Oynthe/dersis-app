"""What ships, and what the build trusts on its way there — ST-SEC-001/004/006.

Three separate holes, one file, because they share a subject: the path from a
commit to a `.exe` on a user's machine.

**ST-SEC-004 is not hypothetical; it already fired.** On 2026-08-28
``https://jrsoftware.org/download.php/is.exe`` — the URL three workflows used to
fetch the Inno Setup compiler — 302s to ``/isdl.php`` and returns
``Content-Type: text/html``, 10478 bytes, starting ``<!``. ``Invoke-WebRequest
-OutFile`` writes that HTML into ``innosetup.exe`` and **exits 0**;
``Start-Process`` then dies *"The file or directory is corrupted and
unreadable."* That is verbatim the failure of build-release runs 11, 13, 14, 15
and 16 (Aug 27-28). Had the substituted bytes been a working executable instead
of a download page, they would have been installed with ``/VERYSILENT`` and used
to compile the installer users download — no hash, no signature, no log line.
``test_every_build_time_download_is_pinned_and_hash_checked`` would have gone red
in June, when the URL was written, instead of breaking silently in August.

**ST-SEC-001.** ``/releases/latest`` is ``v1.0.0-build.10``, ``prerelease:
false``, 105+ downloads, published by ``build-release.yml`` from an unreviewed
push to an unprotected ``main``. That workflow is gone; the tests below pin that
no successor grows the same trigger, and that the surviving publisher gates on a
tag. They also pin that CI *runs* on a tag push — it did not, so ``ci.yml``'s
"Verify tag matches VERSION" step was unreachable and the tag gate gated on
nothing.

**ST-SEC-006.** Two defects in ``scripts/download_release.py``, both measured:
the ``Authorization`` header was forwarded across GitHub's cross-host redirect to
``release-assets.githubusercontent.com`` (3/3 in a two-server harness — Python
3.11/3.12's ``HTTPRedirectHandler`` strips only content-length/content-type, not
credentials), and a release advertising no ``digest`` printed one line and
exited 0 with a downloaded, unverified file.

This module parses YAML and text and drives two ephemeral ``http.server``
instances on loopback. It reaches no network and imports no ``scheduler_app``
code. Whole file: well under a second.
"""
import importlib.util
import os
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

yaml = pytest.importorskip(
    "yaml",
    reason="PyYAML is declared in requirements-dev.txt; a workflow is data and "
           "deserves a parser rather than a grep",
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_DIR = os.path.join(REPO, ".github", "workflows")

# The scripts a build actually executes. `installer.iss` is not here: it runs
# under iscc.exe and fetches nothing.
BUILD_SCRIPTS = ("build_embed.bat", "build_nuitka.bat", "build_mac.sh")

URL_RE = re.compile(r"https?://[^\s\"'`,;)<>]+")
HEX64_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
VERSION_IN_URL_RE = re.compile(r"\d+[._]\d+[._]\d+")
HASH_VERBS = ("get-filehash", "sha256sum", "shasum -a 256", "certutil -hashfile")

# How many lines after a download a verification gate may sit and still count as
# guarding it. Generous, because a batch download has a curl attempt plus a
# PowerShell fallback plus error handling before the check can run.
GATE_WINDOW = 30


# ── the allow-list, and what belongs in it ──────────────────────────────────
# A build-time download that is neither version-pinned nor hash-checked must be
# listed here with the reason it cannot be. The assertion is an EQUALITY, so a
# new unverified fetch turns this file red, and so does an entry that no longer
# corresponds to anything in the tree. Shrinking this dict is progress; growing
# it is a deliberate act that needs a sentence in the commit message.

UNVERIFIED_DOWNLOADS_ALLOWED = {
    "https://bootstrap.pypa.io/get-pip.py": (
        "Rolling by design: pypa serves the current bootstrap here and publishes "
        "no stable digest for this URL, so a hash constant would break the build "
        "on pypa's next pip release rather than on an attack. Pinning it means "
        "pinning a pypa/get-pip commit, which changes which pip version "
        "bootstraps the embeddable interpreter — a build change that cannot be "
        "verified from a developer machine. Known remaining ST-SEC-004 gap; the "
        "blast radius is the build runner, not the shipped tree, because "
        "requirements-lock.txt pins every runtime dependency by exact version."
    ),
}


# ── source readers ──────────────────────────────────────────────────────────

def _read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as fh:
        return fh.read()


def _workflow_paths():
    return sorted(
        os.path.join(WORKFLOW_DIR, n)
        for n in os.listdir(WORKFLOW_DIR)
        if n.endswith((".yml", ".yaml"))
    )


def _workflows():
    """`{filename: parsed}` for every workflow in `.github/workflows/`."""
    out = {}
    for path in _workflow_paths():
        with open(path, encoding="utf-8") as fh:
            out[os.path.basename(path)] = yaml.safe_load(fh)
    return out


def _steps(workflow):
    """Yield `(job_name, step_dict)` for every step in a parsed workflow."""
    for job_name, job in (workflow.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            if isinstance(step, dict):
                yield job_name, step


def _on(workflow):
    """The `on:` block. PyYAML resolves a bare `on` key to the boolean True."""
    return workflow.get("on") if "on" in workflow else workflow.get(True) or {}


def _push_filter(workflow, key):
    """`on.push.<key>` as a list; `[]` when absent."""
    push = (_on(workflow) or {}).get("push") or {}
    if not isinstance(push, dict):
        return []
    value = push.get(key) or []
    return [value] if isinstance(value, str) else list(value)


def _strip_comments(text, markers):
    """Drop whole-line comments so a URL in prose is not read as a fetch."""
    kept = []
    for line in text.splitlines():
        stripped = line.strip().lower()
        if any(stripped.startswith(m) for m in markers):
            kept.append("")
        else:
            kept.append(line)
    return kept


def _resolve_batch_vars(lines):
    """Expand `%VAR%` from the `set VAR=value` assignments in the same script."""
    env = {}
    for line in lines:
        match = re.match(r'\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=([^"\r\n]*)"?\s*$', line)
        if match:
            env[match.group(1)] = match.group(2)
    resolved = []
    for line in lines:
        for _ in range(3):  # values may themselves reference other vars
            new = re.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%",
                         lambda m: env.get(m.group(1), m.group(0)), line)
            if new == line:
                break
            line = new
        resolved.append(line)
    return resolved


def _sites_in(label, lines):
    """`(label, url, guarded)` for every URL in `lines`, with its gate window.

    A ``set VAR=<url>`` line declares a URL; it does not fetch one. The fetch is
    wherever the variable is used, and that is where the digest gate has to sit,
    so a bare assignment is not counted as a download site.
    """
    found = []
    for index, line in enumerate(lines):
        if re.match(r'\s*set\s+"?[A-Za-z_][A-Za-z0-9_]*=', line):
            continue
        for url in URL_RE.findall(line):
            window = "\n".join(lines[index:index + GATE_WINDOW]).lower()
            guarded = (
                bool(VERSION_IN_URL_RE.search(url))
                and any(verb in window for verb in HASH_VERBS)
                and bool(HEX64_RE.search(window))
            )
            found.append((label, url.rstrip("."), guarded))
    return found


def _download_sites():
    """Every build-time URL fetch, from the build scripts and workflow `run:`s.

    Scope is deliberate. A URL passed to an action as an input (say
    ``plugin_marketplaces:``) is not a build-time fetch of a file this build
    executes, and ``pip install`` carries no URL literal — its integrity story is
    requirements-lock.txt's exact-version pins, checked elsewhere.
    """
    sites = []
    for name in BUILD_SCRIPTS:
        markers = ("::", "rem ") if name.endswith(".bat") else ("#",)
        lines = _strip_comments(_read(name), markers)
        if name.endswith(".bat"):
            lines = _resolve_batch_vars(lines)
        sites += _sites_in(name, lines)
    for filename, workflow in _workflows().items():
        for job_name, step in _steps(workflow):
            script = step.get("run")
            if not isinstance(script, str):
                continue
            label = "%s::%s::%s" % (filename, job_name, step.get("name", "?"))
            sites += _sites_in(label, _strip_comments(script, ("#",)))
    return sites


def _release_publishing_workflows():
    """Workflows with a step that can create a GitHub Release."""
    names = set()
    for filename, workflow in _workflows().items():
        for _job, step in _steps(workflow):
            uses = str(step.get("uses") or "")
            run = str(step.get("run") or "")
            if "action-gh-release" in uses or "gh release create" in run:
                names.add(filename)
    return names


# ── ST-SEC-004: what the build downloads and runs ───────────────────────────

def test_every_build_time_download_is_pinned_and_hash_checked():
    """A fetched-and-executed file must be named by version and checked by digest.

    The failure this pins is not a supply-chain hypothetical. It happened: the
    unpinned Inno Setup URL started serving an HTML download page, PowerShell
    wrote it into ``innosetup.exe``, exit code 0, and five consecutive release
    builds died on the next line with "corrupted and unreadable".
    """
    unguarded = {url: label for label, url, guarded in _download_sites() if not guarded}
    allowed = set(UNVERIFIED_DOWNLOADS_ALLOWED)

    new = sorted(set(unguarded) - allowed)
    assert not new, (
        "build-time download(s) that are not both version-pinned and "
        "hash-checked:\n"
        + "\n".join("  %s\n      in %s" % (u, unguarded[u]) for u in new)
        + "\n\nPin the URL to an immutable versioned artefact and gate it with a "
          "SHA-256 comparison that exits non-zero on mismatch. If it genuinely "
          "cannot be pinned, add it to UNVERIFIED_DOWNLOADS_ALLOWED in this file "
          "with the reason."
    )

    stale = sorted(allowed - set(unguarded))
    assert not stale, (
        "UNVERIFIED_DOWNLOADS_ALLOWED names download(s) that no longer exist "
        "unverified in the tree: %s. Delete the entries — an allow-list nobody "
        "prunes stops describing anything." % stale
    )


def test_the_inno_setup_compiler_is_fetched_from_an_immutable_url():
    """No workflow may go back to the URL that already substituted HTML.

    ``jrsoftware.org/download.php/is.exe`` is a redirector, not an artefact. The
    replacement is the GitHub release asset for a specific Inno Setup version.
    The 6.x pin is deliberate: the same download page now also offers 7.1.0, and
    following it would be a silent major-version change of the compiler that
    builds the installer.
    """
    for filename, workflow in _workflows().items():
        for job_name, step in _steps(workflow):
            script = str(step.get("run") or "")
            assert "download.php/is.exe" not in script, (
                "%s::%s::%s fetches the Inno Setup redirector. It served "
                "text/html on 2026-08-28 and broke five release builds. Use "
                "github.com/jrsoftware/issrc/releases/download/is-6_7_3/"
                "innosetup-6.7.3.exe with a Get-FileHash gate."
                % (filename, job_name, step.get("name", "?"))
            )


# ── ST-SEC-001: what publishes, and when ────────────────────────────────────

def test_no_workflow_publishes_a_release_from_a_branch_push():
    """A push to a branch must never produce a public release.

    ``build-release.yml`` did exactly that: every push to an unprotected ``main``
    created a non-prerelease tag and published it as ``latest``. The June build
    it left pinned there has 105+ downloads and no checksum.
    """
    workflows = _workflows()
    for filename in sorted(_release_publishing_workflows()):
        branches = _push_filter(workflows[filename], "branches")
        assert not branches, (
            "%s can create a GitHub Release and triggers on pushes to %s. A "
            "release must be a deliberate act (a tag, or workflow_dispatch), "
            "never a side effect of merging." % (filename, branches)
        )


def test_the_release_publisher_is_tag_gated_and_does_not_hardcode_prerelease():
    """Exactly one workflow publishes, it fires on `v*`, and the flag is an input.

    ``prerelease: false`` written as a literal is how ``v1.0.0-build.10`` became
    ``latest`` for every user reading the README.
    """
    publishers = _release_publishing_workflows()
    assert publishers == {"release.yml"}, (
        "expected release.yml to be the only workflow that can publish a "
        "GitHub Release; found %s" % sorted(publishers)
    )

    workflow = _workflows()["release.yml"]
    assert _push_filter(workflow, "tags") == ["v*"], (
        "release.yml must publish only on a v* tag push"
    )

    for _job, step in _steps(workflow):
        if "action-gh-release" not in str(step.get("uses") or ""):
            continue
        prerelease = (step.get("with") or {}).get("prerelease")
        assert isinstance(prerelease, str) and "${{" in prerelease, (
            "release.yml hardcodes prerelease=%r. It must derive from the "
            "workflow_dispatch input so a test release cannot claim `latest`."
            % (prerelease,)
        )


def test_ci_runs_on_tag_pushes():
    """The tag that ships must be the tag CI vetted.

    Measured before this landed: ``ci.yml`` triggered on ``push.branches: [main]``
    only, so a tag push ran **zero** tests while three build workflows raced to
    package it.
    """
    assert "v*" in _push_filter(_workflows()["ci.yml"], "tags"), (
        "ci.yml does not run on tag pushes, so the release tag is built and "
        "published without the suite ever seeing it"
    )


def test_no_tag_guarded_step_lives_in_a_tagless_workflow():
    """An `if: refs/tags/...` step in a workflow tags cannot trigger is dead code.

    ``ci.yml``'s "Verify tag matches VERSION (on tag push)" was exactly this: a
    guard that reads like a safety net and had never once executed.
    """
    for filename, workflow in _workflows().items():
        tags = _push_filter(workflow, "tags")
        for job_name, step in _steps(workflow):
            condition = str(step.get("if") or "")
            if "refs/tags" not in condition:
                continue
            assert tags, (
                "%s::%s::%s is guarded by %r but %s has no on.push.tags trigger, "
                "so the guard can never run. Either give the workflow a tag "
                "trigger or delete the step."
                % (filename, job_name, step.get("name", "?"), condition, filename)
            )


def test_only_one_workflow_races_for_a_version_tag():
    """A single `v*` push must not start a stampede of duplicate builds.

    ``release.yml``, ``build-installer.yml`` and ``build-macos.yml`` all listened
    for ``v*``: one human tag meant 2 Windows builds and 4 macOS builds, of which
    only release.yml's three reached a release.
    """
    listeners = sorted(
        name for name, workflow in _workflows().items() if _push_filter(workflow, "tags")
    )
    assert listeners == ["ci.yml", "release.yml"], (
        "workflows triggered by a version tag: %s. Only the publisher and CI "
        "should be; the rest are workflow_dispatch build lanes." % listeners
    )


# ── ST-SEC-006: the download helper ─────────────────────────────────────────

def _download_release():
    """Import `scripts/download_release.py` by path; it is not a package."""
    path = os.path.join(REPO, "scripts", "download_release.py")
    spec = importlib.util.spec_from_file_location("dersis_download_release", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _serve(port, handler):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    # poll_interval is how long shutdown() blocks; the default 0.5 s is most of
    # this file's runtime otherwise.
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01},
                     daemon=True).start()
    return server


def _sink(recorder, payload):
    class Sink(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):
            recorder.append(dict(self.headers.items()))
            body = payload.get(self.path, payload["*"])
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Sink


def _redirector(recorder, target):
    class Redirector(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):
            recorder.append(dict(self.headers.items()))
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    return Redirector


def test_download_release_does_not_leak_credentials_across_a_redirect(monkeypatch):
    """The token authenticates the API, not whatever the API redirects to.

    ``browser_download_url`` 302s to ``release-assets.githubusercontent.com`` — a
    different host, on a URL that already carries its own signed claim. Measured
    with this same two-server shape before the fix: ``Authorization`` present on
    hop 1 and **forwarded** on hop 2, 3/3 cases. Python's
    ``HTTPRedirectHandler.redirect_request`` strips only content-length and
    content-type; there is no host comparison and no credential handling.

    Two hosts here are ``127.0.0.1`` and ``localhost`` — different names, one
    loopback interface, so the test needs no network.
    """
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_TEST_TOKEN_NOT_A_REAL_CREDENTIAL")
    monkeypatch.delenv("GH_TOKEN", raising=False)

    # `localhost` resolves to ::1 first, and on Windows the IPv6 connect sits
    # for 2.0 s (measured) before falling back to 127.0.0.1. The servers here are
    # IPv4; this test is about a header, so resolve v4 only and keep it fast.
    _getaddrinfo = socket.getaddrinfo

    def _ipv4_only(host, port, *args, **kwargs):
        entries = _getaddrinfo(host, port, *args, **kwargs)
        return [e for e in entries if e[0] == socket.AF_INET] or entries

    monkeypatch.setattr(socket, "getaddrinfo", _ipv4_only)
    module = _download_release()

    seen = []
    port_a, port_b = _free_port(), _free_port()
    target = "http://localhost:%d/asset.exe" % port_b
    server_b = _serve(port_b, _sink(seen, {"*": b"payload"}))
    server_a = _serve(port_a, _redirector(seen, target))
    try:
        with module._open("http://127.0.0.1:%d/first-hop" % port_a) as response:
            assert response.read() == b"payload"
    finally:
        server_a.shutdown()
        server_b.shutdown()

    assert len(seen) == 2, "expected the redirect to be followed, saw %d hop(s)" % len(seen)
    assert "Authorization" in seen[0], (
        "the token must still reach the GitHub API host, or the script drops to "
        "60 unauthenticated requests an hour and cannot read a private repo"
    )
    assert "Authorization" not in seen[1], (
        "the Authorization header was forwarded to the redirect target. Use "
        "Request.add_unredirected_header, which keeps hop 1 and strips hop 2."
    )


def test_download_release_fails_loudly_when_no_digest_is_available(tmp_path, monkeypatch):
    """No digest is a failure, not a footnote.

    The shipped code printed "verification skipped" and returned the file with
    exit status 0. Verification quietly not happening is worse than verification
    failing, because the caller cannot tell the difference from success.
    """
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    module = _download_release()

    payload = b"pretend installer bytes"
    port = _free_port()
    server = _serve(port, _sink([], {"*": payload}))
    dest = tmp_path / "Dersis_Setup_v9.9.9.exe"
    asset = {
        "name": "Dersis_Setup_v9.9.9.exe",
        "size": len(payload),
        "browser_download_url": "http://127.0.0.1:%d/asset.exe" % port,
    }
    try:
        with pytest.raises(SystemExit) as excinfo:
            module.download_asset(asset, str(dest))
    finally:
        server.shutdown()

    assert "verif" in str(excinfo.value).lower(), (
        "the failure must say verification did not happen; got %r" % (excinfo.value,)
    )
    assert not dest.exists(), (
        "an unverified download must not be left on disk looking like a "
        "successful one"
    )


def test_download_release_still_verifies_and_still_honours_no_verify(tmp_path, monkeypatch):
    """The loud failure must not have replaced verification with refusal.

    Three paths in one process: a correct digest passes, a wrong digest raises
    and deletes, and ``--no-verify`` downloads without a digest on purpose.
    """
    import hashlib

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    module = _download_release()

    payload = b"pretend installer bytes"
    digest = hashlib.sha256(payload).hexdigest()
    port = _free_port()
    server = _serve(port, _sink([], {"*": payload}))
    url = "http://127.0.0.1:%d/asset.exe" % port

    def asset(**extra):
        base = {"name": "Dersis_Setup_v9.9.9.exe", "size": len(payload),
                "browser_download_url": url}
        base.update(extra)
        return base

    try:
        good = tmp_path / "good.exe"
        module.download_asset(asset(digest="sha256:" + digest), str(good))
        assert good.read_bytes() == payload

        bad = tmp_path / "bad.exe"
        with pytest.raises(SystemExit) as excinfo:
            module.download_asset(asset(digest="sha256:" + "0" * 64), str(bad))
        assert "mismatch" in str(excinfo.value).lower()
        assert not bad.exists(), "a mismatched download must be deleted"

        skipped = tmp_path / "skipped.exe"
        module.download_asset(asset(), str(skipped), verify=False)
        assert skipped.read_bytes() == payload
    finally:
        server.shutdown()


def test_download_release_falls_back_to_the_published_sha256_asset(tmp_path, monkeypatch):
    """A sibling `.sha256` keeps the download verifiable if `digest` goes away.

    It is no *stronger* than ``digest`` — same API, same CDN, same trust root —
    but ``release.yml`` publishes one next to every installer, and a human with
    ``sha256sum`` can use it. Reading it means a release whose ``digest`` field is
    empty still verifies instead of hard-failing.
    """
    import hashlib

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    module = _download_release()

    payload = b"pretend installer bytes"
    name = "Dersis_Setup_v9.9.9.exe"
    sidecar = ("%s  %s" % (hashlib.sha256(payload).hexdigest(), name)).encode()
    port = _free_port()
    server = _serve(port, _sink([], {"/asset.exe": payload, "*": sidecar}))
    release = {
        "assets": [
            {"name": name, "size": len(payload),
             "browser_download_url": "http://127.0.0.1:%d/asset.exe" % port},
            {"name": name + ".sha256", "size": len(sidecar),
             "browser_download_url": "http://127.0.0.1:%d/asset.exe.sha256" % port},
        ]
    }
    dest = tmp_path / name
    try:
        expected = module.expected_sha256(release, release["assets"][0])
        assert expected == hashlib.sha256(payload).hexdigest()
        module.download_asset(release["assets"][0], str(dest), expected=expected)
    finally:
        server.shutdown()

    assert dest.read_bytes() == payload
