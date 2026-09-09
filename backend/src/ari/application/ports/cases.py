from typing import Protocol

from ari.domain.models import MedicalCase


class MedicalCaseCatalog(Protocol):
    def list(self) -> tuple[MedicalCase, ...]: ...

    def get(
        self, case_id: str, version: str, *, scenario_id: str | None = None,
        scenario_version: str | None = None,
    ) -> MedicalCase: ...
