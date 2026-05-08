"""Concentration dedup key — RFC 8785 JCS canonicalization + tag normalization.

Re-emissions of the same `(canonical_payload, sorted_tags, kind)` increment a
counter on the canonical morphogen instead of inserting a fresh row. Source
cell is excluded so cross-cell emissions reinforce. See SPEC §"Concentration
semantics" for the rationale.

The canonicalizer below is a best-effort RFC 8785 implementation: object keys
are sorted by UTF-16 code unit (not Unicode codepoint), JSON whitespace is
stripped, and integer-valued floats normalize to integer form so that ``1.0``
and ``1`` hash to the same key. Numerical edge cases that the RFC specifies in
detail (very large or very small floats whose ECMA-262 representation differs
from Python's ``repr``) are left to upstream payload normalization at the
calling cell — fragmentation from such payloads is a "loose dedup" failure
mode the spec already calls out.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable
from typing import Any

TAG_JOIN_CHAR = "\x1f"  # ASCII unit separator — never appears in normalized tags


def normalize_tags(tags: Iterable[str]) -> list[str]:
    """Lowercase, strip, dedupe, sort. Empty/whitespace-only tags are rejected."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in tags:
        if not isinstance(raw, str):
            raise TypeError(f"tags must be strings; got {type(raw)!r}")
        norm = raw.strip().lower()
        if not norm:
            raise ValueError("empty tag is not allowed")
        if TAG_JOIN_CHAR in norm:
            raise ValueError("tag must not contain the unit-separator character")
        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    out.sort()
    return out


def canonicalize(value: Any) -> str:
    """Serialize a JSON-compatible Python value to RFC 8785 JCS canonical form."""
    return _canonicalize(value)


def payload_hash(payload: dict[str, Any]) -> str:
    """sha256 hex of the canonicalized payload."""
    return hashlib.sha256(canonicalize(payload).encode("utf-8")).hexdigest()


def tags_hash(normalized_tags: list[str]) -> str:
    """sha256 hex of normalized tags joined by ``\\x1f``."""
    joined = TAG_JOIN_CHAR.join(normalized_tags)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _canonicalize(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _serialize_float(value)
    if isinstance(value, str):
        return _serialize_string(value)
    if isinstance(value, list) or isinstance(value, tuple):
        return "[" + ",".join(_canonicalize(item) for item in value) + "]"
    if isinstance(value, dict):
        return _serialize_object(value)
    raise TypeError(f"unsupported type for JCS canonicalization: {type(value)!r}")


def _serialize_float(value: float) -> str:
    if math.isnan(value) or math.isinf(value):
        raise ValueError("NaN and Infinity are not valid JSON")
    if value.is_integer() and abs(value) < 1e16:
        return str(int(value))
    out = repr(value)
    # Python prints `1e-07` / `1e+10`; ECMA-262 prints `1e-7` / `1e+10`. JCS follows
    # ECMA-262, so strip leading zeros from the exponent. Sign is kept only when
    # negative (positive exponents may keep the `+`; both forms hash to the same
    # bytes via this normalization, so the choice doesn't matter as long as it's
    # stable inside this process).
    out = re.sub(r"e([+-])0+(\d)", r"e\1\2", out)
    return out


def _serialize_string(value: str) -> str:
    parts = ['"']
    for ch in value:
        cp = ord(ch)
        if ch == '"':
            parts.append('\\"')
        elif ch == "\\":
            parts.append("\\\\")
        elif ch == "\b":
            parts.append("\\b")
        elif ch == "\f":
            parts.append("\\f")
        elif ch == "\n":
            parts.append("\\n")
        elif ch == "\r":
            parts.append("\\r")
        elif ch == "\t":
            parts.append("\\t")
        elif cp < 0x20:
            parts.append(f"\\u{cp:04x}")
        else:
            parts.append(ch)
    parts.append('"')
    return "".join(parts)


def _serialize_object(value: dict[str, Any]) -> str:
    keys: list[str] = []
    for key in value:
        if not isinstance(key, str):
            raise TypeError(f"JSON object keys must be strings; got {type(key)!r}")
        keys.append(key)
    # RFC 8785 sorts object keys by UTF-16 code unit. Encode to UTF-16-BE
    # so byte-lexicographic comparison gives the right answer for the BMP and
    # the supplementary plane alike.
    keys.sort(key=lambda k: k.encode("utf-16-be"))
    items = [_serialize_string(k) + ":" + _canonicalize(value[k]) for k in keys]
    return "{" + ",".join(items) + "}"
