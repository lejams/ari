from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ari.domain.errors import CaseValidationError
from ari.domain.models import VocabularyHint, VocabularyHintAsset


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _HintData(_StrictModel):
    id: str = Field(min_length=1, max_length=80)
    term: str = Field(min_length=1, max_length=100)
    translation: str = Field(min_length=1, max_length=160)


class _AssetData(_StrictModel):
    id: str
    version: str
    case_id: str
    case_version: str
    language: str
    translation_language: str
    hints: list[_HintData] = Field(min_length=1, max_length=50)


class YamlVocabularyHintCatalog:
    def __init__(self, assets: tuple[VocabularyHintAsset, ...]) -> None:
        self._assets = {(item.case_id, item.case_version): item for item in assets}
        if len(self._assets) != len(assets):
            raise CaseValidationError("Duplicate vocabulary-hint case/version")

    def get_for_case(self, case_id: str, case_version: str) -> VocabularyHintAsset | None:
        return self._assets.get((case_id, case_version))


def load_vocabulary_hints(directory: Path) -> YamlVocabularyHintCatalog:
    assets: list[VocabularyHintAsset] = []
    if not directory.exists():
        return YamlVocabularyHintCatalog(())
    for path in sorted(directory.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            data = _AssetData.model_validate(raw)
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            raise CaseValidationError(f"Invalid vocabulary asset {path.name}: {exc}") from exc
        hint_ids = [item.id for item in data.hints]
        if len(hint_ids) != len(set(hint_ids)):
            raise CaseValidationError(f"Duplicate vocabulary hint in {path.name}")
        assets.append(
            VocabularyHintAsset(
                id=data.id,
                version=data.version,
                case_id=data.case_id,
                case_version=data.case_version,
                language=data.language,
                translation_language=data.translation_language,
                hints=tuple(VocabularyHint(**item.model_dump()) for item in data.hints),
            )
        )
    return YamlVocabularyHintCatalog(tuple(assets))
