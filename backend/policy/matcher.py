"""Glob matching for workspace-relative paths.

``**/`` matches zero or more directories, ``*`` matches within one segment and
matching is case-sensitive.
"""

from __future__ import annotations

import functools
import re


def _segment_to_regex(segment: str) -> str:
    """Translate one path segment, where ``**`` spans separators."""
    out: list[str] = []
    i = 0
    while i < len(segment):
        char = segment[i]
        if char == "*":
            if segment.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = segment.find("]", i + 1)
            if end == -1:
                out.append(re.escape(char))
            else:
                body = segment[i + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body + "]")
                i = end + 1
                continue
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out)


@functools.lru_cache(maxsize=512)
def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a policy glob into an anchored regular expression."""
    pattern = pattern.replace("\\", "/")
    while pattern.startswith("./"):
        pattern = pattern[2:]
    parts = pattern.split("/")
    pieces: list[str] = []
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            if last:
                pieces.append(".*")
            else:
                pieces.append("(?:[^/]+/)*")
            continue
        pieces.append(_segment_to_regex(part))
        if not last:
            pieces.append("/")
    return re.compile("^" + "".join(pieces) + "$")


def path_matches(pattern: str, path: str) -> bool:
    """Return True when a workspace-relative path matches a policy glob."""
    normalised = path.replace("\\", "/")
    while normalised.startswith("./"):
        normalised = normalised[2:]
    return glob_to_regex(pattern).match(normalised) is not None
