"""Read and atomically update dotenv assignments without evaluating shell code."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ASSIGNMENT_RE = re.compile(
    r"^(?P<prefix>\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*)"
    r"(?P<value>.*)$"
)


def _decode_value(raw: str) -> str:
    value = raw.lstrip()
    if not value:
        return ""

    quote = value[0] if value[0] in "\"'" else None
    if quote is not None:
        decoded: list[str] = []
        index = 1
        while index < len(value):
            char = value[index]
            if char == "\\" and index + 1 < len(value):
                next_char = value[index + 1]
                if quote == "'" and next_char in ("'", "\\"):
                    decoded.append(next_char)
                    index += 2
                    continue
                if quote == '"':
                    escapes = {
                        '"': '"',
                        "\\": "\\",
                        "$": "$",
                        "n": "\n",
                        "r": "\r",
                        "t": "\t",
                    }
                    if next_char in escapes:
                        decoded.append(escapes[next_char])
                        index += 2
                        continue
                decoded.extend((char, next_char))
                index += 2
                continue
            if char == quote:
                trailer = value[index + 1 :].strip()
                if trailer and not trailer.startswith("#"):
                    raise ValueError("unexpected text after quoted dotenv value")
                return "".join(decoded)
            decoded.append(char)
            index += 1
        raise ValueError("unterminated quoted dotenv value")

    for index, char in enumerate(value):
        if char == "#" and index > 0 and value[index - 1].isspace():
            value = value[:index]
            break
    return value.rstrip()


def parse_env_text(text: str) -> dict[str, str]:
    """Parse simple dotenv assignments without interpolation or execution."""
    values: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = _ASSIGNMENT_RE.match(line)
        if match is None:
            continue
        try:
            values[match.group("key")] = _decode_value(match.group("value"))
        except ValueError as exc:
            raise ValueError(f"invalid dotenv value on line {line_number}") from exc
    return values


def _quoted_value(value: str) -> str:
    if "\n" in value or "\r" in value or "\0" in value:
        raise ValueError("dotenv values must be single-line text")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _comment_suffix(raw_value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(raw_value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char == "#" and index > 0 and raw_value[index - 1].isspace():
            start = index - 1
            while start > 0 and raw_value[start - 1].isspace():
                start -= 1
            return raw_value[start:]
    return ""


def replace_env_value(text: str, key: str, value: str) -> str:
    """Replace one dotenv key, preserving all unrelated lines byte-for-byte."""
    if not _KEY_RE.fullmatch(key):
        raise ValueError("invalid dotenv key")
    encoded = _quoted_value(value)
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    matches = 0
    updated: list[str] = []
    for line in lines:
        ending = ""
        body = line
        if body.endswith("\r\n"):
            body, ending = body[:-2], "\r\n"
        elif body.endswith(("\n", "\r")):
            body, ending = body[:-1], body[-1]
        match = _ASSIGNMENT_RE.match(body)
        if match is None or match.group("key") != key:
            updated.append(line)
            continue
        matches += 1
        if matches > 1:
            raise ValueError(f"dotenv key {key!r} appears more than once")
        suffix = _comment_suffix(match.group("value"))
        updated.append(match.group("prefix") + encoded + suffix + ending)

    if matches == 0:
        if updated and not updated[-1].endswith(("\n", "\r")):
            updated[-1] += newline
        updated.append(f"{key}={encoded}{newline}")
    return "".join(updated)


def update_env_file(path: str | os.PathLike[str], key: str, value: str) -> None:
    """Atomically replace one dotenv value while retaining file metadata."""
    target = Path(path)
    if target.is_symlink():
        raise ValueError("refusing to update a symlinked dotenv file")
    original = target.read_bytes()
    text = original.decode("utf-8")
    updated = replace_env_value(text, key, value).encode("utf-8")
    if updated == original:
        return

    stat = target.stat()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, stat.st_mode & 0o777)
        if hasattr(os, "fchown"):
            os.fchown(descriptor, stat.st_uid, stat.st_gid)
        with os.fdopen(descriptor, "wb") as output:
            output.write(updated)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        if os.name != "nt":
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
