"""Personal lexicon: fed by every analysed session, revised by spaced repetition.

Entry states only move on learner evidence (a review rating, a term spoken in a later
session). The LLM proposes candidates; it never declares a word mastered.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from ari.application.ports.evaluator import EvaluationOutcome
from ari.application.ports.lexicon import LexiconIngestReport, LexiconRepository
from ari.domain.errors import InvalidStateError
from ari.domain.models import (
    ConversationSession,
    LexiconEntry,
    LexiconReview,
    LexiconSource,
    MedicalCase,
    PatientResponseKind,
    SrsRating,
    SrsState,
    VocabularyState,
    new_id,
    utc_now,
)
from ari.domain.srs import SRS_VERSION, schedule
from ari.domain.text import normalize_answer, term_used

# Unused case terms are only worth adding once the learner actually held a conversation.
MIN_TURNS_FOR_UNUSED_TERMS = 3
MASTERY_SESSIONS = 2
MASTERY_REPETITIONS = 3


@dataclass(frozen=True, slots=True)
class LexiconOverview:
    due: tuple[LexiconEntry, ...]
    entries: tuple[LexiconEntry, ...]
    by_state: Mapping[str, int]
    srs_version: str = SRS_VERSION


class LexiconService:
    def __init__(
        self, repository: LexiconRepository, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self.repository = repository
        self._clock = clock

    # ----- ingestion -----------------------------------------------------------------

    def ingest_session(
        self, session: ConversationSession, case: MedicalCase, outcome: EvaluationOutcome
    ) -> LexiconIngestReport:
        """Idempotent: re-analysing a session touches the same entries, never duplicates."""
        now = self._clock()
        entries = {
            e.lemma_key: e for e in self.repository.list(session.learner_id, include_archived=True)
        }
        changed: dict[str, LexiconEntry] = {}

        def touch(entry: LexiconEntry) -> None:
            changed[entry.lemma_key] = replace(entry, last_session_id=session.id, updated_at=now)

        def add(lemma: str, translation: str, example: str, source: LexiconSource) -> None:
            key = normalize_answer(lemma)
            if not key:
                return
            if key in entries or key in changed:
                touch(changed.get(key) or entries[key])
                return
            changed[key] = LexiconEntry(
                id=new_id(),
                learner_id=session.learner_id,
                lemma_key=key,
                lemma=lemma.strip(),
                translation=translation.strip(),
                example=example.strip(),
                source=source,
                state=VocabularyState.IDENTIFIED,
                srs=SrsState(due_at=now),
                first_session_id=session.id,
                last_session_id=session.id,
                created_at=now,
                updated_at=now,
            )

        def mark_used(entry: LexiconEntry) -> None:
            # Only words that entered the lexicon in an earlier session count as used.
            if entry.first_session_id == session.id:
                return
            changed[entry.lemma_key] = self._used_in(
                changed.get(entry.lemma_key) or entry, session.id, now
            )

        for item in outcome.vocabulary_candidates:
            lemma = str(item["lemma"])
            kind = str(item.get("kind", "missing"))
            if kind == "well_used":
                existing = entries.get(normalize_answer(lemma))
                if existing is not None:
                    mark_used(existing)
                continue
            add(
                lemma,
                str(item["translation"]),
                str(item["example"]),
                LexiconSource.EVALUATION_CANDIDATE,
            )

        learner_turns = [t.user_text for t in session.turns]
        for term in case.terminology:
            key = normalize_answer(term.german)
            if any(term_used(term.german, text) for text in learner_turns):
                existing = entries.get(key)
                if existing is not None:
                    mark_used(existing)
            elif len(learner_turns) >= MIN_TURNS_FOR_UNUSED_TERMS:
                add(term.german, term.french or "", "", LexiconSource.TERMINOLOGY_UNUSED)

        for code_switch in outcome.evaluation.code_switches:
            if code_switch.intended_term:
                add(code_switch.intended_term, code_switch.fragment, "", LexiconSource.CODE_SWITCH)

        self.repository.upsert(changed.values())
        after = self.repository.list(session.learner_id, include_archived=True)
        report = LexiconIngestReport(
            session_id=session.id,
            added=tuple(e for e in after if e.first_session_id == session.id),
            promoted=tuple(
                e
                for e in after
                if session.id in e.used_session_ids and e.first_session_id != session.id
            ),
            wrong_language_turns=tuple(
                t.sequence
                for t in session.turns
                if t.patient_response_kind == PatientResponseKind.WRONG_LANGUAGE.value
            ),
            srs_version=SRS_VERSION,
        )
        self.repository.save_report(report)
        return report

    @staticmethod
    def _used_in(entry: LexiconEntry, session_id: str, now: datetime) -> LexiconEntry:
        used = tuple(dict.fromkeys((*entry.used_session_ids, session_id)))
        state = entry.state
        if state in {VocabularyState.IDENTIFIED, VocabularyState.REVIEWED}:
            state = VocabularyState.USED
        if (
            state is VocabularyState.USED
            and len(used) >= MASTERY_SESSIONS
            and entry.srs.repetitions >= MASTERY_REPETITIONS
        ):
            state = VocabularyState.MASTERED
        return replace(
            entry, state=state, used_session_ids=used, last_session_id=session_id, updated_at=now
        )

    # ----- review ------------------------------------------------------------------

    def review(
        self, learner_id: str, entry_id: str, event_id: str, rating: SrsRating
    ) -> LexiconEntry:
        entry = self.repository.get(learner_id, entry_id)
        if self.repository.find_review(entry_id, event_id) is not None:
            return entry
        if entry.archived:
            raise InvalidStateError("Cette entrée est archivée")
        now = self._clock()
        srs = schedule(entry.srs, rating, now)
        state = entry.state
        if rating is SrsRating.AGAIN:
            if state is VocabularyState.MASTERED:
                state = VocabularyState.USED
        elif state is VocabularyState.IDENTIFIED:
            state = VocabularyState.REVIEWED
        if (
            state is VocabularyState.USED
            and len(entry.used_session_ids) >= MASTERY_SESSIONS
            and srs.repetitions >= MASTERY_REPETITIONS
        ):
            state = VocabularyState.MASTERED
        updated = replace(entry, srs=srs, state=state, updated_at=now)
        review = LexiconReview(
            id=new_id(), entry_id=entry.id, event_id=event_id, rating=rating, reviewed_at=now
        )
        return self.repository.record_review(review, updated)

    # ----- manual edits --------------------------------------------------------------

    def add_manual(
        self, learner_id: str, lemma: str, translation: str, example: str
    ) -> LexiconEntry:
        key = normalize_answer(lemma)
        if not key:
            raise InvalidStateError("Le mot est vide")
        now = self._clock()
        existing = next(
            (
                e
                for e in self.repository.list(learner_id, include_archived=True)
                if e.lemma_key == key
            ),
            None,
        )
        if existing is not None:
            entry = replace(
                existing,
                translation=translation.strip() or existing.translation,
                example=example.strip() or existing.example,
                archived=False,
                updated_at=now,
            )
        else:
            entry = LexiconEntry(
                id=new_id(),
                learner_id=learner_id,
                lemma_key=key,
                lemma=lemma.strip(),
                translation=translation.strip(),
                example=example.strip(),
                source=LexiconSource.MANUAL,
                state=VocabularyState.IDENTIFIED,
                srs=SrsState(due_at=now),
                created_at=now,
                updated_at=now,
            )
        self.repository.upsert((entry,))
        return self.repository.get(learner_id, entry.id)

    def set_archived(self, learner_id: str, entry_id: str, archived: bool) -> LexiconEntry:
        entry = self.repository.get(learner_id, entry_id)
        self.repository.upsert((replace(entry, archived=archived, updated_at=self._clock()),))
        return self.repository.get(learner_id, entry_id)

    # ----- read ----------------------------------------------------------------------

    def overview(self, learner_id: str) -> LexiconOverview:
        now = self._clock()
        entries = tuple(
            sorted(
                self.repository.list(learner_id),
                key=lambda e: (e.srs.due_at, e.lemma_key),
            )
        )
        due = tuple(e for e in entries if e.srs.due_at <= now)
        by_state = {state.value: 0 for state in VocabularyState}
        for entry in entries:
            by_state[entry.state.value] += 1
        return LexiconOverview(due=due, entries=entries, by_state=by_state)
