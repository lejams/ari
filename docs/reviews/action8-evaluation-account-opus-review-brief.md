# Brief de revue Opus : compte d'évaluation (action 8, point 1)

À coller dans une session qui tourne sur **Opus 5.5**, sur la branche de la PR
(`claude/project-thread-f6hwc2`). L'auteur est une autre session et demande un examen
contradictoire, pas une approbation. Revue obligatoire avant fusion : c'est une migration en
production (décision du propriétaire du 24 octobre 2026, « ok pour A »). La boucle de revue Codex
s'y ajoute, elle ne la remplace pas.

---

You are the mandatory independent reviewer (Opus) of a production migration in ARI: the
**evaluation account** that the staging target of `voice-sim@1` (action 8) needs. The author is a
different session and wants adversarial scrutiny, not approval. READ-ONLY: do not modify, create,
stage or commit any file in the repository (use the scratchpad for experiments, on a COPY). You may
run read-only commands, python snippets (`PYTHONPATH=backend/src`) and create throwaway databases
on the local PostgreSQL test server (`postgres:postgres@localhost:5432`, see `CLAUDE.md`,
"Pièges connus").

## The owner's decision (do not reopen it, check the code matches it)
- A dedicated evaluation account, **marked by an administrator**.
- **Exempt from the exam quota.** The quota stays unchanged for every other account; it is never
  disabled for learners.
- Its sessions are **marked as evaluation** and **excluded from the learner metrics, the weekly
  programme and the Costs page**.

## What to read
1. The spec: `ARI_plan_claude_code.md`, sections 2 (invariants, especially 9 and 11), 3, 6, and the
   `voice-sim@1` paragraph of "### Action 8". `docs/reviews/action8-note-de-fin.md`, point 1.
2. The migration: `backend/migrations/platform/versions/0005_evaluation_accounts.py`.
3. The code:
   - `domain/models.py` (`LearnerProfile.evaluation_since`, `evaluation_account`;
     `ConversationSession.evaluation_account`);
   - `infrastructure/persistence/platform/repository.py` (`LearnerRow`, `SessionRow`,
     `get_learner`, `update_learner`, `_insert_session`, `get_session`);
   - `infrastructure/persistence/platform/evaluation_accounts.py` (mark, unmark, list);
   - `backoffice_api/cli.py` (`evaluation-account mark|unmark|list`) and `backoffice_api/container.py`;
   - `application/services/usage_guard.py`, `application/ports/usage.py`,
     `infrastructure/persistence/platform/usage.py`;
   - `application/services/conversation.py` (`create_session`, lexicon ingest in `end_session`);
   - the exclusions: `api/program.py`, `application/services/learner_model.py`, `api/practice.py`
     (`/api/progression`), `api/app.py` (`/api/cases/summary`);
   - `infrastructure/persistence/costs.py` and `backoffice-web/app.mjs` (one line on the Costs page);
   - `api/voice_session_dto.py` (`evaluation_account` added to the public session).
4. The tests: `backend/tests/test_evaluation_accounts.py`, and `test_usage_limits.py`,
   `test_execution_costs.py`, `test_program.py`, `test_progress_axes.py` for the unchanged behaviour.
5. Docs: `docs/EVALS.md` section 4, `docs/MIGRATIONS.md`, `docs/DEPLOYMENT.md` (staging).

## Run these first
- `make lint` with the locked versions (`pip install -r backend/requirements.lock`).
- `make test-backend` from the repository root.
- On a fresh database: `alembic -c alembic.ini -n platform upgrade head`, `check`, then
  `downgrade 0004_evaluation_runs` and `upgrade head` with learners and sessions in place.

## What the author specifically wants challenged (priority order)

1. **Can a learner obtain the exemption?** The mark must only be writable by an administrator.
   - Is there any path where a learner request writes `learners.evaluation_since` (profile update,
     onboarding, alpha profile adoption in `api/auth.py`, `update_learner`, `create_learner`,
     `replace(...)` of a `LearnerProfile` read then written back)?
   - Can a learner create a session marked `evaluation_account` (request body, idempotent replay of
     `request_id`, `find_session_request`)? The field is taken from the learner row at creation.
   - Is the CLI the right administrator boundary (it runs inside the server's container)? No web
     route was added on purpose.
2. **Quota correctness for everyone else.**
   - `UsageGuard.check_session_start` skips the quota when `is_evaluation_account(learner_id)`. It
     is called at session creation (`POST /api/sessions`) and again when the call opens
     (`voice_socket.py`). Any path that starts an exam without it?
   - `exam_sessions_started` now ignores `sessions.evaluation_account = true`. Can this undercount a
     real learner's exams (default value, NULLs, rows created by an image that predates the column
     during the deploy window)?
   - Unmarking: the quota applies again from that moment, past evaluation sessions do not count.
     Is that the right reading of the decision?
   - The optional abuse breaker deliberately still applies to an evaluation account. Agree?
3. **The migration.** Two `ADD COLUMN`s, one nullable, one `NOT NULL DEFAULT false`. Locking and
   duration on PostgreSQL 16 (metadata-only default?), behaviour of the old image during a deploy
   (inserts without the column), downgrade data loss, `alembic check` drift (`server_default` on the
   model).
4. **Exclusions: complete and not over-broad.**
   - Programme (`completed_since_week_start`), learner model, progression axes and groups, worked
   cases summary, lexicon ingest. Anything that still reads evaluation sessions as learning (for
   example `ari.learning`, next actions, placement, history counts, exam report)?
   - The history keeps evaluation sessions on purpose. Agree?
   - Costs page: an execution is excluded when its session is marked, or when its learner is
     **currently** marked (placement calls have no session). Check the correlated `EXISTS`
     subqueries (aliases, correlation with the `recent_sessions` join), that no group, median or
     day total still includes them, and that the excluded total is reported apart rather than
     hidden (invariant 9 spirit: never silently shrink the bill).
5. **Scope.** Nothing beyond the decision: no change to the quota value, no euro ceiling, no new
   behaviour for normal learners, no prompt or model change.

## Expected output
A verdict, "approuvé", "approuvé avec réserves" or "bloquant", then numbered findings, each with
file and line, a reproducible scenario (inputs, state, wrong result) and a severity. Separate real
defects from suggestions. Write in French.
