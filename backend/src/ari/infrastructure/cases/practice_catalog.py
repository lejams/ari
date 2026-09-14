"""Publication-verified exercise factory."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ari.domain.errors import NotFoundError
from ari.domain.practice import PracticeContent
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.persistence.clinical_rows import ScenarioRow


class PublishedPracticeCatalog:
    def __init__(self, store: ClinicalStore) -> None:
        self.store = store

    def list(self) -> tuple[PracticeContent, ...]:
        content = []
        with Session(self.store.engine) as db:
            for row in db.scalars(
                select(ScenarioRow)
                .where(
                    ScenarioRow.status == "published",
                    ScenarioRow.phase.in_(("arzt_arzt", "fachbegriffe")),
                )
                .order_by(ScenarioRow.id, ScenarioRow.version)
            ):
                bundle = self.store._bundle(db, row)
                if bundle.scenarios[0].practice is not None:
                    content.append(
                        PracticeContent(
                            bundle=bundle,
                            scenario_id=row.id,
                            scenario_version=row.version,
                            provenance="published",
                        )
                    )
        return tuple(content)

    def get(self, scenario_id: str, version: str) -> PracticeContent:
        for content in self.list():
            if (content.scenario_id, content.scenario_version) == (scenario_id, version):
                return content
        raise NotFoundError("Aucun exercice approuvé disponible pour cette version")
