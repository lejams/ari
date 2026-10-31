"""Builds `evaluation-language@2` from invented cases and invented doctor utterances.

Usage, from the repository root:
    PYTHONPATH=backend/src .venv/bin/python evals/datasets/build_synthetic_v2.py [out_dir]

Why a version 2 (decision of 31 October 2026, after the first run on the real models):
- version 1 injected no `word_choice` or `register` error, so a single report of those categories
  gave a precision of zero; both are injected now, in every family;
- version 1 had one holdout family, which a run had already shown to the people tuning the
  prompt; it is now a development family, and the holdout is three new families with their own
  phrasings;
- correct but unusual sentences ("natural" variants: spoken ellipsis, topicalised questions,
  regional greetings) are added, so that an evaluator that over-corrects is measured;
- bigger splits: 100 development and 75 holdout transcripts.

The four families of version 1 are reused as they are (cases, slots) and receive extra variants.
Everything is invented, nothing comes from a real patient, a real session or an exam protocol
(plan, invariant 8). The output is deterministic and a test rebuilds it: the committed files are
never edited by hand.

Annotation rule for the categories that were confused (see also the scorer):
- gender: the form belongs to the right case but to another gender ("dem Familie" for "der
  Familie", "die Schmerz" for "der Schmerz");
- case: the form belongs to the right gender but to another case ("die Familie" for "der Familie"
  after "aus", "mich" for "mir", "den Rückenschmerzen" for "die Rückenschmerzen");
- word_choice: a word that does not fit or does not exist ("essen" a medication, "Berührung"
  for "Kontakt"); a wrong FORM of a word that exists is never word_choice;
- register: "du" for "Sie", or a vulgar word.

The German variants were written by the agent and still need a review by a German speaker
before the language gate is trusted (see the manifest limitations).
"""

from __future__ import annotations

import hashlib
import json
import random
import runpy
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
V1 = runpy.run_path(str(HERE / "build_synthetic_v1.py"))
fact, case = V1["fact"], V1["case"]

CREATED_ON = "2026-10-31"
AGENT = {"id": "agent:claude-sonnet-5.5", "role": "rédaction synthétique", "annotated_on": CREATED_ON}
DEV_FAMILIES = ["brustschmerz", "bauchschmerz", "atemnot", "kopfschmerz"]
HOLDOUT_FAMILIES = ["rueckenschmerz", "schwindel", "halsschmerz"]
TRANSCRIPTS_PER_FAMILY = 25
LANGUAGE_KINDS = ("gender", "case", "verb_form", "word_order", "word_choice", "register")
KINDS = (*LANGUAGE_KINDS, "stt", "french", "natural")
OPTIONAL_SECTIONS = V1["OPTIONAL_SECTIONS"]

SECTIONS = [
    ("patientendaten", "Patientendaten"),
    ("aktuelle_beschwerden", "Aktuelle Beschwerden"),
    ("vorerkrankungen", "Vorerkrankungen"),
    ("medikamente", "Medikamente"),
    ("allergien", "Allergien"),
    ("noxen", "Noxen"),
    ("familienanamnese", "Familienanamnese"),
    ("sozialanamnese", "Sozialanamnese"),
]


def sections(current: list[str]) -> list[tuple[str, str, list[str]]]:
    by_id = {
        "patientendaten": ["identity"],
        "aktuelle_beschwerden": current,
        "vorerkrankungen": ["history"],
        "medikamente": ["medication"],
        "allergien": ["allergy"],
        "noxen": ["smoking"],
        "familienanamnese": ["family"],
        "sozialanamnese": ["occupation"],
    }
    return [(sid, label, by_id[sid]) for sid, label in SECTIONS]


NEW_CASES: dict[str, dict[str, Any]] = {
    "rueckenschmerz": case(
        "SYN-RUECKEN",
        "Synthetischer Fall: Rückenschmerz",
        "Guten Tag. Ich habe seit einer Woche starke Rückenschmerzen.",
        "sachlich, etwas ungeduldig",
        [
            fact("identity", "identity", "Thomas Brandt, 52 Jahre", "Ich bin Thomas Brandt, zweiundfünfzig Jahre alt."),
            fact("reason", "reason", "Rückenschmerzen", "Ich habe seit einer Woche starke Rückenschmerzen.", "spontaneous"),
            fact("onset", "onset", "seit einer Woche, nach dem Heben eines Schranks", "Es hat angefangen, als ich einen Schrank getragen habe."),
            fact("character", "pain_character", "stechend, unterer Rücken", "Es sticht unten im Rücken."),
            fact("radiation", "pain_radiation", "Ausstrahlung ins rechte Bein", "Der Schmerz zieht in das rechte Bein."),
            fact("associated", "associated_symptoms", "Kribbeln im rechten Fuß", "Mein rechter Fuß kribbelt manchmal."),
            fact("history", "medical_history", "Bandscheibenvorfall vor drei Jahren", "Vor drei Jahren hatte ich einen Bandscheibenvorfall."),
            fact("medication", "medication", "Diclofenac 50 mg bei Bedarf", "Ich nehme Diclofenac, fünfzig Milligramm, wenn es schlimm ist."),
            fact("allergy", "allergy", "keine bekannten Allergien", "Allergien habe ich keine."),
            fact("smoking", "smoking", "10 Zigaretten täglich", "Ich rauche etwa zehn Zigaretten am Tag.", "when_explicitly_asked"),
            fact("family", "family_history", "Vater: Osteoporose", "Mein Vater hatte Osteoporose."),
            fact("occupation", "occupation", "Lagerarbeiter, hebt täglich schwer", "Ich arbeite im Lager und hebe jeden Tag schwere Kisten."),
        ],
        sections(["reason", "onset", "character", "radiation", "associated"]),
    ),
    "schwindel": case(
        "SYN-SCHWINDEL",
        "Synthetischer Fall: Schwindel",
        "Guten Tag. Mir ist seit gestern ständig schwindelig.",
        "unsicher, spricht langsam",
        [
            fact("identity", "identity", "Petra Lindner, 71 Jahre", "Ich bin Petra Lindner und einundsiebzig Jahre alt."),
            fact("reason", "reason", "Schwindel", "Mir ist seit gestern ständig schwindelig.", "spontaneous"),
            fact("onset", "onset", "erstmals gestern Morgen", "Gestern Morgen ist es zum ersten Mal passiert."),
            fact("character", "dizziness_type", "Drehschwindel", "Alles dreht sich wie auf einem Karussell."),
            fact("trigger", "trigger", "schlimmer beim Aufstehen", "Beim Aufstehen wird es viel schlimmer."),
            fact("associated", "associated_symptoms", "Übelkeit und Ohrensausen links", "Mir ist übel, und links rauscht es im Ohr."),
            fact("history", "medical_history", "Bluthochdruck seit 15 Jahren", "Ich habe seit fünfzehn Jahren hohen Blutdruck."),
            fact("medication", "medication", "Amlodipin 5 mg morgens", "Morgens nehme ich Amlodipin, fünf Milligramm."),
            fact("allergy", "allergy", "Latexallergie", "Gegen Latex bin ich allergisch."),
            fact("smoking", "smoking", "Nichtraucherin", "Ich habe nie geraucht.", "when_explicitly_asked"),
            fact("family", "family_history", "Schwester: Schlaganfall mit 68", "Meine Schwester hatte mit achtundsechzig einen Schlaganfall."),
            fact("occupation", "occupation", "Rentnerin, früher Verkäuferin", "Ich bin Rentnerin, früher war ich Verkäuferin."),
        ],
        sections(["reason", "onset", "character", "trigger", "associated"]),
    ),
    "halsschmerz": case(
        "SYN-HALS",
        "Synthetischer Fall: Halsschmerz",
        "Guten Abend. Mein Hals tut seit drei Tagen sehr weh.",
        "heiser, flüstert fast",
        [
            fact("identity", "identity", "Jonas Albrecht, 28 Jahre", "Ich heiße Jonas Albrecht und bin achtundzwanzig."),
            fact("reason", "reason", "Halsschmerzen", "Mein Hals tut seit drei Tagen sehr weh.", "spontaneous"),
            fact("onset", "onset", "seit drei Tagen", "Seit drei Tagen geht das schon so."),
            fact("character", "pain_character", "schlimmer beim Schlucken", "Beim Schlucken ist es am schlimmsten."),
            fact("associated", "associated_symptoms", "Fieber 38,6 °C, Schüttelfrost", "Gestern hatte ich achtunddreißig sechs Fieber und Schüttelfrost."),
            fact("history", "medical_history", "Mandelentzündung vor zwei Jahren", "Vor zwei Jahren hatte ich eine Mandelentzündung."),
            fact("medication", "medication", "Ibuprofen 400 mg", "Ich habe gestern Ibuprofen vierhundert genommen."),
            fact("allergy", "allergy", "Pollenallergie", "Ich habe eine Pollenallergie."),
            fact("smoking", "smoking", "Gelegenheitsraucher", "Ich rauche manchmal am Wochenende.", "when_explicitly_asked"),
            fact("family", "family_history", "Bruder: häufige Halsentzündungen", "Mein Bruder hat oft Halsentzündungen."),
            fact("occupation", "occupation", "Erzieher in einer Kita", "Ich bin Erzieher in einer Kita."),
        ],
        sections(["reason", "onset", "character", "associated"]),
    ),
}

CASES: dict[str, dict[str, Any]] = {
    family: V1["CASES"][family] for family in DEV_FAMILIES
} | NEW_CASES

# ---------------------------------------------------------------------------------------
# Slots. (facts revealed, correct utterance, {variant kind: utterance or (utterance, term)}).
# `natural` is a correct sentence worded differently: nothing is injected, any report on it is
# a false error. The four families of version 1 are reused and extended below.
# ---------------------------------------------------------------------------------------
V1_SLOTS: dict[str, list[tuple[list[str], str, dict[str, Any]]]] = V1["SLOTS"]

EXTRA: dict[str, list[dict[str, Any]]] = {
    "brustschmerz": [
        {"register": "Guten Tag, ich bin Doktor Martin. Was führt dich heute zu uns?",
         "natural": "Guten Tag, Doktor Martin hier. Was führt Sie zu uns?"},
        {"word_choice": "Wie benennen Sie sich, und wie alt sind Sie?",
         "natural": "Name und Alter, bitte?"},
        {"word_choice": "Seit wann besitzen Sie die Schmerzen?",
         "register": "Seit wann hast du die Schmerzen?",
         "natural": "Und die Schmerzen, seit wann?"},
        {"word_choice": "Wie fühlt sich der Schmerz an? Ist er eher drückend oder stoßend?",
         "natural": "Wie würden Sie den Schmerz beschreiben, eher drückend oder stechend?"},
        {"register": "Strahlt der Schmerz bei dir in den Arm oder in den Rücken aus?",
         "natural": "Zieht der Schmerz in den Arm oder in den Rücken?"},
        {"word_choice": "Leiden Sie an einer langen Erkrankung, zum Beispiel an Bluthochdruck?",
         "natural": "Haben Sie eine chronische Erkrankung, etwa Bluthochdruck?"},
        {"word_choice": "Essen Sie regelmäßig Medikamente?",
         "register": "Nimmst du regelmäßig Medikamente ein?",
         "natural": "Medikamente nehmen Sie regelmäßig ein?"},
        {"register": "Bist du gegen ein Medikament allergisch?",
         "natural": "Allergisch gegen irgendein Medikament?"},
        {"register": "Rauchst du? Wenn ja, wie viele Zigaretten am Tag?",
         "natural": "Sie rauchen? Wie viele Zigaretten am Tag?"},
        {"register": "Gibt es in deiner Familie Herzerkrankungen?",
         "natural": "Herzerkrankungen in der Familie, gibt es da welche?"},
        {"word_choice": "Was sind Sie von Arbeit?",
         "register": "Was bist du von Beruf?",
         "natural": "Und beruflich, was machen Sie?"},
    ],
    "bauchschmerz": [
        {"register": "Hallo, mein Name ist Doktor Weber. Was kann ich für dich tun?",
         "natural": "Hallo, Doktor Weber. Wie kann ich Ihnen helfen?"},
        {"word_choice": "Darf ich Ihren Namen und Ihre Jahre erfahren?",
         "natural": "Ihren Namen und Ihr Alter, bitte?"},
        {"word_choice": "Wann haben die Bauchschmerzen gestartet?",
         "register": "Wann haben deine Bauchschmerzen angefangen?",
         "natural": "Die Bauchschmerzen, wann ging das los?"},
        {"word_choice": "Zeigen Sie mir bitte, wo der Schmerz jetzt am größten ist.",
         "natural": "Wo genau tut es jetzt am meisten weh? Zeigen Sie es mir bitte."},
        {"word_choice": "Ist Ihnen übel, oder mussten Sie sich übersetzen?",
         "register": "Ist dir übel, oder musstest du dich übergeben?",
         "natural": "Ist Ihnen übel? Haben Sie erbrochen?"},
        {"word_choice": "Haben Sie Fieber gewogen?",
         "register": "Hast du Fieber gemessen?",
         "natural": "Fieber gemessen, haben Sie das?"},
        {"word_choice": "Sind Sie wegen einer Krankheit in Pflege?",
         "natural": "Werden Sie wegen einer Krankheit behandelt?"},
        {"word_choice": "Welche Medikamente trinken Sie im Moment?",
         "register": "Welche Medikamente nimmst du im Moment?",
         "natural": "Und Medikamente, welche nehmen Sie gerade?"},
        {"register": "Hast du eine Allergie?",
         "natural": "Allergien?"},
        {"register": "Wie viel Alkohol saufen Sie in der Woche?"},
        {"natural": "In Ihrer Familie, gab es da Probleme mit der Galle?"},
        {"word_choice": "Welchen Beruf spielen Sie aus?",
         "register": "Welchen Beruf übst du aus?",
         "natural": "Was machen Sie beruflich?"},
    ],
    "atemnot": [
        {"register": "Guten Morgen, Mehmet. Was ist der Grund für deinen Besuch?",
         "natural": "Guten Morgen, Herr Yilmaz. Weshalb sind Sie heute hier?"},
        {"word_choice": "Können Sie mir bitte Ihr Geburtsdatum oder Ihr Alter erzählen?",
         "natural": "Wann sind Sie geboren, oder wie alt sind Sie?"},
        {"word_choice": "Seit wie vielen Tagen tragen Sie die Atemnot?",
         "natural": "Die Atemnot, seit wie vielen Tagen schon?"},
        {"word_choice": "Husten Sie auch? Wenn ja, haben Sie Spucke?",
         "register": "Hustest du auch? Wenn ja, hast du Auswurf?",
         "natural": "Müssen Sie auch husten? Kommt etwas hoch?"},
        {"register": "Welche Krankheiten sind bei dir bekannt?",
         "natural": "Was für Vorerkrankungen kennen Sie bei sich?"},
        {"word_choice": "Verbrauchen Sie einen Inhalator oder andere Medikamente?",
         "register": "Benutzt du einen Inhalator oder andere Medikamente?",
         "natural": "Haben Sie einen Inhalator, oder nehmen Sie andere Medikamente?"},
        {"register": "Wogegen bist du allergisch?",
         "natural": "Allergisch, wogegen denn?"},
        {"register": "Hast du früher geraucht oder rauchst du noch?",
         "natural": "Sie haben früher geraucht, rauchen Sie noch?"},
        {"natural": "Gab es in Ihrer Familie Lungenerkrankungen?"},
        {"register": "Was hast du früher beruflich gemacht?"},
        {"word_choice": "Wie schlafen Sie nachts? Können Sie platt liegen?",
         "natural": "Und nachts, schlafen Sie gut? Liegen Sie flach?"},
    ],
    "kopfschmerz": [
        {"register": "Grüß Gott, ich bin deine Ärztin heute. Was bringt dich zu mir?",
         "natural": "Grüß Gott, ich bin heute Ihre Ärztin. Was führt Sie zu mir?"},
        {"register": "Nenn mir bitte deinen vollständigen Namen und dein Alter.",
         "natural": "Sagen Sie mir bitte Ihren vollständigen Namen und Ihr Alter."},
        {"word_choice": "Wie lange besitzen Sie diese Kopfschmerzen schon?",
         "natural": "Seit wann plagen Sie diese Kopfschmerzen?"},
        {"word_choice": "Ist der Kopfschmerz pochend oder eher stumpf?",
         "natural": "Pocht es, oder ist es eher dumpf?"},
        {"word_choice": "Ärgert Sie helles Licht oder Lärm?",
         "register": "Stört dich helles Licht oder Lärm?"},
        {"word_choice": "Sind bei Ihnen Vorkrankheiten bekannt?",
         "natural": "Vorerkrankungen, sind welche bekannt?"},
        {"word_choice": "Was essen Sie gegen die Schmerzen?",
         "register": "Was nimmst du gegen die Schmerzen ein?",
         "natural": "Und gegen die Schmerzen, was nehmen Sie da?"},
        {"register": "Reagierst du allergisch auf bestimmte Lebensmittel oder Medikamente?",
         "natural": "Vertragen Sie bestimmte Lebensmittel oder Medikamente nicht?"},
        {"register": "Bist du Raucherin?",
         "natural": "Rauchen Sie?"},
        {"word_choice": "Besitzt jemand aus Ihrer Familie ebenfalls Migräne?",
         "natural": "Hat jemand in Ihrer Familie auch Migräne?"},
        {"register": "In welchem Bereich arbeitest du?",
         "natural": "Arbeiten Sie in einem bestimmten Bereich?"},
        {"register": "Kannst du trotz der Schmerzen gut schlafen?",
         "natural": "Schlafen Sie trotz der Schmerzen gut?"},
    ],
}

NEW_SLOTS: dict[str, list[tuple[list[str], str, dict[str, Any]]]] = {
    "rueckenschmerz": [
        (["reason"], "Guten Tag, mein Name ist Doktor Hoffmann. Wie kann ich Ihnen helfen?", {
            "word_order": "Guten Tag, mein Name ist Doktor Hoffmann. Wie ich kann Ihnen helfen?",
            "register": "Guten Tag, mein Name ist Doktor Hoffmann. Wie kann ich dir helfen?",
            "natural": "Guten Tag, Doktor Hoffmann hier. Was führt Sie zu mir?",
            "stt": "guten tag mein name ist doktor hoffmann wie kann ich ihnen helfen",
        }),
        (["identity"], "Wie ist Ihr Name, und wie alt sind Sie?", {
            "verb_form": "Wie ist Ihr Name, und wie alt seid Sie?",
            "word_choice": "Wie ist Ihr Name, und wie viele Jahre sind Sie?",
            "natural": "Ihr Name und Ihr Alter, bitte?",
            "stt": "wie ist ihr name und wie alt sind sie",
            "french": ("Wie ist Ihr Name, und quel âge avez-vous?", "Wie alt sind Sie"),
        }),
        (["onset"], "Seit wann haben Sie die Rückenschmerzen?", {
            "verb_form": "Seit wann hast Sie die Rückenschmerzen?",
            "case": "Seit wann haben Sie den Rückenschmerzen?",
            "word_order": "Seit wann Sie haben die Rückenschmerzen?",
            "natural": "Seit wann plagen Sie die Rückenschmerzen?",
            "stt": "seit wann haben sie die rücken schmerzen",
            "french": ("Seit wann haben Sie le mal de dos?", "Rückenschmerzen"),
        }),
        (["character"], "Ist der Schmerz stechend oder eher dumpf?", {
            "gender": "Ist die Schmerz stechend oder eher dumpf?",
            "word_choice": "Ist der Schmerz stechend oder eher stumpf?",
            "natural": "Ist es ein stechender oder eher ein dumpfer Schmerz?",
            "stt": "ist der schmerz stechend oder eher dumpf",
        }),
        (["radiation"], "Strahlt der Schmerz in das Bein aus?", {
            "case": "Strahlt der Schmerz in dem Bein aus?",
            "verb_form": "Ausstrahlt der Schmerz in das Bein?",
            "stt": "strahlt der schmerz in das bein aus",
            "french": ("Strahlt der Schmerz dans la jambe aus?", "Bein"),
        }),
        (["associated"], "Spüren Sie ein Kribbeln oder ein Taubheitsgefühl?", {
            "gender": "Spüren Sie einen Kribbeln oder ein Taubheitsgefühl?",
            "word_choice": "Spüren Sie ein Kribbeln oder ein Betäubungsgefühl?",
            "natural": "Kribbelt es irgendwo, oder fühlt sich etwas taub an?",
            "stt": "spüren sie ein kribbeln oder ein taubheitsgefühl",
        }),
        (["history"], "Hatten Sie schon einmal Probleme mit der Wirbelsäule?", {
            "case": "Hatten Sie schon einmal Probleme mit die Wirbelsäule?",
            "gender": "Hatten Sie schon einmal Probleme mit dem Wirbelsäule?",
            "verb_form": "Hattet Sie schon einmal Probleme mit der Wirbelsäule?",
            "register": "Hattest du schon einmal Probleme mit der Wirbelsäule?",
            "stt": "hatten sie schon einmal probleme mit der wirbelsäule",
        }),
        (["medication"], "Welche Schmerzmittel nehmen Sie?", {
            "verb_form": "Welche Schmerzmittel nimmst Sie?",
            "word_order": "Welche Schmerzmittel Sie nehmen?",
            "word_choice": "Welche Schmerztabletten essen Sie?",
            "register": "Welche Schmerzmittel nimmst du?",
            "natural": "Und was nehmen Sie gegen die Schmerzen?",
            "stt": "welche schmerzmittel nehmen sie",
            "french": ("Welche analgésiques nehmen Sie?", "Schmerzmittel"),
        }),
        (["allergy"], "Sind bei Ihnen Allergien bekannt?", {
            "case": "Sind bei Sie Allergien bekannt?",
            "register": "Bist du gegen etwas allergisch?",
            "natural": "Allergien? Gibt es da etwas?",
            "stt": "sind bei ihnen allergien bekannt",
        }),
        (["smoking"], "Rauchen Sie, und wenn ja, wie viel?", {
            "verb_form": "Raucht Sie, und wenn ja, wie viel?",
            "natural": "Sind Sie Raucher? Wie viel am Tag?",
            "stt": "rauchen sie und wenn ja wie viel",
            "french": ("Fumez-vous, und wenn ja, wie viel?", "Rauchen Sie"),
        }),
        (["family"], "Kommen Rückenleiden in Ihrer Familie vor?", {
            "case": "Kommen Rückenleiden in Ihre Familie vor?",
            "gender": "Kommen Rückenleiden in Ihrem Familie vor?",
            "natural": "Gibt es Rückenleiden in Ihrer Familie?",
            "stt": "kommen rückenleiden in ihrer familie vor",
        }),
        (["occupation"], "Was arbeiten Sie, und müssen Sie viel heben?", {
            "word_order": "Was Sie arbeiten, und müssen Sie viel heben?",
            "verb_form": "Was arbeiten Sie, und müssen Sie viel gehoben?",
            "word_choice": "Was arbeiten Sie, und müssen Sie viel aufheben?",
            "register": "Was arbeitest du, und musst du viel heben?",
            "stt": "was arbeiten sie und müssen sie viel heben",
            "french": ("Quel est votre travail, und müssen Sie viel heben?", "Was arbeiten Sie"),
        }),
    ],
    "schwindel": [
        (["reason"], "Guten Tag, ich bin Doktor Schäfer. Wie geht es Ihnen heute?", {
            "word_order": "Guten Tag, ich bin Doktor Schäfer. Wie es Ihnen geht heute?",
            "register": "Guten Tag, ich bin Doktor Schäfer. Wie geht es dir heute?",
            "natural": "Guten Tag, Schäfer mein Name. Was führt Sie zu mir?",
            "stt": "guten tag ich bin doktor schäfer wie geht es ihnen heute",
        }),
        (["identity"], "Wie lautet Ihr Name, und wie alt sind Sie?", {
            "verb_form": "Wie lautet Ihr Name, und wie alt bist Sie?",
            "gender": "Wie lautet Ihre Name, und wie alt sind Sie?",
            "word_choice": "Wie lautet Ihr Name, und wie viele Jahre haben Sie?",
            "stt": "wie lautet ihr name und wie alt sind sie",
            "french": ("Quel est votre nom, und wie alt sind Sie?", "Wie lautet Ihr Name"),
        }),
        (["onset"], "Wann hat der Schwindel zum ersten Mal angefangen?", {
            "gender": "Wann hat die Schwindel zum ersten Mal angefangen?",
            "verb_form": "Wann hat der Schwindel zum ersten Mal angefangt?",
            "case": "Wann hat der Schwindel zu den ersten Mal angefangen?",
            "word_order": "Wann der Schwindel hat zum ersten Mal angefangen?",
            "natural": "Der Schwindel, wann hat er angefangen?",
            "stt": "wann hat der schwindel zum ersten mal angefangen",
            "french": ("Wann hat le vertige zum ersten Mal angefangen?", "Schwindel"),
        }),
        (["character"], "Dreht sich alles, oder fühlt es sich eher wie Schwanken an?", {
            "verb_form": "Dreht sich alles, oder fühlt es sich eher wie Schwanken?",
            "word_order": "Dreht sich alles, oder es fühlt sich eher wie Schwanken an?",
            "register": "Dreht sich bei dir alles, oder fühlt es sich eher wie Schwanken an?",
            "natural": "Ist es ein Drehen oder eher ein Schwanken?",
            "stt": "dreht sich alles oder fühlt es sich eher wie schwanken an",
        }),
        (["trigger"], "Wird der Schwindel beim Aufstehen schlimmer?", {
            "gender": "Wird die Schwindel beim Aufstehen schlimmer?",
            "case": "Wird der Schwindel von das Aufstehen schlimmer?",
            "word_choice": "Wird der Schwindel beim Aufstehen böser?",
            "natural": "Beim Aufstehen, wird es da schlimmer?",
            "stt": "wird der schwindel beim aufstehen schlimmer",
            "french": ("Wird der Schwindel beim lever schlimmer?", "Aufstehen"),
        }),
        (["associated"], "Haben Sie Ohrensausen oder Übelkeit?", {
            "gender": "Haben Sie einen Ohrensausen oder Übelkeit?",
            "word_choice": "Haben Sie Ohrenlärm oder Übelkeit?",
            "verb_form": "Habt Sie Ohrensausen oder Übelkeit?",
            "register": "Hast du Ohrensausen oder Übelkeit?",
            "natural": "Rauscht es in den Ohren, oder ist Ihnen übel?",
            "stt": "haben sie ohrensausen oder übelkeit",
        }),
        (["history"], "Haben Sie Bluthochdruck oder eine Herzkrankheit?", {
            "gender": "Haben Sie Bluthochdruck oder einen Herzkrankheit?",
            "word_choice": "Haben Sie Bluthochdruck oder eine Herzsucht?",
            "register": "Hast du Bluthochdruck oder eine Herzkrankheit?",
            "natural": "Sie haben Bluthochdruck oder eine Herzkrankheit?",
            "stt": "haben sie blut hochdruck oder eine herzkrankheit",
        }),
        (["medication"], "Welche Tabletten nehmen Sie jeden Tag?", {
            "verb_form": "Welche Tabletten nimmt Sie jeden Tag?",
            "case": "Welche Tabletten nehmen Sie jedem Tag?",
            "word_order": "Welche Tabletten nehmen jeden Tag Sie?",
            "natural": "Und jeden Tag, welche Tabletten nehmen Sie da?",
            "stt": "welche tabletten nehmen sie jeden tag",
            "french": ("Welche comprimés nehmen Sie jeden Tag?", "Tabletten"),
        }),
        (["allergy"], "Gibt es Allergien, die Sie kennen?", {
            "word_choice": "Gibt es Allergien, die Sie wissen?",
            "verb_form": "Gibt es Allergien, die Sie kennt?",
            "register": "Gibt es Allergien, die du kennst?",
            "natural": "Allergien, sind Ihnen welche bekannt?",
            "stt": "gibt es allergien die sie kennen",
        }),
        (["smoking"], "Haben Sie jemals geraucht?", {
            "verb_form": "Haben Sie jemals rauchen?",
            "word_order": "Haben Sie geraucht jemals?",
            "register": "Hast du jemals geraucht?",
            "natural": "Jemals geraucht?",
            "stt": "haben sie jemals geraucht",
            "french": ("Haben Sie jamais geraucht?", "jemals"),
        }),
        (["family"], "Gab es in Ihrer Familie einen Schlaganfall?", {
            "gender": "Gab es in Ihrer Familie eine Schlaganfall?",
            "case": "Gab es in Ihre Familie einen Schlaganfall?",
            "word_choice": "Gab es in Ihrer Familie einen Hirnangriff?",
            "natural": "In Ihrer Familie, gab es da einen Schlaganfall?",
            "stt": "gab es in ihrer familie einen schlaganfall",
        }),
        (["occupation"], "Welchen Beruf hatten Sie, bevor Sie in Rente gingen?", {
            "verb_form": "Welchen Beruf hattet Sie, bevor Sie in Rente gingen?",
            "word_order": "Welchen Beruf hatten Sie, bevor Sie gingen in Rente?",
            "register": "Welchen Beruf hattest du, bevor du in Rente gingst?",
            "natural": "Und früher, was haben Sie gearbeitet?",
            "stt": "welchen beruf hatten sie bevor sie in rente gingen",
        }),
    ],
    "halsschmerz": [
        (["reason"], "Guten Abend, mein Name ist Doktor Krämer. Was fehlt Ihnen denn?", {
            "word_order": "Guten Abend, mein Name ist Doktor Krämer. Was Ihnen fehlt denn?",
            "register": "Guten Abend, mein Name ist Doktor Krämer. Was fehlt dir denn?",
            "natural": "Guten Abend, Doktor Krämer. Was kann ich für Sie tun?",
            "stt": "guten abend mein name ist doktor krämer was fehlt ihnen denn",
        }),
        (["identity"], "Würden Sie mir bitte Ihren Namen und Ihr Alter sagen?", {
            "verb_form": "Würdet Sie mir bitte Ihren Namen und Ihr Alter sagen?",
            "gender": "Würden Sie mir bitte Ihren Namen und Ihren Alter sagen?",
            "case": "Würden Sie mich bitte Ihren Namen und Ihr Alter sagen?",
            "word_choice": "Würden Sie mir bitte Ihren Namen und Ihre Jahre sagen?",
            "natural": "Sagen Sie mir bitte kurz Ihren Namen und Ihr Alter?",
            "stt": "würden sie mir bitte ihren namen und ihr alter sagen",
        }),
        (["onset"], "Seit wie vielen Tagen tut Ihnen der Hals weh?", {
            "case": "Seit wie vielen Tagen tut Sie der Hals weh?",
            "verb_form": "Seit wie vielen Tagen tun Ihnen der Hals weh?",
            "gender": "Seit wie vielen Tagen tut Ihnen die Hals weh?",
            "word_order": "Seit wie vielen Tagen der Hals tut Ihnen weh?",
            "natural": "Der Hals tut Ihnen weh, seit wann?",
            "stt": "seit wie vielen tagen tut ihnen der hals weh",
            "french": ("Seit wie vielen Tagen tut Ihnen la gorge weh?", "Hals"),
        }),
        (["character"], "Tut es besonders beim Schlucken weh?", {
            "word_choice": "Tut es besonders beim Verschlucken weh?",
            "verb_form": "Tut es besonders beim Schlucken wehen?",
            "register": "Tut es dir besonders beim Schlucken weh?",
            "natural": "Besonders beim Schlucken, tut es da weh?",
            "stt": "tut es besonders beim schlucken weh",
            "french": ("Tut es besonders beim avaler weh?", "Schlucken"),
        }),
        (["associated"], "Haben Sie Fieber gehabt oder Schüttelfrost?", {
            "verb_form": "Haben Sie Fieber haben oder Schüttelfrost?",
            "word_choice": "Haben Sie Fieber gehabt oder Schüttelkälte?",
            "gender": "Haben Sie ein Fieber gehabt oder eine Schüttelfrost?",
            "register": "Hast du Fieber gehabt oder Schüttelfrost?",
            "natural": "Fieber oder Schüttelfrost, hatten Sie das?",
            "stt": "haben sie fieber gehabt oder schüttelfrost",
        }),
        (["history"], "Hatten Sie schon einmal eine Mandelentzündung?", {
            "gender": "Hatten Sie schon einmal einen Mandelentzündung?",
            "verb_form": "Hattet Sie schon einmal eine Mandelentzündung?",
            "word_choice": "Hatten Sie schon einmal eine Mandelverbrennung?",
            "register": "Hattest du schon einmal eine Mandelentzündung?",
            "natural": "Mandelentzündung, hatten Sie die schon mal?",
            "stt": "hatten sie schon einmal eine mandel entzündung",
        }),
        (["medication"], "Nehmen Sie schon etwas gegen die Beschwerden?", {
            "word_order": "Sie schon etwas gegen die Beschwerden nehmen?",
            "case": "Nehmen Sie schon etwas gegen den Beschwerden?",
            "word_choice": "Nehmen Sie schon etwas gegen die Klagen?",
            "verb_form": "Nehmt Sie schon etwas gegen die Beschwerden?",
            "register": "Nimmst du schon etwas gegen die Beschwerden?",
            "natural": "Haben Sie schon etwas dagegen genommen?",
            "stt": "nehmen sie schon etwas gegen die beschwerden",
        }),
        (["allergy"], "Wissen Sie von irgendwelchen Allergien?", {
            "case": "Wissen Sie von irgendwelche Allergien?",
            "verb_form": "Weißt Sie von irgendwelchen Allergien?",
            "word_choice": "Kennen Sie von irgendwelchen Allergien?",
            "register": "Weißt du von irgendwelchen Allergien?",
            "natural": "Sind Ihnen Allergien bekannt?",
            "stt": "wissen sie von irgendwelchen allergien",
        }),
        (["smoking"], "Rauchen Sie oder dampfen Sie?", {
            "verb_form": "Rauchen Sie oder dampft Sie?",
            "word_choice": "Rauchen Sie oder verdampfen Sie?",
            "register": "Rauchst du oder dampfst du?",
            "natural": "Rauchen, dampfen, irgendetwas davon?",
            "stt": "rauchen sie oder dampfen sie",
            "french": ("Fumez-vous oder dampfen Sie?", "Rauchen Sie"),
        }),
        (["family"], "Hat jemand in Ihrer Familie häufig Halsentzündungen?", {
            "case": "Hat jemand in Ihre Familie häufig Halsentzündungen?",
            "gender": "Hat jemand in Ihrem Familie häufig Halsentzündungen?",
            "verb_form": "Haben jemand in Ihrer Familie häufig Halsentzündungen?",
            "natural": "Gibt es in Ihrer Familie häufig Halsentzündungen?",
            "stt": "hat jemand in ihrer familie häufig hals entzündungen",
        }),
        (["occupation"], "Haben Sie im Beruf viel Kontakt mit Kindern?", {
            "case": "Haben Sie im Beruf viel Kontakt mit Kinder?",
            "verb_form": "Habt Sie im Beruf viel Kontakt mit Kindern?",
            "word_order": "Sie im Beruf viel Kontakt mit Kindern haben?",
            "word_choice": "Haben Sie im Beruf viel Berührung mit Kindern?",
            "register": "Hast du im Beruf viel Kontakt mit Kindern?",
            "natural": "Im Beruf, haben Sie da viel mit Kindern zu tun?",
            "stt": "haben sie im beruf viel kontakt mit kindern",
        }),
    ],
}


def slots_of(family: str) -> list[tuple[list[str], str, dict[str, Any]]]:
    if family in NEW_SLOTS:
        return NEW_SLOTS[family]
    base = V1_SLOTS[family]
    extras = EXTRA[family]
    assert len(base) == len(extras), family
    return [
        (facts, correct, {**variants, **more})
        for (facts, correct, variants), more in zip(base, extras, strict=True)
    ]


def language_items(family: str) -> list[dict[str, Any]]:
    spec = CASES[family]
    phrases = {f["id"]: f["patient_phrase"] for f in spec["facts"]}
    section_of = {f: s["id"] for s in spec["anamnesis_sections"] for f in s["fact_ids"]}
    optional = [s["id"] for s in spec["anamnesis_sections"] if s["id"] in OPTIONAL_SECTIONS]
    all_slots = slots_of(family)
    items = []
    for index in range(1, TRANSCRIPTS_PER_FAMILY + 1):
        rng = random.Random(f"evaluation-language@2:{family}:{index}")
        dropped = rng.choice(optional) if index % 3 == 0 else None
        slots = [s for s in all_slots if section_of[s[0][0]] != dropped]
        changes: dict[int, str] = {}
        for _ in range(rng.choice((1, 2, 3, 3, 4, 4))):
            kind = rng.choice(KINDS)
            candidates = [i for i, s in enumerate(slots) if kind in s[2] and i not in changes]
            if candidates:
                changes[rng.choice(candidates)] = kind
        turns, errors, switches, stt = [], [], [], []
        for position, (facts, correct, variants) in enumerate(slots):
            sequence = position + 1
            kind = changes.get(position)
            doctor, revealed, response_kind = correct, facts, "sources"
            if kind in LANGUAGE_KINDS:
                doctor = variants[kind]
                errors.append({"turn": sequence, "category": kind, "correct": correct})
            elif kind == "natural":
                doctor = variants["natural"]
            elif kind == "stt":
                doctor = variants["stt"]
                stt.append(sequence)
            elif kind == "french":
                doctor, term = variants["french"]
                switches.append({"turn": sequence, "intended_term": term})
                revealed, response_kind = [], "wrong_language"
            turns.append({
                "sequence": sequence,
                "doctor": doctor,
                "patient": " ".join(phrases[f] for f in revealed) or spec["out_of_scope_response"],
                "revealed_fact_ids": revealed,
                "patient_response_kind": response_kind,
            })
        heard = {f for t in turns for f in t["revealed_fact_ids"]}
        missing = [s["id"] for s in spec["anamnesis_sections"] if not heard & set(s["fact_ids"])]
        items.append({
            "id": f"{family}-{index:02d}",
            "family": family,
            "case_id": spec["id"],
            "turns": turns,
            "target_cefr": "B2",
            "truth": {
                "language_errors": errors,
                "code_switches": switches,
                "stt_artifact_turns": stt,
                "missing_sections": missing,
            },
        })
    return items


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def main(out: Path) -> None:
    directory = out / "evaluation-language" / "2"
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        "cases.json": json.dumps({"cases": list(CASES.values())}, ensure_ascii=False, indent=2)
        + "\n",
        "dev.jsonl": _jsonl([row for f in DEV_FAMILIES for row in language_items(f)]),
        "holdout.jsonl": _jsonl([row for f in HOLDOUT_FAMILIES for row in language_items(f)]),
    }
    for name, content in files.items():
        (directory / name).write_text(content, encoding="utf-8")
    manifest = {
        "suite": "evaluation-language",
        "version": "2",
        "description": (
            "Transcripts synthétiques à erreurs injectées (genre, cas, forme verbale, ordre des "
            "mots, choix du mot, registre, mot français, artefact STT, section oubliée) et à "
            "formulations correctes mais inhabituelles : vérité connue par construction."
        ),
        "synthetic": True,
        "source": "Cas et énoncés inventés par l'agent, générés par evals/datasets/build_synthetic_v2.py",
        "created_on": CREATED_ON,
        "consent": "not_applicable:synthetic",
        "annotators": [AGENT],
        "splits": {"dev": DEV_FAMILIES, "holdout": HOLDOUT_FAMILIES},
        "files": {
            name: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for name, content in files.items()
        },
        "limitations": [
            "Variantes allemandes rédigées par l'agent : relecture par un germanophone requise "
            "avant de se fier aux seuils.",
            "Une phrase « correcte » qui contiendrait une vraie faute compterait comme fausse "
            "erreur du modèle.",
            "La famille kopfschmerz, holdout de la version 1, a été vue lors de la mise au point "
            ": elle est en développement dans cette version. Le holdout est neuf.",
            "Règle d'annotation genre / cas : voir l'en-tête de build_synthetic_v2.py.",
        ],
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
