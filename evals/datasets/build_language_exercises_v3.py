"""Builds `language-exercises@3`: the same measurement plus a family of defects it missed.

Usage, from the repository root:
    PYTHONPATH=backend/src .venv/bin/python evals/datasets/build_language_exercises_v3.py [out_dir]

Why a third version. The only run of the version 2 holdout (4 November 2026, run 41805202714)
failed the gate on one false acceptance: in `e-h05-choice-cloze:no_correct`, the keyed answer was
"angeschlagt", a misspelling of "angeschlagen", and the checks accepted it once out of three
passes. That holdout is burnt: it is read here as development data, nothing in it is used to judge
the new checks.

What changed.
1. The development split is the whole of version 2 (both of its splits, 28 families), so the
   regression stays: `e-h05` is still there, with every other defect of version 2.
2. A new defect, `near_miss_key`: the keyed answer is understandable but misspelt or wrongly
   inflected (a letter missing, added or swapped; a truncated ending). It is derived for every
   fill-in and correction exercise by the table `NEAR_MISS` below, one string per family. The
   good exercise of the family is its counter-example, as for every other defect. Register
   exercises have no such defect.
3. The holdout is new: 14 good exercises (families `e-k01` to `e-k14`) and 2 medically absurd
   ones, written after the checks of version 2 were fixed and before they were run on them. No
   verdict of the real models was read while writing them.

The German was written by the agent and has not been read by a German speaker; the manifest says
so. The output is deterministic and a test rebuilds it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build_language_exercises_v1 import (  # noqa: E402
    AGENT,
    Base,
    base,
    candidate,
    jsonl,
    row,
    variants,
)
from build_language_exercises_v2 import (  # noqa: E402
    DEV_ABSURD as V2_DEV_ABSURD,
)
from build_language_exercises_v2 import (  # noqa: E402
    DEV_BASES as V2_DEV_BASES,
)
from build_language_exercises_v2 import (  # noqa: E402
    HOLDOUT_ABSURD as V2_HOLDOUT_ABSURD,
)
from build_language_exercises_v2 import (  # noqa: E402
    HOLDOUT_BASES as V2_HOLDOUT_BASES,
)

CREATED_ON = "2026-11-04"

# family -> the keyed answer misspelt: the text that replaces the key (the whole sentence for a
# correction exercise). It must be understandable, wrong in its form, and not a word of its own.
NEAR_MISS: dict[str, str] = {
    "e-d01-gender-cloze": "dei",
    "e-d02-case-correction": "Ich danke dem Azrt für die Hilfe.",
    "e-d03-verb-cloze": "isd",
    "e-d04-order-correction": "Ich gehe zur Arbeit, obwohl ich Fiber habe.",
    "e-d05-choice-cloze": "durchgefürt",
    "e-d06-case-cloze": "dm",
    "e-d07-verb-cloze": "hättte",
    "e-d08-order-correction": (
        "Der Patient hatte seit Tagen hohes Fieber. Dennoch erschein er pünktlich zur Kontrolle."
    ),
    "e-d10-case-cloze": "dre",
    "e-d11-verb-cloze": "operirte",
    "e-d12-choice-cloze": "erhärtt",
    "e-d13-order-correction": "Der Oberarzt gab an, er habe die Dosis nicht erhöt.",
    "e-h01-gender-cloze": "einne",
    "e-h02-case-correction": "Ich war gestern bei meinem Hausartz.",
    "e-h03-verb-cloze": "haat",
    "e-h04-order-correction": "Der Arzt sagt, dass ich mehr trinken mus.",
    "e-h05-choice-cloze": "angeschlagenn",
    "e-h06-case-cloze": "dsa",
    "e-h07-verb-cloze": "operirt",
    "e-h08-order-correction": "Nachdem die Patientin geschlafn hatte, ging es ihr besser.",
    "e-h10-case-cloze": "einner",
    "e-h11-verb-cloze": "hätet",
    "e-h12-choice-cloze": "lengen",
    "e-h13-order-correction": (
        "Kaum hatte der Patient das Medikament genomen, setzte die Wirkung ein."
    ),
    "e-k01-gender-cloze": "einne",
    "e-k02-case-correction": "Der Arzt erklärt der Patientin die Behandlng.",
    "e-k03-verb-cloze": "hta",
    "e-k04-order-correction": (
        "Bevor der Patient nach Hause geht, muss er das Formular unterscheiben."
    ),
    "e-k05-choice-cloze": "einzunemen",
    "e-k06-case-cloze": "dern",
    "e-k07-verb-cloze": "häte",
    "e-k08-order-correction": (
        "Der Arzt, der den Patienten untersucht hat, vermutet, dass eine Infektoin vorliegt."
    ),
    "e-k10-case-cloze": "dse",
    "e-k11-verb-cloze": "impfne",
    "e-k12-choice-cloze": "hni",
    "e-k13-order-correction": (
        "Der Patient bedauert, dass er die Behandlung nicht hat fortsetzen könen."
    ),
}

# The good exercises of the new holdout. Each wrong option is wrong without a context to rescue it,
# each fill-in stem leaves one defensible word, and the level is the one of the grammar point.
NEW_HOLDOUT_BASES: list[Base] = [
    base(
        "e-k01-gender-cloze",
        "B1",
        "gender",
        "cloze",
        stem="Die Ärztin empfiehlt mir ___ Salbe gegen den Juckreiz.",
        key="eine",
        distractors=["einen", "ein"],
        explanation_fr="« Salbe » est féminin : à l'accusatif, l'article indéfini féminin est « eine ».",
        setting="prescription",
        second_correct="die",
        wrong_variant="einem",
        bad_explanation="« Salbe » est masculin : à l'accusatif, l'article indéfini masculin est « einen ».",
        other_category="verb_form",
    ),
    base(
        "e-k02-case-correction",
        "B1",
        "case",
        "correct_sentence",
        stem="Der Arzt erklärt den Patientin die Behandlung.",
        key="Der Arzt erklärt der Patientin die Behandlung.",
        distractors=[
            "Der Arzt erklärt die Patientin die Behandlung.",
            "Der Arzt erklärt der Patientinnen die Behandlung.",
        ],
        explanation_fr="« erklären » se construit avec la personne au datif : « der Patientin » (féminin singulier).",
        setting="consultation",
        second_correct="Der Arzt erklärt der Patientin die Behandlung ausführlich.",
        wrong_variant="Der Arzt erklärt der Patientin der Behandlung.",
        bad_explanation="« erklären » se construit avec la personne à l'accusatif : « die Patientin ».",
        other_category="verb_form",
    ),
    base(
        "e-k03-verb-cloze",
        "B1",
        "verb_form",
        "cloze",
        stem="Die Patientin ___ seit gestern nichts mehr gegessen.",
        key="hat",
        distractors=["ist", "wird"],
        explanation_fr="« essen » est un verbe transitif : son parfait se forme avec « haben », « hat gegessen ».",
        setting="anamnèse",
        second_correct=None,
        wrong_variant="haben",
        bad_explanation="« essen » forme son parfait avec « sein » : « ist gegessen ».",
        other_category="case",
    ),
    base(
        "e-k04-order-correction",
        "B1",
        "word_order",
        "correct_sentence",
        stem="Bevor der Patient geht nach Hause, muss er das Formular unterschreiben.",
        key="Bevor der Patient nach Hause geht, muss er das Formular unterschreiben.",
        distractors=[
            "Bevor geht der Patient nach Hause, muss er das Formular unterschreiben.",
            "Bevor der Patient nach Hause geht, er muss das Formular unterschreiben.",
        ],
        explanation_fr="Dans une subordonnée introduite par « bevor », le verbe conjugué se place en fin de proposition.",
        setting="sortie",
        second_correct="Bevor der Patient endlich nach Hause geht, muss er das Formular unterschreiben.",
        wrong_variant="Bevor der Patient nach Hause geht, muss er das Formular unterschreibt.",
        bad_explanation="Dans une subordonnée introduite par « bevor », le verbe conjugué reste en deuxième position.",
        other_category="verb_form",
    ),
    base(
        "e-k05-choice-cloze",
        "B2",
        "word_choice",
        "cloze",
        stem="Der Arzt rät dem Patienten, das Medikament regelmäßig ___, auch wenn die Beschwerden abklingen.",
        key="einzunehmen",
        distractors=["einzuschalten", "einzuzäunen"],
        explanation_fr="« ein Medikament einnehmen » est la collocation usuelle pour prendre un médicament.",
        setting="conseil thérapeutique",
        second_correct="anzuwenden",
        wrong_variant="einzunehmend",
        bad_explanation="« einnehmen » ne s'emploie que pour des troupes ou des places et jamais pour un médicament.",
        other_category="case",
    ),
    base(
        "e-k06-case-cloze",
        "B2",
        "case",
        "cloze",
        stem="Die Patientin, ___ Befunde ich Ihnen gestern geschickt habe, ist heute wieder in unserer Praxis.",
        key="deren",
        distractors=["dessen", "die"],
        explanation_fr="Le pronom relatif au génitif reprend « die Patientin » (féminin singulier) : « deren ».",
        setting="suivi",
        second_correct=None,
        wrong_variant="derer",
        bad_explanation="Le pronom relatif est ici au datif féminin : « der ».",
        other_category="verb_form",
    ),
    base(
        "e-k07-verb-cloze",
        "B2",
        "verb_form",
        "cloze",
        stem="Wäre der Patient nicht erkältet gewesen, ___ der Chirurg den Eingriff nicht verschoben.",
        key="hätte",
        distractors=["wäre", "würde"],
        explanation_fr="Condition irréelle du passé : subjonctif II de « haben » et participe passé, « hätte … verschoben ».",
        setting="analyse de cas",
        second_correct=None,
        wrong_variant="hätten",
        bad_explanation="Pour une condition irréelle du passé, on emploie « sein » au subjonctif II : « wäre … verschoben ».",
        other_category="word_choice",
    ),
    base(
        "e-k08-order-correction",
        "B2",
        "word_order",
        "correct_sentence",
        stem="Der Arzt, der hat den Patienten untersucht, vermutet, dass eine Infektion vorliegt.",
        key="Der Arzt, der den Patienten untersucht hat, vermutet, dass eine Infektion vorliegt.",
        distractors=[
            "Der Arzt, der den Patienten untersucht hat, vermutet, dass vorliegt eine Infektion.",
            "Der Arzt, der den Patienten untersucht hat, vermutet, dass eine Infektion liegt vor.",
        ],
        explanation_fr="Dans une subordonnée relative, le verbe conjugué (ici l'auxiliaire « hat ») se place en fin de proposition, après le participe.",
        setting="visite",
        second_correct="Der Arzt, der den Patienten gründlich untersucht hat, vermutet, dass eine Infektion vorliegt.",
        wrong_variant="Der Arzt, der den Patienten untersucht hat, vermutet, dass eine Infektion vorligt.",
        bad_explanation="Dans une subordonnée relative, le verbe conjugué reste en deuxième position.",
        other_category="verb_form",
    ),
    base(
        "e-k09-register-choice",
        "B2",
        "register",
        "register_choice",
        context_fr="Vous téléphonez à un médecin traitant pour lui demander les résultats d'examen d'un patient.",
        stem="Hallo, ich brauch mal schnell die Werte vom Müller, geht das?",
        key="Guten Tag, ich rufe wegen der Laborwerte von Herrn Müller an. Könnten Sie mir diese bitte übermitteln?",
        distractors=[
            "Hey, schick mir mal die Werte vom Müller rüber, ich bin in Eile.",
            "Hiermit ergeht an Sie die förmliche Aufforderung, die Laborwerte des Herrn Müller unverzüglich zu übermitteln.",
        ],
        explanation_fr="Entre médecins, au téléphone, on reste poli et précis : ni familier et pressant, ni impérieux et administratif.",
        setting="échange entre médecins",
        second_correct="Guten Tag, hier spricht Dr. Weber. Ich benötige die aktuellen Laborwerte von Herrn Müller; könnten Sie diese bitte schicken?",
        wrong_variant="Gib mir die Werte von Müller.",
        bad_explanation="Entre collègues, on se tutoie et on emploie un ton très direct pour gagner du temps.",
        other_category=None,
    ),
    base(
        "e-k10-case-cloze",
        "B2",
        "case",
        "cloze",
        stem="Aufgrund ___ hohen Blutdrucks wurde die Operation vorerst zurückgestellt.",
        key="des",
        distractors=["den", "dem"],
        explanation_fr="« aufgrund » est suivi du génitif ; « Blutdruck » est masculin : au génitif singulier, l'article défini est « des ».",
        setting="chirurgie",
        second_correct="dieses",
        wrong_variant="dess",
        bad_explanation="« aufgrund » est suivi de l'accusatif ; « Blutdruck » est masculin : l'article défini est « den ».",
        other_category="verb_form",
    ),
    base(
        "e-k11-verb-cloze",
        "B2",
        "verb_form",
        "cloze",
        stem="Der Patient hat sich nicht ___ lassen, obwohl man ihm dringend dazu geraten hatte.",
        key="impfen",
        distractors=["impfend", "geimpft"],
        explanation_fr="Avec « lassen » à l'infinitif, le verbe qui accompagne est lui aussi à l'infinitif : « sich impfen lassen », et « lassen » garde l'infinitif au parfait.",
        setting="prévention",
        second_correct=None,
        wrong_variant="impfte",
        bad_explanation="Avec « lassen », on emploie toujours le participe passé : « geimpft ».",
        other_category="case",
    ),
    base(
        "e-k12-choice-cloze",
        "C1",
        "word_choice",
        "cloze",
        stem="Die Ärztin wies den Patienten ausdrücklich darauf ___, dass er das Medikament nicht mit Alkohol kombinieren dürfe.",
        key="hin",
        distractors=["auf", "an"],
        explanation_fr="« jemanden auf etwas hinweisen » : le préverbe se sépare et « hin » se place en fin de proposition principale.",
        setting="information du patient",
        second_correct=None,
        wrong_variant="hien",
        bad_explanation="« hinweisen » est un verbe inséparable : on dirait « darauf hinwies ».",
        other_category="verb_form",
    ),
    base(
        "e-k13-order-correction",
        "C1",
        "word_order",
        "correct_sentence",
        stem="Der Patient bedauert, dass er die Behandlung nicht fortsetzen können hat.",
        key="Der Patient bedauert, dass er die Behandlung nicht hat fortsetzen können.",
        distractors=[
            "Der Patient bedauert, dass er die Behandlung nicht hat fortgesetzt können.",
            "Der Patient bedauert, dass hat er die Behandlung nicht fortsetzen können.",
        ],
        explanation_fr="Au parfait d'un verbe modal, le participe est remplacé par un infinitif ; dans la subordonnée, l'auxiliaire conjugué passe devant les deux infinitifs : « hat fortsetzen können ».",
        setting="suivi",
        second_correct="Der Patient bedauert, dass er die Behandlung leider nicht hat fortsetzen können.",
        wrong_variant="Der Patient bedauert, dass er die Behandlung nicht hat fortsetzen könnt.",
        bad_explanation="Dans une subordonnée au parfait avec un modal, l'auxiliaire se place toujours en toute dernière position.",
        other_category="verb_form",
    ),
    base(
        "e-k14-register-choice",
        "C1",
        "register",
        "register_choice",
        context_fr="Vous rédigez la lettre de sortie adressée au médecin traitant d'un patient.",
        stem="Der Patient war ziemlich fertig, hat sich dann aber wieder berappelt.",
        key="Der Patient war initial in reduziertem Allgemeinzustand und hat sich im Verlauf stabilisiert.",
        distractors=[
            "Dem armen Kerl ging es erst richtig mies, aber dann ist er zum Glück wieder auf die Beine gekommen.",
            "Es wird mitgeteilt, dass sich seitens des Patienten zunächst eine Schwächung des Zustandes manifestierte, welche im weiteren Verlauf behoben wurde.",
        ],
        explanation_fr="Une lettre de sortie est écrite dans un registre médical factuel et concis : ni familier, ni excessivement administratif.",
        setting="lettre de sortie",
        second_correct="Der Patient war zu Beginn in reduziertem Allgemeinzustand; im Verlauf besserte sich sein Zustand.",
        wrong_variant="Dem Typen ging's erst mies.",
        bad_explanation="Dans une lettre de sortie, on emploie volontairement un registre familier pour que le médecin traitant se sente proche du patient.",
        other_category=None,
    ),
]

NEW_HOLDOUT_ABSURD: list[Base] = [
    base(
        "e-k15-absurd",
        "B1",
        "case",
        "cloze",
        stem="Gegen seinen Husten bekommt der Patient eine Salbe für ___ großen Zeh.",
        key="den",
        distractors=["dem", "der"],
        explanation_fr="« für » est suivi de l'accusatif ; « Zeh » est masculin : « den ».",
        setting="traitement",
    ),
    base(
        "e-k15-absurd",
        "B2",
        "gender",
        "cloze",
        stem="Wegen eines Haarrisses im Knöchel wird dem Patienten ___ Zahn gezogen.",
        key="ein",
        distractors=["einen", "eine"],
        explanation_fr="« Zahn » est masculin et sujet du passif : au nominatif, l'article indéfini est « ein ».",
        setting="traitement",
    ),
]


def with_near_misses(bases: list[Base]) -> list[dict]:
    """The rows of every base, and the near-miss copy of those that have one."""
    rows: list[dict] = []
    for b in bases:
        rows.extend(variants(b))
        if b["family"] in NEAR_MISS:
            rows.append(
                row(b, "near_miss_key", candidate(b, key=NEAR_MISS[b["family"]]), 0)
            )
    return rows


def build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    dev_bases = [*V2_DEV_BASES, *V2_HOLDOUT_BASES]
    dev_absurd = [*V2_DEV_ABSURD, *V2_HOLDOUT_ABSURD]
    for b in dev_bases + NEW_HOLDOUT_BASES:
        if b["format"] != "register_choice":
            assert b["family"] in NEAR_MISS, f"no near miss for {b['family']}"
    dev = with_near_misses(dev_bases)
    dev += [
        row(b, "medical_absurd", candidate(b), n)
        for n, b in enumerate(dev_absurd, start=1)
    ]
    holdout = with_near_misses(NEW_HOLDOUT_BASES)
    holdout += [
        row(b, "medical_absurd", candidate(b), n)
        for n, b in enumerate(NEW_HOLDOUT_ABSURD, start=1)
    ]
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
        "version": "3",
        "description": (
            "Exercices de langue dont le défaut est connu par construction : 42 exercices "
            "corrects écrits à la main (28 en développement, repris de la version 2, et 14 "
            "nouveaux en holdout) et leurs variantes défectueuses (mauvaise clé, deux réponses "
            "justes, aucune réponse juste, réponse presque juste, explication fausse, mauvaise "
            "catégorie, mauvais niveau, options en double, format cassé, décor médical absurde)."
        ),
        "synthetic": True,
        "source": "Exercices inventés par l'agent, générés par evals/datasets/build_language_exercises_v3.py",
        "created_on": CREATED_ON,
        "consent": "not_applicable:synthetic",
        "annotators": [{**AGENT, "annotated_on": CREATED_ON}],
        "splits": {
            "dev": [b["family"] for b in dev_bases] + ["e-d15-absurd", "e-h15-absurd"],
            "holdout": [b["family"] for b in NEW_HOLDOUT_BASES] + ["e-k15-absurd"],
        },
        "files": files,
        "limitations": [
            "L'allemand a été écrit par l'agent et n'a pas été relu par une personne germanophone : une base peut elle-même être discutable, et un item « valide » refusé est à regarder avant de conclure que le contrôle se trompe.",
            "Quatorze bases valides en holdout : un exercice juste refusé vaut 7 points de fausse rejection ; un zéro fausse acceptation sur ce jeu n'est pas une garantie.",
            "Le développement contient les deux splits de la version 2, dont le holdout a été lancé une fois (échec sur `e-h05`) : il ne sert plus de test indépendant. Le holdout de la version 3 est neuf et n'a été lu par aucun modèle avant le gel des contrôles.",
            "Les défauts sont fabriqués à partir d'exercices corrects ; les fautes subtiles qu'un modèle auteur produit vraiment y sont moins bien représentées. Les fautes de frappe du défaut `near_miss_key` sont choisies une par famille.",
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "language-exercises" / "3")
