"""SQLite-backed implementation of ``MorphogenRepository``.

Mirrors the bundled queue's ``infra/repository.py`` pattern: an injected
``sqlite3.Connection`` (so callers can swap in ``:memory:`` for tests),
a ``connect`` classmethod that opens a real database file, and an
``init_schema`` step that runs ``schema.sql`` plus any idempotent
forward migrations the schema needs.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from morphogen.domain.models import (
    Morphogen,
    MorphogenResponse,
)


class SQLiteRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        db_path: str | None = None,
    ) -> None:
        self.connection = connection
        self.db_path = db_path
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    @classmethod
    def connect(cls, db_path: str | Path) -> SQLiteRepository:
        connection = sqlite3.connect(str(db_path), check_same_thread=False)
        repository = cls(connection, db_path=str(db_path))
        repository.init_schema()
        return repository

    def init_schema(self) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        self.connection.executescript(schema_path.read_text())
        self.connection.commit()

    # --- morphogens -------------------------------------------------------

    def add_morphogen(self, morphogen: Morphogen) -> None:
        self.connection.execute(
            """
            INSERT INTO morphogens (
                id, source_cell, kind, payload, payload_hash, tags_hash,
                nonce, emitted_at, ttl_seconds, concentration, status,
                claimed_by, claimed_at, lease_expires_at,
                fulfilled_at, fulfilled_outcome, fulfilled_link
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                morphogen.id,
                morphogen.source_cell,
                morphogen.kind,
                json.dumps(morphogen.payload),
                morphogen.payload_hash,
                morphogen.tags_hash,
                morphogen.nonce,
                morphogen.emitted_at,
                morphogen.ttl_seconds,
                morphogen.concentration,
                morphogen.status,
                morphogen.claimed_by,
                morphogen.claimed_at,
                morphogen.lease_expires_at,
                morphogen.fulfilled_at,
                morphogen.fulfilled_outcome,
                morphogen.fulfilled_link,
            ),
        )
        if morphogen.tags:
            self.connection.executemany(
                "INSERT INTO morphogen_tags (morphogen_id, tag) VALUES (?, ?)",
                [(morphogen.id, tag) for tag in morphogen.tags],
            )
        self.connection.commit()

    def update_morphogen(self, morphogen: Morphogen) -> None:
        # Tags are immutable post-emit (the FieldService never mutates the
        # tag list); only the lifecycle/state columns change. Updating just
        # those keeps the write small and avoids needing to diff tag rows.
        cursor = self.connection.execute(
            """
            UPDATE morphogens
               SET concentration     = ?,
                   status            = ?,
                   claimed_by        = ?,
                   claimed_at        = ?,
                   lease_expires_at  = ?,
                   fulfilled_at      = ?,
                   fulfilled_outcome = ?,
                   fulfilled_link    = ?
             WHERE id = ?
            """,
            (
                morphogen.concentration,
                morphogen.status,
                morphogen.claimed_by,
                morphogen.claimed_at,
                morphogen.lease_expires_at,
                morphogen.fulfilled_at,
                morphogen.fulfilled_outcome,
                morphogen.fulfilled_link,
                morphogen.id,
            ),
        )
        if cursor.rowcount == 0:
            raise LookupError(f"unknown morphogen: {morphogen.id}")
        self.connection.commit()

    def get_morphogen(self, morphogen_id: str) -> Morphogen | None:
        row = self.connection.execute(
            "SELECT * FROM morphogens WHERE id = ?",
            (morphogen_id,),
        ).fetchone()
        if row is None:
            return None
        return self._morphogen_from_row(row, self._tags_for(morphogen_id))

    def find_active_dedup(
        self, payload_hash: str, tags_hash: str, kind: str
    ) -> Morphogen | None:
        row = self.connection.execute(
            """
            SELECT * FROM morphogens
             WHERE payload_hash = ?
               AND tags_hash    = ?
               AND kind         = ?
               AND status       = 'emitted'
               AND nonce IS NULL
             LIMIT 1
            """,
            (payload_hash, tags_hash, kind),
        ).fetchone()
        if row is None:
            return None
        return self._morphogen_from_row(row, self._tags_for(row["id"]))

    def list_morphogens(self) -> list[Morphogen]:
        rows = self.connection.execute(
            "SELECT * FROM morphogens ORDER BY emitted_at, id"
        ).fetchall()
        if not rows:
            return []
        tag_index = self._all_tags_indexed()
        return [
            self._morphogen_from_row(row, tag_index.get(row["id"], [])) for row in rows
        ]

    # --- responses --------------------------------------------------------

    def add_response(self, response: MorphogenResponse) -> None:
        self.connection.execute(
            """
            INSERT INTO morphogen_responses (
                id, morphogen_id, responder_cell, response_payload,
                link, responded_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                response.id,
                response.morphogen_id,
                response.responder_cell,
                json.dumps(response.response_payload),
                response.link,
                response.responded_at,
            ),
        )
        self.connection.commit()

    def list_responses(self, morphogen_id: str) -> list[MorphogenResponse]:
        rows = self.connection.execute(
            """
            SELECT * FROM morphogen_responses
             WHERE morphogen_id = ?
             ORDER BY responded_at, id
            """,
            (morphogen_id,),
        ).fetchall()
        return [self._response_from_row(row) for row in rows]

    # --- counts -----------------------------------------------------------

    def count_by_status(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS n FROM morphogens GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    def count_responses(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS n FROM morphogen_responses"
        ).fetchone()
        return int(row["n"])

    # --- helpers ----------------------------------------------------------

    def _tags_for(self, morphogen_id: str) -> list[str]:
        rows = self.connection.execute(
            "SELECT tag FROM morphogen_tags WHERE morphogen_id = ? ORDER BY tag",
            (morphogen_id,),
        ).fetchall()
        return [row["tag"] for row in rows]

    def _all_tags_indexed(self) -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        for row in self.connection.execute(
            "SELECT morphogen_id, tag FROM morphogen_tags ORDER BY morphogen_id, tag"
        ):
            index.setdefault(row["morphogen_id"], []).append(row["tag"])
        return index

    @staticmethod
    def _morphogen_from_row(row: sqlite3.Row, tags: list[str]) -> Morphogen:
        payload: dict[str, Any] = json.loads(row["payload"])
        return Morphogen(
            id=row["id"],
            source_cell=row["source_cell"],
            kind=row["kind"],
            payload=payload,
            payload_hash=row["payload_hash"],
            tags=tags,
            tags_hash=row["tags_hash"],
            nonce=row["nonce"],
            emitted_at=row["emitted_at"],
            ttl_seconds=row["ttl_seconds"],
            concentration=row["concentration"],
            status=row["status"],
            claimed_by=row["claimed_by"],
            claimed_at=row["claimed_at"],
            lease_expires_at=row["lease_expires_at"],
            fulfilled_at=row["fulfilled_at"],
            fulfilled_outcome=row["fulfilled_outcome"],
            fulfilled_link=row["fulfilled_link"],
        )

    @staticmethod
    def _response_from_row(row: sqlite3.Row) -> MorphogenResponse:
        return MorphogenResponse(
            id=row["id"],
            morphogen_id=row["morphogen_id"],
            responder_cell=row["responder_cell"],
            response_payload=json.loads(row["response_payload"]),
            link=row["link"],
            responded_at=row["responded_at"],
        )
