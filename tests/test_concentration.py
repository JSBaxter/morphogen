"""Unit tests for the JCS canonicalizer + tag normalizer + dedup helpers."""

from __future__ import annotations

import hashlib

import pytest

from morphogen.domain.concentration import (
    canonicalize,
    normalize_tags,
    payload_hash,
    tags_hash,
)


class TestCanonicalize:
    def test_object_keys_are_sorted(self) -> None:
        assert canonicalize({"b": 1, "a": 2}) == '{"a":2,"b":1}'

    def test_nested_objects_are_sorted_recursively(self) -> None:
        assert (
            canonicalize({"outer": {"z": 1, "a": 2}, "alpha": 1})
            == '{"alpha":1,"outer":{"a":2,"z":1}}'
        )

    def test_empty_object_and_array(self) -> None:
        assert canonicalize({}) == "{}"
        assert canonicalize([]) == "[]"

    def test_arrays_preserve_order(self) -> None:
        assert canonicalize([3, 1, 2]) == "[3,1,2]"

    def test_booleans_and_null(self) -> None:
        assert canonicalize(True) == "true"
        assert canonicalize(False) == "false"
        assert canonicalize(None) == "null"

    def test_int_and_int_valued_float_match(self) -> None:
        # 1.0 must hash identically to 1 to avoid trivial fragmentation.
        assert canonicalize(1) == canonicalize(1.0) == "1"
        assert canonicalize(0) == canonicalize(0.0) == "0"
        assert canonicalize(-7) == canonicalize(-7.0) == "-7"

    def test_string_basic_escapes(self) -> None:
        assert canonicalize("") == '""'
        assert canonicalize("hello") == '"hello"'
        assert canonicalize('he said "hi"') == '"he said \\"hi\\""'
        assert canonicalize("a\\b") == '"a\\\\b"'
        assert canonicalize("line\nfeed") == '"line\\nfeed"'

    def test_control_chars_use_uXXXX_form(self) -> None:
        assert canonicalize("") == '"\\u0001"'

    def test_forward_slash_is_not_escaped(self) -> None:
        # JCS / ECMA-262 do not escape forward slash.
        assert canonicalize("/path/here") == '"/path/here"'

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(TypeError):
            canonicalize({"k": object()})

    def test_nan_inf_rejected(self) -> None:
        with pytest.raises(ValueError):
            canonicalize(float("nan"))
        with pytest.raises(ValueError):
            canonicalize(float("inf"))

    def test_dict_with_non_string_key_rejected(self) -> None:
        with pytest.raises(TypeError):
            canonicalize({1: "v"})


class TestPayloadHashStability:
    def test_key_order_does_not_affect_hash(self) -> None:
        h1 = payload_hash({"a": 1, "b": [1, 2], "c": {"x": True}})
        h2 = payload_hash({"c": {"x": True}, "b": [1, 2], "a": 1})
        assert h1 == h2

    def test_int_vs_float_collapse_to_same_hash(self) -> None:
        assert payload_hash({"n": 1}) == payload_hash({"n": 1.0})

    def test_distinct_payloads_have_distinct_hashes(self) -> None:
        assert payload_hash({"x": 1}) != payload_hash({"x": 2})
        assert payload_hash({"x": 1}) != payload_hash({"y": 1})

    def test_hash_is_sha256_hex(self) -> None:
        h = payload_hash({"hello": "world"})
        assert len(h) == 64
        bytes.fromhex(h)


class TestNormalizeTags:
    def test_lowercases_strips_dedupes_sorts(self) -> None:
        assert normalize_tags(["  Analysis ", "analysis", "Build"]) == [
            "analysis",
            "build",
        ]

    def test_empty_input_returns_empty(self) -> None:
        assert normalize_tags([]) == []

    def test_empty_tag_rejected(self) -> None:
        with pytest.raises(ValueError):
            normalize_tags(["valid", "   "])

    def test_unit_separator_rejected(self) -> None:
        with pytest.raises(ValueError):
            normalize_tags(["bad\x1ftag"])

    def test_non_string_rejected(self) -> None:
        with pytest.raises(TypeError):
            normalize_tags(["ok", 7])  # type: ignore[list-item]


class TestTagsHash:
    def test_stable_after_normalization(self) -> None:
        # Different surface forms hash to the same key once normalized.
        a = tags_hash(normalize_tags(["analysis", "Build"]))
        b = tags_hash(normalize_tags(["BUILD", "  analysis"]))
        assert a == b

    def test_distinct_tag_sets_have_distinct_hashes(self) -> None:
        a = tags_hash(normalize_tags(["alpha"]))
        b = tags_hash(normalize_tags(["alpha", "beta"]))
        assert a != b

    def test_separator_choice_matches_spec(self) -> None:
        # Recorded so we notice if anyone changes the join character.
        expected = hashlib.sha256("alpha\x1fbeta".encode("utf-8")).hexdigest()
        assert tags_hash(["alpha", "beta"]) == expected
