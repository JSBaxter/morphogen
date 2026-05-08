"""FieldService — pure-Python implementation of the broadcast field.

The MCP server in ``morphogen.server`` and the SQLite repository in
``morphogen.infra`` both build on this layer. No I/O lives here; the
repository is an injected ``MorphogenRepository`` Protocol.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Any, Protocol

from morphogen.domain.commands import (
    ClaimRequest,
    EmitRequest,
    EmitSignal,
    FulfillRequest,
    GetMorphogen,
    ReadField,
    ReleaseClaim,
    RenewClaim,
    RespondSignal,
    SweepDecayed,
)
from morphogen.domain.concentration import (
    normalize_tags,
    payload_hash,
    tags_hash,
)
from morphogen.domain.events import (
    ClaimRenewed,
    ClaimReleased,
    FieldSwept,
    RequestClaimed,
    RequestEmitted,
    RequestFulfilled,
    SignalEmitted,
    SignalResponded,
)
from morphogen.domain.models import (
    KIND_REQUEST,
    KIND_SIGNAL,
    KINDS,
    STATUS_CLAIMED,
    STATUS_DECAYED,
    STATUS_EMITTED,
    STATUS_FULFILLED,
    HealthReport,
    Morphogen,
    MorphogenResponse,
    MorphogenView,
)


class MorphogenRepository(Protocol):
    def add_morphogen(self, morphogen: Morphogen) -> None: ...
    def update_morphogen(self, morphogen: Morphogen) -> None: ...
    def get_morphogen(self, morphogen_id: str) -> Morphogen | None: ...
    def find_active_dedup(
        self, payload_hash: str, tags_hash: str, kind: str
    ) -> Morphogen | None: ...
    def list_morphogens(self) -> list[Morphogen]: ...
    def add_response(self, response: MorphogenResponse) -> None: ...
    def list_responses(self, morphogen_id: str) -> list[MorphogenResponse]: ...
    def count_by_status(self) -> dict[str, int]: ...
    def count_responses(self) -> int: ...


NowFactory = Callable[[], str]
IdFactory = Callable[[], str]


class FieldService:
    def __init__(
        self,
        repository: MorphogenRepository,
        now_factory: NowFactory | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self.repository = repository
        self.now_factory = now_factory or _default_now
        self.id_factory = id_factory or _default_id

    # --- command dispatch -------------------------------------------------

    def handle(self, command: Any) -> Any:
        if isinstance(command, EmitRequest):
            return self._handle_emit(command, KIND_REQUEST)
        if isinstance(command, EmitSignal):
            return self._handle_emit(command, KIND_SIGNAL)
        if isinstance(command, ClaimRequest):
            return self._handle_claim_request(command)
        if isinstance(command, RenewClaim):
            return self._handle_renew_claim(command)
        if isinstance(command, ReleaseClaim):
            return self._handle_release_claim(command)
        if isinstance(command, FulfillRequest):
            return self._handle_fulfill_request(command)
        if isinstance(command, RespondSignal):
            return self._handle_respond_signal(command)
        if isinstance(command, SweepDecayed):
            return self._handle_sweep_decayed(command)
        raise TypeError(f"unsupported command: {type(command)!r}")

    # --- read side --------------------------------------------------------

    def read_field(self, command: ReadField) -> list[Morphogen]:
        now = self.now_factory()
        kinds = self._validate_kinds(command.kinds)
        reader_tags = normalize_tags(command.reader_tags) if command.reader_tags else []
        wanted_status = command.status
        # TTL is only a filter for "still in the field" reads. Terminal
        # statuses (decayed, fulfilled) ignore TTL — those rows are by
        # definition past it.
        apply_ttl = wanted_status is None or wanted_status in (
            STATUS_EMITTED,
            STATUS_CLAIMED,
        )

        out: list[Morphogen] = []
        for morphogen in self.repository.list_morphogens():
            if not self._passes_status_filter(morphogen, wanted_status):
                continue
            if apply_ttl and _ttl_expired(morphogen, now):
                continue
            if kinds and morphogen.kind not in kinds:
                continue
            if command.since is not None and morphogen.emitted_at < command.since:
                continue
            if reader_tags and not _tags_match(morphogen.tags, reader_tags):
                continue
            out.append(morphogen)
        out.sort(key=lambda m: m.emitted_at)
        return out

    def get_morphogen(self, command: GetMorphogen) -> MorphogenView | None:
        morphogen = self.repository.get_morphogen(command.morphogen_id)
        if morphogen is None:
            return None
        responses = self.repository.list_responses(command.morphogen_id)
        return MorphogenView(morphogen=morphogen, responses=responses)

    def health_report(self) -> HealthReport:
        counts = self.repository.count_by_status()
        active = counts.get(STATUS_EMITTED, 0) + counts.get(STATUS_CLAIMED, 0)
        decayed = counts.get(STATUS_DECAYED, 0)
        field_size = sum(counts.values())
        return HealthReport(
            ok=True,
            field_size=field_size,
            active_count=active,
            decayed_count=decayed,
            response_count=self.repository.count_responses(),
            checked_at=self.now_factory(),
        )

    # --- emit handlers ----------------------------------------------------

    def _handle_emit(
        self, command: EmitRequest | EmitSignal, kind: str
    ) -> RequestEmitted | SignalEmitted:
        if not command.source_cell.strip():
            raise ValueError("source_cell is required")
        if command.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        normalized = normalize_tags(command.tags)
        if not normalized:
            raise ValueError("at least one tag is required")
        if not isinstance(command.payload, dict):
            raise TypeError("payload must be a JSON object")

        ph = payload_hash(command.payload)
        th = tags_hash(normalized)

        if command.nonce is None:
            existing = self.repository.find_active_dedup(ph, th, kind)
            if existing is not None:
                existing.concentration += 1
                self.repository.update_morphogen(existing)
                return _emit_event(kind, existing, fresh=False)

        morphogen = Morphogen(
            id=self.id_factory(),
            source_cell=command.source_cell,
            kind=kind,
            payload=dict(command.payload),
            payload_hash=ph,
            tags=list(normalized),
            tags_hash=th,
            nonce=command.nonce,
            emitted_at=self.now_factory(),
            ttl_seconds=command.ttl_seconds,
        )
        self.repository.add_morphogen(morphogen)
        return _emit_event(kind, morphogen, fresh=True)

    # --- singleton-claim handlers -----------------------------------------

    def _handle_claim_request(self, command: ClaimRequest) -> RequestClaimed:
        morphogen = self._require_morphogen(command.morphogen_id)
        if morphogen.kind != KIND_REQUEST:
            raise ValueError(
                f"morphogen {morphogen.id} is a signal; only requests can be claimed"
            )
        if command.lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        if not command.claimer_cell.strip():
            raise ValueError("claimer_cell is required")
        now = self.now_factory()
        self._reap_lease_inplace(morphogen, now)
        if morphogen.status != STATUS_EMITTED:
            raise ValueError(
                f"morphogen {morphogen.id} not claimable; status={morphogen.status}"
            )
        if _ttl_expired(morphogen, now):
            raise ValueError(f"morphogen {morphogen.id} has decayed")
        morphogen.status = STATUS_CLAIMED
        morphogen.claimed_by = command.claimer_cell
        morphogen.claimed_at = now
        morphogen.lease_expires_at = _iso_offset(now, command.lease_ttl_seconds)
        self.repository.update_morphogen(morphogen)
        return RequestClaimed(morphogen=morphogen)

    def _handle_renew_claim(self, command: RenewClaim) -> ClaimRenewed:
        morphogen = self._require_morphogen(command.morphogen_id)
        if command.lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        if morphogen.status != STATUS_CLAIMED:
            raise ValueError(
                f"morphogen {morphogen.id} is not claimed; status={morphogen.status}"
            )
        if morphogen.claimed_by != command.claimer_cell:
            raise ValueError(
                f"morphogen {morphogen.id} claimed by "
                f"{morphogen.claimed_by!r}, not {command.claimer_cell!r}"
            )
        now = self.now_factory()
        if morphogen.lease_expires_at and morphogen.lease_expires_at < now:
            raise ValueError(
                f"morphogen {morphogen.id} lease already expired at "
                f"{morphogen.lease_expires_at}; release and re-claim instead"
            )
        morphogen.lease_expires_at = _iso_offset(now, command.lease_ttl_seconds)
        self.repository.update_morphogen(morphogen)
        return ClaimRenewed(morphogen=morphogen)

    def _handle_release_claim(self, command: ReleaseClaim) -> ClaimReleased:
        morphogen = self._require_morphogen(command.morphogen_id)
        if morphogen.status != STATUS_CLAIMED:
            raise ValueError(
                f"morphogen {morphogen.id} is not claimed; status={morphogen.status}"
            )
        if morphogen.claimed_by != command.claimer_cell:
            raise ValueError(
                f"morphogen {morphogen.id} claimed by "
                f"{morphogen.claimed_by!r}, not {command.claimer_cell!r}"
            )
        morphogen.status = STATUS_EMITTED
        morphogen.claimed_by = None
        morphogen.claimed_at = None
        morphogen.lease_expires_at = None
        self.repository.update_morphogen(morphogen)
        return ClaimReleased(morphogen=morphogen)

    def _handle_fulfill_request(self, command: FulfillRequest) -> RequestFulfilled:
        morphogen = self._require_morphogen(command.morphogen_id)
        if morphogen.kind != KIND_REQUEST:
            raise ValueError(
                f"morphogen {morphogen.id} is a signal; only requests can be fulfilled"
            )
        if morphogen.status != STATUS_CLAIMED:
            raise ValueError(
                f"morphogen {morphogen.id} is not claimed; status={morphogen.status}"
            )
        if morphogen.claimed_by != command.claimer_cell:
            raise ValueError(
                f"morphogen {morphogen.id} claimed by "
                f"{morphogen.claimed_by!r}, not {command.claimer_cell!r}"
            )
        if not command.outcome.strip():
            raise ValueError("outcome is required")
        morphogen.status = STATUS_FULFILLED
        morphogen.fulfilled_at = self.now_factory()
        morphogen.fulfilled_outcome = command.outcome
        morphogen.fulfilled_link = command.link
        self.repository.update_morphogen(morphogen)
        return RequestFulfilled(morphogen=morphogen)

    # --- signal-response handler ------------------------------------------

    def _handle_respond_signal(self, command: RespondSignal) -> SignalResponded:
        morphogen = self._require_morphogen(command.morphogen_id)
        if morphogen.kind != KIND_SIGNAL:
            raise ValueError(
                f"morphogen {morphogen.id} is a request; respond_signal "
                "is for signals only"
            )
        now = self.now_factory()
        if _ttl_expired(morphogen, now):
            raise ValueError(f"morphogen {morphogen.id} has decayed")
        if not command.responder_cell.strip():
            raise ValueError("responder_cell is required")
        if not isinstance(command.response_payload, dict):
            raise TypeError("response_payload must be a JSON object")
        response = MorphogenResponse(
            id=self.id_factory(),
            morphogen_id=morphogen.id,
            responder_cell=command.responder_cell,
            response_payload=dict(command.response_payload),
            link=command.link,
            responded_at=now,
        )
        self.repository.add_response(response)
        return SignalResponded(morphogen=morphogen, response=response)

    # --- sweep handler ----------------------------------------------------

    def _handle_sweep_decayed(self, command: SweepDecayed) -> FieldSwept:
        del command
        now = self.now_factory()
        decayed: list[Morphogen] = []
        reaped: list[Morphogen] = []
        for morphogen in self.repository.list_morphogens():
            if morphogen.status not in (STATUS_EMITTED, STATUS_CLAIMED):
                continue
            if _ttl_expired(morphogen, now):
                morphogen.status = STATUS_DECAYED
                self.repository.update_morphogen(morphogen)
                decayed.append(morphogen)
                continue
            if (
                morphogen.status == STATUS_CLAIMED
                and morphogen.lease_expires_at is not None
                and morphogen.lease_expires_at < now
            ):
                morphogen.status = STATUS_EMITTED
                morphogen.claimed_by = None
                morphogen.claimed_at = None
                morphogen.lease_expires_at = None
                self.repository.update_morphogen(morphogen)
                reaped.append(morphogen)
        return FieldSwept(decayed=decayed, reaped=reaped)

    # --- helpers ----------------------------------------------------------

    def _require_morphogen(self, morphogen_id: str) -> Morphogen:
        morphogen = self.repository.get_morphogen(morphogen_id)
        if morphogen is None:
            raise LookupError(f"unknown morphogen: {morphogen_id!r}")
        return morphogen

    def _passes_status_filter(self, morphogen: Morphogen, wanted: str | None) -> bool:
        if wanted is None:
            return morphogen.status in (STATUS_EMITTED, STATUS_CLAIMED)
        return morphogen.status == wanted

    def _validate_kinds(self, kinds: list[str] | None) -> set[str]:
        if not kinds:
            return set()
        unknown = set(kinds) - KINDS
        if unknown:
            raise ValueError(f"unknown kind(s): {sorted(unknown)!r}")
        return set(kinds)

    def _reap_lease_inplace(self, morphogen: Morphogen, now: str) -> None:
        if morphogen.status != STATUS_CLAIMED:
            return
        if morphogen.lease_expires_at is None:
            return
        if morphogen.lease_expires_at >= now:
            return
        morphogen.status = STATUS_EMITTED
        morphogen.claimed_by = None
        morphogen.claimed_at = None
        morphogen.lease_expires_at = None


def _emit_event(
    kind: str, morphogen: Morphogen, fresh: bool
) -> RequestEmitted | SignalEmitted:
    if kind == KIND_REQUEST:
        return RequestEmitted(morphogen=morphogen, fresh=fresh)
    return SignalEmitted(morphogen=morphogen, fresh=fresh)


def _ttl_expired(morphogen: Morphogen, now: str) -> bool:
    emitted = datetime.fromisoformat(morphogen.emitted_at)
    expiry = emitted + timedelta(seconds=morphogen.ttl_seconds)
    now_dt = datetime.fromisoformat(now)
    return now_dt >= expiry


def _iso_offset(now_iso: str, seconds: int) -> str:
    now_dt = datetime.fromisoformat(now_iso)
    return (now_dt + timedelta(seconds=seconds)).isoformat()


def _tags_match(morphogen_tags: list[str], reader_tags: list[str]) -> bool:
    """A morphogen tag matches a reader tag if either is a prefix of the
    other in the dot hierarchy. Returns True iff any pair matches."""
    for mt in morphogen_tags:
        for rt in reader_tags:
            if mt == rt:
                return True
            if mt.startswith(rt + "."):
                return True
            if rt.startswith(mt + "."):
                return True
    return False


def _default_now() -> str:
    return datetime.now(UTC).isoformat()


def _default_id() -> str:
    return f"m_{token_urlsafe(8)}"
