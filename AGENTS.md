# ARI Engineering Rules

## Architecture

- Business/domain code must never import vendor SDKs.
- External AI services must be behind explicit interfaces.
- Prefer dependency injection.
- Keep patient simulation, evaluation and persistence separate.
- Prompts and medical cases are versioned assets.
- Do not over-engineer.

## Quality

- Use strict typing.
- Add tests for new behavior.
- Run focused tests, lint and type checks before declaring implementation work
  complete when relevant.
- Avoid duplicated business logic.
- Prefer small cohesive modules.
- Preserve existing behavior unless the task explicitly changes it.

## AI

- All structured AI outputs must use schemas.
- Provider/model/prompt/case versions must be traceable.
- Changing provider must not require changing application logic.

## Medical

- Clinical facts must originate from the case definition.
- Evaluation must be grounded in transcript and rubric.
- Never invent regulatory information.

## Frontend

- Preserve existing product behavior unless explicitly requested otherwise.
- Prefer cohesive reusable components over duplicated UI logic.
- Keep frontend state ownership explicit.
- Avoid unnecessary architectural rewrites for visual changes.
- Verify important user flows after meaningful frontend modifications.

## Completion

Before declaring an implementation complete:

- verify the requested behavior
- verify intended modifications exist on disk
- inspect the final diff
- run focused validation
- ensure no unintended files were modified

Do not report an implementation as complete if the expected repository changes
are absent.

## Review

Spawn `reviewer` only when the final change has meaningful:

- regression risk
- architectural risk
- security risk
- concurrency/state-management risk
- data-integrity risk
- API compatibility risk
- broad cross-component impact

Do not invoke reviewer automatically.
