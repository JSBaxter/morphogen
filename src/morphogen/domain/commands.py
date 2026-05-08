from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EmitRequest:
    source_cell: str
    tags: list[str]
    payload: dict[str, Any]
    ttl_seconds: int
    nonce: str | None = None


@dataclass(slots=True)
class EmitSignal:
    source_cell: str
    tags: list[str]
    payload: dict[str, Any]
    ttl_seconds: int
    nonce: str | None = None


@dataclass(slots=True)
class ReadField:
    reader_cell: str
    reader_tags: list[str] = field(default_factory=list)
    kinds: list[str] | None = None
    since: str | None = None
    status: str | None = None  # None == "active" (emitted or claimed)


@dataclass(slots=True)
class GetMorphogen:
    morphogen_id: str


@dataclass(slots=True)
class ClaimRequest:
    morphogen_id: str
    claimer_cell: str
    lease_ttl_seconds: int


@dataclass(slots=True)
class RenewClaim:
    morphogen_id: str
    claimer_cell: str
    lease_ttl_seconds: int


@dataclass(slots=True)
class ReleaseClaim:
    morphogen_id: str
    claimer_cell: str
    reason: str | None = None


@dataclass(slots=True)
class FulfillRequest:
    morphogen_id: str
    claimer_cell: str
    outcome: str
    link: str | None = None


@dataclass(slots=True)
class RespondSignal:
    morphogen_id: str
    responder_cell: str
    response_payload: dict[str, Any]
    link: str | None = None


@dataclass(slots=True)
class SweepDecayed:
    """No arguments — sweeps everything decayable as of `now`."""
