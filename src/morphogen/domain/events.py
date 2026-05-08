from __future__ import annotations

from dataclasses import dataclass

from morphogen.domain.models import Morphogen, MorphogenResponse


@dataclass(slots=True)
class RequestEmitted:
    morphogen: Morphogen
    fresh: bool  # True iff a new row was inserted (False on concentration bump)


@dataclass(slots=True)
class SignalEmitted:
    morphogen: Morphogen
    fresh: bool


@dataclass(slots=True)
class RequestClaimed:
    morphogen: Morphogen


@dataclass(slots=True)
class ClaimRenewed:
    morphogen: Morphogen


@dataclass(slots=True)
class ClaimReleased:
    morphogen: Morphogen


@dataclass(slots=True)
class RequestFulfilled:
    morphogen: Morphogen


@dataclass(slots=True)
class SignalResponded:
    morphogen: Morphogen
    response: MorphogenResponse


@dataclass(slots=True)
class FieldSwept:
    """Returned by `sweep_decayed`. ``decayed`` are TTL-expired emissions
    moved to status=decayed; ``reaped`` are claims whose lease expired,
    moved back to status=emitted (claim fields cleared)."""

    decayed: list[Morphogen]
    reaped: list[Morphogen]
