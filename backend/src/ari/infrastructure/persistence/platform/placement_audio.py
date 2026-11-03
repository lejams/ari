"""SQL store of the recorded audio of the placement listenings (append-only)."""

from __future__ import annotations

import hashlib

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ari.application.ports.placement import StoredAudio
from ari.domain.errors import InvalidStateError
from ari.domain.models import utc_now
from ari.infrastructure.persistence.platform.placement_rows import PlacementAudioRow


class SqlPlacementAudioStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, set_hash: str, item_id: str) -> StoredAudio | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(PlacementAudioRow)
                .where(PlacementAudioRow.set_hash == set_hash, PlacementAudioRow.item_id == item_id)
                .order_by(PlacementAudioRow.created_at.desc())
                .limit(1)
            )
            if row is None:
                return None
            if hashlib.sha256(row.audio).hexdigest() != row.audio_sha256:
                raise InvalidStateError(f"Audio altéré: {item_id} ({row.voice})")
            return StoredAudio(row.audio, row.sample_rate, row.voice, row.model)

    def has(self, set_hash: str, item_id: str, voice: str) -> bool:
        with Session(self.engine) as db:
            return (
                db.scalar(
                    select(PlacementAudioRow.item_id).where(
                        PlacementAudioRow.set_hash == set_hash,
                        PlacementAudioRow.item_id == item_id,
                        PlacementAudioRow.voice == voice,
                    )
                )
                is not None
            )

    def put(
        self, set_id: str, version: str, set_hash: str, item_id: str, audio: StoredAudio
    ) -> bool:
        statement = (
            insert(PlacementAudioRow)
            .values(
                set_hash=set_hash,
                item_id=item_id,
                voice=audio.voice,
                set_id=set_id,
                set_version=version,
                model=audio.model,
                sample_rate=audio.sample_rate,
                audio_sha256=hashlib.sha256(audio.pcm16).hexdigest(),
                audio=audio.pcm16,
                created_at=utc_now(),
            )
            .on_conflict_do_nothing()
            .returning(PlacementAudioRow.item_id)
        )
        with Session(self.engine) as db, db.begin():
            return db.scalar(statement) is not None
