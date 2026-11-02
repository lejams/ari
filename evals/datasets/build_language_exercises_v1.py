"""Builds `language-exercises@1`: exercises whose defects are known by construction.

Usage, from the repository root:
    PYTHONPATH=backend/src .venv/bin/python evals/datasets/build_language_exercises_v1.py [out_dir]

The suite measures the checks that stand between the author model and the bank: the mechanical
checks, the blind solver and the rubric judge. An item is an exercise and the truth about it:
either it is good (the pipeline must accept it) or it carries one defect (the pipeline must
refuse it). The main metric is the false acceptance rate, defective exercises that got through.

Each of the 16 bases below is a correct exercise written by hand, with three extra strings
(a second correct answer, a wrong answer, a false explanation). The code derives the defective
variants from it, so the defect is known and is the only thing that changed:

- wrong_key: the declared answer is a distractor, the real answer sits among the distractors;
- two_correct: one distractor is replaced by a second acceptable answer;
- no_correct: the correct option is replaced by a wrong one, and declared as the answer;
- bad_explanation: the French explanation states a false rule;
- wrong_category: the exercise is labelled with a category it does not train;
- level_mismatch: a B1 exercise labelled C1, or a C1 exercise labelled B1;
- duplicate_options: the key is repeated as a distractor (mechanical);
- bad_format: no blank in a cloze, nothing to correct in a correction (mechanical);
- medical_absurd: a medically absurd setting in an otherwise correct exercise (4 hand-written).

Everything is invented. The German was written by the agent and has not been read by a
German speaker: the manifest says so. The output is deterministic and a test rebuilds it: the
committed files are never edited by hand.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from ari.domain.language_exercises import ExerciseCandidate, assemble

HERE = Path(__file__).resolve().parent
CREATED_ON = "2026-11-02"
AGENT = {
    "id": "agent:claude-sonnet-5.5",
    "role": "rédaction synthétique",
    "annotated_on": CREATED_ON,
}

# family, level, category, format, context_fr, stem, key, distractors, explanation_fr, setting,
# then: second_correct, wrong_variant, bad_explanation, other_category (None: skip that defect).
Base = dict[str, Any]


def base(family: str, level: str, category: str, fmt: str, **fields: Any) -> Base:
    return {
        "family": family,
        "level": level,
        "category": category,
        "format": fmt,
        **fields,
    }


DEV_BASES: list[Base] = [
    base(
        "d1-gender-cloze",
        "B1",
        "gender",
        "cloze",
        stem="Haben Sie ___ Tablette heute schon genommen?",
        key="die",
        distractors=["den", "das"],
        explanation_fr="« Tablette » est féminin : à l'accusatif, l'article défini féminin est « die ».",
        setting="anamnèse",
        second_correct="eine",
        wrong_variant="dem",
        bad_explanation="« Tablette » est neutre : à l'accusatif, l'article défini neutre est « die ».",
        other_category="case",
    ),
    base(
        "d2-case-correction",
        "B1",
        "case",
        "correct_sentence",
        stem="Ich habe mit den Arzt gesprochen.",
        key="Ich habe mit dem Arzt gesprochen.",
        distractors=[
            "Ich habe mit der Arzt gesprochen.",
            "Ich habe mit dem Arztes gesprochen.",
        ],
        explanation_fr="« mit » est toujours suivi du datif : « dem Arzt » (masculin singulier).",
        setting="entretien",
        second_correct="Ich habe mit dem Doktor gesprochen.",
        wrong_variant="Ich habe mit dem Arzt gesprechen.",
        bad_explanation="« mit » est suivi de l'accusatif, donc « den Arzt » est correct.",
        other_category="verb_form",
    ),
    base(
        "d3-verb-cloze",
        "B1",
        "verb_form",
        "cloze",
        stem="Gestern ___ ich starke Kopfschmerzen.",
        key="hatte",
        distractors=["habe", "hattet"],
        explanation_fr="Avec « gestern », on met le verbe au prétérit (« hatte »), ici à la 1re personne du singulier.",
        setting="anamnèse",
        second_correct="bekam",
        wrong_variant="hatten",
        bad_explanation="Avec « gestern », on emploie obligatoirement le présent (« habe »).",
        other_category="word_choice",
    ),
    base(
        "d4-order-correction",
        "B2",
        "word_order",
        "correct_sentence",
        stem="Ich weiß nicht, ob er hat Fieber.",
        key="Ich weiß nicht, ob er Fieber hat.",
        distractors=[
            "Ich weiß nicht, ob Fieber er hat.",
            "Ich weiß nicht, ob hat er Fieber.",
        ],
        explanation_fr="Dans une subordonnée introduite par « ob », le verbe conjugué est rejeté en fin de proposition.",
        setting="anamnèse",
        second_correct="Ich weiß nicht, ob er denn Fieber hat.",
        wrong_variant="Ich weiß nicht, ob er Fieber hatte gehabt.",
        bad_explanation="Dans une subordonnée introduite par « ob », le verbe conjugué se place en deuxième position.",
        other_category="verb_form",
    ),
    base(
        "d5-choice-cloze",
        "B2",
        "word_choice",
        "cloze",
        stem="Die Patientin muss das Medikament dreimal täglich ___.",
        key="einnehmen",
        distractors=["anziehen", "mitnehmen"],
        explanation_fr="On « nimmt ein » un médicament : « einnehmen » est le verbe usuel pour prendre un médicament.",
        setting="prescription",
        second_correct="schlucken",
        wrong_variant="annehmen",
        bad_explanation="« einnehmen » signifie « enfiler un vêtement » et ne s'emploie donc pas avec un médicament.",
        other_category="verb_form",
    ),
    base(
        "d6-register-choice",
        "B2",
        "register",
        "register_choice",
        context_fr="Vous parlez à une patiente âgée que vous ne connaissez pas.",
        stem="Hast du heute Morgen schon was gegessen?",
        key="Haben Sie heute Morgen schon etwas gegessen?",
        distractors=[
            "Hast du heute Morgen schon etwas gegessen?",
            "Na, schon was geknabbert heute Morgen?",
        ],
        explanation_fr="À une patiente inconnue, on dit « Sie » et on garde un ton neutre et poli.",
        setting="accueil",
        second_correct="Haben Sie heute Morgen schon etwas zu sich genommen?",
        wrong_variant="Habt ihr heute Morgen schon etwas gegessen?",
        bad_explanation="À une patiente âgée inconnue, on emploie « du » pour montrer de la proximité.",
        other_category=None,
    ),
    base(
        "d7-case-cloze",
        "C1",
        "case",
        "cloze",
        stem="Aufgrund ___ anhaltenden Beschwerden wurde der Patient stationär aufgenommen.",
        key="der",
        distractors=["den", "dem"],
        explanation_fr="« aufgrund » est suivi du génitif ; au génitif pluriel, l'article défini est « der ».",
        setting="admission",
        second_correct="seiner",
        wrong_variant="des",
        bad_explanation="« aufgrund » est suivi du datif ; au datif pluriel, l'article défini est « den ».",
        other_category="gender",
    ),
    base(
        "d8-verb-correction",
        "C1",
        "verb_form",
        "correct_sentence",
        stem="Hätte ich mehr Zeit gehabt, hätte ich Sie gründlicher untersuchen.",
        key="Hätte ich mehr Zeit gehabt, hätte ich Sie gründlicher untersucht.",
        distractors=[
            "Hätte ich mehr Zeit gehabt, hätte ich Sie gründlicher untersuchte.",
            "Hätte ich mehr Zeit gehabt, wäre ich Sie gründlicher untersucht.",
        ],
        explanation_fr="Le plus-que-parfait du subjonctif se forme avec « hätte » et le participe passé : « untersucht ».",
        setting="consultation",
        second_correct="Hätte ich mehr Zeit gehabt, hätte ich Sie gründlicher untersuchen können.",
        wrong_variant="Hätte ich mehr Zeit gehabt, hätte ich Sie gründlicher untersucht worden.",
        bad_explanation="Le plus-que-parfait du subjonctif se forme avec « würde » et le participe passé.",
        other_category="word_order",
    ),
]

HOLDOUT_BASES: list[Base] = [
    base(
        "h1-gender-cloze",
        "B1",
        "gender",
        "cloze",
        stem="Ich habe ___ Rezept leider vergessen.",
        key="das",
        distractors=["die", "den"],
        explanation_fr="« Rezept » est neutre : à l'accusatif, l'article défini neutre est « das ».",
        setting="pharmacie",
        second_correct="mein",
        wrong_variant="dem",
        bad_explanation="« Rezept » est féminin : à l'accusatif, l'article défini est donc « die ».",
        other_category="case",
    ),
    base(
        "h2-case-correction",
        "B2",
        "case",
        "correct_sentence",
        stem="Er wartet auf seinem Befund.",
        key="Er wartet auf seinen Befund.",
        distractors=["Er wartet auf seiner Befund.", "Er wartet für seinen Befund."],
        explanation_fr="« warten auf » se construit avec l'accusatif : « auf seinen Befund ».",
        setting="attente de résultats",
        second_correct="Er wartet auf den Befund.",
        wrong_variant="Er wartet auf seinen Befunde.",
        bad_explanation="« warten auf » se construit avec le datif, d'où « auf seinem Befund ».",
        other_category="gender",
    ),
    base(
        "h3-verb-cloze",
        "B2",
        "verb_form",
        "cloze",
        stem="Der Patient ist gestern Abend plötzlich ___.",
        key="gestürzt",
        distractors=["gesturzt", "stürzte"],
        explanation_fr="Le participe passé de « stürzen » est « gestürzt », avec l'auxiliaire « sein » : « ist gestürzt ».",
        setting="urgences",
        second_correct="gefallen",
        wrong_variant="gestürzte",
        bad_explanation="Le verbe « stürzen » forme son parfait avec « haben », donc « hat gestürzt ».",
        other_category="word_choice",
    ),
    base(
        "h4-order-correction",
        "B1",
        "word_order",
        "correct_sentence",
        stem="Gestern ich habe Fieber gehabt.",
        key="Gestern habe ich Fieber gehabt.",
        distractors=[
            "Gestern habe Fieber ich gehabt.",
            "Gestern ich Fieber habe gehabt.",
        ],
        explanation_fr="Le verbe conjugué est toujours en deuxième position : après « gestern », il précède le sujet.",
        setting="anamnèse",
        second_correct="Gestern hatte ich Fieber.",
        wrong_variant="Gestern habe ich gehabt Fieber.",
        bad_explanation="Après « gestern » en début de phrase, le sujet reste en première position et le verbe vient après.",
        other_category="verb_form",
    ),
    base(
        "h5-choice-cloze",
        "B1",
        "word_choice",
        "cloze",
        stem="Ich möchte einen ___ bei Doktor Weber vereinbaren.",
        key="Termin",
        distractors=["Beruf", "Ausweis"],
        explanation_fr="On « vereinbart » un rendez-vous : « einen Termin vereinbaren » est l'expression usuelle.",
        setting="prise de rendez-vous",
        second_correct="Untersuchungstermin",
        wrong_variant="Ausflug",
        bad_explanation="« Termin » est neutre en allemand, donc l'article correct serait « ein ».",
        other_category="gender",
    ),
    base(
        "h6-register-choice",
        "C1",
        "register",
        "register_choice",
        context_fr="Vous signalez en une phrase l'état d'un patient à un collègue, lors de la relève.",
        stem="Also, der Typ im Zimmer 4 sieht echt schlecht aus, kannst du mal gucken?",
        key="Der Patient in Zimmer 4 wirkt reduziert; könntest du dir ihn bitte ansehen?",
        distractors=[
            "Der Patient in Zimmer 4 befindet sich in einem Zustand, der Ihrerseits umgehend zur Kenntnis zu nehmen wäre.",
            "Hey, der Typ in 4 sieht übel aus, schau mal rein.",
        ],
        explanation_fr="Entre collègues, le ton reste professionnel mais direct : « du », vocabulaire clinique, sans familiarité ni lourdeur.",
        setting="relève",
        second_correct="Der Patient in Zimmer 4 macht mir Sorgen; kannst du bitte nach ihm sehen?",
        wrong_variant="Hey, der Patient in Zimmer 4 sieht echt mies aus, gucken Sie mal!",
        bad_explanation="Entre collègues, à la relève, on utilise exclusivement la forme de politesse « Sie ».",
        other_category=None,
    ),
    base(
        "h7-case-cloze",
        "B2",
        "case",
        "cloze",
        stem="Der Arzt spricht mit ___ Eltern des Patienten.",
        key="den",
        distractors=["dem", "der"],
        explanation_fr="« mit » est suivi du datif ; au datif pluriel, l'article défini est « den ».",
        setting="entretien",
        second_correct="beiden",
        wrong_variant="des",
        bad_explanation="« mit » est suivi du génitif ; au génitif pluriel, l'article défini est « der ».",
        other_category="gender",
    ),
    base(
        "h8-verb-cloze",
        "C1",
        "verb_form",
        "cloze",
        stem="Wenn der Patient früher gekommen ___, wäre die Operation nicht nötig gewesen.",
        key="wäre",
        distractors=["hätte", "würde"],
        explanation_fr="« kommen » forme son plus-que-parfait avec « sein » : au subjonctif II, « wäre gekommen ».",
        setting="anamnèse",
        second_correct=None,
        wrong_variant="ist",
        bad_explanation="Le verbe « kommen » forme le plus-que-parfait du subjonctif avec « haben ».",
        other_category="word_choice",
    ),
]

# Correct German, medically absurd (the coarse plausibility check): level, category, format, ...
DEV_ABSURD: list[Base] = [
    base(
        "d9-absurd",
        "B1",
        "case",
        "cloze",
        stem="Die Patientin klagt über Zahnschmerzen in ___ linken Ellenbogen.",
        key="dem",
        distractors=["den", "der"],
        explanation_fr="« in » avec un lieu où l'on est se construit avec le datif ; masculin singulier : « dem ».",
        setting="anamnèse",
    ),
    base(
        "d9-absurd",
        "B1",
        "gender",
        "cloze",
        stem="Der Arzt verschreibt ___ Patienten ein Schmerzmittel gegen seinen gebrochenen Haarschnitt.",
        key="dem",
        distractors=["den", "der"],
        explanation_fr="« verschreiben » se construit avec le datif pour la personne : « dem Patienten ».",
        setting="prescription",
    ),
]
HOLDOUT_ABSURD: list[Base] = [
    base(
        "h9-absurd",
        "B2",
        "gender",
        "cloze",
        stem="Der Arzt untersucht ___ Blinddarm im Gehirn des Patienten.",
        key="den",
        distractors=["die", "das"],
        explanation_fr="« Blinddarm » est masculin : à l'accusatif, l'article défini masculin est « den ».",
        setting="consultation",
    ),
    base(
        "h9-absurd",
        "B2",
        "case",
        "cloze",
        stem="Die Ärztin röntgt ___ Patienten den Fuß, weil er Halsschmerzen hat.",
        key="dem",
        distractors=["den", "der"],
        explanation_fr="« röntgen » avec une personne et une partie du corps : la personne est au datif, « dem Patienten ».",
        setting="radiologie",
    ),
]

# A category a defective copy can be relabelled with, per base, is in the base itself.
LEVEL_SWAP = {"B1": "C1", "C1": "B1"}


def candidate(b: Base, **changes: Any) -> ExerciseCandidate:
    fields: dict[str, Any] = {
        "level": b["level"],
        "category": b["category"],
        "format": b["format"],
        "stem": b["stem"],
        "key": b["key"],
        "distractors": list(b["distractors"]),
        "explanation_fr": b["explanation_fr"],
        "setting": b["setting"],
        "context_fr": b.get("context_fr"),
    }
    fields.update(changes)
    return assemble(**fields)


def row(
    b: Base, defect: str | None, cand: ExerciseCandidate, number: int
) -> dict[str, Any]:
    return {
        "id": f"{b['family']}:{defect or 'valid'}{number if number else ''}",
        "family": b["family"],
        "candidate": json.loads(cand.model_dump_json()),
        "truth": {"valid": defect is None, "defect": defect},
    }


def variants(b: Base) -> list[dict[str, Any]]:
    rows = [row(b, None, candidate(b), 0)]
    valid = candidate(b)
    wrong_index = next(i for i in range(len(valid.options)) if i != valid.answer_index)
    rows.append(
        row(b, "wrong_key", valid.model_copy(update={"answer_index": wrong_index}), 0),
    )
    if b.get("second_correct"):
        rows.append(
            row(
                b,
                "two_correct",
                candidate(b, distractors=[*b["distractors"][:-1], b["second_correct"]]),
                0,
            )
        )
    # The wrong string replaces the correct one and is declared as the answer.
    rows.append(row(b, "no_correct", candidate(b, key=b["wrong_variant"]), 0))
    rows.append(
        row(b, "bad_explanation", candidate(b, explanation_fr=b["bad_explanation"]), 0)
    )
    if b.get("other_category"):
        rows.append(
            row(b, "wrong_category", candidate(b, category=b["other_category"]), 0)
        )
    if b["level"] in LEVEL_SWAP:
        rows.append(
            row(b, "level_mismatch", candidate(b, level=LEVEL_SWAP[b["level"]]), 0)
        )
    rows.append(
        row(
            b,
            "duplicate_options",
            candidate(b, distractors=[b["key"], *b["distractors"][1:]]),
            0,
        )
    )
    if b["format"] == "cloze":
        broken = candidate(b, stem=b["stem"].replace("___", b["key"]))
    elif b["format"] == "correct_sentence":
        broken = candidate(b, stem=b["key"])
    else:
        broken = candidate(b, context_fr=None)
    rows.append(row(b, "bad_format", broken, 0))
    return rows


def absurd(b: Base, number: int) -> dict[str, Any]:
    return row(b, "medical_absurd", candidate(b), number)


def jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows
    )


def build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    dev = [r for b in DEV_BASES for r in variants(b)]
    dev += [absurd(b, n) for n, b in enumerate(DEV_ABSURD, start=1)]
    holdout = [r for b in HOLDOUT_BASES for r in variants(b)]
    holdout += [absurd(b, n) for n, b in enumerate(HOLDOUT_ABSURD, start=1)]
    for name, rows in (("dev.jsonl", dev), ("holdout.jsonl", holdout)):
        ids = [r["id"] for r in rows]
        assert len(ids) == len(set(ids)), f"duplicate ids in {name}"
        (out / name).write_text(jsonl(rows), encoding="utf-8")
    files = {
        name: hashlib.sha256((out / name).read_bytes()).hexdigest()
        for name in sorted(("dev.jsonl", "holdout.jsonl"))
    }
    manifest = {
        "suite": "language-exercises",
        "version": "1",
        "description": (
            "Exercices de langue dont le défaut est connu par construction : 16 exercices "
            "corrects écrits à la main et leurs variantes défectueuses (mauvaise clé, deux "
            "réponses justes, aucune réponse juste, explication fausse, mauvaise catégorie, "
            "mauvais niveau, options en double, format cassé, décor médical absurde)."
        ),
        "synthetic": True,
        "source": "Exercices inventés par l'agent, générés par evals/datasets/build_language_exercises_v1.py",
        "created_on": CREATED_ON,
        "consent": "not_applicable:synthetic",
        "annotators": [AGENT],
        "splits": {
            "dev": [b["family"] for b in DEV_BASES] + ["d9-absurd"],
            "holdout": [b["family"] for b in HOLDOUT_BASES] + ["h9-absurd"],
        },
        "files": files,
        "limitations": [
            "L'allemand a été écrit par l'agent et n'a pas été relu par une personne germanophone : une base peut elle-même être discutable, et un item « valide » refusé est à regarder avant de conclure que le contrôle se trompe.",
            "Seize bases seulement : le taux de fausse acceptation est mesuré sur peu d'items, avec 3 répétitions chacun ; un zéro n'est pas une garantie, c'est l'absence d'un défaut parmi ceux que le jeu sait fabriquer.",
            "Les défauts sont fabriqués à partir d'exercices corrects ; les fautes subtiles qu'un modèle auteur produit vraiment (une distractrice presque acceptable) y sont moins bien représentées.",
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "language-exercises" / "1")
