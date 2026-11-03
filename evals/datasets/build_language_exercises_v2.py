"""Builds `language-exercises@2`: the same measurement, with better exercises to measure on.

Usage, from the repository root:
    PYTHONPATH=backend/src .venv/bin/python evals/datasets/build_language_exercises_v2.py [out_dir]

Why a second version. The first measurement of the checks on the real models (3 November 2026,
dev split) refused 5 of the 8 good exercises, all by the strict judge. Reading its reasons showed
that most were right: the level written on the base was wrong (a B1 subordinate clause marked
B2, a B2 genitive marked C1), one explanation over-generalised, one distractor was acceptable in
speech, one register exercise repeated an option in its stem. A suite whose "good" exercises are
not good measures the author of the suite. Eight good exercises per split also made one
exercise worth 12.5 points of false rejection.

What changed, and nothing else: the good exercises are rewritten (14 per split instead of 8),
each with a level that matches the grammar point on the usual CEFR lists, richer vocabulary where
the level asks for it, a distractor that is wrong in every register, and an explanation that does
not state a rule broader than the one the exercise trains. The families are new in both splits:
the old holdout is not reused, and nothing here was tuned against its verdicts. The defects are
derived from each base exactly as in version 1 (`build_language_exercises_v1.py`), and the judge,
the prompts and the thresholds are untouched.

Everything is invented. The German was written by the agent and has not been read by a German
speaker: the manifest says so. The output is deterministic and a test rebuilds it.
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
    absurd,
    base,
    jsonl,
    variants,
)

CREATED_ON = "2026-11-03"

DEV_BASES: list[Base] = [
    base(
        "e-d01-gender-cloze",
        "B1",
        "gender",
        "cloze",
        stem="Die Schwester bereitet ___ Spritze für den Patienten vor.",
        key="die",
        distractors=["den", "das"],
        explanation_fr="« Spritze » est féminin : à l'accusatif, l'article défini féminin est « die ».",
        setting="soins",
        second_correct="eine",
        wrong_variant="dem",
        bad_explanation="« Spritze » est neutre : à l'accusatif, l'article défini neutre est « die ».",
        other_category="verb_form",
    ),
    base(
        "e-d02-case-correction",
        "B1",
        "case",
        "correct_sentence",
        stem="Ich danke der Arzt für die Hilfe.",
        key="Ich danke dem Arzt für die Hilfe.",
        distractors=[
            "Ich danke den Arzt für die Hilfe.",
            "Ich danke dem Arztes für die Hilfe.",
        ],
        explanation_fr="« danken » se construit avec le datif : « dem Arzt » (masculin singulier).",
        setting="fin de consultation",
        second_correct="Ich danke dem Doktor für die Hilfe.",
        wrong_variant="Ich danke dem Arzt für den Hilfe.",
        bad_explanation="« danken » se construit avec l'accusatif : « den Arzt ».",
        other_category="verb_form",
    ),
    base(
        "e-d03-verb-cloze",
        "B1",
        "verb_form",
        "cloze",
        stem="Der Patient ___ gestern zur Röntgenabteilung gegangen.",
        key="ist",
        distractors=["hat", "wurde"],
        explanation_fr="« gehen » est un verbe de mouvement : son parfait se forme avec « sein », « ist gegangen ».",
        setting="parcours de soins",
        second_correct=None,
        wrong_variant="haben",
        bad_explanation="« gehen » forme son parfait avec « haben » : « hat gegangen ».",
        other_category="case",
    ),
    base(
        "e-d04-order-correction",
        "B1",
        "word_order",
        "correct_sentence",
        stem="Ich gehe zur Arbeit, obwohl ich habe Fieber.",
        key="Ich gehe zur Arbeit, obwohl ich Fieber habe.",
        distractors=[
            "Ich gehe zur Arbeit, obwohl habe ich Fieber.",
            "Ich gehe zur Arbeit, obwohl Fieber ich habe.",
        ],
        explanation_fr="Dans une subordonnée introduite par « obwohl », le verbe conjugué se place en fin de proposition.",
        setting="anamnèse",
        second_correct="Ich gehe zur Arbeit, obwohl ich hohes Fieber habe.",
        wrong_variant="Ich gehe zur Arbeit, obwohl ich Fieber hat.",
        bad_explanation="Dans une subordonnée introduite par « obwohl », le verbe conjugué reste en deuxième position.",
        other_category="word_choice",
    ),
    base(
        "e-d05-choice-cloze",
        "B2",
        "word_choice",
        "cloze",
        stem="Der Chirurg hat den Eingriff ohne Komplikationen ___.",
        key="durchgeführt",
        distractors=["eingeführt", "übergeführt"],
        explanation_fr="On « führt einen Eingriff durch » : « durchführen » est le verbe usuel pour réaliser une intervention.",
        setting="chirurgie",
        second_correct="vorgenommen",
        wrong_variant="durchgeführte",
        bad_explanation="« durchführen » ne s'emploie que pour un voyage et jamais pour une intervention médicale.",
        other_category="case",
    ),
    base(
        "e-d06-case-cloze",
        "B2",
        "case",
        "cloze",
        stem="Der Patient, mit ___ ich gestern die Entlassung besprochen habe, ist wieder zu Hause.",
        key="dem",
        distractors=["den", "der"],
        explanation_fr="« mit » est suivi du datif ; le pronom relatif reprend « der Patient » (masculin singulier) : « dem ».",
        setting="suivi après hospitalisation",
        second_correct=None,
        wrong_variant="denen",
        bad_explanation="« mit » est suivi de l'accusatif : le pronom relatif est donc « den ».",
        other_category="verb_form",
    ),
    base(
        "e-d07-verb-cloze",
        "B2",
        "verb_form",
        "cloze",
        stem="Wenn ich mehr Zeit ___, würde ich Ihnen die Untersuchung genauer erklären.",
        key="hätte",
        distractors=["haben", "hätten"],
        explanation_fr="Pour une condition irréelle au présent, on emploie le subjonctif II : « hätte » à la 1re personne du singulier.",
        setting="consultation",
        second_correct=None,
        wrong_variant="hättest",
        bad_explanation="Pour une condition irréelle au présent, on emploie l'indicatif présent : « habe ».",
        other_category="word_choice",
    ),
    base(
        "e-d08-order-correction",
        "B2",
        "word_order",
        "correct_sentence",
        stem="Der Patient hatte seit Tagen hohes Fieber. Dennoch er erschien pünktlich zur Kontrolle.",
        key="Der Patient hatte seit Tagen hohes Fieber. Dennoch erschien er pünktlich zur Kontrolle.",
        distractors=[
            "Der Patient hatte seit Tagen hohes Fieber. Dennoch erschien zur Kontrolle pünktlich er.",
            "Der Patient hatte seit Tagen hohes Fieber. Dennoch er pünktlich zur Kontrolle erschien.",
        ],
        explanation_fr="« dennoch » en début de phrase est suivi du verbe conjugué, puis du sujet : « Dennoch erschien er ».",
        setting="suivi",
        second_correct="Der Patient hatte seit Tagen hohes Fieber. Dennoch erschien er noch pünktlich zur Kontrolle.",
        wrong_variant="Der Patient hatte seit Tagen hohes Fieber. Dennoch erschienen er pünktlich zur Kontrolle.",
        bad_explanation="« dennoch » est une conjonction de subordination : le verbe conjugué part en fin de proposition.",
        other_category="gender",
    ),
    base(
        "e-d09-register-choice",
        "B2",
        "register",
        "register_choice",
        context_fr="Vous annoncez à un patient adulte qu'il doit rester une nuit de plus à l'hôpital.",
        stem="Sie bleiben heute noch hier, keine Diskussion.",
        key="Wir würden Sie gern noch eine Nacht zur Beobachtung hierbehalten, wenn Sie einverstanden sind.",
        distractors=[
            "Sie bleiben noch eine Nacht hier, ob es Ihnen passt oder nicht.",
            "Na, noch eine Nacht Hotel Krankenhaus, was meinen Sie?",
        ],
        explanation_fr="On annonce une décision médicale sur un ton poli et explicatif, qui laisse la place à l'accord du patient : ni sec et autoritaire, ni familier et ironique.",
        setting="annonce au patient",
        second_correct="Ich würde Ihnen empfehlen, noch eine Nacht zur Beobachtung hierzubleiben.",
        wrong_variant="Du solltest noch eine Nacht zur Beobachtung hierbleiben.",
        bad_explanation="Pour annoncer une décision médicale, on emploie un ton autoritaire et sans explication pour éviter toute discussion.",
        other_category=None,
    ),
    base(
        "e-d10-case-cloze",
        "C1",
        "case",
        "cloze",
        stem="Angesichts ___ schlechten Prognose entschied sich die Familie für eine palliative Betreuung.",
        key="der",
        distractors=["die", "dem"],
        explanation_fr="« angesichts » est suivi du génitif ; « Prognose » est féminin : au génitif singulier, l'article défini est « der ».",
        setting="soins palliatifs",
        second_correct="dieser",
        wrong_variant="des",
        bad_explanation="« angesichts » est suivi de l'accusatif ; « Prognose » est féminin : l'article défini est « die ».",
        other_category="verb_form",
    ),
    base(
        "e-d11-verb-cloze",
        "C1",
        "verb_form",
        "cloze",
        stem="Die gestern ___ Patientin, bei der der Eingriff gut verlief, liegt jetzt auf der Überwachungsstation.",
        key="operierte",
        distractors=["operiert", "operierende"],
        explanation_fr="Le participe passé employé comme adjectif prend la terminaison de l'adjectif : « die operierte Patientin ».",
        setting="chirurgie",
        second_correct=None,
        wrong_variant="operierten",
        bad_explanation="Pour désigner une personne qui a subi l'opération, on emploie toujours le participe présent « operierende ».",
        other_category="word_choice",
    ),
    base(
        "e-d12-choice-cloze",
        "C1",
        "word_choice",
        "cloze",
        stem="Der Befund ___ den Verdacht auf eine Autoimmunerkrankung: Die Diagnose gilt damit als gesichert.",
        key="erhärtet",
        distractors=["erhält", "erhört"],
        explanation_fr="« einen Verdacht erhärten » signifie confirmer un soupçon.",
        setting="diagnostic",
        second_correct="bestätigt",
        wrong_variant="erhärten",
        bad_explanation="« erhärten » signifie durcir physiquement et ne s'emploie jamais pour un soupçon.",
        other_category="case",
    ),
    base(
        "e-d13-order-correction",
        "C1",
        "word_order",
        "correct_sentence",
        stem="Nur selten der Patient erinnert sich an den Unfall.",
        key="Nur selten erinnert sich der Patient an den Unfall.",
        distractors=[
            "Nur selten der Patient sich erinnert an den Unfall.",
            "Nur selten erinnert an den Unfall sich der Patient.",
        ],
        explanation_fr="Après « nur selten » en début de phrase, le verbe conjugué vient avant le sujet : « Nur selten erinnert sich der Patient… ».",
        setting="analyse de cas",
        second_correct="Nur selten erinnert sich der Patient noch an den Unfall.",
        wrong_variant="Nur selten erinnert sich der Patient an der Unfall.",
        bad_explanation="Après « nur selten », le sujet précède toujours le verbe conjugué.",
        other_category="word_choice",
    ),
    base(
        "e-d14-register-choice",
        "C1",
        "register",
        "register_choice",
        context_fr="Vous écrivez dans le dossier médical, à l'attention du médecin traitant.",
        stem="Der Typ hat total Schiss vor der Spritze und hat rumgeheult.",
        key="Der Patient äußerte ausgeprägte Angst vor der Injektion und reagierte emotional.",
        distractors=[
            "Der Patient hat gewaltig Bammel vor dem Piks und flennte rum.",
            "Es wird hiermit zur Kenntnis gebracht, dass seitens des Patienten eine ausgeprägte Furcht vor injektiven Maßnahmen obwaltete.",
        ],
        explanation_fr="Dans un dossier médical, on emploie un registre écrit, factuel et neutre : ni familier, ni excessivement administratif.",
        setting="dossier médical",
        second_correct="Der Patient zeigte deutliche Angst vor der Injektion und war emotional aufgewühlt.",
        wrong_variant="Der Typ hatte Schiss vor der Spritze.",
        bad_explanation="Dans un dossier médical, on emploie volontairement un registre familier pour rester proche du patient.",
        other_category=None,
    ),
]

HOLDOUT_BASES: list[Base] = [
    base(
        "e-h01-gender-cloze",
        "B1",
        "gender",
        "cloze",
        stem="Ich habe ___ Allergie gegen Pollen.",
        key="eine",
        distractors=["einen", "ein"],
        explanation_fr="« Allergie » est féminin : à l'accusatif, l'article indéfini féminin est « eine ».",
        setting="anamnèse",
        second_correct="keine",
        wrong_variant="einem",
        bad_explanation="« Allergie » est masculin : à l'accusatif, l'article indéfini masculin est « einen ».",
        other_category="verb_form",
    ),
    base(
        "e-h02-case-correction",
        "B1",
        "case",
        "correct_sentence",
        stem="Ich war gestern bei meinen Hausarzt.",
        key="Ich war gestern bei meinem Hausarzt.",
        distractors=[
            "Ich war gestern bei meiner Hausarzt.",
            "Ich war gestern bei meinem Hausarztes.",
        ],
        explanation_fr="« bei » est toujours suivi du datif : « meinem Hausarzt » (masculin singulier).",
        setting="anamnèse",
        second_correct="Ich war gestern bei meinem Arzt.",
        wrong_variant="Ich war gestern bei meinem Hausärzte.",
        bad_explanation="« bei » est suivi de l'accusatif : « meinen Hausarzt ».",
        other_category="verb_form",
    ),
    base(
        "e-h03-verb-cloze",
        "B1",
        "verb_form",
        "cloze",
        stem="Die Patientin ___ mir gestern ihre Befunde gezeigt.",
        key="hat",
        distractors=["ist", "wird"],
        explanation_fr="« zeigen » est un verbe transitif : son parfait se forme avec « haben », « hat gezeigt ».",
        setting="consultation",
        second_correct=None,
        wrong_variant="haben",
        bad_explanation="« zeigen » forme son parfait avec « sein » : « ist gezeigt ».",
        other_category="word_choice",
    ),
    base(
        "e-h04-order-correction",
        "B1",
        "word_order",
        "correct_sentence",
        stem="Der Arzt sagt, dass ich muss mehr trinken.",
        key="Der Arzt sagt, dass ich mehr trinken muss.",
        distractors=[
            "Der Arzt sagt, dass muss ich mehr trinken.",
            "Der Arzt sagt, dass ich trinken mehr muss.",
        ],
        explanation_fr="Dans une subordonnée introduite par « dass », le verbe conjugué (ici le modal) se place en dernière position.",
        setting="conseils",
        second_correct="Der Arzt sagt, dass ich viel mehr trinken muss.",
        wrong_variant="Der Arzt sagt, dass ich mehr trinken müssen.",
        bad_explanation="Dans une subordonnée introduite par « dass », le verbe conjugué se place en deuxième position.",
        other_category="verb_form",
    ),
    base(
        "e-h05-choice-cloze",
        "B2",
        "word_choice",
        "cloze",
        stem="Die Therapie hat bei der Patientin leider nicht ___.",
        key="angeschlagen",
        distractors=["angeklagt", "angeschrieben"],
        explanation_fr="« bei jemandem anschlagen » se dit d'un traitement qui fait effet.",
        setting="suivi thérapeutique",
        second_correct="gewirkt",
        wrong_variant="angeschlagt",
        bad_explanation="« anschlagen » signifie commencer et ne s'emploie pas pour un traitement qui fait effet.",
        other_category="verb_form",
    ),
    base(
        "e-h06-case-cloze",
        "B2",
        "case",
        "cloze",
        stem="Das Medikament, ___ ich Ihnen gestern gegen die Schlafstörungen verschrieben habe, nehmen Sie bitte abends.",
        key="das",
        distractors=["dem", "dessen"],
        explanation_fr="Le pronom relatif reprend « das Medikament » (neutre) et est complément d'objet direct : accusatif neutre « das ».",
        setting="prescription",
        second_correct=None,
        wrong_variant="dass",
        bad_explanation="Le pronom relatif est ici complément indirect : on emploie le datif « dem ».",
        other_category="verb_form",
    ),
    base(
        "e-h07-verb-cloze",
        "B2",
        "verb_form",
        "cloze",
        stem="Der Patient ist gestern erfolgreich ___ worden.",
        key="operiert",
        distractors=["operieren", "operierte"],
        explanation_fr="Au passif parfait, on emploie le participe passé suivi de « worden » : « operiert worden ».",
        setting="chirurgie",
        second_correct=None,
        wrong_variant="operierten",
        bad_explanation="Au passif parfait, le verbe principal reste à l'infinitif.",
        other_category="case",
    ),
    base(
        "e-h08-order-correction",
        "B2",
        "word_order",
        "correct_sentence",
        stem="Nachdem die Patientin hatte geschlafen, ging es ihr besser.",
        key="Nachdem die Patientin geschlafen hatte, ging es ihr besser.",
        distractors=[
            "Nachdem hatte die Patientin geschlafen, ging es ihr besser.",
            "Nachdem die Patientin hatte geschlafen, es ging ihr besser.",
        ],
        explanation_fr="Dans la subordonnée introduite par « nachdem », l'auxiliaire conjugué passe en fin de proposition, après le participe passé.",
        setting="suivi",
        second_correct="Nachdem die Patientin lange geschlafen hatte, ging es ihr besser.",
        wrong_variant="Nachdem die Patientin geschlafen hatten, ging es ihr besser.",
        bad_explanation="Dans la subordonnée introduite par « nachdem », l'auxiliaire reste en deuxième position avant le participe.",
        other_category="verb_form",
    ),
    base(
        "e-h09-register-choice",
        "B2",
        "register",
        "register_choice",
        context_fr="Vous répondez au téléphone au cabinet : un patient demande un rendez-vous.",
        stem="Ja, ja, kommen Se morgen vorbei, passt schon.",
        key="Gern, kommen Sie morgen um zehn Uhr vorbei, das passt gut.",
        distractors=[
            "Ey, komm halt morgen vorbei, geht klar.",
            "Wir erlauben uns, Ihnen mitzuteilen, dass ein Erscheinen Ihrerseits am morgigen Tage genehmigt wird.",
        ],
        explanation_fr="Au téléphone avec un patient, on reste courtois et clair, avec « Sie » et une proposition précise : ni familier (« Ey », tutoiement), ni administratif et pompeux.",
        setting="accueil téléphonique",
        second_correct="Sehr gern, Sie können morgen um zehn Uhr vorbeikommen.",
        wrong_variant="Komm morgen um zehn vorbei.",
        bad_explanation="Au téléphone, on tutoie systématiquement les patients pour créer de la proximité.",
        other_category=None,
    ),
    base(
        "e-h10-case-cloze",
        "C1",
        "case",
        "cloze",
        stem="Infolge ___ Komplikation musste die Operation abgebrochen werden.",
        key="einer",
        distractors=["eine", "einem"],
        explanation_fr="« infolge » est suivi du génitif ; « Komplikation » est féminin : au génitif singulier, l'article indéfini est « einer ».",
        setting="chirurgie",
        second_correct="der",
        wrong_variant="eines",
        bad_explanation="« infolge » est suivi de l'accusatif : l'article indéfini féminin est « eine ».",
        other_category="verb_form",
    ),
    base(
        "e-h11-verb-cloze",
        "C1",
        "verb_form",
        "cloze",
        stem="Der Patient ___ die Komplikation vermeiden können, wenn er früher gekommen wäre.",
        key="hätte",
        distractors=["wäre", "hätten"],
        explanation_fr="Au subjonctif II passé avec un modal : « hätte … vermeiden können » (auxiliaire « haben » et infinitif).",
        setting="analyse de cas",
        second_correct=None,
        wrong_variant="hättet",
        bad_explanation="Au subjonctif II passé, ce groupe verbal se forme avec « sein » : « wäre vermeiden können ».",
        other_category="case",
    ),
    base(
        "e-h12-choice-cloze",
        "C1",
        "word_choice",
        "cloze",
        stem="Die Befunde ___ den Verdacht auf eine Autoimmunerkrankung nahe.",
        key="legen",
        distractors=["setzen", "stellen"],
        explanation_fr="« etwas nahelegen » signifie suggérer, rendre vraisemblable.",
        setting="diagnostic",
        second_correct=None,
        wrong_variant="legt",
        bad_explanation="« nahelegen » signifie placer à proximité physique et ne s'emploie jamais pour un soupçon.",
        other_category="case",
    ),
    base(
        "e-h13-order-correction",
        "C1",
        "word_order",
        "correct_sentence",
        stem="Kaum der Patient hatte das Medikament genommen, setzte die Wirkung ein.",
        key="Kaum hatte der Patient das Medikament genommen, setzte die Wirkung ein.",
        distractors=[
            "Kaum hatte der Patient genommen das Medikament, setzte die Wirkung ein.",
            "Kaum der Patient das Medikament genommen hatte, setzte die Wirkung ein.",
        ],
        explanation_fr="Après « kaum » en début de phrase, le verbe conjugué vient avant le sujet : « Kaum hatte der Patient… ».",
        setting="suivi thérapeutique",
        second_correct="Kaum hatte der Patient die Tablette genommen, setzte die Wirkung ein.",
        wrong_variant="Kaum hatte der Patient das Medikament genommen, setzten die Wirkung ein.",
        bad_explanation="Après « kaum », le sujet précède toujours le verbe conjugué.",
        other_category="verb_form",
    ),
    base(
        "e-h14-register-choice",
        "C1",
        "register",
        "register_choice",
        context_fr="Vous répondez par écrit à la réclamation d'un patient mécontent de l'attente aux urgences.",
        stem="Tja, hätten Sie halt früher kommen sollen, das ist nicht unser Problem.",
        key="Wir bedauern die lange Wartezeit sehr und erklären Ihnen gern, wie die Dringlichkeit der Fälle eingeschätzt wird.",
        distractors=[
            "Die Wartezeit lag im üblichen Rahmen, weitere Beschwerden werden nicht bearbeitet.",
            "Mensch, das tut uns total leid, da lief halt grad ganz schön viel, sorry!",
        ],
        explanation_fr="Une réponse écrite à une réclamation est polie, factuelle et empathique, sans condescendance ni familiarité.",
        setting="réclamation",
        second_correct="Wir entschuldigen uns für die lange Wartezeit und erläutern Ihnen gern unser Vorgehen bei der Einschätzung der Dringlichkeit.",
        wrong_variant="Das ist leider so, warten gehört dazu.",
        bad_explanation="Une réponse à une réclamation doit rester brève et ne jamais présenter d'excuses ni d'explications.",
        other_category=None,
    ),
]

# Correct German, medically absurd (the coarse plausibility check).
DEV_ABSURD: list[Base] = [
    base(
        "e-d15-absurd",
        "B1",
        "case",
        "cloze",
        stem="Der Patient klagt über starken Husten in ___ rechten Knie.",
        key="dem",
        distractors=["den", "der"],
        explanation_fr="« in » avec un lieu où l'on est se construit avec le datif ; « Knie » est neutre : « dem ».",
        setting="anamnèse",
    ),
    base(
        "e-d15-absurd",
        "B2",
        "gender",
        "cloze",
        stem="Der Chirurg operiert ___ Blinddarm am Herzen des Patienten.",
        key="den",
        distractors=["die", "das"],
        explanation_fr="« Blinddarm » est masculin : à l'accusatif, l'article défini masculin est « den ».",
        setting="chirurgie",
    ),
]
HOLDOUT_ABSURD: list[Base] = [
    base(
        "e-h15-absurd",
        "B1",
        "case",
        "cloze",
        stem="Gegen ihre Kopfschmerzen bekommt die Patientin einen Gips um ___ Kopf.",
        key="den",
        distractors=["dem", "der"],
        explanation_fr="« um » est suivi de l'accusatif ; « Kopf » est masculin : « den ».",
        setting="traitement",
    ),
    base(
        "e-h15-absurd",
        "B2",
        "gender",
        "cloze",
        stem="Zur Behandlung der Grippe verordnet der Arzt ___ Herztransplantation.",
        key="eine",
        distractors=["einen", "ein"],
        explanation_fr="« Herztransplantation » est féminin : à l'accusatif, l'article indéfini féminin est « eine ».",
        setting="prescription",
    ),
]


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
        "version": "2",
        "description": (
            "Exercices de langue dont le défaut est connu par construction : 28 exercices "
            "corrects écrits à la main (14 par split, niveau et ambiguïté revus après la "
            "première mesure) et leurs variantes défectueuses (mauvaise clé, deux réponses "
            "justes, aucune réponse juste, explication fausse, mauvaise catégorie, mauvais "
            "niveau, options en double, format cassé, décor médical absurde)."
        ),
        "synthetic": True,
        "source": "Exercices inventés par l'agent, générés par evals/datasets/build_language_exercises_v2.py",
        "created_on": CREATED_ON,
        "consent": "not_applicable:synthetic",
        "annotators": [{**AGENT, "annotated_on": CREATED_ON}],
        "splits": {
            "dev": [b["family"] for b in DEV_BASES] + ["e-d15-absurd"],
            "holdout": [b["family"] for b in HOLDOUT_BASES] + ["e-h15-absurd"],
        },
        "files": files,
        "limitations": [
            "L'allemand a été écrit par l'agent et n'a pas été relu par une personne germanophone : une base peut elle-même être discutable, et un item « valide » refusé est à regarder avant de conclure que le contrôle se trompe (c'est ce qui a motivé cette version).",
            "Quatorze bases valides par split : un exercice juste refusé vaut 7 points de fausse rejection ; le taux de fausse acceptation est mesuré sur peu d'items, un zéro n'est pas une garantie.",
            "Les défauts sont fabriqués à partir d'exercices corrects ; les fautes subtiles qu'un modèle auteur produit vraiment (une distractrice presque acceptable) y sont moins bien représentées.",
            "Le holdout de la version 2 est neuf (familles neuves) : il n'a pas été utilisé pour régler quoi que ce soit.",
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "language-exercises" / "2")
