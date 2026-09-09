# Practice lifecycle

ARI exposes one provider-neutral lifecycle for two intentionally separate
workflows:

* `structured_text` uses `PracticeService` and `practice_runs`.
* `patient_voice` uses `ConversationOrchestrator` and `sessions`.

`PracticeLifecycle` is a registry over these workflows. It provides
`start_practice`, `submit_turn`, `resume_practice`, `end_practice`, and
`get_feedback`, while projections use the common `PracticeHandle`,
`PracticeTurn`, and feedback envelope.

Voice transports remain implementation details. Pipeline voice retains STT,
patient simulation, TTS and delivery acknowledgements. Realtime receives a
transport-only instruction and voices the canonical response produced by the
application simulator. The canonical response is stored before synthesis;
observed Realtime text is stored separately and compared deterministically.
A mismatch or failed delivery credits no facts.

For an already reserved voice transcript, `complete_reserved_turn` is the
internal submit phase used by both pipeline and Realtime. It runs the patient
simulation and persists the canonical response before the transport starts;
the public lifecycle still exposes the same five operations.

Migration `20260906_0009` adds nullable `turns.normalized_user_text`,
`turns.canonical_response`, and `turns.observed_response_text`. It performs no
backfill and can be downgraded without changing historical rows.
