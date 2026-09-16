"""Placement test registry: import, one linguistic review of the exact hash, publish.

Mirrors the clinical registry with a lighter approval: a placement set carries no clinical
claim, so a single linguistic approval unlocks publication. Content rows are immutable.
"""

from typing import Any

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ari.domain.clinical import RawCaseSource
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.models import new_id, utc_now
from ari.domain.placement import PlacementBundle, PlacementReview, PlacementSetVersion
from ari.infrastructure.cases.clinical_store import decode
from ari.infrastructure.persistence.placement_rows import (
    PlacementEventRow,
    PlacementReviewRow,
    PlacementSetRow,
    PlacementSourceRow,
)


class PlacementStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def import_bundle(self, bundle: PlacementBundle) -> dict[str, int]:
        bundle = decode(PlacementBundle, bundle.model_dump(mode="json"))
        counts = {"tests_nouveaux": 0, "tests_identiques": 0}
        try:
            with Session(self.engine) as db, db.begin():
                for source in bundle.sources:
                    existing_source = db.get(PlacementSourceRow, source.id)
                    if existing_source is not None:
                        if existing_source.content_hash != source.content_hash:
                            raise InvalidStateError(f"Conflit de source: {source.id}")
                        continue
                    db.add(
                        PlacementSourceRow(
                            id=source.id,
                            content_hash=source.content_hash,
                            payload=source.model_dump(mode="json"),
                        )
                    )
                db.flush()
                for placement_set in bundle.sets:
                    existing = db.get(PlacementSetRow, (placement_set.id, placement_set.version))
                    if existing is not None:
                        if existing.content_hash != placement_set.content_hash:
                            raise InvalidStateError(
                                f"Conflit de version: {placement_set.id}@{placement_set.version}"
                            )
                        counts["tests_identiques"] += 1
                        continue
                    counts["tests_nouveaux"] += 1
                    db.add(
                        PlacementSetRow(
                            id=placement_set.id,
                            version=placement_set.version,
                            content_hash=placement_set.content_hash,
                            language=placement_set.language,
                            payload=placement_set.model_dump(mode="json"),
                            status="draft_unvalidated",
                        )
                    )
        except IntegrityError as exc:
            raise InvalidStateError("Conflit concurrent; import annulé") from exc
        return counts

    @staticmethod
    def _row(db: Session, set_id: str, version: str, *, lock: bool = False) -> PlacementSetRow:
        if lock:
            db.execute(
                update(PlacementSetRow)
                .where(PlacementSetRow.id == set_id, PlacementSetRow.version == version)
                .values(status=PlacementSetRow.status)
            )
        row = db.get(PlacementSetRow, (set_id, version), populate_existing=True)
        if row is None:
            raise NotFoundError("Test de niveau inconnu")
        return row

    @staticmethod
    def _set(row: PlacementSetRow) -> PlacementSetVersion:
        placement_set = decode(PlacementSetVersion, row.payload)
        if placement_set.content_hash != row.content_hash:
            raise InvalidStateError("Intégrité du test de niveau invalide")
        return placement_set

    def get(self, set_id: str, version: str) -> tuple[PlacementSetVersion, str]:
        with Session(self.engine) as db:
            row = self._row(db, set_id, version)
            return self._set(row), row.status

    def published(self, language: str | None = None) -> PlacementSetVersion | None:
        """The published set, preferring the requested language; None when nothing is."""
        with Session(self.engine) as db:
            rows = db.scalars(
                select(PlacementSetRow)
                .where(PlacementSetRow.status == "published")
                .order_by(PlacementSetRow.id, PlacementSetRow.version)
            ).all()
        if not rows:
            return None
        preferred = [row for row in rows if language is None or row.language == language]
        return self._set((preferred or rows)[-1])

    def _blockers(self, db: Session, row: PlacementSetRow) -> list[str]:
        blockers = []
        placement_set = self._set(row)
        for ref in placement_set.sources:
            source_row = db.get(PlacementSourceRow, ref.source_id)
            source = decode(RawCaseSource, source_row.payload) if source_row else None
            if source is None or source.rights != "compatible":
                blockers.append(f"Droits non compatibles ou inconnus: {ref.source_id}")
        approved = False
        for review_row in db.scalars(
            select(PlacementReviewRow)
            .where(
                PlacementReviewRow.set_id == row.id,
                PlacementReviewRow.set_version == row.version,
            )
            .order_by(PlacementReviewRow.sequence)
        ):
            review = decode(PlacementReview, review_row.payload)
            if review.set_hash == row.content_hash:
                approved = review.decision == "approve"
        if not approved:
            blockers.append("Approbation linguistique manquante pour le contenu exact")
        return blockers

    def inspect(self, set_id: str, version: str) -> dict[str, Any]:
        with Session(self.engine) as db:
            row = self._row(db, set_id, version)
            return {
                "status": row.status,
                "set_hash": row.content_hash,
                "set": row.payload,
                "blockers": self._blockers(db, row),
            }

    def record_review(self, review: PlacementReview) -> None:
        review = decode(PlacementReview, review.model_dump(mode="json"))
        with Session(self.engine) as db, db.begin():
            row = self._row(db, review.set.id, review.set.version, lock=True)
            if row.status != "draft_unvalidated":
                raise InvalidStateError(
                    "Retirer une publication avant de créer une version corrigée"
                )
            if review.set_hash != row.content_hash:
                raise InvalidStateError("La revue ne correspond pas au contenu exact")
            db.add(
                PlacementReviewRow(
                    id=review.id,
                    set_id=row.id,
                    set_version=row.version,
                    set_hash=row.content_hash,
                    payload=review.model_dump(mode="json"),
                )
            )

    def publish(self, set_id: str, version: str, *, actor: str) -> None:
        if not actor.strip():
            raise InvalidStateError("Identité déclarée requise")
        with Session(self.engine) as db, db.begin():
            row = self._row(db, set_id, version, lock=True)
            if row.status != "draft_unvalidated":
                raise InvalidStateError("Le test n'est plus un brouillon")
            blockers = self._blockers(db, row)
            if blockers:
                raise InvalidStateError("Publication refusée: " + "; ".join(blockers))
            for prior in db.scalars(
                select(PlacementSetRow).where(
                    PlacementSetRow.id == row.id,
                    PlacementSetRow.status == "published",
                )
            ).all():
                prior.status = "withdrawn"
                self._audit(db, prior, "superseded", actor)
            db.flush()
            row.status = "published"
            self._audit(db, row, "publish", actor)

    def withdraw(self, set_id: str, version: str, *, actor: str) -> None:
        if not actor.strip():
            raise InvalidStateError("Identité déclarée requise")
        with Session(self.engine) as db, db.begin():
            row = self._row(db, set_id, version, lock=True)
            if row.status != "published":
                raise InvalidStateError("Seul un test publié peut être retiré")
            row.status = "withdrawn"
            self._audit(db, row, "withdraw", actor)

    @staticmethod
    def _audit(db: Session, row: PlacementSetRow, action: str, actor: str) -> None:
        db.add(
            PlacementEventRow(
                id=new_id(),
                set_id=row.id,
                set_version=row.version,
                payload={
                    "action": action,
                    "actor": actor,
                    "set_hash": row.content_hash,
                    "at": utc_now().isoformat(),
                },
            )
        )
