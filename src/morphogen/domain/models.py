from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

KIND_REQUEST = "request"
KIND_SIGNAL = "signal"
KINDS = frozenset({KIND_REQUEST, KIND_SIGNAL})

STATUS_EMITTED = "emitted"
STATUS_CLAIMED = "claimed"
STATUS_FULFILLED = "fulfilled"
STATUS_DECAYED = "decayed"
STATUSES = frozenset({STATUS_EMITTED, STATUS_CLAIMED, STATUS_FULFILLED, STATUS_DECAYED})


@dataclass(slots=True)
class Morphogen:
    """One row in the broadcast field — request or signal envelope.

    Singleton-claim fields (claimed_by, claimed_at, lease_expires_at,
    fulfilled_*) are always None for ``kind=signal``.
    """

    id: str
    source_cell: str
    kind: str
    payload: dict[str, Any]
    payload_hash: str
    tags: list[str]
    tags_hash: str
    nonce: str | None
    emitted_at: str
    ttl_seconds: int
    concentration: int = 1
    status: str = STATUS_EMITTED
    claimed_by: str | None = None
    claimed_at: str | None = None
    lease_expires_at: str | None = None
    fulfilled_at: str | None = None
    fulfilled_outcome: str | None = None
    fulfilled_link: str | None = None


@dataclass(slots=True)
class MorphogenResponse:
    id: str
    morphogen_id: str
    responder_cell: str
    response_payload: dict[str, Any]
    link: str | None
    responded_at: str


@dataclass(slots=True)
class MorphogenView:
    """Read-side projection: morphogen + its responses (signals only)."""

    morphogen: Morphogen
    responses: list[MorphogenResponse] = field(default_factory=list)


@dataclass(slots=True)
class HealthReport:
    ok: bool
    field_size: int
    active_count: int
    decayed_count: int
    response_count: int
    checked_at: str
