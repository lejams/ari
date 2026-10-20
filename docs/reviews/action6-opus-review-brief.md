# Brief de revue Opus : action 6, noyau `ari.learning`

À coller dans une session qui tourne sur **Opus 5.5**, dépôt `/home/smail/projects/ari`, branche
`feature/learning-core`. L'auteur est un autre modèle (Sonnet 5.5) et demande un examen contradictoire,
pas une approbation. Ce document sert à la revue obligatoire des actions 5, 6, 7, 11 et 12 (voir
`CLAUDE.md`).

---

You are the mandatory independent reviewer (Opus) of ACTION 6 of the ARI execution plan: the new
package `ari.learning`. The author is a different model and wants adversarial scrutiny, not approval.
READ-ONLY: do not modify, create, stage or commit any file in the repository (use the scratchpad for
experiments). You may run read-only commands and python snippets (`PYTHONPATH=backend/src`).

Repository: branch `feature/learning-core`. The work is committed (`c682fb9`, with `main` merged in).

## What to read
1. The spec: `ARI_plan_claude_code.md`, sections 1, 2 (invariants), 3 (constants) and
   "### Action 6 : noyau `ari.learning`" (in French). The code must implement that action exactly and
   must not exceed it (no policy, no storage, no FSP concept).
2. The code: `backend/src/ari/learning/{__init__,model,mastery,framework,replay,beta,clock}.py`.
3. The tests: `backend/tests/learning/` (test_learning_beta/framework/mastery/replay/purity and
   toy_domain.py).
4. `docs/LEARNING_CORE.md` (check every numeric claim by computing it) and the import-linter contract
   "The learning core imports nothing from ARI and no third-party library" in `backend/pyproject.toml`.
5. For context only: `backend/src/ari/application/services/learner_model.py`, `program.py`,
   `domain/srs.py` (the existing code the core will eventually serve).

## Run these first
- `.venv/bin/pytest backend/tests/learning -p no:cacheprovider -o addopts="" -q` (no database needed)
- `.venv/bin/ruff check backend`, `.venv/bin/mypy backend/src`,
  `PYTHONPATH=backend/src .venv/bin/lint-imports --config backend/pyproject.toml`

## What the author specifically wants challenged
1. `recheck` semantics: mastery is judged on the evidence as it stood at the LAST observation
   (`lower_at_last`, `autonomous_at_last`, counts of contexts and attempts), and `recheck` appears when
   `now - last > half_life_days`, while the returned mean/lower/upper are the faded ones. The plan says
   "`recheck` si `mastered` mais `now - last_observed_at > H`". Is this a faithful and sound reading?
   Can a learner see a "mastered" status next to numbers that contradict it? Any case where the status
   flips in a surprising way over time?
2. The replacement key was changed to (attempt_ref, producer, skill_id, item_ref, evidence_key); the
   plan says same attempt_ref + same producer. Is including item_ref right? Think about what Action 7's
   adapter will do (observations from `weighted_assessment` items, language errors by category, empathy,
   lexicon reviews, placement) and whether the key can wrongly collapse or wrongly fail to collapse real
   evidence. Replacement happens AFTER the `observed_at <= now` filter: right for as-of replays?
3. The per-attempt weight cap and the repeated-item factor depend on chronological processing order
   (observed_at, then id). Look for order dependence, ties on identical timestamps, interaction with
   replacement, and whether any input ordering can change the result (the author claims order
   independence of the input list, not of timestamps).
4. Beta numerics in `beta.py` (Lentz continued fraction + bisection): correctness for small shape
   parameters (priors below 1 are allowed), very large counts, x near 0 or 1, convergence, accuracy
   claims. Try to break it and compare with an independent computation (mpmath if available, else
   closed forms or high-precision numerical integration).

## Then look for what the author did NOT think of
Examples (not exhaustive): float underflow or overflow in `_fade` with extreme elapsed times or
half-lives; naive vs aware datetimes anywhere; observations with reliability 0 or an outcome strictly
between 0 and 1 with kinds `failure`/`success` (does `kind` agree with `outcome`?); `assisted` and
`stt_suspect` kinds and critical failures or contexts; critical-skill blocking uses only
`failure`/`required_omission` kinds, not low outcomes; distinct_contexts and distinct_items counting
only applied observations; `MasteryEstimate` invariants; the `uncertain`-before-`weak` precedence;
thresholds and statuses at exact boundaries; `SkillFramework` validation holes (edge threshold on
part_of, NaN or inf half-life or threshold, negative or NaN reliability, `bool` as number, huge graphs),
stability of the hash across Python versions and float formatting; `from_mapping` strictness; `replay`
with priors for unknown skills; mutable `evidence` mappings inside frozen dataclasses; hidden I/O or
clock use; anything that violates plan invariants 1, 4, 5 (section 2) or the "no abstraction without
immediate use" rule. Check the purity test and the import-linter contract: can they be bypassed
(dynamic imports, `__import__`, importlib, aliasing `datetime`)? Could the tests fail for the
behaviours they claim (mutation-style thinking: reverse a comparison or remove the cap, would a test
fail)? Try a few mutations on a COPY in the scratchpad, never on the repo.

## Output
Write the report in FRENCH:
1. Verdict: APPROUVÉ / APPROUVÉ AVEC RÉSERVES / BLOQUANT, one line with the reason.
2. Findings, each with severity (BLOQUANT / MAJEUR / MINEUR / REMARQUE), file:line, what is wrong, a
   minimal reproduction (snippet and its output), and a concrete suggested fix. Order by severity. Only
   report what you verified; flag suspicions explicitly.
3. Where the code deviates from the plan text, and whether each deviation is acceptable.
4. Verification of the numeric claims in `docs/LEARNING_CORE.md`.
5. What you checked and found correct (brief).
Be precise and economical; no praise padding.

Afterwards, record in the final note of the action: who reviewed, which PR, what was corrected.
