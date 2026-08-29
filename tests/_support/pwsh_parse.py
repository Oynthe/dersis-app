"""Read a PowerShell `run:` body as data instead of as one flat string.

Two tests used to pin release.yml's "Verify build output" step by asking
``"$dist\\Dersis.exe" in run_body``. A PowerShell ``#`` comment leaves the text
in that body and removes the check, so both tests were pins against *deletion*
only: commenting the line out kept them green while the release stopped
verifying that the launcher every installer shortcut points at was ever built.
(Measured: mutation R1 below returned exit 0 on both modules; only outright
deletion, R2, went red.)

These helpers extract the *live* members of the array the ``foreach`` actually
iterates, so a disabled check reads as an absent check. They deliberately do not
try to be a PowerShell parser — the step body is six quoted paths and one
variable assignment, and anything more elaborate would be a second thing to get
wrong.
"""
import re

__all__ = ["powershell_string_array", "powershell_scalar_assignments", "expand"]

_QUOTED = re.compile(r'"([^"]*)"')


def _array_literal(body, name):
    """The text between the parentheses of ``$<name> = @( ... )``."""
    opener = re.search(r"\$" + re.escape(name) + r"\s*=\s*@\(", body)
    if opener is None:
        return None
    depth = 1
    i = opener.end()
    while i < len(body) and depth:
        if body[i] == "(":
            depth += 1
        elif body[i] == ")":
            depth -= 1
            if not depth:
                return body[opener.end():i]
        i += 1
    return None


def powershell_string_array(body, name):
    """Live double-quoted members of ``$<name> = @( ... )``, in source order.

    Lines whose first non-space character is ``#`` are dropped before any
    matching: that is the whole point. A trailing ``#`` comment on a live line
    is left alone, because the member in front of it is still a member.
    """
    literal = _array_literal(body, name)
    if literal is None:
        return None
    members = []
    for line in literal.splitlines():
        if line.lstrip().startswith("#"):
            continue
        members.extend(_QUOTED.findall(line))
    return members


def powershell_scalar_assignments(body):
    """``{name: value}`` for each ``$name = "value"`` on its own line."""
    out = {}
    for line in body.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = re.match(r'\s*\$([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"\s*$', line)
        if match:
            out[match.group(1)] = match.group(2)
    return out


def expand(text, assignments):
    """Substitute ``$name`` from `assignments`, longest name first."""
    for name in sorted(assignments, key=len, reverse=True):
        text = text.replace("$" + name, assignments[name])
    return text
