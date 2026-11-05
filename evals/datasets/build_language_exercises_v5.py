"""Builds `language-exercises@5`: a new holdout, and a dev that no longer holds two doubtful references.

Usage, from the repository root:
    PYTHONPATH=backend/src python3 evals/datasets/build_language_exercises_v5.py [out_dir]

Why a fifth version. The development measure of 5 November 2026 (run 41999479170, 3 repetitions,
Claude Haiku 5.5 as the checking model) passed every threshold but one: the false acceptance, at
0.004, came from two families only, `e-k12-choice-cloze` (a "wrong category" variant accepted in
one repetition out of three) and `e-k13-order-correction` (a "wrong category" variant accepted in
two repetitions, a "bad explanation" variant in one). Outside them the false acceptance is 0 over
about a thousand runs. The valid exercise of each of these two families is refused by the checks
(by both Sonnet and Haiku for `e-k13`, and by Haiku for `e-k12`), which is read as: their
references are defensible German, so the variants built from them are not clear-cut defects. On the
owner's decision (5 November 2026) they leave the development split. They are kept in version 4,
which never changes.

What changed.
1. The development split is version 4 without those two families, plus the 14 families of the
   version 4 holdout (`e-m01` to `e-m14` and `e-m15-absurd`), burnt by their own gate run.
2. The holdout is new: 14 good exercises (`e-n01` to `e-n14`) and 2 medically absurd ones
   (`e-n15-absurd`), written by the agent without reading any verdict of the real models. They were
   written against the faults found in the earlier references: no distractor that a native speaker
   could accept (no dative after a genitive preposition, no verb-final order after a conjunction
   that allows it, no register option that is only formal), a "wrong category" label that the
   exercise plainly does not train, a stem that says who speaks.
3. The checks, the prompts, the rubric and the thresholds are those of version 4.

The German has still not been read by a German speaker; the manifest says so. The output is
deterministic and a test rebuilds it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build_language_exercises_v1 import (
    AGENT,
    Base,
    base,
    candidate,
    jsonl,
    row,
)
from build_language_exercises_v2 import (
    DEV_ABSURD as V2_DEV_ABSURD,
)
from build_language_exercises_v2 import (
    DEV_BASES as V2_DEV_BASES,
)
from build_language_exercises_v2 import (
    HOLDOUT_ABSURD as V2_HOLDOUT_ABSURD,
)
from build_language_exercises_v2 import (
    HOLDOUT_BASES as V2_HOLDOUT_BASES,
)
from build_language_exercises_v3 import (
    NEW_HOLDOUT_ABSURD as V3_HOLDOUT_ABSURD,
)
from build_language_exercises_v3 import (
    NEW_HOLDOUT_BASES as V3_HOLDOUT_BASES,
)
from build_language_exercises_v4 import (
    HOLDOUT_ABSURD as V4_HOLDOUT_ABSURD,
)
from build_language_exercises_v4 import (
    HOLDOUT_BASES as V4_HOLDOUT_BASES,
)
from build_language_exercises_v4 import (
    NEAR_MISS as V4_NEAR_MISS,
)
from build_language_exercises_v4 import (
    with_near_misses,
)

CREATED_ON = "2026-11-05"

# The two families whose references the checks themselves find defensible (see the docstring).
DROPPED_FAMILIES: frozenset[str] = frozenset(
    {"e-k12-choice-cloze", "e-k13-order-correction"}
)

NEAR_MISS: dict[str, str] = {
    **V4_NEAR_MISS,
    "e-n01-gender-cloze": "dsa",
    "e-n02-case-correction": "Wir warten seit einer Stunde auf den Azrt.",
    "e-n03-verb-cloze": "its",
    "e-n04-order-correction": "Der Patient sagt, dass er seit gestern Fieber hta.",
    "e-n05-case-cloze": "dre",
    "e-n06-verb-cloze": "wäer",
    "e-n07-order-correction": "Weil die Ergebnisse noch nicht vorligen, muss die Patientin warten.",
    "e-n08-choice-cloze": "zugezogn",
    "e-n10-case-cloze": "dme",
    "e-n11-verb-cloze": "verschobn",
    "e-n12-choice-cloze": "abgeklungn",
    "e-n13-order-correction": "Nur wenn der Patient nüchtern ist, darf die Untersuchng beginnen.",
}

HOLDOUT_BASES: list[Base] = [
    base(
        "e-n01-gender-cloze",
        "B1",
        "gender",
        "cloze",
        stem="Die Sprechstundenhilfe reicht dem Patienten ___ Formular.",
        key="das",
        distractors=["der", "die"],
        explanation_fr="« Formular » est neutre : à l'accusatif, l'article défini neutre est « das ». « der » et « die » sont des articles masculin et féminin : ils ne vont pas avec un nom neutre.",
        setting="accueil",
        second_correct="ein",
        wrong_variant="dem",
        bad_explanation="« Formular » est masculin : à l'accusatif, l'article défini masculin est « das ».",
        other_category="verb_form",
    ),
    base(
        "e-n02-case-correction",
        "B1",
        "case",
        "correct_sentence",
        stem="Wir warten seit einer Stunde auf der Arzt.",
        key="Wir warten seit einer Stunde auf den Arzt.",
        distractors=[
            "Wir warten seit einer Stunde auf dem Arzt.",
            "Wir warten seit einer Stunde auf des Arztes.",
        ],
        explanation_fr="« warten auf » est toujours suivi de l'accusatif : « auf den Arzt » (masculin singulier). « dem Arzt » est un datif et « des Arztes » un génitif : ni l'un ni l'autre ne s'emploie avec « warten auf ».",
        setting="salle d'attente",
        second_correct="Wir warten seit einer Stunde auf unseren Arzt.",
        wrong_variant="Wir warten seit einer Stunde für den Arzt.",
        bad_explanation="« warten auf » est suivi du datif : « auf dem Arzt » est donc la forme correcte.",
        other_category="verb_form",
    ),
    base(
        "e-n03-verb-cloze",
        "B1",
        "verb_form",
        "cloze",
        stem="Letzte Woche ___ der Patient mit starken Rückenschmerzen in die Praxis gekommen.",
        key="ist",
        distractors=["hat", "wird"],
        explanation_fr="« kommen » est un verbe de mouvement : son parfait se forme avec « sein », donc « ist gekommen ». « hat gekommen » est incorrect, et « wird gekommen » ne forme aucun temps.",
        setting="consultation",
        second_correct=None,
        wrong_variant="bin",
        bad_explanation="« kommen » forme son parfait avec « haben » : « hat gekommen ».",
        other_category="case",
    ),
    base(
        "e-n04-order-correction",
        "B1",
        "word_order",
        "correct_sentence",
        stem="Der Patient sagt, dass er hat seit gestern Fieber.",
        key="Der Patient sagt, dass er seit gestern Fieber hat.",
        distractors=[
            "Der Patient sagt, dass hat er seit gestern Fieber.",
            "Der Patient sagt, dass er seit gestern hat Fieber.",
        ],
        explanation_fr="Dans une subordonnée introduite par « dass », le verbe conjugué se place en dernière position : « dass er seit gestern Fieber hat ». Dans les deux autres versions, « hat » est placé trop tôt.",
        setting="anamnèse",
        second_correct=None,
        wrong_variant="Der Patient sagt, dass er seit gestern Fieber habt.",
        bad_explanation="Dans une subordonnée introduite par « dass », le verbe conjugué se place en deuxième position.",
        other_category="gender",
    ),
    base(
        "e-n05-case-cloze",
        "B2",
        "case",
        "cloze",
        stem="Die Ärztin, ___ ich vertraue, hat die Befunde gestern besprochen.",
        key="der",
        distractors=["die", "deren"],
        explanation_fr="« vertrauen » se construit avec le datif. Le pronom relatif renvoie à « Ärztin » (féminin singulier) et se met donc au datif féminin : « der ». « die » serait un accusatif et « deren » un génitif : « vertrauen » n'admet ni l'un ni l'autre.",
        setting="suivi",
        second_correct=None,
        wrong_variant="dem",
        bad_explanation="« vertrauen » se construit avec l'accusatif : on dit « die Ärztin, die ich vertraue ».",
        other_category="verb_form",
    ),
    base(
        "e-n06-verb-cloze",
        "B2",
        "verb_form",
        "cloze",
        stem="Wenn der Patient früher gekommen ___, hätte man die Entzündung behandeln können.",
        key="wäre",
        distractors=["hätte", "würde"],
        explanation_fr="« kommen » forme son passé avec « sein » : à l'irréel du passé, on dit « wäre gekommen ». « hätte gekommen » et « würde gekommen » sont incorrects.",
        setting="analyse de cas",
        second_correct=None,
        wrong_variant="wärre",
        bad_explanation="« kommen » forme son passé avec « haben » : à l'irréel du passé, on dit « hätte gekommen ».",
        other_category="case",
    ),
    base(
        "e-n07-order-correction",
        "B2",
        "word_order",
        "correct_sentence",
        stem="Weil die Ergebnisse noch nicht liegen vor, muss die Patientin warten.",
        key="Weil die Ergebnisse noch nicht vorliegen, muss die Patientin warten.",
        distractors=[
            "Weil liegen die Ergebnisse noch nicht vor, muss die Patientin warten.",
            "Weil die Ergebnisse noch nicht vorliegen, die Patientin muss warten.",
        ],
        explanation_fr="Après « weil », le verbe conjugué va à la fin de la subordonnée et le verbe à particule séparable s'y écrit en un seul mot : « vorliegen ». Quand la subordonnée ouvre la phrase, la principale commence par le verbe : « muss die Patientin ».",
        setting="résultats",
        second_correct=None,
        wrong_variant="Weil die Ergebnisse noch nicht vorliegt, muss die Patientin warten.",
        bad_explanation="Après « weil », le verbe conjugué reste en deuxième position et la particule « vor » le suit.",
        other_category="case",
    ),
    base(
        "e-n08-choice-cloze",
        "B2",
        "word_choice",
        "cloze",
        stem="Der Patient hat sich bei der Gartenarbeit eine Prellung ___.",
        key="zugezogen",
        distractors=["angezogen", "vollzogen"],
        explanation_fr="« sich eine Verletzung zuziehen » signifie se blesser : « sich eine Prellung zugezogen ». « angezogen » (de « anziehen ») et « vollzogen » (de « vollziehen ») ne se disent pas d'une blessure.",
        setting="traumatologie",
        second_correct=None,
        wrong_variant="zugehört",
        bad_explanation="« zuziehen » signifie fermer un rideau ; pour une blessure, on dit « sich eine Prellung angezogen ».",
        other_category="case",
    ),
    base(
        "e-n09-register-choice",
        "B2",
        "register",
        "register_choice",
        context_fr="Vous prévenez par écrit un patient âgé que son rendez-vous de demain est décalé de deux heures. Soyez poli, dites de combien l'horaire change et excusez-vous.",
        stem="Ihr Termin morgen wird verschoben, ist halt so.",
        key="Leider müssen wir Ihren Termin morgen um zwei Stunden verschieben. Bitte entschuldigen Sie die Umstände.",
        distractors=[
            "Ihr Termin ist jetzt später, regen Sie sich bitte nicht auf!",
            "Hiermit wird Ihnen mitgeteilt, dass Ihr Termin einer Verschiebung unterliegt.",
        ],
        explanation_fr="Il faut un ton poli, préciser le changement (deux heures) et s'excuser. La première option est familière, presque agacée, et ne dit pas de combien l'horaire change. La seconde est administrative et impersonnelle : elle ne précise pas le changement et ne s'excuse pas.",
        setting="prise de rendez-vous",
        second_correct="Es tut mir leid: Ihr Termin morgen verschiebt sich um zwei Stunden. Vielen Dank für Ihr Verständnis.",
        wrong_variant="Termin morgen später.",
        bad_explanation="Face à un patient âgé, il vaut mieux rester bref et familier, sans excuses, pour ne pas l'inquiéter.",
        other_category=None,
    ),
    base(
        "e-n10-case-cloze",
        "C1",
        "case",
        "cloze",
        stem="Der Eingriff, ___ die Patientin sich ohne Zögern unterzog, dauerte vier Stunden.",
        key="dem",
        distractors=["den", "der"],
        explanation_fr="« sich unterziehen » se construit avec le datif. Le pronom relatif renvoie à « Eingriff » (masculin singulier) et se met au datif masculin : « dem ». « den » serait un accusatif et « der » ne s'accorde pas avec un nom masculin au datif.",
        setting="chirurgie",
        second_correct=None,
        wrong_variant="demm",
        bad_explanation="« sich unterziehen » se construit avec l'accusatif : « den Eingriff ».",
        other_category="verb_form",
    ),
    base(
        "e-n11-verb-cloze",
        "C1",
        "verb_form",
        "cloze",
        stem="Die Operation hätte ___ werden müssen.",
        key="verschoben",
        distractors=["verschieben", "verschob"],
        explanation_fr="Au passif avec un verbe modal, on emploie le participe passé suivi de « werden » puis du modal : « hätte verschoben werden müssen ». L'infinitif « verschieben » et le prétérit « verschob » ne peuvent pas précéder « werden ».",
        setting="planification",
        second_correct=None,
        wrong_variant="verschobt",
        bad_explanation="Après « hätte », on met toujours l'infinitif du verbe : « hätte verschieben werden müssen ».",
        other_category="case",
    ),
    base(
        "e-n12-choice-cloze",
        "C1",
        "word_choice",
        "cloze",
        stem="Die Beschwerden sind nach wenigen Tagen von selbst ___.",
        key="abgeklungen",
        distractors=["abgelaufen", "abgeschlossen"],
        explanation_fr="« abklingen » se dit de symptômes qui s'atténuent peu à peu. « abgelaufen » (de « ablaufen ») se dit d'un délai ou d'un liquide, « abgeschlossen » (de « abschließen ») d'une procédure : aucun ne se dit de douleurs.",
        setting="suivi",
        second_correct="abgeflaut",
        wrong_variant="abgeholt",
        bad_explanation="« abklingen » signifie commencer ; on l'emploie pour des symptômes qui apparaissent.",
        other_category="case",
    ),
    base(
        "e-n13-order-correction",
        "C1",
        "word_order",
        "correct_sentence",
        stem="Nur wenn der Patient nüchtern ist, die Untersuchung darf beginnen.",
        key="Nur wenn der Patient nüchtern ist, darf die Untersuchung beginnen.",
        distractors=[
            "Nur wenn der Patient ist nüchtern, darf die Untersuchung beginnen.",
            "Nur wenn der Patient nüchtern ist, darf beginnen die Untersuchung.",
        ],
        explanation_fr="Quand la subordonnée ouvre la phrase, la principale commence par le verbe conjugué : « darf die Untersuchung beginnen ». Dans la subordonnée, « ist » reste en dernière position. Les deux autres versions déplacent « ist » ou « darf » à tort.",
        setting="préparation",
        second_correct=None,
        wrong_variant="Nur wenn der Patient nüchtern ist, darfst die Untersuchung beginnen.",
        bad_explanation="Quand la subordonnée ouvre la phrase, la principale garde l'ordre sujet puis verbe : « die Untersuchung darf beginnen ».",
        other_category="gender",
    ),
    base(
        "e-n14-register-choice",
        "C1",
        "register",
        "register_choice",
        context_fr="Vous refusez par écrit, avec tact, la demande d'un patient qui souhaite un arrêt de travail pour les trois dernières semaines, période pendant laquelle vous ne l'avez pas vu.",
        stem="Rückwirkend krankschreiben geht gar nicht, vergessen Sie's.",
        key="Leider kann ich Ihnen für die vergangenen drei Wochen keine Krankschreibung ausstellen, da ich Ihren Zustand in dieser Zeit nicht beurteilen konnte. Gern besprechen wir das weitere Vorgehen.",
        distractors=[
            "Nee, das mache ich nicht, so läuft das hier nicht.",
            "Ihrem Antrag wird nicht stattgegeben; weitere Rückfragen werden nicht beantwortet.",
        ],
        explanation_fr="Le refus doit rester poli, donner une raison et proposer une suite. La première formulation est sèche et familière. La seconde est administrative, ne donne aucune raison et ferme toute discussion.",
        setting="arrêt de travail",
        second_correct="Es tut mir leid, aber für die vergangenen drei Wochen kann ich keine Krankschreibung ausstellen, weil ich Sie in dieser Zeit nicht untersucht habe. Lassen Sie uns gemeinsam überlegen, wie es weitergeht.",
        wrong_variant="Nö, das geht nicht, Pech gehabt.",
        bad_explanation="Un refus écrit à un patient doit rester sec et sans raison pour éviter toute discussion.",
        other_category=None,
    ),
]

HOLDOUT_ABSURD: list[Base] = [
    base(
        "e-n15-absurd",
        "B1",
        "case",
        "cloze",
        stem="Zur Behandlung eines Schnupfens wird dem Patienten ___ Bein amputiert.",
        key="das",
        distractors=["der", "die"],
        explanation_fr="« Bein » est neutre et sujet du passif : au nominatif, l'article défini neutre est « das ».",
        setting="traitement",
    ),
    base(
        "e-n15-absurd",
        "B2",
        "gender",
        "cloze",
        stem="Gegen Haarausfall bekommt der Patient ___ Herzschrittmacher.",
        key="einen",
        distractors=["ein", "eine"],
        explanation_fr="« Herzschrittmacher » est masculin : à l'accusatif, l'article indéfini masculin est « einen ».",
        setting="traitement",
    ),
]


def build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    dev_bases = [
        b
        for b in [*V2_DEV_BASES, *V2_HOLDOUT_BASES, *V3_HOLDOUT_BASES]
        if b["family"] not in DROPPED_FAMILIES
    ] + list(V4_HOLDOUT_BASES)
    dev_absurd = [
        *V2_DEV_ABSURD,
        *V2_HOLDOUT_ABSURD,
        *V3_HOLDOUT_ABSURD,
        *V4_HOLDOUT_ABSURD,
    ]
    for b in dev_bases + HOLDOUT_BASES:
        if b["format"] != "register_choice":
            assert b["family"] in NEAR_MISS, f"no near miss for {b['family']}"
    # Rows are built with the near-miss table of this version (it holds the new families).
    import build_language_exercises_v4 as v4

    v4.NEAR_MISS = NEAR_MISS
    dev = with_near_misses(dev_bases)
    dev += [
        row(b, "medical_absurd", candidate(b), n)
        for n, b in enumerate(dev_absurd, start=1)
    ]
    holdout = with_near_misses(HOLDOUT_BASES)
    holdout += [
        row(b, "medical_absurd", candidate(b), n)
        for n, b in enumerate(HOLDOUT_ABSURD, start=1)
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
        "version": "5",
        "description": (
            "Exercices de langue dont le défaut est connu par construction : 68 exercices "
            "corrects écrits à la main (54 en développement, repris des versions 2, 3 et 4 sans "
            "deux références jugées défendables, et 14 nouveaux en holdout) et leurs variantes "
            "défectueuses (mauvaise clé, deux réponses justes, aucune réponse juste, réponse "
            "presque juste, explication fausse, mauvaise catégorie, mauvais niveau, options en "
            "double, format cassé, décor médical absurde)."
        ),
        "synthetic": True,
        "source": "Exercices inventés par l'agent, générés par evals/datasets/build_language_exercises_v5.py",
        "created_on": CREATED_ON,
        "consent": "not_applicable:synthetic",
        "annotators": [{**AGENT, "annotated_on": CREATED_ON}],
        "splits": {
            "dev": [b["family"] for b in dev_bases]
            + ["e-d15-absurd", "e-h15-absurd", "e-k15-absurd", "e-m15-absurd"],
            "holdout": [b["family"] for b in HOLDOUT_BASES] + ["e-n15-absurd"],
        },
        "files": files,
        "limitations": [
            "L'allemand a été écrit par l'agent et n'a pas été relu par une personne germanophone. Le holdout de la version 5 n'a pas non plus été relu par un lecteur extérieur au moment de sa création : cette relecture, si elle a lieu, est notée dans la note de fin et donne une version corrigée avant la première mesure. Un item « valide » refusé est à regarder avant de conclure que le contrôle se trompe.",
            "Quatorze bases valides en holdout : un exercice juste refusé vaut 7 points de fausse rejection ; un zéro fausse acceptation sur ce jeu n'est pas une garantie.",
            "Le développement contient tout ce qui a déjà été mesuré (versions 2, 3 et 4, dont trois holdouts lancés une fois chacun) : il ne sert plus de test indépendant. Les familles e-k12-choice-cloze et e-k13-order-correction, dont les contrôles jugent les références défendables, en ont été retirées sur décision du propriétaire (5 novembre 2026) ; elles restent dans la version 4. Le holdout de la version 5 est neuf et n'a été lu par aucun modèle des contrôles.",
            "Les défauts sont fabriqués à partir d'exercices corrects ; les fautes subtiles qu'un modèle auteur produit vraiment y sont moins bien représentées. Les fautes de frappe du défaut `near_miss_key` sont choisies une par famille.",
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "language-exercises" / "5")
