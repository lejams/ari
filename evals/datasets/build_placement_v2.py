"""Builds `placement@2`: version 1, two labels corrected, and a new holdout four times larger.

Usage, from the repository root:
    PYTHONPATH=backend/src .venv/bin/python evals/datasets/build_placement_v2.py [out_dir]

Same construction as version 1 (`build_placement_v1.py`: six sentences at a known level, five
variants per text, errors known by construction). What changes, and why (5 November 2026):

1. Two labels of version 1 were doubtful, so they are replaced by unambiguous errors:
   - `arbeitstag` B2, slot 0: « Wenn ich mehr Zeit habe, würde ich … » is common in spoken German;
     replaced by a participle error (« an den Schichtdienst gewöhnen »);
   - `arbeitstag` C1, slot 3: « den gesamten Team » is wrong both in case and in gender, and the
     model called it `gender`; replaced by a pure case error (« das gesamte Team » for the dative).
2. The holdout of version 1 (`deutschlernen`) was run once on 5 November 2026 (run 42023493910):
   it is burnt and joins the development split.
3. The new holdout has four topics never measured (60 transcripts instead of 15), every category
   in it (version 1 had no case or register error there). With 15 items, a gate of 10 % lets one
   item change, so a rater really at 10 % fails one time out of two; with 60 items the verdict
   is no longer a lottery.

Everything is invented, nothing comes from a real learner; no German-speaking human reviewed the
sentences (decision D3 of 2 November 2026; limitation recorded in the manifest). The output is
deterministic and a test rebuilds it: the committed files are never edited by hand.
"""

from __future__ import annotations

import copy
import hashlib
import json
import runpy
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CREATED_ON = "2026-11-05"
AGENT = {
    "id": "agent:claude-opus-5.5",
    "role": "rédaction synthétique",
    "annotated_on": CREATED_ON,
}
V1 = runpy.run_path(str(HERE / "build_placement_v1.py"))
VARIANTS: dict[str, tuple[int, ...]] = V1["VARIANTS"]
CASE_ID: str = V1["CASE_ID"]

FAMILIES: dict[str, dict[str, Any]] = copy.deepcopy(V1["FAMILIES"])
FAMILIES["arbeitstag"]["levels"]["B2"][0] = (
    "verb_form",
    "Inzwischen habe ich mich gut an den Schichtdienst gewöhnt.",
    "Inzwischen habe ich mich gut an den Schichtdienst gewöhnen.",
    "an den Schichtdienst gewöhnen",
    "an den Schichtdienst gewöhnt",
)
FAMILIES["arbeitstag"]["levels"]["C1"][3] = (
    "case",
    "Ich bin dem gesamten Team dankbar, das mich in der Anfangszeit so tatkräftig unterstützt hat.",
    "Ich bin das gesamte Team dankbar, das mich in der Anfangszeit so tatkräftig unterstützt hat.",
    "das gesamte Team",
    "dem gesamten Team",
)

# Slot: (category, correct sentence, sentence with the error, quote as spoken, correction).
# Categories, as in version 1: `gender` is the right case of another gender, `case` the right
# gender in another case, `verb_form` a conjugation, participle, infinitive or auxiliary,
# `word_order` the place of the verb, `word_choice` a wrong word or collocation, `register` « du »
# where the situation calls for « Sie ».
FAMILIES["uebergabe"] = {
    "task": "Sie übergeben am Ende Ihres Dienstes einen Patienten an eine Kollegin. Berichten Sie, was passiert ist und was noch zu tun ist.",
    "levels": {
        "B1": [
            (
                "verb_form",
                "Herr Braun ist heute Morgen in die Notaufnahme gekommen.",
                "Herr Braun hat heute Morgen in die Notaufnahme gekommen.",
                "hat heute Morgen in die Notaufnahme gekommen",
                "ist heute Morgen in die Notaufnahme gekommen",
            ),
            (
                "verb_form",
                "Er nimmt seine Tabletten jeden Morgen.",
                "Er nehmt seine Tabletten jeden Morgen.",
                "Er nehmt",
                "Er nimmt",
            ),
            (
                "case",
                "Ich habe mit seiner Frau telefoniert.",
                "Ich habe mit seine Frau telefoniert.",
                "mit seine Frau",
                "mit seiner Frau",
            ),
            (
                "case",
                "Die Ergebnisse liegen auf dem Tisch im Arztzimmer.",
                "Die Ergebnisse liegen auf den Tisch im Arztzimmer.",
                "auf den Tisch",
                "auf dem Tisch",
            ),
            (
                "gender",
                "Das Röntgenbild zeigt keine Auffälligkeiten.",
                "Der Röntgenbild zeigt keine Auffälligkeiten.",
                "Der Röntgenbild",
                "Das Röntgenbild",
            ),
            (
                "word_order",
                "Ich glaube, dass er morgen nach Hause gehen kann.",
                "Ich glaube, dass er kann morgen nach Hause gehen.",
                "dass er kann morgen nach Hause gehen",
                "dass er morgen nach Hause gehen kann",
            ),
        ],
        "B2": [
            (
                "case",
                "Der Patient klagt seit gestern über starke Schmerzen im rechten Unterbauch.",
                "Der Patient klagt seit gestern über starken Schmerzen im rechten Unterbauch.",
                "über starken Schmerzen",
                "über starke Schmerzen",
            ),
            (
                "case",
                "Ich habe dem diensthabenden Oberarzt den Befund gezeigt.",
                "Ich habe den diensthabenden Oberarzt den Befund gezeigt.",
                "den diensthabenden Oberarzt",
                "dem diensthabenden Oberarzt",
            ),
            (
                "word_choice",
                "Die Laborwerte sprechen für eine Entzündung.",
                "Die Laborwerte reden für eine Entzündung.",
                "reden für",
                "sprechen für",
            ),
            (
                "word_choice",
                "Er hat das Schmerzmittel gut vertragen.",
                "Er hat das Schmerzmittel gut ertragen.",
                "gut ertragen",
                "gut vertragen",
            ),
            (
                "verb_form",
                "Falls sich sein Zustand verschlechtert, sollte er sofort operiert werden.",
                "Falls sich sein Zustand verschlechtert, sollte er sofort operieren werden.",
                "operieren werden",
                "operiert werden",
            ),
            (
                "word_order",
                "Deshalb habe ich die Blutabnahme für morgen früh angeordnet.",
                "Deshalb ich habe die Blutabnahme für morgen früh angeordnet.",
                "Deshalb ich habe",
                "Deshalb habe ich",
            ),
        ],
        "C1": [
            (
                "word_order",
                "Da sich die Symptomatik unter der Therapie kaum gebessert hat, sollten wir die Antibiose umstellen.",
                "Da sich die Symptomatik unter der Therapie kaum hat gebessert, sollten wir die Antibiose umstellen.",
                "kaum hat gebessert",
                "kaum gebessert hat",
            ),
            (
                "word_order",
                "Ob eine Operation notwendig ist, lässt sich derzeit noch nicht beurteilen.",
                "Ob eine Operation ist notwendig, lässt sich derzeit noch nicht beurteilen.",
                "Ob eine Operation ist notwendig",
                "Ob eine Operation notwendig ist",
            ),
            (
                "verb_form",
                "Die Angehörigen möchten umgehend benachrichtigt werden, sobald neue Befunde vorliegen.",
                "Die Angehörigen möchten umgehend benachrichtigen werden, sobald neue Befunde vorliegen.",
                "benachrichtigen werden",
                "benachrichtigt werden",
            ),
            (
                "verb_form",
                "Hätten wir die Laborwerte früher gekannt, hätten wir anders entschieden.",
                "Hätten wir die Laborwerte früher gekennt, hätten wir anders entschieden.",
                "früher gekennt",
                "früher gekannt",
            ),
            (
                "case",
                "Angesichts der steigenden Entzündungswerte halte ich eine CT-Untersuchung für angezeigt.",
                "Angesichts die steigenden Entzündungswerte halte ich eine CT-Untersuchung für angezeigt.",
                "Angesichts die steigenden Entzündungswerte",
                "Angesichts der steigenden Entzündungswerte",
            ),
            (
                "word_choice",
                "Ich würde vorschlagen, die Entscheidung über das weitere Vorgehen bis morgen zurückzustellen.",
                "Ich würde vorschlagen, die Entscheidung über das weitere Vorgehen bis morgen zurückzulegen.",
                "zurückzulegen",
                "zurückzustellen",
            ),
        ],
    },
}
FAMILIES["anamnese"] = {
    "task": "Eine Patientin kommt mit Bauchschmerzen in Ihre Sprechstunde. Begrüßen Sie sie, stellen Sie Ihre Fragen und erklären Sie, was Sie als Nächstes tun.",
    "levels": {
        "B1": [
            (
                "register",
                "Guten Morgen, Frau Schulz, was führt Sie zu mir?",
                "Guten Morgen, Frau Schulz, was führt dich zu mir?",
                "was führt dich zu mir",
                "was führt Sie zu mir",
            ),
            (
                "register",
                "Seit wann haben Sie die Schmerzen?",
                "Seit wann hast du die Schmerzen?",
                "hast du",
                "haben Sie",
            ),
            (
                "verb_form",
                "Haben Sie heute schon etwas getrunken?",
                "Haben Sie heute schon etwas getrinkt?",
                "getrinkt",
                "getrunken",
            ),
            (
                "verb_form",
                "Nehmen Sie zurzeit Medikamente?",
                "Nimmen Sie zurzeit Medikamente?",
                "Nimmen Sie",
                "Nehmen Sie",
            ),
            (
                "case",
                "Bitte legen Sie sich auf die Liege.",
                "Bitte legen Sie sich auf der Liege.",
                "auf der Liege",
                "auf die Liege",
            ),
            (
                "word_choice",
                "Danach mache ich einen Ultraschall.",
                "Danach tue ich einen Ultraschall.",
                "tue ich einen Ultraschall",
                "mache ich einen Ultraschall",
            ),
        ],
        "B2": [
            (
                "word_choice",
                "Können Sie den Schmerz genauer beschreiben?",
                "Können Sie den Schmerz genauer erzählen?",
                "den Schmerz genauer erzählen",
                "den Schmerz genauer beschreiben",
            ),
            (
                "word_choice",
                "Ich verschreibe Ihnen ein Medikament gegen die Übelkeit.",
                "Ich schreibe Ihnen ein Medikament gegen die Übelkeit vor.",
                "schreibe Ihnen ein Medikament gegen die Übelkeit vor",
                "verschreibe Ihnen ein Medikament gegen die Übelkeit",
            ),
            (
                "register",
                "Ich verstehe, dass Sie sich Sorgen machen.",
                "Ich verstehe, dass du dir Sorgen machst.",
                "dass du dir Sorgen machst",
                "dass Sie sich Sorgen machen",
            ),
            (
                "register",
                "Wären Sie damit einverstanden, dass wir eine Blutprobe nehmen?",
                "Wärst du damit einverstanden, dass wir eine Blutprobe nehmen?",
                "Wärst du",
                "Wären Sie",
            ),
            (
                "gender",
                "Die Untersuchung, die wir jetzt durchführen, ist völlig schmerzfrei.",
                "Die Untersuchung, den wir jetzt durchführen, ist völlig schmerzfrei.",
                "den wir jetzt durchführen",
                "die wir jetzt durchführen",
            ),
            (
                "verb_form",
                "Sollte das Fieber steigen, kommen Sie bitte sofort wieder.",
                "Sollte das Fieber steigt, kommen Sie bitte sofort wieder.",
                "Sollte das Fieber steigt",
                "Sollte das Fieber steigen",
            ),
        ],
        "C1": [
            (
                "register",
                "Ich kann gut nachvollziehen, dass Sie verunsichert sind.",
                "Ich kann gut nachvollziehen, dass du verunsichert bist.",
                "dass du verunsichert bist",
                "dass Sie verunsichert sind",
            ),
            (
                "register",
                "Darf ich Sie bitten, mir Ihre bisherigen Befunde mitzubringen?",
                "Darf ich dich bitten, mir deine bisherigen Befunde mitzubringen?",
                "dich bitten, mir deine bisherigen Befunde",
                "Sie bitten, mir Ihre bisherigen Befunde",
            ),
            (
                "case",
                "Aufgrund Ihrer Beschwerden halte ich eine Magenspiegelung für sinnvoll.",
                "Aufgrund Ihre Beschwerden halte ich eine Magenspiegelung für sinnvoll.",
                "Aufgrund Ihre Beschwerden",
                "Aufgrund Ihrer Beschwerden",
            ),
            (
                "case",
                "Wir sollten uns zunächst einen Überblick über Ihre Vorerkrankungen verschaffen.",
                "Wir sollten uns zunächst einen Überblick über Ihren Vorerkrankungen verschaffen.",
                "über Ihren Vorerkrankungen",
                "über Ihre Vorerkrankungen",
            ),
            (
                "word_choice",
                "Ein bösartiger Befund ist zwar unwahrscheinlich, lässt sich aber nicht gänzlich ausschließen.",
                "Ein bösartiger Befund ist zwar unwahrscheinlich, lässt sich aber nicht gänzlich ausschalten.",
                "gänzlich ausschalten",
                "gänzlich ausschließen",
            ),
            (
                "gender",
                "Die Ursache, auf die Ihre Beschwerden zurückzuführen sind, lässt sich meist gut behandeln.",
                "Die Ursache, auf den Ihre Beschwerden zurückzuführen sind, lässt sich meist gut behandeln.",
                "auf den Ihre Beschwerden",
                "auf die Ihre Beschwerden",
            ),
        ],
    },
}
FAMILIES["freizeit"] = {
    "task": "Erzählen Sie, wie Sie Ihre Freizeit in Deutschland verbringen und wie Sie sich hier eingelebt haben.",
    "levels": {
        "B1": [
            (
                "gender",
                "Am Wochenende gehe ich gern in den Park.",
                "Am Wochenende gehe ich gern in das Park.",
                "in das Park",
                "in den Park",
            ),
            (
                "gender",
                "Meine Wohnung liegt ganz in der Nähe der Klinik.",
                "Mein Wohnung liegt ganz in der Nähe der Klinik.",
                "Mein Wohnung",
                "Meine Wohnung",
            ),
            (
                "word_order",
                "Manchmal koche ich mit meinen Nachbarn.",
                "Manchmal ich koche mit meinen Nachbarn.",
                "Manchmal ich koche",
                "Manchmal koche ich",
            ),
            (
                "word_order",
                "Ich lerne jeden Abend Deutsch, weil ich die Sprache besser sprechen möchte.",
                "Ich lerne jeden Abend Deutsch, weil ich möchte die Sprache besser sprechen.",
                "weil ich möchte die Sprache besser sprechen",
                "weil ich die Sprache besser sprechen möchte",
            ),
            (
                "verb_form",
                "Letzten Sommer bin ich mit Freunden an die Ostsee gefahren.",
                "Letzten Sommer bin ich mit Freunden an die Ostsee gefahrt.",
                "gefahrt",
                "gefahren",
            ),
            (
                "case",
                "Im Winter fahre ich mit dem Zug zu meiner Schwester.",
                "Im Winter fahre ich mit den Zug zu meiner Schwester.",
                "mit den Zug",
                "mit dem Zug",
            ),
        ],
        "B2": [
            (
                "verb_form",
                "Anfangs hätte ich nicht gedacht, dass ich mich so schnell einleben würde.",
                "Anfangs hätte ich nicht gedenkt, dass ich mich so schnell einleben würde.",
                "nicht gedenkt",
                "nicht gedacht",
            ),
            (
                "verb_form",
                "Mittlerweile werde ich von meinen Nachbarn oft zum Essen eingeladen.",
                "Mittlerweile werde ich von meinen Nachbarn oft zum Essen eingeladet.",
                "zum Essen eingeladet",
                "zum Essen eingeladen",
            ),
            (
                "gender",
                "Der Verein, in dem ich Fußball spiele, trainiert zweimal pro Woche.",
                "Der Verein, in der ich Fußball spiele, trainiert zweimal pro Woche.",
                "in der ich Fußball spiele",
                "in dem ich Fußball spiele",
            ),
            (
                "gender",
                "Besonders gefällt mir das kulturelle Angebot der Stadt.",
                "Besonders gefällt mir der kulturelle Angebot der Stadt.",
                "der kulturelle Angebot",
                "das kulturelle Angebot",
            ),
            (
                "word_choice",
                "In meiner Freizeit treibe ich viel Sport.",
                "In meiner Freizeit spiele ich viel Sport.",
                "spiele ich viel Sport",
                "treibe ich viel Sport",
            ),
            (
                "case",
                "Ich interessiere mich sehr für die deutsche Geschichte.",
                "Ich interessiere mich sehr für der deutschen Geschichte.",
                "für der deutschen Geschichte",
                "für die deutsche Geschichte",
            ),
        ],
        "C1": [
            (
                "case",
                "Ungeachtet der anfänglichen Sprachbarrieren habe ich schnell Anschluss gefunden.",
                "Ungeachtet die anfänglichen Sprachbarrieren habe ich schnell Anschluss gefunden.",
                "Ungeachtet die anfänglichen Sprachbarrieren",
                "Ungeachtet der anfänglichen Sprachbarrieren",
            ),
            (
                "case",
                "Ich bin meinen Nachbarn für ihre Hilfsbereitschaft sehr dankbar.",
                "Ich bin meine Nachbarn für ihre Hilfsbereitschaft sehr dankbar.",
                "Ich bin meine Nachbarn",
                "Ich bin meinen Nachbarn",
            ),
            (
                "word_choice",
                "Das kulturelle Leben hier hat meine Erwartungen bei Weitem übertroffen.",
                "Das kulturelle Leben hier hat meine Erwartungen bei Weitem überholt.",
                "bei Weitem überholt",
                "bei Weitem übertroffen",
            ),
            (
                "word_choice",
                "Ehrenamtliche Tätigkeiten bieten eine gute Gelegenheit, Kontakte zu knüpfen.",
                "Ehrenamtliche Tätigkeiten bieten eine gute Gelegenheit, Kontakte zu binden.",
                "Kontakte zu binden",
                "Kontakte zu knüpfen",
            ),
            (
                "verb_form",
                "Wäre ich nicht nach Deutschland gekommen, hätte ich diese Erfahrungen nie gemacht.",
                "Wäre ich nicht nach Deutschland gekommen, hätte ich diese Erfahrungen nie machen.",
                "nie machen",
                "nie gemacht",
            ),
            (
                "word_order",
                "Obwohl ich anfangs Heimweh hatte, fühle ich mich hier inzwischen zu Hause.",
                "Obwohl ich hatte anfangs Heimweh, fühle ich mich hier inzwischen zu Hause.",
                "Obwohl ich hatte anfangs Heimweh",
                "Obwohl ich anfangs Heimweh hatte",
            ),
        ],
    },
}
FAMILIES["medikament"] = {
    "task": "Sie verschreiben einer Patientin ein neues Medikament. Erklären Sie ihr, wie und wann sie es einnehmen soll und worauf sie achten muss.",
    "levels": {
        "B1": [
            (
                "word_choice",
                "Bitte nehmen Sie die Tablette nach dem Frühstück.",
                "Bitte essen Sie die Tablette nach dem Frühstück.",
                "essen Sie die Tablette",
                "nehmen Sie die Tablette",
            ),
            (
                "word_choice",
                "Bitte bewahren Sie das Medikament im Kühlschrank auf.",
                "Bitte halten Sie das Medikament im Kühlschrank auf.",
                "halten Sie das Medikament im Kühlschrank auf",
                "bewahren Sie das Medikament im Kühlschrank auf",
            ),
            (
                "register",
                "Haben Sie noch Fragen?",
                "Hast du noch Fragen?",
                "Hast du noch Fragen",
                "Haben Sie noch Fragen",
            ),
            (
                "register",
                "Rufen Sie mich bitte an, wenn Sie Probleme haben.",
                "Ruf mich bitte an, wenn du Probleme hast.",
                "Ruf mich bitte an, wenn du Probleme hast",
                "Rufen Sie mich bitte an, wenn Sie Probleme haben",
            ),
            (
                "gender",
                "Das Medikament senkt den Blutdruck.",
                "Der Medikament senkt den Blutdruck.",
                "Der Medikament",
                "Das Medikament",
            ),
            (
                "word_order",
                "Wenn Ihnen schwindelig wird, setzen Sie sich bitte hin.",
                "Wenn Ihnen wird schwindelig, setzen Sie sich bitte hin.",
                "Wenn Ihnen wird schwindelig",
                "Wenn Ihnen schwindelig wird",
            ),
        ],
        "B2": [
            (
                "register",
                "Ich möchte Ihnen kurz erklären, wie dieses Medikament wirkt.",
                "Ich möchte dir kurz erklären, wie dieses Medikament wirkt.",
                "möchte dir kurz erklären",
                "möchte Ihnen kurz erklären",
            ),
            (
                "register",
                "Achten Sie bitte darauf, keinen Alkohol zu trinken.",
                "Achte bitte darauf, keinen Alkohol zu trinken.",
                "Achte bitte darauf",
                "Achten Sie bitte darauf",
            ),
            (
                "case",
                "Nehmen Sie das Medikament nicht zusammen mit anderen Schmerzmitteln ein.",
                "Nehmen Sie das Medikament nicht zusammen mit andere Schmerzmittel ein.",
                "mit andere Schmerzmittel",
                "mit anderen Schmerzmitteln",
            ),
            (
                "case",
                "Die Dosis hängt von Ihrem Körpergewicht ab.",
                "Die Dosis hängt von Ihr Körpergewicht ab.",
                "von Ihr Körpergewicht",
                "von Ihrem Körpergewicht",
            ),
            (
                "verb_form",
                "Die Tabletten dürfen nicht zerkleinert werden.",
                "Die Tabletten dürfen nicht zerkleinern werden.",
                "zerkleinern werden",
                "zerkleinert werden",
            ),
            (
                "word_choice",
                "In seltenen Fällen kann es zu Kopfschmerzen kommen.",
                "In seltenen Fällen kann es zu Kopfschmerzen gehen.",
                "zu Kopfschmerzen gehen",
                "zu Kopfschmerzen kommen",
            ),
        ],
        "C1": [
            (
                "verb_form",
                "Hätten Sie das Medikament regelmäßig eingenommen, wäre der Blutdruck vermutlich stabil geblieben.",
                "Hätten Sie das Medikament regelmäßig eingenommen, wäre der Blutdruck vermutlich stabil gebleibt.",
                "stabil gebleibt",
                "stabil geblieben",
            ),
            (
                "verb_form",
                "Es empfiehlt sich, die Einnahme nicht eigenmächtig abzubrechen.",
                "Es empfiehlt sich, die Einnahme nicht eigenmächtig abzubrochen.",
                "eigenmächtig abzubrochen",
                "eigenmächtig abzubrechen",
            ),
            (
                "register",
                "Ich kann Ihnen versichern, dass sich dieses Präparat seit Jahren bewährt hat.",
                "Ich kann dir versichern, dass sich dieses Präparat seit Jahren bewährt hat.",
                "kann dir versichern",
                "kann Ihnen versichern",
            ),
            (
                "register",
                "Sollten Sie Bedenken haben, sprechen Sie mich jederzeit an.",
                "Solltest du Bedenken haben, sprich mich jederzeit an.",
                "Solltest du Bedenken haben, sprich mich",
                "Sollten Sie Bedenken haben, sprechen Sie mich",
            ),
            (
                "gender",
                "Das Risiko, dem wir damit vorbeugen, ist ein erneuter Schlaganfall.",
                "Das Risiko, der wir damit vorbeugen, ist ein erneuter Schlaganfall.",
                "der wir damit vorbeugen",
                "dem wir damit vorbeugen",
            ),
            (
                "case",
                "Infolge der Dosiserhöhung kann vorübergehend Müdigkeit auftreten.",
                "Infolge die Dosiserhöhung kann vorübergehend Müdigkeit auftreten.",
                "Infolge die Dosiserhöhung",
                "Infolge der Dosiserhöhung",
            ),
        ],
    },
}
DEV_FAMILIES = ["arbeitstag", "aufklaerung", "deutschlernen"]
HOLDOUT_FAMILIES = ["uebergabe", "anamnese", "freizeit", "medikament"]


def items(family: str) -> list[dict[str, Any]]:
    """Same rows as version 1 (`build_placement_v1.items`), from this version's sentences."""
    spec = FAMILIES[family]
    rows: list[dict[str, Any]] = []
    for level, slots in spec["levels"].items():
        assert len(slots) == 6, (family, level)
        assert slots[0][0] == slots[1][0] and slots[2][0] == slots[3][0], (family, level)
        assert len({slots[0][0], slots[2][0], slots[4][0], slots[5][0]}) == 4, (family, level)
        for kind, chosen in VARIANTS.items():
            sentences = []
            errors = []
            for index, (category, ok, wrong, quote, correction) in enumerate(slots):
                assert wrong.count(quote) == 1 and wrong.replace(quote, correction) == ok, (
                    family,
                    level,
                    index,
                )
                if index in chosen:
                    sentences.append(wrong)
                    errors.append({"category": category, "quote": quote, "correction": correction})
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
    directory = out / "placement" / "2"
    directory.mkdir(parents=True, exist_ok=True)
    cases = [
        case
        for case in json.loads(
            (HERE / "evaluation-language" / "3" / "cases.json").read_text(encoding="utf-8")
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
        "version": "2",
        "description": (
            "Réponses orales de test de niveau, à erreurs injectées (genre, cas, forme verbale, "
            "ordre des mots, choix du mot, registre), construites comme la version 1 : en dev, les "
            "trois sujets de la version 1 (deux étiquettes douteuses remplacées, l'ancien holdout "
            "brûlé inclus) ; en holdout, quatre sujets neufs (60 transcripts, toutes les catégories)."
        ),
        "synthetic": True,
        "source": "Énoncés inventés par l'agent, générés par evals/datasets/build_placement_v2.py",
        "created_on": CREATED_ON,
        "consent": "not_applicable:synthetic",
        "annotators": [V1["AGENT"], AGENT],
        "splits": {"dev": DEV_FAMILIES, "holdout": HOLDOUT_FAMILIES},
        "files": {
            name: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for name, content in files.items()
        },
        "limitations": [
            "Énoncés allemands rédigés par l'agent et non relus par un germanophone (décision D3) : "
            "une phrase « correcte » qui contiendrait une vraie faute compterait comme fausse "
            "erreur du modèle.",
            "Le niveau attendu est celui du texte sans erreur, écrit par l'agent : il est jugé à un "
            "niveau près (les erreurs injectées peuvent faire baisser l'estimation d'un cran), le "
            "niveau exact n'est qu'indicatif.",
            "Les erreurs de `case` restent à distinguer de `gender` selon la règle de "
            "build_synthetic_v2.py (forme du bon cas mais d'un autre genre : `gender`).",
            "Le dev contient l'ancien holdout (`deutschlernen`), déjà lancé une fois : aucun "
            "réglage ne doit se juger sur lui seul.",
            "Le cas `SYN-BRUST` n'est qu'un support exigé par le runner : la suite ne le lit pas.",
        ],
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
