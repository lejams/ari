"""The rubrics every generated FSP case shares. Versioned assets: change them by bumping.

A scenario pins a rubric by (id, version, hash); learners compare across cases only because
the same rubric scores them. New wording or dimensions therefore mean a new version, never
an edit in place.
"""

from ari.domain.clinical import AnamnesisSectionId, RubricDimension, RubricVersion

ANAMNESIS_RUBRIC = RubricVersion(
    id="fsp-anamnesis",
    version="1",
    dimensions=(
        RubricDimension(
            id="clinical_coverage",
            label="Couverture clinique",
            max_score=5,
            description="Les éléments du protocole effectivement recueillis pendant l'anamnèse",
        ),
        RubricDimension(
            id="communication",
            label="Communication",
            max_score=5,
            description="Salutation, présentation, reformulation et clôture de l'entretien",
        ),
    ),
    scoring_version="assessment-weighted-v1",
)

ARZT_ARZT_RUBRIC = RubricVersion(
    id="fsp-arzt-arzt",
    version="1",
    dimensions=(
        RubricDimension(
            id="structured_presentation",
            label="Présentation structurée",
            max_score=5,
            description="Correspondance avec les formulations attendues de ce cas",
        ),
    ),
    scoring_version="practice-exact-answer-v1",
)

FACHBEGRIFFE_RUBRIC = RubricVersion(
    id="fsp-fachbegriffe",
    version="1",
    dimensions=(
        RubricDimension(
            id="lexical",
            label="Lexique",
            max_score=5,
            description="Explication d'un terme médical en allemand courant, variantes de ce cas",
        ),
    ),
    scoring_version="practice-exact-answer-v1",
)

# German labels of the canonical anamnesis sections, as examiners name them.
SECTION_LABELS_DE: dict[AnamnesisSectionId, str] = {
    "patientendaten": "Patientendaten",
    "aktuelle_beschwerden": "Aktuelle Beschwerden",
    "vorerkrankungen": "Vorerkrankungen",
    "medikamente": "Medikamente",
    "allergien": "Allergien",
    "noxen": "Noxen",
    "familienanamnese": "Familienanamnese",
    "sozialanamnese": "Sozialanamnese",
    "vegetative_anamnese": "Vegetative Anamnese",
    "sonstiges": "Sonstiges",
}

# Sections an examiner expects in every anamnesis: their items are required, not optional.
REQUIRED_SECTIONS: frozenset[AnamnesisSectionId] = frozenset(
    {"aktuelle_beschwerden", "medikamente", "allergien"}
)
