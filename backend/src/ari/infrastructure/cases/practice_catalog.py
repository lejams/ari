"""Publication-verified exercise factory; private intake tables are never queried."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.practice import PracticeContent
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.persistence.clinical_rows import ScenarioRow


class PublishedPracticeCatalog:
    def __init__(self, store: ClinicalStore, demos: tuple[PracticeContent, ...] = ()) -> None:
        self.store = store
        self.demos = tuple(PracticeContent.model_validate_json(d.model_dump_json()) for d in demos)
        if any(d.provenance != "synthetic_demo" for d in self.demos):
            raise InvalidStateError(
                "Le catalogue de démonstration exige une provenance synthétique"
            )
        if len({(d.scenario_id, d.scenario_version) for d in self.demos}) != len(self.demos):
            raise InvalidStateError("Démonstration dupliquée")

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
        keys = {(c.scenario_id, c.scenario_version) for c in content}
        if keys & {(d.scenario_id, d.scenario_version) for d in self.demos}:
            raise InvalidStateError("Collision entre démo et contenu publié")
        return (*content, *self.demos)

    def get(self, scenario_id: str, version: str) -> PracticeContent:
        for content in self.list():
            if (content.scenario_id, content.scenario_version) == (scenario_id, version):
                return content
        raise NotFoundError("Aucun exercice approuvé disponible pour cette version")
