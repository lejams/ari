"""Scores one played session: what the learner heard against what the script asked.

Facts and credit are read from the persisted session, not from the socket events. The facts
a turn's answer chose (`selected_fact_ids`) measure the patient; the facts credited to the
learner (`revealed_fact_ids`) must be empty unless the turn is `delivered`, which the server
sets only on the playback receipt.

Outcomes, per step:
- `fact_leak`: a fact outside the step's `allowed_fact_ids` was revealed;
- `invented_fact`: a patient call was rejected by the guard against unknown sources;
- `refusal_missed`: an off-topic or other-language utterance got clinical facts;
- `credit_without_ack`: a turn counts as heard although its playback was not confirmed, or
  was interrupted (critical: the learner would be credited for a fact never heard);
- `ack_not_credited`: a confirmed playback was not credited;
- `interruption_failed`: the answer kept playing after the learner spoke over it;
- `turn_failed`: no answer (transcription, patient or speech failure);
- `transcription_mismatch` (informational): the transcript differs from what was said.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from ari.application.schemas import NON_CLINICAL_KINDS
from ari.domain.models import AudioDeliveryStatus, ConversationSession
from ari.evals.metrics import Ratio
from ari.evals.voice.driver import Mode, StepTrace

NON_ANSWERS = frozenset({"out_of_scope", "wrong_language", *NON_CLINICAL_KINDS})
GUARD_ERROR = "structured_output_guard"

RATIOS = (
    Ratio("leak_rate", ("leak_turns",), ("turns",)),
    Ratio("invented_fact_rate", ("invented_turns",), ("turns",)),
    Ratio("answer.recall", ("answer.found_facts",), ("answer.expected_facts",)),
    Ratio("refusal.any_recall", ("refusal.refused",), ("refusal.expected",)),
    Ratio("turn_failure_rate", ("turns.failed",), ("turns",)),
    Ratio("credit.violation_rate", ("credit.violations",), ("credit.checks",)),
    Ratio("interruption.success_rate", ("interruption.succeeded",), ("interruption.expected",)),
    Ratio("transcription.word_error_rate", ("stt.word_errors",), ("stt.words",)),
)


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def word_errors(said: str, heard: str) -> int:
    """Word-level edit distance (substitutions, insertions, deletions)."""
    reference, hypothesis = words(said), words(heard)
    previous = list(range(len(hypothesis) + 1))
    for i, word in enumerate(reference, start=1):
        current = [i]
        for j, other in enumerate(hypothesis, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (word != other))
            )
        previous = current
    return previous[-1]


def score_session(
    traces: Sequence[StepTrace], session: ConversationSession, mode: Mode
) -> tuple[dict[str, float], list[str], list[dict[str, Any]], str]:
    """Counts, outcomes, per-step detail and the signature of one session."""
    turns = {turn.id: turn for turn in session.turns}
    guard_failures = {
        e.turn_id for e in session.executions if e.error_code == GUARD_ERROR and e.turn_id
    }
    counts: dict[str, float] = {}
    outcomes: set[str] = set()
    detail: list[dict[str, Any]] = []
    signature: list[str] = []

    def count(key: str, amount: float = 1.0) -> None:
        counts[key] = counts.get(key, 0.0) + amount

    for trace in traces:
        step = trace.step
        turn = turns.get(trace.turn_id or "")
        count("turns")
        step_outcomes: list[str] = []
        said_words = len(words(step.utterance))
        count("stt.words", said_words)
        errors = (
            said_words
            if trace.transcript is None
            else word_errors(step.utterance, trace.transcript)
        )
        count("stt.word_errors", errors)
        if errors:
            step_outcomes.append("transcription_mismatch")
        answered = turn is not None and trace.terminal in {
            "patient.audio_done",
            "patient.audio_interrupted",
        }
        if not answered:
            count("turns.failed")
            step_outcomes.append("turn_failed")
        chosen = (
            set(turn.selected_fact_ids) | set(turn.revealed_fact_ids) if turn is not None else set()
        )
        credited_facts = set(turn.revealed_fact_ids) if turn is not None else set()
        leaked = sorted(chosen - set(step.allowed_fact_ids))
        if leaked:
            count("leak_turns")
            step_outcomes.append("fact_leak")
        if trace.turn_id in guard_failures:
            count("invented_turns")
            step_outcomes.append("invented_fact")
        count("answer.expected_facts", len(step.expected_fact_ids))
        count("answer.found_facts", len(set(step.expected_fact_ids) & chosen))
        kind = turn.patient_response_kind if turn is not None else None
        if step.kind in {"off_topic", "foreign"}:
            count("refusal.expected")
            if kind in NON_ANSWERS:
                count("refusal.refused")
            else:
                step_outcomes.append("refusal_missed")
        interrupted = trace.terminal == "patient.audio_interrupted"
        if step.interrupt and mode == "exam":
            count("interruption.expected")
            if interrupted:
                count("interruption.succeeded")
            else:
                step_outcomes.append("interruption_failed")
        if turn is not None:
            should_credit = (
                trace.terminal == "patient.audio_done" and step.acknowledge and not interrupted
            )
            credited = turn.delivery_status is AudioDeliveryStatus.DELIVERED
            count("credit.checks")
            if (credited and not should_credit) or (credited_facts and not credited):
                count("credit.violations")
                step_outcomes.append("credit_without_ack")
            elif should_credit and not credited:
                count("credit.violations")
                step_outcomes.append("ack_not_credited")
        outcomes.update(o for o in step_outcomes if o != "transcription_mismatch")
        signature.append(f"{step.id}:{kind}:{sorted(chosen)}:{trace.terminal}")
        detail.append(
            {
                "step": step.id,
                "said": step.utterance,
                "transcript": trace.transcript,
                "response_kind": kind,
                "chosen_fact_ids": sorted(chosen),
                "credited_fact_ids": sorted(credited_facts),
                "leaked_fact_ids": leaked,
                "terminal": trace.terminal,
                "latency_ms": trace.latency_ms,
                "audio_chunks": trace.chunks,
                "delivery_status": turn.delivery_status.value if turn is not None else None,
                "outcomes": step_outcomes,
                "errors": trace.errors,
            }
        )
    return counts, sorted(outcomes), detail, "|".join(signature)
