"""Builds `placement@1`: spoken placement tasks with errors injected at known places.

Usage, from the repository root:
    PYTHONPATH=backend/src .venv/bin/python evals/datasets/build_placement_v1.py [out_dir]

What it measures (see backend/src/ari/evals/scorers/placement.py): the real rater of the spoken
placement tasks (`placement-speaking-v2`) on a transcript whose errors are known by construction.
A transcript is six sentences of a spoken answer at a known level (B1, B2 or C1). Each sentence
exists twice, correct and with one injected error of a known category (the quote and its
correction are recorded). Slots 0 and 1 carry the same category, slots 2 and 3 another one, slot
4 a third and slot 5 a fourth, so that five variants of each text exist:

- `clean`: no error (false errors are counted against the model);
- `pair-a`: slots 0 and 1, two errors of one category (a weak point is expected);
- `pair-b`: slots 2 and 3, two errors of another category (a weak point is expected);
- `mixed`: slots 0, 2 and 4, three errors of three categories (no weak point is expected);
- `all`: the six slots (the two categories that come twice are the weak points).

Everything is invented, nothing comes from a real learner. The sentences were written by the
agent; no German-speaking human reviewed them (limitation recorded in the manifest). The output
is deterministic and a test rebuilds it: the committed files are never edited by hand.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CREATED_ON = "2026-11-02"
AGENT = {
    "id": "agent:claude-sonnet-5.5",
    "role": "rédaction synthétique",
    "annotated_on": CREATED_ON,
}

# Slot: (category, correct sentence, sentence with the error, quote as spoken, correction).
Slot = tuple[str, str, str, str, str]

FAMILIES: dict[str, dict[str, Any]] = {
    "arbeitstag": {
        "task": "Beschreiben Sie einen typischen Arbeitstag in Ihrer Klinik oder Praxis. Was gefällt Ihnen daran, und was finden Sie schwierig?",
        "levels": {
            "B1": [
                (
                    "verb_form",
                    "Heute bin ich um sieben Uhr in die Klinik gekommen.",
                    "Heute habe ich um sieben Uhr in die Klinik gekommen.",
                    "habe ich um sieben Uhr in die Klinik gekommen",
                    "bin ich um sieben Uhr in die Klinik gekommen",
                ),
                (
                    "verb_form",
                    "Zuerst habe ich die Patienten auf der Station besucht.",
                    "Zuerst habe ich die Patienten auf der Station besuchen.",
                    "habe ich die Patienten auf der Station besuchen",
                    "habe ich die Patienten auf der Station besucht",
                ),
                (
                    "case",
                    "Ich arbeite mit einem sehr netten Oberarzt zusammen.",
                    "Ich arbeite mit einen sehr netten Oberarzt zusammen.",
                    "mit einen sehr netten Oberarzt",
                    "mit einem sehr netten Oberarzt",
                ),
                (
                    "case",
                    "Er erklärt mir jeden Tag die schwierigen Fälle.",
                    "Er erklärt mich jeden Tag die schwierigen Fälle.",
                    "erklärt mich",
                    "erklärt mir",
                ),
                (
                    "gender",
                    "Die Arbeit macht mir viel Spaß, aber die Schichten sind lang.",
                    "Der Arbeit macht mir viel Spaß, aber die Schichten sind lang.",
                    "Der Arbeit",
                    "Die Arbeit",
                ),
                (
                    "word_order",
                    "Am Abend bin ich oft müde, weil ich viel gearbeitet habe.",
                    "Am Abend bin ich oft müde, weil ich habe viel gearbeitet.",
                    "weil ich habe viel gearbeitet",
                    "weil ich viel gearbeitet habe",
                ),
            ],
            "B2": [
                (
                    "verb_form",
                    "Wenn ich mehr Zeit hätte, würde ich länger mit den Patienten sprechen.",
                    "Wenn ich mehr Zeit habe, würde ich länger mit den Patienten sprechen.",
                    "Wenn ich mehr Zeit habe",
                    "Wenn ich mehr Zeit hätte",
                ),
                (
                    "verb_form",
                    "Gestern wurde ein Patient mit starken Schmerzen eingeliefert.",
                    "Gestern wurde ein Patient mit starken Schmerzen einliefern.",
                    "wurde ein Patient mit starken Schmerzen einliefern",
                    "wurde ein Patient mit starken Schmerzen eingeliefert",
                ),
                (
                    "case",
                    "Die Zusammenarbeit mit dem Pflegeteam funktioniert hervorragend.",
                    "Die Zusammenarbeit mit das Pflegeteam funktioniert hervorragend.",
                    "mit das Pflegeteam",
                    "mit dem Pflegeteam",
                ),
                (
                    "case",
                    "Ich verlasse mich auf die Erfahrung meiner Kollegen.",
                    "Ich verlasse mich auf die Erfahrung meinen Kollegen.",
                    "Erfahrung meinen Kollegen",
                    "Erfahrung meiner Kollegen",
                ),
                (
                    "gender",
                    "Die Notaufnahme, in der ich arbeite, ist rund um die Uhr besetzt.",
                    "Die Notaufnahme, in dem ich arbeite, ist rund um die Uhr besetzt.",
                    "in dem ich arbeite",
                    "in der ich arbeite",
                ),
                (
                    "word_order",
                    "Trotz der Belastung macht mir die Arbeit großen Spaß.",
                    "Trotz der Belastung mir macht die Arbeit großen Spaß.",
                    "mir macht die Arbeit",
                    "macht mir die Arbeit",
                ),
            ],
            "C1": [
                (
                    "verb_form",
                    "Hätte man die Übergabe gründlicher vorbereiten können, wäre manches Missverständnis vermeidbar gewesen.",
                    "Hätte man die Übergabe gründlicher vorbereiten gekonnt, wäre manches Missverständnis vermeidbar gewesen.",
                    "vorbereiten gekonnt",
                    "vorbereiten können",
                ),
                (
                    "verb_form",
                    "Die Station, die von einer sehr erfahrenen Oberärztin geleitet wird, gilt als vorbildlich organisiert.",
                    "Die Station, die von einer sehr erfahrenen Oberärztin geleiten wird, gilt als vorbildlich organisiert.",
                    "geleiten wird",
                    "geleitet wird",
                ),
                (
                    "case",
                    "Aufgrund eines Notfalls, der sich kurz vor Dienstbeginn ereignet hatte, musste die Besprechung entfallen.",
                    "Aufgrund einem Notfall, der sich kurz vor Dienstbeginn ereignet hatte, musste die Besprechung entfallen.",
                    "Aufgrund einem Notfall",
                    "Aufgrund eines Notfalls",
                ),
                (
                    "case",
                    "Ich bin dem gesamten Team dankbar, das mich in der Anfangszeit so tatkräftig unterstützt hat.",
                    "Ich bin den gesamten Team dankbar, das mich in der Anfangszeit so tatkräftig unterstützt hat.",
                    "den gesamten Team",
                    "dem gesamten Team",
                ),
                (
                    "gender",
                    "Die Dokumentation, deren Umfang stetig wächst, kostet mich oft mehr Zeit als die Untersuchung selbst.",
                    "Die Dokumentation, dessen Umfang stetig wächst, kostet mich oft mehr Zeit als die Untersuchung selbst.",
                    "dessen Umfang",
                    "deren Umfang",
                ),
                (
                    "word_order",
                    "Nachdem ich die Visite beendet hatte, kümmerte ich mich um die Arztbriefe.",
                    "Nachdem ich die Visite beendet hatte, ich kümmerte mich um die Arztbriefe.",
                    "ich kümmerte mich um die Arztbriefe",
                    "kümmerte ich mich um die Arztbriefe",
                ),
            ],
        },
    },
    "aufklaerung": {
        "task": "Ein Patient hat starke Schmerzen, möchte aber nicht im Krankenhaus bleiben. Erklären Sie ihm höflich, warum eine Beobachtung über Nacht sinnvoll wäre.",
        "levels": {
            "B1": [
                (
                    "register",
                    "Guten Tag, Herr Keller, bitte setzen Sie sich.",
                    "Guten Tag, Herr Keller, bitte setz dich.",
                    "bitte setz dich",
                    "bitte setzen Sie sich",
                ),
                (
                    "register",
                    "Wie geht es Ihnen heute?",
                    "Wie geht es dir heute?",
                    "geht es dir",
                    "geht es Ihnen",
                ),
                (
                    "word_choice",
                    "Ich möchte Ihnen erklären, warum wir Sie über Nacht beobachten.",
                    "Ich möchte Ihnen erklären, warum wir Sie über Nacht schauen.",
                    "Sie über Nacht schauen",
                    "Sie über Nacht beobachten",
                ),
                (
                    "word_choice",
                    "Ihre Schmerzen könnten eine ernste Ursache haben.",
                    "Ihre Schmerzen könnten eine ernste Ursache machen.",
                    "Ursache machen",
                    "Ursache haben",
                ),
                (
                    "verb_form",
                    "Gestern haben Sie noch nichts gegessen.",
                    "Gestern haben Sie noch nichts essen.",
                    "haben Sie noch nichts essen",
                    "haben Sie noch nichts gegessen",
                ),
                (
                    "case",
                    "Wir geben Ihnen ein Medikament gegen die Schmerzen.",
                    "Wir geben Sie ein Medikament gegen die Schmerzen.",
                    "geben Sie ein",
                    "geben Ihnen ein",
                ),
            ],
            "B2": [
                (
                    "register",
                    "Ich würde Ihnen empfehlen, heute Nacht bei uns zu bleiben.",
                    "Ich würde dir empfehlen, heute Nacht bei uns zu bleiben.",
                    "würde dir empfehlen",
                    "würde Ihnen empfehlen",
                ),
                (
                    "register",
                    "Haben Sie vielleicht noch Fragen zu dem Eingriff?",
                    "Hast du vielleicht noch Fragen zu dem Eingriff?",
                    "Hast du vielleicht",
                    "Haben Sie vielleicht",
                ),
                (
                    "word_choice",
                    "Die Untersuchung hat keinen eindeutigen Befund ergeben.",
                    "Die Untersuchung hat keinen eindeutigen Befund gegeben.",
                    "Befund gegeben",
                    "Befund ergeben",
                ),
                (
                    "word_choice",
                    "Wir überwachen Ihren Blutdruck und die Herzfrequenz.",
                    "Wir überwachen Ihren Blutdruck und die Herzgeschwindigkeit.",
                    "Herzgeschwindigkeit",
                    "Herzfrequenz",
                ),
                (
                    "verb_form",
                    "Sollten die Beschwerden stärker werden, melden Sie sich bitte sofort.",
                    "Sollten die Beschwerden stärker werden, melde Sie sich bitte sofort.",
                    "melde Sie sich",
                    "melden Sie sich",
                ),
                (
                    "case",
                    "Wir sprechen morgen früh mit Ihrer Familie über das weitere Vorgehen.",
                    "Wir sprechen morgen früh mit Ihre Familie über das weitere Vorgehen.",
                    "mit Ihre Familie",
                    "mit Ihrer Familie",
                ),
            ],
            "C1": [
                (
                    "register",
                    "Ich darf Sie bitten, sich kurz zu gedulden, bis die Ergebnisse vorliegen.",
                    "Ich darf dich bitten, dich kurz zu gedulden, bis die Ergebnisse vorliegen.",
                    "dich bitten, dich kurz",
                    "Sie bitten, sich kurz",
                ),
                (
                    "register",
                    "Gestatten Sie mir, Ihnen die Beweggründe unserer Empfehlung darzulegen.",
                    "Lass mich dir die Beweggründe unserer Empfehlung darlegen.",
                    "Lass mich dir die Beweggründe unserer Empfehlung darlegen",
                    "Gestatten Sie mir, Ihnen die Beweggründe unserer Empfehlung darzulegen",
                ),
                (
                    "word_choice",
                    "Eine engmaschige Überwachung erscheint uns angesichts Ihrer Vorerkrankungen angezeigt.",
                    "Eine engmaschige Überwachung erscheint uns angesichts Ihrer Vorerkrankungen angesagt.",
                    "angesagt",
                    "angezeigt",
                ),
                (
                    "word_choice",
                    "Die Ursache der Beschwerden lässt sich derzeit nicht abschließend klären.",
                    "Die Ursache der Beschwerden lässt sich derzeit nicht abschließend verklären.",
                    "verklären",
                    "klären",
                ),
                (
                    "verb_form",
                    "Wären Sie bereits früher vorstellig geworden, hätte sich der Eingriff womöglich vermeiden lassen.",
                    "Wären Sie bereits früher vorstellig geworden, hätte sich der Eingriff womöglich vermieden lassen.",
                    "vermieden lassen",
                    "vermeiden lassen",
                ),
                (
                    "case",
                    "Ich würde Ihnen dringend raten, sich der Behandlung nicht zu entziehen.",
                    "Ich würde Ihnen dringend raten, sich die Behandlung nicht zu entziehen.",
                    "sich die Behandlung nicht zu entziehen",
                    "sich der Behandlung nicht zu entziehen",
                ),
            ],
        },
    },
    "deutschlernen": {
        "task": "Erzählen Sie, wie Sie Deutsch lernen und wie Sie sich auf die Fachsprachprüfung vorbereiten.",
        "levels": {
            "B1": [
                (
                    "verb_form",
                    "Ich habe vor zwei Jahren angefangen, Deutsch zu lernen.",
                    "Ich habe vor zwei Jahren angefangen, Deutsch zu gelernt.",
                    "Deutsch zu gelernt",
                    "Deutsch zu lernen",
                ),
                (
                    "verb_form",
                    "Letztes Jahr bin ich nach Deutschland gezogen.",
                    "Letztes Jahr bin ich nach Deutschland gezieht.",
                    "gezieht",
                    "gezogen",
                ),
                (
                    "word_choice",
                    "Ich spreche jeden Tag mit meinen Kollegen auf Deutsch.",
                    "Ich sage jeden Tag mit meinen Kollegen auf Deutsch.",
                    "sage jeden Tag mit",
                    "spreche jeden Tag mit",
                ),
                (
                    "word_choice",
                    "Jeden Morgen warte ich auf den Bus.",
                    "Jeden Morgen warte ich für den Bus.",
                    "für den Bus",
                    "auf den Bus",
                ),
                (
                    "gender",
                    "Die Prüfung findet im Frühling statt.",
                    "Der Prüfung findet im Frühling statt.",
                    "Der Prüfung",
                    "Die Prüfung",
                ),
                (
                    "word_order",
                    "Ich hoffe, dass ich die Prüfung bestehe.",
                    "Ich hoffe, dass ich bestehe die Prüfung.",
                    "dass ich bestehe die Prüfung",
                    "dass ich die Prüfung bestehe",
                ),
            ],
            "B2": [
                (
                    "verb_form",
                    "Seit ich in Deutschland arbeite, habe ich viel dazugelernt.",
                    "Seit ich in Deutschland arbeite, habe ich viel dazugelernen.",
                    "habe ich viel dazugelernen",
                    "habe ich viel dazugelernt",
                ),
                (
                    "verb_form",
                    "Wenn ich früher mit dem Lernen begonnen hätte, wäre mir vieles leichter gefallen.",
                    "Wenn ich früher mit dem Lernen begonnen hätte, wäre mir vieles leichter gefallt.",
                    "leichter gefallt",
                    "leichter gefallen",
                ),
                (
                    "word_choice",
                    "Besonders hilfreich finde ich Gespräche mit Muttersprachlern.",
                    "Besonders hilfsbereit finde ich Gespräche mit Muttersprachlern.",
                    "hilfsbereit",
                    "hilfreich",
                ),
                (
                    "word_choice",
                    "Ich nehme mir fest vor, jeden Tag eine Stunde zu üben.",
                    "Ich setze mir fest vor, jeden Tag eine Stunde zu üben.",
                    "Ich setze mir fest vor",
                    "Ich nehme mir fest vor",
                ),
                (
                    "gender",
                    "Das Fachwörterbuch, das ich mir gekauft habe, hilft mir sehr.",
                    "Das Fachwörterbuch, die ich mir gekauft habe, hilft mir sehr.",
                    "die ich mir gekauft habe",
                    "das ich mir gekauft habe",
                ),
                (
                    "word_order",
                    "Gestern habe ich zum ersten Mal einen Patienten vollständig auf Deutsch aufgeklärt.",
                    "Gestern ich habe zum ersten Mal einen Patienten vollständig auf Deutsch aufgeklärt.",
                    "ich habe zum ersten Mal",
                    "habe ich zum ersten Mal",
                ),
            ],
            "C1": [
                (
                    "verb_form",
                    "Hätte ich geahnt, wie vielschichtig die Fachsprache ist, hätte ich früher mit dem Lernen begonnen.",
                    "Hätte ich geahnt, wie vielschichtig die Fachsprache ist, hätte ich früher mit dem Lernen beginnen.",
                    "mit dem Lernen beginnen",
                    "mit dem Lernen begonnen",
                ),
                (
                    "verb_form",
                    "Die Texte, die ich bislang übersetzt habe, waren überwiegend Arztbriefe.",
                    "Die Texte, die ich bislang übersetzen habe, waren überwiegend Arztbriefe.",
                    "übersetzen habe",
                    "übersetzt habe",
                ),
                (
                    "word_choice",
                    "Mir ist klar, dass die Fachsprache nuancenreicher ist als die Alltagssprache.",
                    "Mir ist klar, dass die Fachsprache nuancenreicher ist wie die Alltagssprache.",
                    "ist wie die Alltagssprache",
                    "ist als die Alltagssprache",
                ),
                (
                    "word_choice",
                    "Entscheidend ist für mich, die Patienten verständlich und zugleich präzise aufzuklären.",
                    "Entscheidend ist für mich, die Patienten verständlich und zugleich präzise aufzuhellen.",
                    "aufzuhellen",
                    "aufzuklären",
                ),
                (
                    "gender",
                    "Die Zusammenarbeit mit den Kollegen, deren Rat ich schätze, hat mir enorm geholfen.",
                    "Die Zusammenarbeit mit den Kollegen, dessen Rat ich schätze, hat mir enorm geholfen.",
                    "dessen Rat",
                    "deren Rat",
                ),
                (
                    "word_order",
                    "Sobald ich die Prüfung bestanden habe, möchte ich eine Stelle in der Inneren Medizin antreten.",
                    "Sobald ich die Prüfung bestanden habe, ich möchte eine Stelle in der Inneren Medizin antreten.",
                    "ich möchte eine Stelle",
                    "möchte ich eine Stelle",
                ),
            ],
        },
    },
}
DEV_FAMILIES = ["arbeitstag", "aufklaerung"]
HOLDOUT_FAMILIES = ["deutschlernen"]
VARIANTS: dict[str, tuple[int, ...]] = {
    "clean": (),
    "pair-a": (0, 1),
    "pair-b": (2, 3),
    "mixed": (0, 2, 4),
    "all": (0, 1, 2, 3, 4, 5),
}
# The case file the runner needs for every item (the suite does not read it).
CASE_ID = "SYN-BRUST"


def items(family: str) -> list[dict[str, Any]]:
    spec = FAMILIES[family]
    rows: list[dict[str, Any]] = []
    for level, slots in spec["levels"].items():
        assert len(slots) == 6, (family, level)
        assert slots[0][0] == slots[1][0] and slots[2][0] == slots[3][0], (
            family,
            level,
        )
        assert len({slots[0][0], slots[2][0], slots[4][0], slots[5][0]}) == 4, (
            family,
            level,
        )
        for kind, chosen in VARIANTS.items():
            sentences = []
            errors = []
            for index, (category, ok, wrong, quote, correction) in enumerate(slots):
                assert (
                    wrong.count(quote) == 1 and wrong.replace(quote, correction) == ok
                ), (
                    family,
                    level,
                    index,
                )
                if index in chosen:
                    sentences.append(wrong)
                    errors.append(
                        {"category": category, "quote": quote, "correction": correction}
                    )
                else:
                    sentences.append(ok)
            rows.append(
                {
                    "id": f"{family}-{level.lower()}-{kind}",
                    "family": family,
                    "case_id": CASE_ID,
                    "task": spec["task"],
                    "task_level_hint": level,
                    "target_seconds": 60,
                    "transcript": " ".join(sentences),
                    "expected_level": level,
                    "truth": {"errors": errors},
                }
            )
    return rows


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def main(out: Path) -> None:
    directory = out / "placement" / "1"
    directory.mkdir(parents=True, exist_ok=True)
    cases = [
        case
        for case in json.loads(
            (HERE / "evaluation-language" / "3" / "cases.json").read_text(
                encoding="utf-8"
            )
        )["cases"]
        if case["id"] == CASE_ID
    ]
    files = {
        "cases.json": json.dumps({"cases": cases}, ensure_ascii=False, indent=2) + "\n",
        "dev.jsonl": _jsonl([row for f in DEV_FAMILIES for row in items(f)]),
        "holdout.jsonl": _jsonl([row for f in HOLDOUT_FAMILIES for row in items(f)]),
    }
    for name, content in files.items():
        (directory / name).write_text(content, encoding="utf-8")
    manifest = {
        "suite": "placement",
        "version": "1",
        "description": (
            "Réponses orales de test de niveau, à erreurs injectées (genre, cas, forme verbale, "
            "ordre des mots, choix du mot, registre) : six phrases à un niveau connu (B1, B2, C1), "
            "cinq variantes par texte (sans erreur, deux paires de même catégorie, trois "
            "catégories mélangées, toutes) : vérité connue par construction."
        ),
        "synthetic": True,
        "source": "Énoncés inventés par l'agent, générés par evals/datasets/build_placement_v1.py",
        "created_on": CREATED_ON,
        "consent": "not_applicable:synthetic",
        "annotators": [AGENT],
        "splits": {"dev": DEV_FAMILIES, "holdout": HOLDOUT_FAMILIES},
        "files": {
            name: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for name, content in files.items()
        },
        "limitations": [
            "Énoncés allemands rédigés par l'agent et non relus par un germanophone : une phrase "
            "« correcte » qui contiendrait une vraie faute compterait comme fausse erreur du modèle.",
            "Un petit jeu (trois sujets, 45 transcripts dont 15 en holdout) : les intervalles du "
            "rapport sont larges, il vérifie la chaîne et les ordres de grandeur, pas une précision fine.",
            "Le niveau attendu est celui du texte sans erreur ; il est jugé à un niveau près "
            "(les erreurs injectées peuvent faire baisser l'estimation d'un cran).",
            "Les erreurs de `case` restent à distinguer de `gender` selon la règle de "
            "build_synthetic_v2.py (forme du bon cas mais d'un autre genre : `gender`).",
            "Le holdout (un sujet) n'a ni erreur de cas ni de registre : le gate porte sur les taux "
            "globaux, pas sur une catégorie.",
            "Le cas `SYN-BRUST` n'est qu'un support exigé par le runner : la suite ne le lit pas.",
        ],
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
