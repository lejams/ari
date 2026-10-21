# Brief de revue Opus : action 5, évaluations immuables

À coller dans une session qui tourne sur **Opus 5.5**, sur la branche de l'action 5
(`claude/evaluation-runs-51a8b7`, à renommer si le propriétaire le souhaite). L'auteur est une autre
session et demande un examen contradictoire, pas une approbation. Revue obligatoire avant fusion
(voir `CLAUDE.md`, section « Revue Opus obligatoire par PR »).

---

You are the mandatory independent reviewer (Opus) of ACTION 5 of the ARI execution plan:
"Évaluations immuables" (immutable evaluation runs). The author is a different session and wants
adversarial scrutiny, not approval. READ-ONLY: do not modify, create, stage or commit any file in
the repository (use the scratchpad for experiments and mutations, on a COPY). You may run read-only
commands, python snippets (`PYTHONPATH=backend/src`) and create throwaway databases on the local
PostgreSQL test server (`postgres:postgres@localhost:5432`, see `CLAUDE.md`, "Pièges connus").

## What to read
1. The spec: `ARI_plan_claude_code.md`, sections 1, 2 (invariants, especially 3, 4, 7, 9, 10),
   3 (constants), 6 (owner-reserved points) and "### Action 5 : évaluations immuables" (in French).
   The change must implement that action exactly and must not exceed it. In particular it must NOT
   touch the vocabulary observations (still deleted then re-inserted: that is action 7).
2. The migration: `backend/migrations/platform/versions/0004_evaluation_runs.py` (table, triggers,
   backfill SQL `BACKFILL_RUNS` and `BACKFILL_POINTERS`, NOT NULL pointer, composite foreign key,
   downgrade).
3. The code: `backend/src/ari/infrastructure/persistence/platform/repository.py`
   (`EvaluationRunRow`, `EvaluationRow`, `ExecutionRow.model_returned`, `get_session`,
   `save_analysis`), `application/ports/repository.py`, `application/services/conversation.py`
   (`end_session`), `domain/models.py` (`ExecutionRecord.model_returned`),
   `infrastructure/providers/openai/llm.py` and `fake.py` (`model_returned`),
   `infrastructure/persistence/ddl.py` (unchanged, reused).
4. The tests: `backend/tests/test_evaluation_runs.py`, and the existing re-analysis tests
   (`test_vertical_slice.py`, `test_lexicon.py`, `test_structured_guards.py`, `test_api.py`).
5. Docs: `docs/MIGRATIONS.md` (paragraph on 0004), the end note
   `docs/reviews/action5-note-de-fin.md`.

## Run these first
- `make lint` (ruff, mypy strict, import-linter). Use the locked versions
  (`pip install -r backend/requirements.lock`): with SQLAlchemy 2.1, mypy reports 13 unrelated
  errors in untouched files.
- `make test-backend` from the repository root (needs PostgreSQL).
- `.venv/bin/alembic -c alembic.ini -n platform check` and `-n content check` on freshly migrated
  databases; also `downgrade 0003_execution_costs` then `upgrade head` with data in place.

## What the author specifically wants challenged (priority order)

1. **The migration on an append-only table, on a database that already holds evidence.**
   - Can the upgrade fail on real production data? Look at `(payload ->> 'created_at')::timestamptz`
     (malformed or naive strings in old payloads, v1 to v4 schemas), missing `schema_version`,
     `prompt_version` or `rubric_version` keys (COALESCE to `'unknown'`), evaluations whose session
     row is missing (inner JOIN on `sessions`: would such a row block `SET NOT NULL`?).
   - Is the backfill truly idempotent and replayable (`NOT EXISTS`, `current_run_id IS NULL`),
     including after a partial failure? The run id is deterministic (`backfill:<session_id>`): can
     it collide with an id produced by `new_id()` (UUID4)?
   - Execution linking: a run is linked to its execution only when the session has exactly one
     succeeded `session_evaluation` call with the same prompt version. Is this rule sound (can the
     unique succeeded call NOT be the one that produced the stored payload? e.g. a call that
     succeeded, then `_build_metrics` or `save_analysis` failed, then an evaluation from another
     path)? Is leaving the others NULL the right reading of invariant 9 ("inconnu visible, jamais
     zéro")?
   - Locking and duration: `ADD COLUMN`, `SET NOT NULL` and `ADD CONSTRAINT ... FOREIGN KEY` on
     `evaluations` take strong locks and scan the table; acceptable for the expected size (a few
     thousand sessions)?
   - Downgrade drops every run except the current one (kept in `evaluations.payload`). It is
     documented as lossy. Is that acceptable, and is a rollback of the APPLICATION alone (old code on
     the new schema) broken? (Old code does `merge(EvaluationRow(session_id, payload))`: an INSERT
     for a new session now violates `current_run_id NOT NULL`.) Should this be stated in the
     deployment runbook (`docs/DEPLOYMENT.md`)?

2. **The triggers.** `ddl.create_append_only_triggers(op, ("evaluation_runs",), ...)` refuses
   row-level UPDATE and DELETE with SQLSTATE class 23. Check what they do NOT cover: `TRUNCATE`
   (no statement trigger, like every other append-only table of the repository), `ALTER TABLE ...
   DISABLE TRIGGER` by the owner role, `DELETE` on `sessions` (blocked by the foreign key, not by a
   trigger), `ON DELETE` behaviour. The `evaluations` pointer itself has no trigger: it can be moved
   backwards or deleted. Is a "pointer only moves to a higher `run_no`" guard needed now, or is it
   speculative (plan rule: no abstraction without immediate use)? Is `session_metrics` (still
   merged, derived from the payload's criteria) acceptable as a non-evidence table?

3. **Re-analyses.** `save_analysis` locks the session row (`SELECT ... FOR UPDATE`), computes
   `max(run_no) + 1`, inserts the run, flushes, then inserts or updates the pointer and its payload
   copy, in one transaction. Look for: races across processes (the API's `analysis_locks` are per
   process); the read path (`get_session` now reads the payload of the CURRENT RUN, not
   `evaluations.payload`: can the two differ?); `end_session` returning early on `COMPLETED`, so a
   re-analysis only happens after `ANALYSIS_FAILED` or `ANALYSIS_PENDING` (for example when the
   lexicon ingestion fails AFTER the run was saved: the run stays, the session is marked failed,
   the retry adds run 2; is that right?); the lexicon re-ingestion on the retry; deadlocks between
   the session row lock and the other writers of `sessions` (`set_status`, `mark_call_ended`).
   API `/api/sessions/{id}` must be unchanged: check `api/voice_session_dto.py`.

4. **Behaviour on technical errors (invariant 4).** A timeout, a provider refusal or a structured
   output guard rejection must never produce a run, never move the pointer and never be readable
   as an evaluation of the learner. Verify every path of `end_session`: `ProviderError` from the
   evaluator, guard rejections (`structured_output_guard`), an exception after `record_execution`
   but before `save_analysis`, an exception inside `save_analysis` (the transaction must roll back
   the run AND the pointer), an exception after it. `save_analysis` refuses an execution that is
   failed, belongs to another session, disagrees on the prompt version, or was never recorded
   (foreign key). Are these guards complete? Should it also check `operation ==
   "session_evaluation"`? Should a failed analysis be recorded as a run with a status instead (the
   author chose NOT to: failures live in `executions` only, so that action 7 can never turn them
   into observations)?

5. **Traceability (`model_returned`).** `ExecutionRecord.model_returned` is new, filled from
   `response.model` by the OpenAI LLM provider, equal to `model` in the fake, NULL elsewhere (STT,
   TTS, realtime). A nullable column `executions.model_returned` was added so the record round-trips.
   Is this the minimal change, and does it collide with action 8 (`ari.evals`, developed in
   parallel, which also needs the returned model in its reports)?

## Then look for what the author did NOT think of
Examples (not exhaustive): JSONB equality between the pointer copy and the run; timezone handling
of `created_at`; the composite foreign key `(session_id, current_run_id)` versus the separate
`session_id` foreign key; `alembic check` stability (constraint names: `uq_evaluation_runs_*`,
`fk_evaluations_current_run`, `ck_evaluation_run_no_positive`); whether the tests would fail for
the behaviours they claim (the author ran six mutations: no lock, no `NOT EXISTS`, no "exactly one
call" rule, no trigger, no status guard, no prompt match; all detected); test isolation (the
backfill tests downgrade and upgrade a per-test database cloned from the template); the content
chain (untouched, `alembic -n content check` must stay clean); anything that violates invariants
3, 4, 7, 9, 10 or section 6 of the plan.

## Output
Write the report in FRENCH:
1. Verdict: APPROUVÉ / APPROUVÉ AVEC RÉSERVES / BLOQUANT, one line with the reason.
2. Findings, each with severity (BLOQUANT / MAJEUR / MINEUR / REMARQUE), file:line, what is wrong,
   a minimal reproduction (snippet or SQL and its output), and a concrete suggested fix. Order by
   severity. Only report what you verified; flag suspicions explicitly.
3. Where the code deviates from the plan text, and whether each deviation is acceptable.
4. What you checked and found correct (brief).
Be precise and economical; no praise padding.

Afterwards, record in the end note of the action (`docs/reviews/action5-note-de-fin.md`): who
reviewed, which PR, what was corrected.
