"""Test doubles — in-memory repository and a deterministic clock.

These exist for the test suite to exercise the domain layer without an
external SQLite file. The real SQLite-backed repository lives under
``morphogen.infra``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from morphogen.domain.models import (
    STATUS_EMITTED,
    Morphogen,
    MorphogenResponse,
)


class InMemoryRepository:
    def __init__(self) -> None:
        self._morphogens: dict[str, Morphogen] = {}
        self._responses: list[MorphogenResponse] = []

    def add_morphogen(self, morphogen: Morphogen) -> None:
        if morphogen.id in self._morphogens:
            raise ValueError(f"duplicate morphogen id: {morphogen.id}")
        self._morphogens[morphogen.id] = morphogen

    def update_morphogen(self, morphogen: Morphogen) -> None:
        if morphogen.id not in self._morphogens:
            raise LookupError(f"unknown morphogen: {morphogen.id}")
        self._morphogens[morphogen.id] = morphogen

    def get_morphogen(self, morphogen_id: str) -> Morphogen | None:
        return self._morphogens.get(morphogen_id)

    def find_active_dedup(
        self, payload_hash: str, tags_hash: str, kind: str
    ) -> Morphogen | None:
        for morphogen in self._morphogens.values():
            if morphogen.status != STATUS_EMITTED:
                continue
            if morphogen.nonce is not None:
                continue
            if (
                morphogen.payload_hash == payload_hash
                and morphogen.tags_hash == tags_hash
                and morphogen.kind == kind
            ):
                return morphogen
        return None

    def list_morphogens(self) -> list[Morphogen]:
        return list(self._morphogens.values())

    def add_response(self, response: MorphogenResponse) -> None:
        self._responses.append(response)

    def list_responses(self, morphogen_id: str) -> list[MorphogenResponse]:
        return [r for r in self._responses if r.morphogen_id == morphogen_id]

    def count_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for morphogen in self._morphogens.values():
            counts[morphogen.status] = counts.get(morphogen.status, 0) + 1
        return counts

    def count_responses(self) -> int:
        return len(self._responses)


class FakeClock:
    """Deterministic clock for time-mocking tests.

    ``now()`` returns the current value as an ISO-8601 string. ``advance``
    moves the clock forward by ``seconds`` seconds.
    """

    def __init__(self, start: str = "2026-01-01T00:00:00+00:00") -> None:
        self._dt = datetime.fromisoformat(start)
        if self._dt.tzinfo is None:
            self._dt = self._dt.replace(tzinfo=UTC)

    def now(self) -> str:
        return self._dt.isoformat()

    def advance(self, seconds: float) -> None:
        self._dt = self._dt + timedelta(seconds=seconds)


class IdSequence:
    """Deterministic id generator for tests."""

    def __init__(self, prefix: str = "m_") -> None:
        self._prefix = prefix
        self._n = 0

    def __call__(self) -> str:
        self._n += 1
        return f"{self._prefix}{self._n:04d}"
