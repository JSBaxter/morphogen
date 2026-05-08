"""SQLite repository round-trip tests.

These hit a real SQLite (in-memory) connection — no mocking — so the
schema, the JSON column round-trips, and the partial unique index for
concentration all exercise their real behavior.

The end-of-file ``TestFieldServiceOnSqlite`` smoke ensures the same
``FieldService`` business logic that passed against the in-memory
repository also passes against the SQLite-backed one. Treat the
in-memory repo as the contract spec; this file proves the SQLite
implementation honors it.
"""

from __future__ import annotations

import sqlite3

import pytest

from morphogen.domain.commands import (
    ClaimRequest,
    EmitRequest,
    EmitSignal,
    FulfillRequest,
    GetMorphogen,
    ReadField,
    RespondSignal,
    SweepDecayed,
)
from morphogen.domain.field import FieldService
from morphogen.domain.models import (
    KIND_REQUEST,
    KIND_SIGNAL,
    STATUS_CLAIMED,
    STATUS_DECAYED,
    STATUS_EMITTED,
    STATUS_FULFILLED,
    Morphogen,
    MorphogenResponse,
)
from morphogen.infra.repository import SQLiteRepository
from tests.fixtures import FakeClock, IdSequence


@pytest.fixture
def repo() -> SQLiteRepository:
    connection = sqlite3.connect(":memory:")
    repository = SQLiteRepository(connection)
    repository.init_schema()
    return repository


def _make_morphogen(
    repository_id: str = "m_0001",
    *,
    kind: str = KIND_REQUEST,
    status: str = STATUS_EMITTED,
    payload_hash: str = "ph_a",
    tags_hash: str = "th_a",
    nonce: str | None = None,
    emitted_at: str = "2026-01-01T00:00:00+00:00",
    tags: list[str] | None = None,
) -> Morphogen:
    return Morphogen(
        id=repository_id,
        source_cell="alpha",
        kind=kind,
        payload={"target": "x"},
        payload_hash=payload_hash,
        tags=tags if tags is not None else ["analysis", "build"],
        tags_hash=tags_hash,
        nonce=nonce,
        emitted_at=emitted_at,
        ttl_seconds=3600,
        status=status,
    )


# --- schema / setup ----------------------------------------------------------


class TestSchema:
    def test_init_schema_is_idempotent(self, repo: SQLiteRepository) -> None:
        repo.init_schema()  # second call must not raise
        repo.init_schema()
        # Sanity: tables actually exist.
        names = {
            row[0]
            for row in repo.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"morphogens", "morphogen_tags", "morphogen_responses"} <= names

    def test_partial_unique_dedup_index_present(self, repo: SQLiteRepository) -> None:
        rows = repo.connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name='idx_morphogens_dedup'"
        ).fetchone()
        assert rows is not None
        sql = rows["sql"]
        assert "WHERE" in sql
        assert "status" in sql and "'emitted'" in sql
        assert "nonce IS NULL" in sql


# --- morphogen round-trip ----------------------------------------------------


class TestMorphogenRoundTrip:
    def test_add_then_get_returns_equal_morphogen(self, repo: SQLiteRepository) -> None:
        original = _make_morphogen()
        repo.add_morphogen(original)
        loaded = repo.get_morphogen(original.id)
        assert loaded == original

    def test_get_unknown_returns_none(self, repo: SQLiteRepository) -> None:
        assert repo.get_morphogen("ghost") is None

    def test_payload_round_trips_nested_json(self, repo: SQLiteRepository) -> None:
        morphogen = _make_morphogen()
        morphogen.payload = {
            "nested": {"k": [1, 2, 3]},
            "list": ["a", "b"],
            "bool": True,
            "none": None,
        }
        repo.add_morphogen(morphogen)
        loaded = repo.get_morphogen(morphogen.id)
        assert loaded is not None
        assert loaded.payload == morphogen.payload

    def test_update_persists_lifecycle_changes(self, repo: SQLiteRepository) -> None:
        morphogen = _make_morphogen()
        repo.add_morphogen(morphogen)
        morphogen.status = STATUS_CLAIMED
        morphogen.claimed_by = "worker-1"
        morphogen.claimed_at = "2026-01-01T00:00:30+00:00"
        morphogen.lease_expires_at = "2026-01-01T00:05:30+00:00"
        morphogen.concentration = 7
        repo.update_morphogen(morphogen)
        loaded = repo.get_morphogen(morphogen.id)
        assert loaded == morphogen

    def test_update_unknown_raises(self, repo: SQLiteRepository) -> None:
        with pytest.raises(LookupError):
            repo.update_morphogen(_make_morphogen("ghost"))

    def test_list_morphogens_orders_by_emitted_at(self, repo: SQLiteRepository) -> None:
        early = _make_morphogen(
            "m_early",
            payload_hash="ph_early",
            emitted_at="2026-01-01T00:00:00+00:00",
        )
        late = _make_morphogen(
            "m_late",
            payload_hash="ph_late",
            emitted_at="2026-01-01T00:01:00+00:00",
        )
        repo.add_morphogen(late)
        repo.add_morphogen(early)
        ids = [m.id for m in repo.list_morphogens()]
        assert ids == ["m_early", "m_late"]

    def test_tags_are_persisted_and_returned(self, repo: SQLiteRepository) -> None:
        morphogen = _make_morphogen(tags=["alpha", "beta", "gamma"])
        repo.add_morphogen(morphogen)
        loaded = repo.get_morphogen(morphogen.id)
        assert loaded is not None
        assert loaded.tags == ["alpha", "beta", "gamma"]

    def test_morphogen_with_no_tags_round_trips(self, repo: SQLiteRepository) -> None:
        morphogen = _make_morphogen(tags=[])
        repo.add_morphogen(morphogen)
        loaded = repo.get_morphogen(morphogen.id)
        assert loaded is not None
        assert loaded.tags == []


# --- find_active_dedup -------------------------------------------------------


class TestFindActiveDedup:
    def test_returns_emitted_match(self, repo: SQLiteRepository) -> None:
        morphogen = _make_morphogen(payload_hash="ph_x", tags_hash="th_x")
        repo.add_morphogen(morphogen)
        assert repo.find_active_dedup("ph_x", "th_x", KIND_REQUEST) == morphogen

    def test_returns_none_for_claimed(self, repo: SQLiteRepository) -> None:
        morphogen = _make_morphogen(
            payload_hash="ph_x", tags_hash="th_x", status=STATUS_CLAIMED
        )
        repo.add_morphogen(morphogen)
        assert repo.find_active_dedup("ph_x", "th_x", KIND_REQUEST) is None

    def test_returns_none_for_fulfilled(self, repo: SQLiteRepository) -> None:
        morphogen = _make_morphogen(
            payload_hash="ph_x", tags_hash="th_x", status=STATUS_FULFILLED
        )
        repo.add_morphogen(morphogen)
        assert repo.find_active_dedup("ph_x", "th_x", KIND_REQUEST) is None

    def test_returns_none_for_decayed(self, repo: SQLiteRepository) -> None:
        morphogen = _make_morphogen(
            payload_hash="ph_x", tags_hash="th_x", status=STATUS_DECAYED
        )
        repo.add_morphogen(morphogen)
        assert repo.find_active_dedup("ph_x", "th_x", KIND_REQUEST) is None

    def test_returns_none_for_nonced_row(self, repo: SQLiteRepository) -> None:
        morphogen = _make_morphogen(
            payload_hash="ph_x", tags_hash="th_x", nonce="parallel-run-7"
        )
        repo.add_morphogen(morphogen)
        assert repo.find_active_dedup("ph_x", "th_x", KIND_REQUEST) is None

    def test_does_not_cross_kinds(self, repo: SQLiteRepository) -> None:
        morphogen = _make_morphogen(
            payload_hash="ph_x", tags_hash="th_x", kind=KIND_REQUEST
        )
        repo.add_morphogen(morphogen)
        assert repo.find_active_dedup("ph_x", "th_x", KIND_SIGNAL) is None


class TestPartialUniqueIndex:
    def test_duplicate_emitted_no_nonce_raises(self, repo: SQLiteRepository) -> None:
        first = _make_morphogen("m_a", payload_hash="ph_x", tags_hash="th_x")
        second = _make_morphogen("m_b", payload_hash="ph_x", tags_hash="th_x")
        repo.add_morphogen(first)
        with pytest.raises(sqlite3.IntegrityError):
            repo.add_morphogen(second)

    def test_nonced_rows_do_not_collide(self, repo: SQLiteRepository) -> None:
        # The partial unique index excludes rows where nonce IS NOT NULL,
        # so two parallel-run requests with the same payload+tags+kind
        # both insert cleanly.
        repo.add_morphogen(
            _make_morphogen("m_a", payload_hash="ph_x", tags_hash="th_x", nonce="run-1")
        )
        repo.add_morphogen(
            _make_morphogen("m_b", payload_hash="ph_x", tags_hash="th_x", nonce="run-2")
        )

    def test_terminal_status_does_not_block_new_emit(
        self, repo: SQLiteRepository
    ) -> None:
        # A decayed/fulfilled row with the same dedup key must not block a
        # fresh emission of equivalent work. The partial index restricts
        # uniqueness to emitted-with-no-nonce only.
        repo.add_morphogen(
            _make_morphogen(
                "m_old",
                payload_hash="ph_x",
                tags_hash="th_x",
                status=STATUS_DECAYED,
            )
        )
        repo.add_morphogen(
            _make_morphogen("m_new", payload_hash="ph_x", tags_hash="th_x")
        )


# --- responses ---------------------------------------------------------------


class TestResponses:
    def test_response_round_trip(self, repo: SQLiteRepository) -> None:
        morphogen = _make_morphogen(kind=KIND_SIGNAL)
        repo.add_morphogen(morphogen)
        response = MorphogenResponse(
            id="r_0001",
            morphogen_id=morphogen.id,
            responder_cell="beta",
            response_payload={"type": "endorse", "body": "+1"},
            link="https://x/y",
            responded_at="2026-01-01T00:00:30+00:00",
        )
        repo.add_response(response)
        loaded = repo.list_responses(morphogen.id)
        assert loaded == [response]

    def test_responses_scoped_by_morphogen_id(self, repo: SQLiteRepository) -> None:
        a = _make_morphogen("m_a", payload_hash="pa", kind=KIND_SIGNAL)
        b = _make_morphogen("m_b", payload_hash="pb", kind=KIND_SIGNAL)
        repo.add_morphogen(a)
        repo.add_morphogen(b)
        repo.add_response(
            MorphogenResponse(
                id="r1",
                morphogen_id="m_a",
                responder_cell="x",
                response_payload={},
                link=None,
                responded_at="2026-01-01T00:00:00+00:00",
            )
        )
        repo.add_response(
            MorphogenResponse(
                id="r2",
                morphogen_id="m_b",
                responder_cell="x",
                response_payload={},
                link=None,
                responded_at="2026-01-01T00:00:00+00:00",
            )
        )
        assert [r.id for r in repo.list_responses("m_a")] == ["r1"]
        assert [r.id for r in repo.list_responses("m_b")] == ["r2"]

    def test_list_responses_unknown_id_returns_empty(
        self, repo: SQLiteRepository
    ) -> None:
        assert repo.list_responses("ghost") == []


# --- counts ------------------------------------------------------------------


class TestCounts:
    def test_count_by_status_excludes_unrepresented_statuses(
        self, repo: SQLiteRepository
    ) -> None:
        repo.add_morphogen(
            _make_morphogen("m_e1", payload_hash="p1", status=STATUS_EMITTED)
        )
        repo.add_morphogen(
            _make_morphogen("m_e2", payload_hash="p2", status=STATUS_EMITTED)
        )
        repo.add_morphogen(
            _make_morphogen("m_c1", payload_hash="p3", status=STATUS_CLAIMED)
        )
        counts = repo.count_by_status()
        assert counts == {STATUS_EMITTED: 2, STATUS_CLAIMED: 1}

    def test_count_responses(self, repo: SQLiteRepository) -> None:
        repo.add_morphogen(_make_morphogen(kind=KIND_SIGNAL))
        assert repo.count_responses() == 0
        repo.add_response(
            MorphogenResponse(
                id="r",
                morphogen_id="m_0001",
                responder_cell="x",
                response_payload={},
                link=None,
                responded_at="2026-01-01T00:00:00+00:00",
            )
        )
        assert repo.count_responses() == 1


# --- end-to-end with FieldService -------------------------------------------


class TestFieldServiceOnSqlite:
    """Walk a non-trivial path through the FieldService while it's wired
    to the SQLite repository. If the in-memory and SQLite repos are
    contract-compatible, this should pass with no FieldService changes."""

    def test_emit_claim_fulfill_respond_sweep(self, repo: SQLiteRepository) -> None:
        clock = FakeClock()
        service = FieldService(
            repository=repo,
            now_factory=clock.now,
            id_factory=IdSequence(),
        )

        # 1. Emit a request twice; second emit must concentrate.
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
        assert second.fresh is False
        assert second.morphogen.id == first.morphogen.id
        assert second.morphogen.concentration == 2

        # 2. Claim, fulfill.
        service.handle(
            ClaimRequest(
                morphogen_id=first.morphogen.id,
                claimer_cell="worker-1",
                lease_ttl_seconds=300,
            )
        )
        fulfilled = service.handle(
            FulfillRequest(
                morphogen_id=first.morphogen.id,
                claimer_cell="worker-1",
                outcome="merged",
            )
        )
        assert fulfilled.morphogen.status == STATUS_FULFILLED

        # 3. Emit a signal, respond, sweep after TTL.
        signal = service.handle(
            EmitSignal(
                source_cell="alpha",
                tags=["review"],
                payload={"q": "ok?"},
                ttl_seconds=10,
            )
        )
        service.handle(
            RespondSignal(
                morphogen_id=signal.morphogen.id,
                responder_cell="beta",
                response_payload={"type": "endorse"},
            )
        )
        clock.advance(11)
        result = service.handle(SweepDecayed())
        assert [m.id for m in result.decayed] == [signal.morphogen.id]

        # 4. read_field default-active sees nothing now (request fulfilled,
        # signal decayed); decayed/fulfilled-status reads find both.
        assert service.read_field(ReadField(reader_cell="r")) == []
        decayed = service.read_field(ReadField(reader_cell="r", status=STATUS_DECAYED))
        assert [m.id for m in decayed] == [signal.morphogen.id]
        fulfilled_rows = service.read_field(
            ReadField(reader_cell="r", status=STATUS_FULFILLED)
        )
        assert [m.id for m in fulfilled_rows] == [first.morphogen.id]

        # 5. get_morphogen returns the signal + its response.
        view = service.get_morphogen(GetMorphogen(morphogen_id=signal.morphogen.id))
        assert view is not None
        assert len(view.responses) == 1

        # 6. Health report counts match.
        report = service.health_report()
        assert report.field_size == 2
        assert report.active_count == 0
        assert report.decayed_count == 1
        assert report.response_count == 1
