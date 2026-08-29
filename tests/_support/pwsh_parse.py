"""Read a PowerShell `run:` body as data instead of as one flat string.

Two tests used to pin release.yml's "Verify build output" step by asking
``"$dist\\Dersis.exe" in run_body``. A PowerShell comment leaves the text in
that body and removes the check, so both tests were pins against *deletion*
only: commenting the line out kept them green while the release stopped
verifying that the launcher every installer shortcut points at was ever built.
(Measured: mutation R1 below returned exit 0 on both modules; only outright
deletion, R2, went red.)

The first fix for that understood exactly one comment spelling — a line whose
first non-space character is ``#`` — and PowerShell has two comment *syntaxes*
across three *positions*, so it closed one third of the hole. Measured on
bd12e58, each of these dropped ``$dist\\Dersis.exe`` from the array the shell
iterates while both modules stayed at exit 0 with 36 passes:

    S1   <# "$dist\\Dersis.exe", #>                     block, inline
    S2   <#\\n  "$dist\\Dersis.exe",\\n  #>               block, spanning lines
    R10  "$dist\\scheduler_app",  # "$dist\\Dersis.exe",  line comment, trailing

So: ``<# ... #>`` blocks are removed before the literal is split into lines,
and each surviving line is truncated at its first ``#`` outside a quoted
string. Both directions of error are safe here — this parser can only ever
report *fewer* live members than the shell sees, and a member that goes missing
turns a pinning test red, which is the direction a mistake should fail in.

Pinning the array's contents is only half a pin, because nothing about a list
of paths says anything acts on it: the whole ``foreach`` could be deleted, or
kept and pointed at a different list, or preceded by a second assignment to the
same name, and a membership assertion sees none of it (measured: mutations R4,
R5, R9 and S3, all exit 0 with 36 passes). ``powershell_foreach`` and
``powershell_assignment_count`` exist so a caller can pin the enforcement
beside the contents.

These helpers deliberately do not try to be a PowerShell parser — the step body
is six quoted paths, one variable assignment and one loop, and anything more
elaborate would be a second thing to get wrong.
"""
import re

__all__ = [
    "powershell_string_array",
    "powershell_scalar_assignments",
    "powershell_assignment_count",
    "powershell_foreach",
    "expand",
]

_QUOTED = re.compile(r'"([^"]*)"')
_BLOCK_COMMENT = re.compile(r"<#.*?#>", re.S)


def _truncate_at_comment(line):
    """*line* up to its first ``#`` that is not inside a quoted string.

    A trailing comment is a comment wherever it starts, so R10's
    ``"$dist\\scheduler_app",  # "$dist\\Dersis.exe",`` has to lose everything
    from the ``#`` on. The quote tracking is what keeps a ``#`` *inside* a path
    from truncating a live member away.
    """
    quote = None
    for index, char in enumerate(line):
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#":
            return line[:index]
    return line


def _strip_comments(text):
    """*text* with both PowerShell comment syntaxes removed, line count intact.

    Block comments go first, because a ``<# ... #>`` that spans lines cannot be
    recognised after the text has been split into them. Each block is replaced
    by the newlines it contained rather than by nothing, so that a member's
    line position — and a trailing comment's ownership of its own line — is
    unchanged.
    """
    text = _BLOCK_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return "\n".join(_truncate_at_comment(line) for line in text.split("\n"))


def _balanced(text, start, opener, closer):
    """The span inside the bracket opened at *start* - 1, or ``None``."""
    depth = 1
    index = start
    while index < len(text) and depth:
        if text[index] == opener:
            depth += 1
        elif text[index] == closer:
            depth -= 1
            if not depth:
                return text[start:index]
        index += 1
    return None


def _array_literal(body, name):
    """The text between the parentheses of ``$<name> = @( ... )``."""
    opener = re.search(r"\$" + re.escape(name) + r"\s*=\s*@\(", body)
    if opener is None:
        return None
    return _balanced(body, opener.end(), "(", ")")


def powershell_string_array(body, name):
    """Live double-quoted members of ``$<name> = @( ... )``, in source order.

    "Live" means what the shell would iterate: anything a ``#`` line comment or
    a ``<# ... #>`` block comment disables is gone before any matching happens,
    in all three positions those can occupy. That is the whole point — a
    disabled check has to read as an absent check, or the pin is a pin against
    deletion only and a release engineer can switch the check off in review-
    invisible ways with the suite green.
    """
    literal = _array_literal(_strip_comments(body), name)
    if literal is None:
        return None
    return _QUOTED.findall(literal)


def powershell_assignment_count(body, name):
    """How many statements assign ``$<name> = ...``.

    More than one and this module's readings are worthless on their face:
    ``_array_literal`` takes the first assignment and the shell obeys the last,
    so a second ``$checks = @("$dist\\VERSION")`` slipped in above the loop
    leaves every membership assertion green over an array nothing iterates.
    ``+=`` is deliberately not counted — appending to the list is adding
    checks, which is progress.
    """
    pattern = r"(?m)^[^\S\n]*\$" + re.escape(name) + r"[^\S\n]*="
    return len(re.findall(pattern, _strip_comments(body)))


def powershell_foreach(body, name):
    """``(loop_variable, loop_body)`` for ``foreach ($x in $<name>) { ... }``.

    Matches only when the iterated expression is the *bare* variable, which is
    the point: ``foreach ($f in @("$dist\\VERSION"))`` iterates a fresh literal
    and leaves ``$<name>`` decorative — the array is still there, still fully
    pinned by a membership assertion, and no longer connected to anything.
    Returns ``None`` when there is no such loop at all.
    """
    header = re.search(
        r"foreach\s*\(\s*\$([A-Za-z_][A-Za-z0-9_]*)\s+in\s+\$"
        + re.escape(name) + r"\s*\)\s*\{",
        _strip_comments(body))
    if header is None:
        return None
    loop_body = _balanced(_strip_comments(body), header.end(), "{", "}")
    if loop_body is None:
        return None
    return header.group(1), loop_body


def powershell_scalar_assignments(body):
    """``{name: value}`` for each ``$name = "value"`` on its own line."""
    out = {}
    for line in _strip_comments(body).split("\n"):
        match = re.match(r'\s*\$([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"\s*$', line)
        if match:
            out[match.group(1)] = match.group(2)
    return out


def expand(text, assignments):
    """Substitute ``$name`` from `assignments`, longest name first."""
    for name in sorted(assignments, key=len, reverse=True):
        text = text.replace("$" + name, assignments[name])
    return text
