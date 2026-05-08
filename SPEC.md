# morphogen — design spec

> **Status**: pre-implementation. Living spec for the morphogen cell — implement against this. Updates as design refinements emerge.

## Purpose

Morphogen is the colony's broadcast field for cross-cell coordination. Cells emit signals into the field; other cells read the field and decide whether to act. Morphogen mediates two related but distinct flows:

1. **Cross-cell feature requests** — cell A emits "I need X" tagged with the relevant capability; cells declaring that capability read the field and one (or many, depending on flow) responds.
2. **New-cell induction** — the colony emits "needs a cell that does Y"; an operator or agent reads it and spawns a new cell from `stem-cell` with that purpose. (Lifecycle TBD — see Open Questions.)

Morphogen is *broadcast / diffusive*. Senders do not address specific cells. Receivers filter by their own declared capabilities. This loose coupling means cell A doesn't need to know cell B exists; the system handles cells appearing, disappearing, and changing.

## Naming

In developmental biology, morphogens are diffusible signaling molecules that form ambient gradients across tissue. Cells *read* the gradient and decide what to differentiate into based on local concentration. Loosely coupled, slow, ambient, integrative — identity-shaping rather than command-passing. The metaphor is biologically exact: emit into the field, others integrate, the colony shapes itself.

## Position in the colony

Morphogen depends conceptually on `atlas` (it consumes the capability vocabulary atlas owns) but is **not** wired to atlas at runtime. Capability matching happens at read time against morphogen's own storage, using the reader cell's own declared tags as the filter. Morphogen still works if atlas is down — just without the operator-facing discovery niceties atlas provides separately.

It is itself a cell — one repo, one purpose, one queue, scaffolded from `stem-cell`.

## Two flows, one envelope

| | `request` (singleton) | `signal` (open) |
|---|---|---|
| Claim semantics | First claim wins, lease-based | No claim; many cells can respond |
| Decay | TTL from emission, *plus* lease expiry on unrenewed claims | TTL from emission only |
| Lifecycle states | emitted → claimed → fulfilled (or back to emitted on lease expiry / decayed) | emitted → decayed (responses accumulate as side-effect) |
| Use case | "Implement feature X", "Run analysis Y" | "Review this design", "Weigh in on this question" |

Both share an envelope: source cell, capability tags, payload, TTL, ID, concentration counter. The lifecycle divergence is significant enough that consumers branch on `kind` anyway, so emit verbs are split (`emit_request` vs `emit_signal`) rather than gated by a discriminator on a unified `emit`.

## Design decisions

1. **Broadcast, not addressed** — senders don't name receivers. Capability tags are the addressing primitive. (Rationale: blackboard architecture pattern, validated by 13–57% gains in Hu et al. 2025; loose coupling, robust to cell churn.)
2. **Push emission, pull reading** — cells call `emit_*` to push; cells call `read_field` on their own cadence to pull. Matches MCP's natural pattern. Pull cadence is fine at agent-cell timescales (minutes-to-hours).
3. **Two emit verbs, shared envelope** — `emit_request` (singleton) and `emit_signal` (open) at the API layer; one underlying schema and storage.
4. **Lease pattern for singleton claims** — `claim_request` / `renew_claim` / `release_claim` / `fulfill_request`. Lifted from MCP Agent Mail. TTL-driven implicit release if the claimer goes dark; the claimer can extend by renewing.
5. **Concentration counter for stigmergic prioritization** — re-emissions of the same `(canonical_payload, sorted_tags, kind)` increment a counter on the canonical morphogen. Multiple cells asking for the same thing creates a stronger signal without LLM round-trips. Quantitative stigmergy (Welty's term). Source cell is *excluded* from the dedup key — different cells reinforce the same signal. See "Concentration semantics" below for the exact recipe.

   Cells can opt out of dedup per-emission via an optional `nonce` field, when they explicitly want a fresh row even on identical payload (typical for `kind=request` where two cells legitimately want independent claims on look-alike work).
6. **Implicit prefix matching at read** — a morphogen tag matches a reader tag if either is a prefix of the other in the dot hierarchy. Matches atlas's matching semantics.
7. **Mutate-in-place storage with status field** (not full event sourcing) — simpler to build; SQLite is fast enough at colony scale (~10k morphogens). Add an event log later if observability demands it.
8. **TTL-driven decay** — `sweep_decayed` is cron-able. Reads can also filter expired records out at query time, so the field stays honest even if the sweep is slow.

## MCP tool surface (~13 tools)

```
Infrastructure:
  health -> {status, field_size, decay_rate, ...}
  ensure_field

Emission:
  emit_request(source_cell, tags, payload, ttl_seconds?, nonce?) -> morphogen_id
    # singleton: claim-then-decay, lease lifecycle
    # nonce bypasses dedup when caller wants a guaranteed-fresh row
  emit_signal(source_cell, tags, payload, ttl_seconds?, nonce?) -> morphogen_id
    # open: timer-decay, fan-out
    # nonce bypasses dedup when caller wants a guaranteed-fresh row

Reading:
  read_field(reader_cell, reader_tags=[...], kinds?, since?, status?) -> [morphogen, ...]
    # reader_tags is the cell's own declared capabilities
    # implicit prefix matching (both directions in the dot tree)
    # default status filter = "active" (emitted or claimed-still-leased)
  get_morphogen(id) -> morphogen

Singleton claim lifecycle (kind=request only):
  claim_request(morphogen_id, claimer_cell, lease_ttl?) -> {claimed, lease_expires_at}
  renew_claim(morphogen_id, claimer_cell, lease_ttl?) -> {lease_expires_at}
  release_claim(morphogen_id, claimer_cell, reason?)
  fulfill_request(morphogen_id, claimer_cell, outcome, link?)

Open signal response (kind=signal only):
  respond_signal(morphogen_id, responder_cell, response_payload, link?)

Field admin:
  sweep_decayed()
    # marks expired emissions + reaps unrenewed claims; cron-able
  list_decayed(since?, kinds?)
```

## SQLite schema

```sql
CREATE TABLE morphogens (
  id              TEXT PRIMARY KEY,
  source_cell     TEXT NOT NULL,
  kind            TEXT NOT NULL,           -- request | signal
  payload         JSON NOT NULL,
  payload_hash    TEXT NOT NULL,           -- sha256 of JCS-canonicalized payload
  tags_hash       TEXT NOT NULL,           -- sha256 of sorted/lowercased tags joined by \x1f
  nonce           TEXT,                    -- optional; when set, row bypasses dedup
  emitted_at      TIMESTAMP NOT NULL,
  ttl_seconds     INTEGER NOT NULL,
  concentration   INTEGER NOT NULL DEFAULT 1,
  status          TEXT NOT NULL DEFAULT 'emitted',
                  -- emitted | claimed | fulfilled | decayed
  -- singleton claim fields (NULL when kind='signal')
  claimed_by         TEXT,
  claimed_at         TIMESTAMP,
  lease_expires_at   TIMESTAMP,
  fulfilled_at       TIMESTAMP,
  fulfilled_outcome  TEXT,
  fulfilled_link     TEXT
);

CREATE TABLE morphogen_tags (
  morphogen_id  TEXT NOT NULL REFERENCES morphogens(id),
  tag           TEXT NOT NULL,
  PRIMARY KEY (morphogen_id, tag)
);

CREATE TABLE morphogen_responses (    -- only for kind='signal'
  id              TEXT PRIMARY KEY,
  morphogen_id    TEXT NOT NULL REFERENCES morphogens(id),
  responder_cell  TEXT NOT NULL,
  response_payload JSON NOT NULL,
  link            TEXT,
  responded_at    TIMESTAMP NOT NULL
);

CREATE INDEX idx_morphogen_tags_tag      ON morphogen_tags(tag);
CREATE INDEX idx_morphogens_status       ON morphogens(status);
CREATE INDEX idx_morphogens_emitted      ON morphogens(emitted_at);
CREATE INDEX idx_morphogens_lease        ON morphogens(lease_expires_at)
                                         WHERE status='claimed';
CREATE UNIQUE INDEX idx_morphogens_dedup ON morphogens(payload_hash, tags_hash, kind)
                                         WHERE status='emitted' AND nonce IS NULL;
```

The partial `idx_morphogens_dedup` makes concentration cheap: on any `emit_*`, look for an active morphogen with the same `(payload_hash, kind)`; if found, increment `concentration`; else insert.

## Read query (prefix matching, conceptual)

```sql
SELECT m.* FROM morphogens m
JOIN morphogen_tags mt ON mt.morphogen_id = m.id
WHERE m.status IN ('emitted', 'claimed')
  AND m.emitted_at + (m.ttl_seconds * INTERVAL '1 second') > NOW()
  AND EXISTS (
    SELECT 1 FROM unnest(:reader_tags) rt
    WHERE mt.tag = rt
       OR mt.tag LIKE (rt || '.%')   -- emit broader, reader specific
       OR rt LIKE (mt.tag || '.%')   -- emit specific, reader broader
  );
```

Both prefix directions match: a morphogen emitted with `analysis` reaches a cell declaring `analysis.static.python`, and vice versa.

## Concentration semantics

The dedup key for `concentration` is:

```
dedup_key = sha256(
  jcs(payload)         || "\x1f" ||      # RFC 8785 canonicalization
  sorted_tags_joined   || "\x1f" ||      # tags lowercased+stripped, sorted, joined by \x1f
  kind                                    # 'request' or 'signal'
)
```

Stored as `(payload_hash, tags_hash, kind)` on the morphogen for index efficiency. Source cell is **excluded** — emissions from different cells reinforce the same signal. Different `kind` (request vs signal) never merges (different "pheromone species" per stigmergy literature).

**Canonicalization rules** (server-side, applied by morphogen at emit time):

- **Payload**: [RFC 8785 JCS](https://www.rfc-editor.org/rfc/rfc8785), not ad-hoc `json.dumps(sort_keys=True)`. Standard sort-keys breaks on Unicode (UTF-16 vs UTF-8 sort) and floats (`1.0` vs `1` vs `1e0`). Use `json-canon` or equivalent.
- **Tags**: lowercase, strip whitespace, sort, deduplicate, join with `\x1f` (information separator).
- **Kind**: lowercase string literal `request` or `signal`.

**Schema-driven normalization** (client-side, cells responsible): atlas can attach an optional JSON schema to a registered tag (`tags.payload_schema`). Cells should fetch the schema (`atlas.get_tag_schema`) and normalize their payload (strip unknown fields, fill defaults) **before** calling `emit_*`. Without this, trivial payload variations create separate morphogens that semantically should have concentrated. The bus boundary doesn't enforce schemas — discipline + tooling does.

**`nonce` opt-out**: when a cell explicitly wants a fresh row regardless of dedup (e.g., two parallel runs of the same singleton work), include a `nonce` (any unique string — uuid is fine) in the emit call. The partial unique index excludes nonce'd rows, so the insert always succeeds.

**Failure modes to know:**

- *Loose-dedup* (the realistic one at small scale): payloads vary trivially → semantically-same requests don't concentrate. Mitigation: tag-scoped payload schemas (above).
- *Aggressive-dedup* (rare but real for `kind=request`): two cells legitimately want independent claims on identical-looking work, get merged. Mitigation: `nonce`.

## Lifecycle state diagrams

### `request` (singleton):
```
emitted ──claim_request──> claimed ──fulfill_request──> fulfilled
   │                          │
   │                          ├── renew_claim ──> claimed (lease extended)
   │                          ├── release_claim ──> emitted (back in field)
   │                          └── lease_expires_at < now ──> emitted (sweep)
   │
   └── emitted_at + ttl < now ──> decayed (sweep)
```

### `signal` (open):
```
emitted ──respond_signal──> emitted (responses accumulate; status unchanged)
   │
   └── emitted_at + ttl < now ──> decayed (sweep; responses retained)
```

## New-cell induction

When the colony needs a capability no current cell provides, induction is the protocol that gets a new cell spawned. This is the second of morphogen's two main use cases.

**Flow shape**: single-phase `signal` (not `request`). Multiple cells/agents/operators can weigh in on whether the proposed cell should exist before anyone spawns it — consensus before action. (Two-phase signal-then-request is the right pattern at scale, but unnecessary ceremony at single-operator scale; promotion later is additive.)

**TTL**: ~24h is a reasonable default. Long enough for review to happen on human timescales; short enough that "no one acts" surfaces as a decay event the operator can investigate.

**Conventional response payload `type` field** — `respond_signal` accepts free-form JSON, but induction signals follow conventions:

| `type` value     | Meaning                                                           |
|------------------|-------------------------------------------------------------------|
| `endorse`        | "Yes, this cell should exist"                                     |
| `dedupe:<cell>`  | "We already have a cell covering this — see `<cell>`"             |
| `refine:<...>`   | "The proposed purpose is ambiguous — refine as `<...>`"           |
| `reject`         | "This shouldn't be a cell" (with rationale in `body`)             |
| `claim-spawn`    | "I'm going to spawn this cell" — soft lock                        |
| `spawned:<cell>` | "Cell has been spawned; `<cell>` is its name; repo at `<url>`"    |

**Spawn flow:**

1. Some cell or operator emits `emit_signal(tags=["needs-cell"], payload={purpose: "...", suggested_capabilities: [...]})`
2. Other actors `respond_signal` with `endorse`/`dedupe`/`refine`/`reject` over the TTL window
3. When consensus is reached (operator judgement at small scale), a spawner `respond_signal` with `claim-spawn`
4. Spawner runs `copier copy gh:JSBaxter/stem-cell ...` to create the new cell
5. The new cell, on first boot, calls `atlas.register_cell(name, purpose, repo_url, induced_by=<morphogen_id>)`
6. Atlas enforces "at most one cell with this `induced_by`" — duplicate-spawn races are rejected at this point
7. The spawner posts `respond_signal` with `spawned:<cell_name>` for visibility
8. The originating signal decays naturally on TTL — no explicit fulfillment needed

**Failure modes:**

- *No one acts* (signal decays with no `claim-spawn`): expected behavior — the colony decided it didn't need this. Decay is the answer. If the original emitter wants escalation, they can re-emit with `tags=["needs-cell", "operator-attention"]` to surface it.
- *Race* (two actors try to spawn at once): atlas's `induced_by` uniqueness constraint resolves it at register-cell time — first registration wins, second gets a clean error.
- *Underspecified purpose*: `refine:` responses force iteration; in practice the spawner waits for refines to settle (operator judgement) before claiming.

## Open questions

1. **Response shape for `signal`.**
   `respond_signal` payloads are currently free-form JSON. Should there be structure (e.g., `{stance: "agree"|"disagree"|"defer", body: "...", links: [...]}`)?
   - Probably yes for some response types; remain free-form for others. Could enforce a schema per signal-type, declared at emit time.

2. **Failure modes for unanswered requests.**
   Decay is the silent default. Should there be an escalation path — e.g., a request decaying triggers an `emit_signal` to the colony? Useful for observability but adds complexity. Skip at first; add if the silent-decay pattern actually hides problems.

3. **Should `respond_signal` itself emit a morphogen?**
   If responses are themselves capability-tagged (e.g., "review by static-analysis cell"), they could fuel further coordination. Probably overkill; revisit if the use case shows up.

4. **Per-cell rate limiting.**
   A buggy cell emitting 1000 morphogens/minute would flood the field. Worth a soft cap with a clear error. Default: ~100/hour per cell, override via operator config.
