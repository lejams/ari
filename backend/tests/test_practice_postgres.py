"""Real PostgreSQL exercise/identity/mode checks in a fresh isolated test schema."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from test_clinical_postgres import postgres_url as postgres_url

from ari.application.services.practice import PracticeService
from ari.config import PROJECT_ROOT
from ari.domain.models import ConversationSession, LearnerProfile, LearningGoal, LearningMode
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.cases.practice_catalog import PublishedPracticeCatalog
from ari.infrastructure.cases.practice_demos import synthetic_demos
from ari.infrastructure.persistence.identity import ProfileCredentials
from ari.infrastructure.persistence.practice import SqlPracticeRepository
from ari.infrastructure.persistence.sqlite import SqliteSessionRepository


def test_real_postgres_practice_identity_and_voice_mode(
    monkeypatch: pytest.MonkeyPatch,
    postgres_url: str,
) -> None:
    monkeypatch.setenv("ARI_DATABASE_URL", postgres_url)
    config = Config(PROJECT_ROOT / "alembic.ini")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    command.check(config)
    repo = SqliteSessionRepository(postgres_url)
    try:
        column = next(
            c for c in inspect(repo.engine).get_columns("practice_runs") if c["name"] == "content"
        )
        assert isinstance(column["type"], JSONB)
        learner = repo.create_learner(LearnerProfile(id="practice-owner", goal=LearningGoal()))
        credentials = ProfileCredentials(repo.engine)
        token = credentials.issue(learner.id)
        assert credentials.resolve(token) == learner.id
        credentials.revoke(token)
        assert credentials.resolve(token) is None
        service = PracticeService(
            PublishedPracticeCatalog(ClinicalStore(repo.engine), synthetic_demos()),
            SqlPracticeRepository(repo.engine, allow_synthetic=True),
        )

        def start(_: int) -> str:
            return service.start(learner.id, "ari-demo-fachbegriffe", "1", "exam", "same-start").id

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(start, (0, 1)))
        assert ids[0] == ids[1]
        run_id = ids[0]

        def answer(_: int) -> int:
            return len(
                service.answer(
                    learner.id, run_id, "first", "Durch den Mund.", "same-answer"
                ).answers
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(answer, (0, 1))) == [1, 1]
        service.repository.set_paused(run_id, learner.id, True)
        service.repository.set_paused(run_id, learner.id, False)
        service.answer(learner.id, run_id, "second", "Beidseitig.", "second-answer")
        completed = service.finish(learner.id, run_id)
        assert completed.feedback is not None
        assert completed.feedback.dimensions[0].score == 5
        assert service.finish(learner.id, run_id) == completed
        voice = ConversationSession(
            id="voice",
            learner_id=learner.id,
            case_id="synthetic-only",
            case_version="1",
            case_hash="isolated-test",
            goal=learner.goal,
            learning_mode=LearningMode.EXAM,
        )
        repo.create_session(voice)
        assert repo.get_session(voice.id).learning_mode is LearningMode.EXAM
        for sql in (
            "UPDATE practice_runs SET content_hash='changed'",
            "UPDATE practice_runs SET status='active', feedback=NULL, ended_at=NULL",
            "UPDATE practice_answers SET event_id='changed'",
            "DELETE FROM practice_answers",
            "UPDATE voice_learning_context SET mode='training'",
            "DELETE FROM voice_learning_context",
        ):
            with pytest.raises(DBAPIError), repo.engine.begin() as db:
                db.execute(text(sql))
    finally:
        repo.engine.dispose()
