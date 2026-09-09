"""Version 1 of authored synthetic demos; never imported, reviewed or published.

No clinical diagnosis or treatment is taught here. These small exercises demonstrate
the product mechanics and text-only evidence rules, not FSP readiness.
"""

from datetime import UTC, datetime

from ari.domain.clinical import (
    ClinicalAssessmentItem,
    ClinicalBundle,
    ClinicalCaseVersion,
    ClinicalFact,
    PracticeCriterion,
    PracticeQuestion,
    PracticeSpecification,
    RawCaseSource,
    RubricDimension,
    RubricVersion,
    SourceRef,
    TerminologyEntry,
    TerminologySetVersion,
    TrainingScenarioVersion,
    VersionRef,
)
from ari.domain.models import DisclosureRule
from ari.domain.practice import PracticeContent


def synthetic_demos() -> tuple[PracticeContent, ...]:
    source = RawCaseSource(
        id="ari-mvp-synthetic-v1",
        source_type="synthetic",
        document_reference="ari/infrastructure/cases/practice_demos.py@1",
        provenance="Fiction authored for an offline software demonstration. No human review.",
        imported_at=datetime(2026, 9, 4, tzinfo=UTC),
        intended_use="Local development/test only, not approved training content",
        private_material_reference="none:synthetic",
    )
    refs = (SourceRef(source_id=source.id),)
    facts = (
        ClinicalFact(
            id="identity",
            category="synthetic-identity",
            value="Alex Beispiel, 40 Jahre",
            polarity="present",
            patient_phrases_de=("Ich heiße Alex Beispiel und bin 40 Jahre alt.",),
            disclosure=DisclosureRule.WHEN_ASKED,
            sources=refs,
        ),
        ClinicalFact(
            id="reason",
            category="synthetic-context",
            value="Kommunikationsübung",
            polarity="present",
            patient_phrases_de=("Ich bin für eine Kommunikationsübung hier.",),
            disclosure=DisclosureRule.WHEN_ASKED,
            sources=refs,
        ),
        ClinicalFact(
            id="medication",
            category="synthetic-unknown",
            value=None,
            polarity="unknown",
            patient_phrases_de=("Zu Medikamenten liegen keine Angaben vor.",),
            uncertainty="Not specified in this fictional scenario; do not infer absence.",
            disclosure=DisclosureRule.WHEN_ASKED,
            sources=refs,
        ),
    )
    terms = TerminologySetVersion(
        id="ari-demo-terms",
        version="1",
        entries=(
            TerminologyEntry(id="oral", german="oral", french="par la bouche"),
            TerminologyEntry(id="bilateral", german="bilateral", french="des deux côtés"),
        ),
    )
    result = []
    for phase in ("arzt_arzt", "fachbegriffe"):
        lexical = phase == "fachbegriffe"
        dimension = "lexical" if lexical else "structured_presentation"
        rubric = RubricVersion(
            id=f"ari-demo-{phase}-rubric",
            version="1",
            scoring_version="practice-exact-answer-v1",
            dimensions=(
                RubricDimension(
                    id=dimension,
                    label="Lexique" if lexical else "Présentation structurée",
                    max_score=5,
                    description="Correspondance avec les variantes de cette démo uniquement",
                ),
            ),
        )
        case = ClinicalCaseVersion(
            id=f"ARI-DEMO-{phase.upper()}",
            version="1",
            region="Fiction",
            city="Fiction",
            title="Démo synthétique · "
            + ("Expliquer deux termes" if lexical else "Présenter Alex Exemple"),
            public_summary="Deux étapes textuelles, sans validation clinique ou linguistique.",
            transcription_context="Synthetische Kommunikationsübung",
            unknown_response="Dazu liegen keine Angaben vor.",
            out_of_scope_response="Das gehört nicht zu dieser Übung.",
            sources=refs,
            facts=facts,
            assessment_items=(
                ClinicalAssessmentItem(
                    id="patient-identity",
                    dimension="clinical_coverage",
                    weight=1.0,
                    expected_behavior="Exemple technique de recueil, non utilisé par cet exercice",
                    evidence_kind="delivered_facts",
                    satisfaction="all",
                    satisfied_by_fact_ids=("identity",),
                ),
            ),
        )
        criteria = (
            PracticeCriterion(
                id="first",
                dimension=dimension,
                weight=1.0,
                expected_behavior="Expliquer oral par la bouche"
                if lexical
                else "Présenter nom, âge et motif donnés, sans ajout",
                accepted_answers=("Durch den Mund.", "Über den Mund.")
                if lexical
                else (
                    "Ich stelle Alex Beispiel vor. Alex ist 40 Jahre alt "
                    "und kommt für eine Kommunikationsübung.",
                    "Alex Beispiel ist 40 Jahre alt und kommt für eine Kommunikationsübung.",
                ),
            ),
            PracticeCriterion(
                id="second",
                dimension=dimension,
                weight=1.0,
                expected_behavior="Expliquer bilateral par des deux côtés"
                if lexical
                else "Signaler explicitement l'absence d'information sur les médicaments",
                accepted_answers=("Auf beiden Seiten.", "Beidseitig.")
                if lexical
                else (
                    "Zu Medikamenten liegen keine Angaben vor.",
                    "Zur Medikation liegen keine Angaben vor.",
                ),
            ),
        )
        questions = (
            PracticeQuestion(
                id="first",
                kind="term_definition" if lexical else "presentation",
                prompt_de="Was bedeutet oral in einfachen Worten?"
                if lexical
                else "Stellen Sie mir die Testperson kurz vor: Name, Alter und Anlass.",
                coaching_fr="Utilise une expression allemande courante."
                if lexical
                else "Commence par « Ich stelle … vor ». Reste dans les informations du contexte.",
                assessment_item_id="first",
                term_id="oral" if lexical else None,
            ),
            PracticeQuestion(
                id="second",
                kind="term_definition" if lexical else "followup",
                prompt_de="Was bedeutet bilateral in einfachen Worten?"
                if lexical
                else "Vielen Dank. Welche Angaben zur Medikation liegen vor?",
                coaching_fr="Indique les côtés concernés."
                if lexical
                else "Une information manquante n'est pas une négation. "
                "Signale qu'elle n'est pas documentée.",
                assessment_item_id="second",
                term_id="bilateral" if lexical else None,
            ),
        )
        scenario = TrainingScenarioVersion(
            id=f"ari-demo-{phase}",
            version="1",
            case=VersionRef(id=case.id, version="1"),
            case_hash=case.content_hash,
            rubric=VersionRef(id=rubric.id, version="1"),
            rubric_hash=rubric.content_hash,
            terminology=VersionRef(id=terms.id, version="1"),
            terminology_hash=terms.content_hash,
            phase="fachbegriffe" if lexical else "arzt_arzt",
            persona="Médecin simulé, dialogue scripté",
            difficulty="démonstration",
            cefr="B2",
            opening="Beginnen wir mit der Übung.",
            objectives=("Tester le parcours, sans certification de niveau",),
            duration_minutes=5,
            practice=PracticeSpecification(
                context_fact_ids=() if lexical else tuple(f.id for f in facts),
                questions=questions,
                assessment_items=criteria,
            ),
        )
        result.append(
            PracticeContent(
                bundle=ClinicalBundle(
                    sources=(source,),
                    rubrics=(rubric,),
                    terminology_sets=(terms,),
                    cases=(case,),
                    scenarios=(scenario,),
                ),
                scenario_id=scenario.id,
                scenario_version=scenario.version,
                provenance="synthetic_demo",
            )
        )
    return tuple(result)
