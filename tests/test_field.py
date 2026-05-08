"""End-to-end business-logic tests for the FieldService.

Exercises the domain layer through commands + repository round-trips, with
``FakeClock`` injected so TTL/lease windows are deterministic. The MCP and
SQLite layers are out of scope here and live in their own test files.
"""

from __future__ import annotations

import pytest

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
from morphogen.domain.events import (
    ClaimReleased,
    ClaimRenewed,
    FieldSwept,
    RequestClaimed,
    RequestEmitted,
    RequestFulfilled,
    SignalEmitted,
    SignalResponded,
)
from morphogen.domain.field import FieldService
from morphogen.domain.models import (
    KIND_REQUEST,
    KIND_SIGNAL,
    STATUS_CLAIMED,
    STATUS_DECAYED,
    STATUS_EMITTED,
    STATUS_FULFILLED,
)
from tests.fixtures import FakeClock, IdSequence, InMemoryRepository


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def service(clock: FakeClock) -> FieldService:
    return FieldService(
        repository=InMemoryRepository(),
        now_factory=clock.now,
        id_factory=IdSequence(),
    )


# --- emit / concentration ----------------------------------------------------


class TestEmit:
    def test_emit_request_creates_fresh_morphogen(self, service: FieldService) -> None:
        event = service.handle(
            EmitRequest(
                source_cell="alpha",
                tags=["analysis.static"],
                payload={"target": "auth_module"},
                ttl_seconds=3600,
            )
        )
        assert isinstance(event, RequestEmitted)
        assert event.fresh is True
        assert event.morphogen.kind == KIND_REQUEST
        assert event.morphogen.source_cell == "alpha"
        assert event.morphogen.tags == ["analysis.static"]
        assert event.morphogen.concentration == 1
        assert event.morphogen.status == STATUS_EMITTED

    def test_emit_signal_creates_fresh_morphogen(self, service: FieldService) -> None:
        event = service.handle(
            EmitSignal(
                source_cell="alpha",
                tags=["review"],
                payload={"q": "ok?"},
                ttl_seconds=600,
            )
        )
        assert isinstance(event, SignalEmitted)
        assert event.fresh is True
        assert event.morphogen.kind == KIND_SIGNAL

    def test_re_emit_increments_concentration(self, service: FieldService) -> None:
        first = service.handle(
            EmitRequest(
                source_cell="alpha",
                tags=["analysis"],
                payload={"target": "x"},
                ttl_seconds=3600,
            )
        )
        second = service.handle(
            EmitRequest(
                source_cell="beta",
                tags=["analysis"],
                payload={"target": "x"},
                ttl_seconds=3600,
            )
        )
        assert first.morphogen.id == second.morphogen.id
        assert second.fresh is False
        assert second.morphogen.concentration == 2
        assert second.morphogen.source_cell == "alpha"  # original source preserved

    def test_re_emit_with_different_payload_does_not_concentrate(
        self, service: FieldService
    ) -> None:
        first = service.handle(
            EmitRequest(
                source_cell="alpha",
                tags=["analysis"],
                payload={"target": "x"},
                ttl_seconds=3600,
            )
        )
        second = service.handle(
            EmitRequest(
                source_cell="alpha",
                tags=["analysis"],
                payload={"target": "y"},
                ttl_seconds=3600,
            )
        )
        assert first.morphogen.id != second.morphogen.id
        assert second.fresh is True

    def test_request_and_signal_with_same_payload_do_not_merge(
        self, service: FieldService
    ) -> None:
        req = service.handle(
            EmitRequest(
                source_cell="a",
                tags=["t"],
                payload={"k": 1},
                ttl_seconds=600,
            )
        )
        sig = service.handle(
            EmitSignal(
                source_cell="a",
                tags=["t"],
                payload={"k": 1},
                ttl_seconds=600,
            )
        )
        assert req.morphogen.id != sig.morphogen.id

    def test_nonce_bypasses_dedup(self, service: FieldService) -> None:
        first = service.handle(
            EmitRequest(
                source_cell="alpha",
                tags=["analysis"],
                payload={"target": "x"},
                ttl_seconds=3600,
            )
        )
        second = service.handle(
            EmitRequest(
                source_cell="alpha",
                tags=["analysis"],
                payload={"target": "x"},
                ttl_seconds=3600,
                nonce="parallel-run-7",
            )
        )
        assert first.morphogen.id != second.morphogen.id
        assert second.fresh is True
        assert second.morphogen.concentration == 1

    def test_does_not_concentrate_into_claimed_morphogen(
        self, service: FieldService
    ) -> None:
        emitted = service.handle(
            EmitRequest(
                source_cell="alpha",
                tags=["analysis"],
                payload={"k": 1},
                ttl_seconds=3600,
            )
        )
        service.handle(
            ClaimRequest(
                morphogen_id=emitted.morphogen.id,
                claimer_cell="worker-1",
                lease_ttl_seconds=300,
            )
        )
        re_emit = service.handle(
            EmitRequest(
                source_cell="beta",
                tags=["analysis"],
                payload={"k": 1},
                ttl_seconds=3600,
            )
        )
        assert re_emit.morphogen.id != emitted.morphogen.id
        assert re_emit.fresh is True

    def test_emit_rejects_empty_tags(self, service: FieldService) -> None:
        with pytest.raises(ValueError, match="at least one tag"):
            service.handle(
                EmitRequest(source_cell="alpha", tags=[], payload={}, ttl_seconds=60)
            )

    def test_emit_rejects_non_positive_ttl(self, service: FieldService) -> None:
        with pytest.raises(ValueError, match="ttl_seconds"):
            service.handle(
                EmitRequest(source_cell="alpha", tags=["t"], payload={}, ttl_seconds=0)
            )

    def test_emit_rejects_blank_source_cell(self, service: FieldService) -> None:
        with pytest.raises(ValueError, match="source_cell"):
            service.handle(
                EmitRequest(source_cell="   ", tags=["t"], payload={}, ttl_seconds=60)
            )

    def test_emit_rejects_non_object_payload(self, service: FieldService) -> None:
        with pytest.raises(TypeError, match="payload"):
            service.handle(
                EmitRequest(
                    source_cell="a",
                    tags=["t"],
                    payload="not a dict",  # type: ignore[arg-type]
                    ttl_seconds=60,
                )
            )


# --- read field --------------------------------------------------------------


class TestReadField:
    def test_returns_emitted_morphogens(self, service: FieldService) -> None:
        service.handle(
            EmitRequest(
                source_cell="alpha", tags=["analysis"], payload={}, ttl_seconds=600
            )
        )
        results = service.read_field(ReadField(reader_cell="reader"))
        assert len(results) == 1

    def test_filters_by_kind(self, service: FieldService) -> None:
        service.handle(
            EmitRequest(source_cell="a", tags=["t"], payload={"x": 1}, ttl_seconds=600)
        )
        service.handle(
            EmitSignal(source_cell="a", tags=["t"], payload={"x": 2}, ttl_seconds=600)
        )
        only_signals = service.read_field(
            ReadField(reader_cell="r", kinds=[KIND_SIGNAL])
        )
        assert len(only_signals) == 1
        assert only_signals[0].kind == KIND_SIGNAL

    def test_excludes_ttl_expired_even_before_sweep(
        self, service: FieldService, clock: FakeClock
    ) -> None:
        service.handle(
            EmitRequest(source_cell="a", tags=["t"], payload={}, ttl_seconds=10)
        )
        clock.advance(11)
        results = service.read_field(ReadField(reader_cell="r"))
        assert results == []

    def test_excludes_decayed_when_default_active(
        self, service: FieldService, clock: FakeClock
    ) -> None:
        emit = service.handle(
            EmitSignal(source_cell="a", tags=["t"], payload={}, ttl_seconds=10)
        )
        clock.advance(11)
        service.handle(SweepDecayed())
        # default status filter is "active" (emitted | claimed)
        assert service.read_field(ReadField(reader_cell="r")) == []
        # explicit decayed filter sees it
        decayed = service.read_field(ReadField(reader_cell="r", status=STATUS_DECAYED))
        assert len(decayed) == 1
        assert decayed[0].id == emit.morphogen.id

    def test_bidirectional_tag_prefix_match(self, service: FieldService) -> None:
        # broad emitter, specific reader
        broad = service.handle(
            EmitSignal(
                source_cell="a", tags=["analysis"], payload={"k": 1}, ttl_seconds=600
            )
        )
        # specific emitter, broad reader
        specific = service.handle(
            EmitSignal(
                source_cell="b",
                tags=["analysis.static.python"],
                payload={"k": 2},
                ttl_seconds=600,
            )
        )

        specific_reader = service.read_field(
            ReadField(reader_cell="r", reader_tags=["analysis.static.python"])
        )
        assert {m.id for m in specific_reader} == {
            broad.morphogen.id,
            specific.morphogen.id,
        }

        broad_reader = service.read_field(
            ReadField(reader_cell="r", reader_tags=["analysis"])
        )
        assert {m.id for m in broad_reader} == {
            broad.morphogen.id,
            specific.morphogen.id,
        }

        unrelated = service.read_field(
            ReadField(reader_cell="r", reader_tags=["build"])
        )
        assert unrelated == []

    def test_prefix_match_only_at_dot_boundary(self, service: FieldService) -> None:
        # `analysis-static` must NOT match `analysis` (no dot boundary)
        service.handle(
            EmitSignal(
                source_cell="a",
                tags=["analysis-static"],
                payload={},
                ttl_seconds=600,
            )
        )
        results = service.read_field(
            ReadField(reader_cell="r", reader_tags=["analysis"])
        )
        assert results == []

    def test_filters_by_since(self, service: FieldService, clock: FakeClock) -> None:
        service.handle(
            EmitSignal(source_cell="a", tags=["t"], payload={"k": 1}, ttl_seconds=600)
        )
        clock.advance(60)
        cutoff = clock.now()
        clock.advance(60)
        service.handle(
            EmitSignal(source_cell="a", tags=["t"], payload={"k": 2}, ttl_seconds=600)
        )
        recent = service.read_field(ReadField(reader_cell="r", since=cutoff))
        assert len(recent) == 1
        assert recent[0].payload == {"k": 2}

    def test_results_sorted_by_emitted_at(
        self, service: FieldService, clock: FakeClock
    ) -> None:
        first = service.handle(
            EmitSignal(source_cell="a", tags=["t"], payload={"k": 1}, ttl_seconds=600)
        )
        clock.advance(1)
        second = service.handle(
            EmitSignal(source_cell="a", tags=["t"], payload={"k": 2}, ttl_seconds=600)
        )
        results = service.read_field(ReadField(reader_cell="r"))
        assert [m.id for m in results] == [first.morphogen.id, second.morphogen.id]

    def test_unknown_kind_rejected(self, service: FieldService) -> None:
        with pytest.raises(ValueError, match="unknown kind"):
            service.read_field(ReadField(reader_cell="r", kinds=["bogus"]))


# --- claim lifecycle ---------------------------------------------------------


class TestClaimLifecycle:
    def _emit_request(self, service: FieldService) -> str:
        event = service.handle(
            EmitRequest(
                source_cell="alpha",
                tags=["analysis"],
                payload={},
                ttl_seconds=3600,
            )
        )
        return event.morphogen.id

    def test_claim_request_marks_claimed(self, service: FieldService) -> None:
        mid = self._emit_request(service)
        event = service.handle(
            ClaimRequest(
                morphogen_id=mid, claimer_cell="worker-1", lease_ttl_seconds=300
            )
        )
        assert isinstance(event, RequestClaimed)
        assert event.morphogen.status == STATUS_CLAIMED
        assert event.morphogen.claimed_by == "worker-1"
        assert event.morphogen.lease_expires_at is not None

    def test_cannot_claim_signal(self, service: FieldService) -> None:
        signal_id = service.handle(
            EmitSignal(source_cell="a", tags=["t"], payload={}, ttl_seconds=600)
        ).morphogen.id
        with pytest.raises(ValueError, match="signal"):
            service.handle(
                ClaimRequest(
                    morphogen_id=signal_id,
                    claimer_cell="w",
                    lease_ttl_seconds=300,
                )
            )

    def test_second_claim_rejected_while_lease_valid(
        self, service: FieldService
    ) -> None:
        mid = self._emit_request(service)
        service.handle(
            ClaimRequest(
                morphogen_id=mid, claimer_cell="worker-1", lease_ttl_seconds=300
            )
        )
        with pytest.raises(ValueError, match="not claimable"):
            service.handle(
                ClaimRequest(
                    morphogen_id=mid,
                    claimer_cell="worker-2",
                    lease_ttl_seconds=300,
                )
            )

    def test_lease_expiry_returns_to_emitted_for_re_claim(
        self, service: FieldService, clock: FakeClock
    ) -> None:
        mid = self._emit_request(service)
        service.handle(
            ClaimRequest(
                morphogen_id=mid, claimer_cell="worker-1", lease_ttl_seconds=10
            )
        )
        clock.advance(11)
        # implicit lease reap on the next claim attempt
        event = service.handle(
            ClaimRequest(
                morphogen_id=mid, claimer_cell="worker-2", lease_ttl_seconds=300
            )
        )
        assert isinstance(event, RequestClaimed)
        assert event.morphogen.claimed_by == "worker-2"

    def test_renew_claim_extends_lease(
        self, service: FieldService, clock: FakeClock
    ) -> None:
        mid = self._emit_request(service)
        service.handle(
            ClaimRequest(
                morphogen_id=mid, claimer_cell="worker-1", lease_ttl_seconds=300
            )
        )
        clock.advance(60)
        event = service.handle(
            RenewClaim(morphogen_id=mid, claimer_cell="worker-1", lease_ttl_seconds=600)
        )
        assert isinstance(event, ClaimRenewed)
        # New expiry is now (60s in) + 600s.
        assert event.morphogen.lease_expires_at is not None
        assert event.morphogen.lease_expires_at > clock.now()

    def test_renew_by_wrong_cell_rejected(self, service: FieldService) -> None:
        mid = self._emit_request(service)
        service.handle(
            ClaimRequest(
                morphogen_id=mid, claimer_cell="worker-1", lease_ttl_seconds=300
            )
        )
        with pytest.raises(ValueError, match="claimed by"):
            service.handle(
                RenewClaim(
                    morphogen_id=mid,
                    claimer_cell="worker-2",
                    lease_ttl_seconds=300,
                )
            )

    def test_renew_after_lease_expired_rejected(
        self, service: FieldService, clock: FakeClock
    ) -> None:
        mid = self._emit_request(service)
        service.handle(
            ClaimRequest(
                morphogen_id=mid, claimer_cell="worker-1", lease_ttl_seconds=10
            )
        )
        clock.advance(11)
        with pytest.raises(ValueError, match="lease already expired"):
            service.handle(
                RenewClaim(
                    morphogen_id=mid,
                    claimer_cell="worker-1",
                    lease_ttl_seconds=300,
                )
            )

    def test_release_returns_to_emitted(self, service: FieldService) -> None:
        mid = self._emit_request(service)
        service.handle(
            ClaimRequest(
                morphogen_id=mid, claimer_cell="worker-1", lease_ttl_seconds=300
            )
        )
        event = service.handle(ReleaseClaim(morphogen_id=mid, claimer_cell="worker-1"))
        assert isinstance(event, ClaimReleased)
        assert event.morphogen.status == STATUS_EMITTED
        assert event.morphogen.claimed_by is None

    def test_release_by_wrong_cell_rejected(self, service: FieldService) -> None:
        mid = self._emit_request(service)
        service.handle(
            ClaimRequest(
                morphogen_id=mid, claimer_cell="worker-1", lease_ttl_seconds=300
            )
        )
        with pytest.raises(ValueError, match="claimed by"):
            service.handle(ReleaseClaim(morphogen_id=mid, claimer_cell="worker-2"))

    def test_fulfill_marks_fulfilled(self, service: FieldService) -> None:
        mid = self._emit_request(service)
        service.handle(
            ClaimRequest(
                morphogen_id=mid, claimer_cell="worker-1", lease_ttl_seconds=300
            )
        )
        event = service.handle(
            FulfillRequest(
                morphogen_id=mid,
                claimer_cell="worker-1",
                outcome="merged",
                link="https://github.com/x/pull/42",
            )
        )
        assert isinstance(event, RequestFulfilled)
        assert event.morphogen.status == STATUS_FULFILLED
        assert event.morphogen.fulfilled_outcome == "merged"
        assert event.morphogen.fulfilled_link == "https://github.com/x/pull/42"

    def test_fulfill_when_not_claimed_rejected(self, service: FieldService) -> None:
        mid = self._emit_request(service)
        with pytest.raises(ValueError, match="not claimed"):
            service.handle(
                FulfillRequest(morphogen_id=mid, claimer_cell="worker-1", outcome="x")
            )

    def test_fulfill_by_wrong_cell_rejected(self, service: FieldService) -> None:
        mid = self._emit_request(service)
        service.handle(
            ClaimRequest(
                morphogen_id=mid, claimer_cell="worker-1", lease_ttl_seconds=300
            )
        )
        with pytest.raises(ValueError, match="claimed by"):
            service.handle(
                FulfillRequest(morphogen_id=mid, claimer_cell="worker-2", outcome="x")
            )


# --- respond_signal ----------------------------------------------------------


class TestRespondSignal:
    def test_response_appended(self, service: FieldService) -> None:
        signal = service.handle(
            EmitSignal(
                source_cell="a",
                tags=["needs-cell"],
                payload={"purpose": "..."},
                ttl_seconds=600,
            )
        )
        event = service.handle(
            RespondSignal(
                morphogen_id=signal.morphogen.id,
                responder_cell="b",
                response_payload={"type": "endorse"},
            )
        )
        assert isinstance(event, SignalResponded)
        assert event.response.responder_cell == "b"

        view = service.get_morphogen(GetMorphogen(morphogen_id=signal.morphogen.id))
        assert view is not None
        assert len(view.responses) == 1

    def test_cannot_respond_to_request(self, service: FieldService) -> None:
        request = service.handle(
            EmitRequest(source_cell="a", tags=["t"], payload={}, ttl_seconds=600)
        )
        with pytest.raises(ValueError, match="signals only"):
            service.handle(
                RespondSignal(
                    morphogen_id=request.morphogen.id,
                    responder_cell="b",
                    response_payload={},
                )
            )

    def test_cannot_respond_after_decay(
        self, service: FieldService, clock: FakeClock
    ) -> None:
        signal = service.handle(
            EmitSignal(source_cell="a", tags=["t"], payload={}, ttl_seconds=10)
        )
        clock.advance(11)
        with pytest.raises(ValueError, match="decayed"):
            service.handle(
                RespondSignal(
                    morphogen_id=signal.morphogen.id,
                    responder_cell="b",
                    response_payload={},
                )
            )


# --- sweep_decayed -----------------------------------------------------------


class TestSweepDecayed:
    def test_marks_ttl_expired_emissions_as_decayed(
        self, service: FieldService, clock: FakeClock
    ) -> None:
        fresh = service.handle(
            EmitSignal(
                source_cell="a",
                tags=["t"],
                payload={"k": "fresh"},
                ttl_seconds=600,
            )
        )
        stale = service.handle(
            EmitSignal(
                source_cell="a",
                tags=["t"],
                payload={"k": "stale"},
                ttl_seconds=10,
            )
        )
        clock.advance(11)
        result = service.handle(SweepDecayed())
        assert isinstance(result, FieldSwept)
        assert [m.id for m in result.decayed] == [stale.morphogen.id]
        assert result.reaped == []
        # Fresh one untouched.
        view = service.get_morphogen(GetMorphogen(morphogen_id=fresh.morphogen.id))
        assert view is not None
        assert view.morphogen.status == STATUS_EMITTED

    def test_reaps_expired_leases_back_to_emitted(
        self, service: FieldService, clock: FakeClock
    ) -> None:
        emitted = service.handle(
            EmitRequest(
                source_cell="a",
                tags=["t"],
                payload={},
                ttl_seconds=3600,
            )
        )
        service.handle(
            ClaimRequest(
                morphogen_id=emitted.morphogen.id,
                claimer_cell="w",
                lease_ttl_seconds=10,
            )
        )
        clock.advance(11)
        result = service.handle(SweepDecayed())
        assert [m.id for m in result.reaped] == [emitted.morphogen.id]
        assert result.decayed == []
        view = service.get_morphogen(GetMorphogen(morphogen_id=emitted.morphogen.id))
        assert view is not None
        assert view.morphogen.status == STATUS_EMITTED
        assert view.morphogen.claimed_by is None

    def test_ttl_decay_takes_precedence_over_lease_reap(
        self, service: FieldService, clock: FakeClock
    ) -> None:
        # If both TTL and lease have expired, the row decays — it does
        # not go back to emitted just to immediately decay on the next sweep.
        emitted = service.handle(
            EmitRequest(source_cell="a", tags=["t"], payload={}, ttl_seconds=10)
        )
        service.handle(
            ClaimRequest(
                morphogen_id=emitted.morphogen.id,
                claimer_cell="w",
                lease_ttl_seconds=5,
            )
        )
        clock.advance(20)
        result = service.handle(SweepDecayed())
        assert [m.id for m in result.decayed] == [emitted.morphogen.id]
        assert result.reaped == []

    def test_idempotent_when_nothing_to_sweep(self, service: FieldService) -> None:
        service.handle(
            EmitSignal(source_cell="a", tags=["t"], payload={}, ttl_seconds=600)
        )
        first = service.handle(SweepDecayed())
        second = service.handle(SweepDecayed())
        assert first.decayed == [] and first.reaped == []
        assert second.decayed == [] and second.reaped == []


# --- get_morphogen / health --------------------------------------------------


class TestReadHelpers:
    def test_get_morphogen_returns_view_with_responses(
        self, service: FieldService
    ) -> None:
        signal = service.handle(
            EmitSignal(source_cell="a", tags=["t"], payload={}, ttl_seconds=600)
        )
        service.handle(
            RespondSignal(
                morphogen_id=signal.morphogen.id,
                responder_cell="b",
                response_payload={"type": "endorse"},
            )
        )
        view = service.get_morphogen(GetMorphogen(morphogen_id=signal.morphogen.id))
        assert view is not None
        assert view.morphogen.id == signal.morphogen.id
        assert len(view.responses) == 1

    def test_get_morphogen_unknown_id_returns_none(self, service: FieldService) -> None:
        assert service.get_morphogen(GetMorphogen(morphogen_id="ghost")) is None

    def test_health_report_counts_match_state(
        self, service: FieldService, clock: FakeClock
    ) -> None:
        service.handle(
            EmitSignal(source_cell="a", tags=["t"], payload={}, ttl_seconds=10)
        )
        service.handle(
            EmitRequest(
                source_cell="a",
                tags=["t"],
                payload={"k": 2},
                ttl_seconds=3600,
            )
        )
        clock.advance(11)
        service.handle(SweepDecayed())
        report = service.health_report()
        assert report.ok is True
        assert report.field_size == 2
        assert report.active_count == 1
        assert report.decayed_count == 1
        assert report.response_count == 0
