"""Builds `evaluation-language@3`: version 2 for development, a fresh holdout of three families.

Usage, from the repository root:
    PYTHONPATH=backend/src .venv/bin/python evals/datasets/build_synthetic_v3.py [out_dir]

Why a version 3 (1 November 2026): the holdout of version 2 was run once on the real models and
failed (stability 0.20, word_choice recall 0.68). Its results are known, so it can no longer say
anything about a new evaluator: it becomes development data, and the holdout is three new families
(knee pain, diarrhoea, skin rash) with their own cases and phrasings, never shown to anyone tuning
the evaluator.

- dev: every transcript of version 2, development and holdout, unchanged (175 transcripts);
- holdout: 25 new transcripts per new family (75), built like version 2 with the same kinds of
  injected errors and the same annotation rule (see the header of build_synthetic_v2.py).

Everything is invented, nothing comes from a real patient, a real session or an exam protocol
(plan, invariant 8). The output is deterministic and a test rebuilds it: the committed files are
never edited by hand. A variant is only written where the category is clear; a slot without a
clear word_choice error simply has none.
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
V2 = runpy.run_path(str(HERE / "build_synthetic_v2.py"))
fact, case, sections = V2["V1"]["fact"], V2["V1"]["case"], V2["sections"]

CREATED_ON = "2026-11-01"
AGENT = {"id": "agent:claude-opus-5.5", "role": "rédaction synthétique", "annotated_on": CREATED_ON}
V2_DEV, V2_HOLDOUT = V2["DEV_FAMILIES"], V2["HOLDOUT_FAMILIES"]
DEV_FAMILIES = [*V2_DEV, *V2_HOLDOUT]
HOLDOUT_FAMILIES = ["knieschmerz", "durchfall", "hautausschlag"]
TRANSCRIPTS_PER_FAMILY = V2["TRANSCRIPTS_PER_FAMILY"]
LANGUAGE_KINDS = V2["LANGUAGE_KINDS"]
KINDS = V2["KINDS"]
OPTIONAL_SECTIONS = V2["OPTIONAL_SECTIONS"]

NEW_CASES: dict[str, dict[str, Any]] = {
    "knieschmerz": case(
        "SYN-KNIE",
        "Synthetischer Fall: Knieschmerz",
        "Guten Tag. Mein linkes Knie tut seit ein paar Tagen weh.",
        "sportlich, spricht schnell",
        [
            fact("identity", "identity", "Sabine Vogt, 45 Jahre", "Ich heiße Sabine Vogt und bin fünfundvierzig."),
            fact("reason", "reason", "Knieschmerzen links", "Mein linkes Knie tut seit ein paar Tagen weh.", "spontaneous"),
            fact("onset", "onset", "vor vier Tagen beim Joggen gestürzt", "Ich bin vor vier Tagen beim Joggen gestürzt."),
            fact("character", "pain_location", "Innenseite des linken Knies", "Es tut innen am linken Knie weh."),
            fact("associated", "associated_symptoms", "Schwellung des Knies", "Das Knie ist dick geworden."),
            fact("trigger", "trigger", "Belastung schmerzhaft, hinkt", "Wenn ich auftrete, tut es weh, ich humple."),
            fact("history", "medical_history", "Meniskusoperation rechts vor zehn Jahren", "Am rechten Knie wurde vor zehn Jahren der Meniskus operiert."),
            fact("medication", "medication", "keine regelmäßigen Medikamente", "Regelmäßig nehme ich nichts."),
            fact("allergy", "allergy", "Penicillinallergie", "Penicillin vertrage ich nicht."),
            fact("smoking", "smoking", "Nichtraucherin, selten Alkohol", "Ich rauche nicht und trinke selten Alkohol.", "when_explicitly_asked"),
            fact("family", "family_history", "Mutter: Arthrose", "Meine Mutter hat Arthrose."),
            fact("occupation", "occupation", "Grundschullehrerin", "Ich bin Grundschullehrerin."),
        ],
        sections(["reason", "onset", "character", "associated", "trigger"]),
    ),
    "durchfall": case(
        "SYN-DURCHFALL",
        "Synthetischer Fall: Durchfall",
        "Guten Morgen. Ich habe seit zwei Tagen starken Durchfall.",
        "erschöpft, etwas peinlich berührt",
        [
            fact("identity", "identity", "Lukas Becker, 34 Jahre", "Ich bin Lukas Becker, vierunddreißig Jahre alt."),
            fact("reason", "reason", "Durchfall", "Ich habe seit zwei Tagen starken Durchfall.", "spontaneous"),
            fact("onset", "onset", "seit zwei Tagen, nach Rückkehr aus Thailand", "Seit zwei Tagen, seit ich aus Thailand zurück bin."),
            fact("character", "stool", "wässrig, bis zu acht Mal am Tag", "Es ist ganz wässrig, bis zu acht Mal am Tag."),
            fact("associated", "associated_symptoms", "Bauchkrämpfe, Fieber 38,2 °C", "Ich habe Bauchkrämpfe und achtunddreißig zwei Fieber."),
            fact("blood", "stool_blood", "kein Blut im Stuhl", "Blut habe ich keins gesehen."),
            fact("history", "medical_history", "Laktoseintoleranz", "Ich habe eine Laktoseintoleranz."),
            fact("medication", "medication", "einmal Loperamid genommen", "Ich habe gestern einmal Loperamid genommen."),
            fact("allergy", "allergy", "keine bekannten Allergien", "Allergien sind keine bekannt."),
            fact("smoking", "smoking", "Nichtraucher, am Wochenende Bier", "Ich rauche nicht, am Wochenende trinke ich Bier.", "when_explicitly_asked"),
            fact("family", "family_history", "Mutter: Morbus Crohn", "Meine Mutter hat Morbus Crohn."),
            fact("occupation", "occupation", "IT-Berater, beruflich in Thailand", "Ich bin IT-Berater und war gerade beruflich in Thailand."),
        ],
        sections(["reason", "onset", "character", "associated", "blood"]),
    ),
    "hautausschlag": case(
        "SYN-HAUT",
        "Synthetischer Fall: Hautausschlag",
        "Guten Tag. Ich habe einen Ausschlag an den Armen, der furchtbar juckt.",
        "genervt vom Juckreiz, kratzt sich ständig",
        [
            fact("identity", "identity", "Elena Schubert, 39 Jahre", "Ich bin Elena Schubert und neununddreißig Jahre alt."),
            fact("reason", "reason", "juckender Hautausschlag", "Ich habe einen Ausschlag an den Armen, der furchtbar juckt.", "spontaneous"),
            fact("onset", "onset", "seit fünf Tagen", "Vor fünf Tagen ist er zum ersten Mal aufgetreten."),
            fact("character", "rash_character", "rot, juckend, beide Unterarme", "Er ist rot und juckt an beiden Unterarmen."),
            fact("associated", "associated_symptoms", "kein Fieber, keine Atemnot", "Fieber oder Atemnot habe ich nicht."),
            fact("trigger", "trigger", "neues Waschmittel vor einer Woche", "Letzte Woche habe ich ein neues Waschmittel gekauft."),
            fact("history", "medical_history", "Neurodermitis in der Kindheit", "Als Kind hatte ich Neurodermitis."),
            fact("medication", "medication", "Antibabypille", "Ich nehme die Pille."),
            fact("allergy", "allergy", "Nickelallergie", "Ich bin gegen Nickel allergisch."),
            fact("smoking", "smoking", "Raucherin, fünf Zigaretten täglich", "Ich rauche etwa fünf Zigaretten am Tag.", "when_explicitly_asked"),
            fact("family", "family_history", "Mutter: Heuschnupfen", "Meine Mutter hat Heuschnupfen."),
            fact("occupation", "occupation", "Friseurin, Kontakt mit Färbemitteln", "Ich bin Friseurin und arbeite viel mit Haarfarbe."),
        ],
        sections(["reason", "onset", "character", "associated", "trigger"]),
    ),
}

CASES: dict[str, dict[str, Any]] = V2["CASES"] | NEW_CASES

# (facts revealed, correct utterance, {variant kind: utterance or (utterance, term)}).
NEW_SLOTS: dict[str, list[tuple[list[str], str, dict[str, Any]]]] = {
    "knieschmerz": [
        (["reason"], "Guten Tag, ich bin Doktor Neumann. Womit kann ich Ihnen helfen?", {
            "word_order": "Guten Tag, ich bin Doktor Neumann. Womit ich kann Ihnen helfen?",
            "register": "Guten Tag, ich bin Doktor Neumann. Womit kann ich dir helfen?",
            "case": "Guten Tag, ich bin Doktor Neumann. Womit kann ich Sie helfen?",
            "natural": "Guten Tag, Doktor Neumann. Was ist passiert?",
            "stt": "guten tag ich bin doktor neumann womit kann ich ihnen helfen",
        }),
        (["identity"], "Darf ich nach Ihrem Namen und Ihrem Alter fragen?", {
            "case": "Darf ich nach Ihren Namen und Ihrem Alter fragen?",
            "gender": "Darf ich nach Ihrer Namen und Ihrem Alter fragen?",
            "verb_form": "Darf ich nach Ihrem Namen und Ihrem Alter fragt?",
            "natural": "Ihr Name und Ihr Alter, wenn ich fragen darf?",
            "stt": "darf ich nach ihrem namen und ihrem alter fragen",
            "french": ("Darf ich nach Ihrem Namen und votre âge fragen?", "Alter"),
        }),
        (["onset"], "Wann ist der Unfall passiert?", {
            "verb_form": "Wann ist der Unfall passieren?",
            "gender": "Wann ist die Unfall passiert?",
            "word_order": "Wann der Unfall ist passiert?",
            "word_choice": "Wann ist der Unfall gelaufen?",
            "register": "Wann ist dir der Unfall passiert?",
            "natural": "Der Unfall, wann war das genau?",
            "stt": "wann ist der unfall passiert",
        }),
        (["character"], "Wo genau tut Ihnen das Knie weh?", {
            "case": "Wo genau tut Sie das Knie weh?",
            "gender": "Wo genau tut Ihnen der Knie weh?",
            "verb_form": "Wo genau tun Ihnen das Knie weh?",
            "register": "Wo genau tut dir das Knie weh?",
            "natural": "Zeigen Sie mir bitte, wo genau es am Knie wehtut.",
            "stt": "wo genau tut ihnen das knie weh",
            "french": ("Wo genau tut Ihnen le genou weh?", "Knie"),
        }),
        (["associated"], "Ist das Knie geschwollen?", {
            "word_choice": "Ist das Knie aufgeblasen?",
            "gender": "Ist die Knie geschwollen?",
            "verb_form": "Ist das Knie geschwellt?",
            "register": "Ist dein Knie geschwollen?",
            "natural": "Ist das Knie dick geworden?",
            "stt": "ist das knie geschwollen",
        }),
        (["trigger"], "Können Sie das Bein voll belasten?", {
            "verb_form": "Könnt Sie das Bein voll belasten?",
            "word_order": "Können Sie das Bein belasten voll?",
            "word_choice": "Können Sie das Bein voll beladen?",
            "case": "Können Sie dem Bein voll belasten?",
            "register": "Kannst du das Bein voll belasten?",
            "natural": "Und auftreten, geht das?",
            "stt": "können sie das bein voll belasten",
        }),
        (["history"], "Hatten Sie früher schon Probleme mit den Knien?", {
            "case": "Hatten Sie früher schon Probleme mit die Knie?",
            "verb_form": "Hattet Sie früher schon Probleme mit den Knien?",
            "register": "Hattest du früher schon Probleme mit den Knien?",
            "natural": "Früher schon mal etwas an den Knien gehabt?",
            "stt": "hatten sie früher schon probleme mit den knien",
        }),
        (["medication"], "Nehmen Sie dauerhaft Medikamente ein?", {
            "verb_form": "Nimmt Sie dauerhaft Medikamente ein?",
            "word_order": "Nehmen Sie ein dauerhaft Medikamente?",
            "word_choice": "Trinken Sie dauerhaft Tabletten ein?",
            "register": "Nimmst du dauerhaft Medikamente ein?",
            "natural": "Dauerhaft Medikamente, nehmen Sie da welche?",
            "stt": "nehmen sie dauerhaft medikamente ein",
            "french": ("Nehmen Sie dauerhaft des médicaments ein?", "Medikamente"),
        }),
        (["allergy"], "Haben Sie eine Allergie gegen Medikamente?", {
            "case": "Haben Sie eine Allergie gegen Medikamenten?",
            "gender": "Haben Sie einen Allergie gegen Medikamente?",
            "verb_form": "Hat Sie eine Allergie gegen Medikamente?",
            "register": "Hast du eine Allergie gegen Medikamente?",
            "natural": "Vertragen Sie irgendein Medikament nicht?",
            "stt": "haben sie eine allergie gegen medikamente",
        }),
        (["smoking"], "Trinken Sie Alkohol, oder rauchen Sie?", {
            "verb_form": "Trinkt Sie Alkohol, oder rauchen Sie?",
            "word_choice": "Essen Sie Alkohol, oder rauchen Sie?",
            "register": "Trinkst du Alkohol, oder rauchst du?",
            "natural": "Alkohol, Zigaretten, wie sieht es damit aus?",
            "stt": "trinken sie alkohol oder rauchen sie",
            "french": ("Buvez-vous de l'alcool, oder rauchen Sie?", "Trinken Sie Alkohol"),
        }),
        (["family"], "Gibt es in Ihrer Familie Gelenkerkrankungen?", {
            "case": "Gibt es in Ihre Familie Gelenkerkrankungen?",
            "gender": "Gibt es in Ihrem Familie Gelenkerkrankungen?",
            "register": "Gibt es in deiner Familie Gelenkerkrankungen?",
            "natural": "Gelenkprobleme in der Familie, ist da etwas bekannt?",
            "stt": "gibt es in ihrer familie gelenk erkrankungen",
        }),
        (["occupation"], "Was ist Ihr Beruf?", {
            "verb_form": "Was seid Ihr Beruf?",
            "gender": "Was ist Ihre Beruf?",
            "word_choice": "Was ist Ihr Gewerbe?",
            "register": "Was ist dein Beruf?",
            "natural": "Und im Beruf, was machen Sie da?",
            "stt": "was ist ihr beruf",
        }),
    ],
    "durchfall": [
        (["reason"], "Guten Morgen, ich bin Doktor Schulz. Was kann ich für Sie tun?", {
            "word_order": "Guten Morgen, ich bin Doktor Schulz. Was ich kann für Sie tun?",
            "case": "Guten Morgen, ich bin Doktor Schulz. Was kann ich für Ihnen tun?",
            "register": "Guten Morgen, ich bin Doktor Schulz. Was kann ich für dich tun?",
            "natural": "Guten Morgen, Doktor Schulz. Was führt Sie zu uns?",
            "stt": "guten morgen ich bin doktor schulz was kann ich für sie tun",
        }),
        (["identity"], "Wie heißen Sie, und wann sind Sie geboren?", {
            "verb_form": "Wie heißt Sie, und wann sind Sie geboren?",
            "word_order": "Wie Sie heißen, und wann sind Sie geboren?",
            "word_choice": "Wie rufen Sie sich, und wann sind Sie geboren?",
            "register": "Wie heißt du, und wann bist du geboren?",
            "natural": "Ihr Name und Ihr Geburtsdatum, bitte?",
            "stt": "wie heißen sie und wann sind sie geboren",
        }),
        (["onset"], "Seit wann haben Sie den Durchfall?", {
            "case": "Seit wann haben Sie der Durchfall?",
            "gender": "Seit wann haben Sie die Durchfall?",
            "verb_form": "Seit wann habt Sie den Durchfall?",
            "word_choice": "Seit wann führen Sie den Durchfall?",
            "natural": "Der Durchfall, seit wann geht das so?",
            "stt": "seit wann haben sie den durchfall",
            "french": ("Seit wann haben Sie la diarrhée?", "Durchfall"),
        }),
        (["character"], "Wie oft am Tag müssen Sie auf die Toilette?", {
            "case": "Wie oft am Tag müssen Sie auf der Toilette?",
            "verb_form": "Wie oft am Tag muss Sie auf die Toilette?",
            "word_order": "Wie oft am Tag Sie müssen auf die Toilette?",
            "register": "Wie oft am Tag musst du auf die Toilette?",
            "natural": "Wie oft gehen Sie am Tag zur Toilette?",
            "stt": "wie oft am tag müssen sie auf die toilette",
        }),
        (["associated"], "Haben Sie auch Bauchkrämpfe oder Fieber?", {
            "word_choice": "Haben Sie auch Bauchzüge oder Fieber?",
            "verb_form": "Habe Sie auch Bauchkrämpfe oder Fieber?",
            "register": "Hast du auch Bauchkrämpfe oder Fieber?",
            "natural": "Krämpfe im Bauch, Fieber, haben Sie so etwas?",
            "stt": "haben sie auch bauch krämpfe oder fieber",
            "french": ("Haben Sie auch des crampes oder Fieber?", "Bauchkrämpfe"),
        }),
        (["blood"], "Haben Sie Blut im Stuhl bemerkt?", {
            "case": "Haben Sie Blut in den Stuhl bemerkt?",
            "verb_form": "Haben Sie Blut im Stuhl bemerken?",
            "word_choice": "Haben Sie Blut im Sessel bemerkt?",
            "register": "Hast du Blut im Stuhl bemerkt?",
            "natural": "War Blut dabei?",
            "stt": "haben sie blut im stuhl bemerkt",
        }),
        (["history"], "Haben Sie Vorerkrankungen, zum Beispiel am Darm?", {
            "case": "Haben Sie Vorerkrankungen, zum Beispiel an den Darm?",
            "gender": "Haben Sie Vorerkrankungen, zum Beispiel an der Darm?",
            "verb_form": "Hat Sie Vorerkrankungen, zum Beispiel am Darm?",
            "register": "Hast du Vorerkrankungen, zum Beispiel am Darm?",
            "natural": "Vorerkrankungen, etwa am Darm, gibt es da etwas?",
            "stt": "haben sie vorerkrankungen zum beispiel am darm",
        }),
        (["medication"], "Haben Sie schon etwas gegen den Durchfall genommen?", {
            "verb_form": "Haben Sie schon etwas gegen den Durchfall genehmt?",
            "word_order": "Haben Sie genommen schon etwas gegen den Durchfall?",
            "case": "Haben Sie schon etwas gegen dem Durchfall genommen?",
            "register": "Hast du schon etwas gegen den Durchfall genommen?",
            "natural": "Etwas dagegen eingenommen haben Sie schon?",
            "stt": "haben sie schon etwas gegen den durchfall genommen",
            "french": ("Haben Sie schon un médicament gegen den Durchfall genommen?", "Medikament"),
        }),
        (["allergy"], "Sind Sie gegen irgendetwas allergisch?", {
            "verb_form": "Seid Sie gegen irgendetwas allergisch?",
            "word_order": "Gegen irgendetwas Sie sind allergisch?",
            "register": "Bist du gegen irgendetwas allergisch?",
            "natural": "Allergien, haben Sie welche?",
            "stt": "sind sie gegen irgendetwas allergisch",
        }),
        (["smoking"], "Rauchen Sie, oder trinken Sie regelmäßig Alkohol?", {
            "verb_form": "Raucht Sie, oder trinken Sie regelmäßig Alkohol?",
            "word_choice": "Rauchen Sie, oder kauen Sie regelmäßig Alkohol?",
            "register": "Rauchst du, oder trinkst du regelmäßig Alkohol?",
            "natural": "Zigaretten oder Alkohol, wie halten Sie es damit?",
            "stt": "rauchen sie oder trinken sie regelmäßig alkohol",
        }),
        (["family"], "Hat jemand in Ihrer Familie eine Darmerkrankung?", {
            "case": "Hat jemand in Ihre Familie eine Darmerkrankung?",
            "gender": "Hat jemand in Ihrer Familie einen Darmerkrankung?",
            "verb_form": "Haben jemand in Ihrer Familie eine Darmerkrankung?",
            "register": "Hat jemand in deiner Familie eine Darmerkrankung?",
            "natural": "Darmerkrankungen in der Familie, ist da etwas bekannt?",
            "stt": "hat jemand in ihrer familie eine darm erkrankung",
        }),
        (["occupation"], "Waren Sie in letzter Zeit beruflich im Ausland?", {
            "verb_form": "Wart Sie in letzter Zeit beruflich im Ausland?",
            "word_order": "In letzter Zeit Sie waren beruflich im Ausland?",
            "case": "Waren Sie in letzter Zeit beruflich ins Ausland?",
            "word_choice": "Waren Sie in letzter Zeit beruflich im Draußen?",
            "register": "Warst du in letzter Zeit beruflich im Ausland?",
            "natural": "Beruflich unterwegs gewesen, im Ausland vielleicht?",
            "stt": "waren sie in letzter zeit beruflich im ausland",
        }),
    ],
    "hautausschlag": [
        (["reason"], "Guten Tag, Doktor Richter. Was ist der Grund Ihres Besuchs?", {
            "case": "Guten Tag, Doktor Richter. Was ist der Grund Ihrem Besuch?",
            "word_order": "Guten Tag, Doktor Richter. Was der Grund ist Ihres Besuchs?",
            "register": "Guten Tag, Doktor Richter. Was ist der Grund deines Besuchs?",
            "natural": "Guten Tag, Doktor Richter. Was bringt Sie heute her?",
            "stt": "guten tag doktor richter was ist der grund ihres besuchs",
        }),
        (["identity"], "Sagen Sie mir bitte, wie Sie heißen und wie alt Sie sind.", {
            "word_order": "Sagen Sie mir bitte, wie heißen Sie und wie alt Sie sind.",
            "case": "Sagen Sie mich bitte, wie Sie heißen und wie alt Sie sind.",
            "verb_form": "Sagt Sie mir bitte, wie Sie heißen und wie alt Sie sind.",
            "register": "Sag mir bitte, wie du heißt und wie alt du bist.",
            "natural": "Ihr Name, und wie alt Sie sind, bitte.",
            "stt": "sagen sie mir bitte wie sie heißen und wie alt sie sind",
        }),
        (["onset"], "Wann ist der Ausschlag zum ersten Mal aufgetreten?", {
            "verb_form": "Wann ist der Ausschlag zum ersten Mal auftreten?",
            "gender": "Wann ist die Ausschlag zum ersten Mal aufgetreten?",
            "word_choice": "Wann ist der Ausschlag zum ersten Mal aufgestanden?",
            "word_order": "Wann der Ausschlag ist zum ersten Mal aufgetreten?",
            "natural": "Der Ausschlag, seit wann haben Sie den?",
            "stt": "wann ist der ausschlag zum ersten mal aufgetreten",
            "french": ("Wann ist l'éruption zum ersten Mal aufgetreten?", "Ausschlag"),
        }),
        (["character"], "Juckt der Ausschlag?", {
            "verb_form": "Jucken der Ausschlag?",
            "gender": "Juckt das Ausschlag?",
            "register": "Juckt dich der Ausschlag?",
            "natural": "Und jucken tut es auch?",
            "stt": "juckt der ausschlag",
        }),
        (["associated"], "Haben Sie Fieber oder Atemnot bemerkt?", {
            "verb_form": "Habt Sie Fieber oder Atemnot bemerkt?",
            "gender": "Haben Sie einen Fieber oder Atemnot bemerkt?",
            "register": "Hast du Fieber oder Atemnot bemerkt?",
            "natural": "Fieber, Atemnot, ist Ihnen da etwas aufgefallen?",
            "stt": "haben sie fieber oder atemnot bemerkt",
        }),
        (["trigger"], "Haben Sie in letzter Zeit ein neues Waschmittel benutzt?", {
            "gender": "Haben Sie in letzter Zeit einen neuen Waschmittel benutzt?",
            "case": "Haben Sie in letzter Zeit einem neuen Waschmittel benutzt?",
            "verb_form": "Haben Sie in letzter Zeit ein neues Waschmittel benutzen?",
            "word_order": "Haben Sie benutzt in letzter Zeit ein neues Waschmittel?",
            "register": "Hast du in letzter Zeit ein neues Waschmittel benutzt?",
            "natural": "Neues Waschmittel, Duschgel, haben Sie da etwas gewechselt?",
            "stt": "haben sie in letzter zeit ein neues waschmittel benutzt",
            "french": ("Haben Sie in letzter Zeit une nouvelle lessive benutzt?", "Waschmittel"),
        }),
        (["history"], "Hatten Sie früher schon Hautprobleme, zum Beispiel Neurodermitis?", {
            "verb_form": "Hattet Sie früher schon Hautprobleme, zum Beispiel Neurodermitis?",
            "case": "Hatten Sie früher schon Hautproblemen, zum Beispiel Neurodermitis?",
            "word_choice": "Hatten Sie früher schon Fellprobleme, zum Beispiel Neurodermitis?",
            "register": "Hattest du früher schon Hautprobleme, zum Beispiel Neurodermitis?",
            "natural": "Hautprobleme, etwa Neurodermitis, kennen Sie das von früher?",
            "stt": "hatten sie früher schon haut probleme zum beispiel neurodermitis",
        }),
        (["medication"], "Welche Medikamente nehmen Sie zurzeit?", {
            "verb_form": "Welche Medikamente nimmt Sie zurzeit?",
            "word_order": "Welche Medikamente Sie nehmen zurzeit?",
            "register": "Welche Medikamente nimmst du zurzeit?",
            "natural": "Und zurzeit, nehmen Sie da Medikamente?",
            "stt": "welche medikamente nehmen sie zurzeit",
            "french": ("Welche médicaments nehmen Sie zurzeit?", "Medikamente"),
        }),
        (["allergy"], "Sind bei Ihnen Allergien bekannt, etwa gegen Metalle?", {
            "case": "Sind bei Ihnen Allergien bekannt, etwa gegen Metallen?",
            "verb_form": "Ist bei Ihnen Allergien bekannt, etwa gegen Metalle?",
            "register": "Sind bei dir Allergien bekannt, etwa gegen Metalle?",
            "natural": "Allergien, zum Beispiel gegen Nickel, haben Sie welche?",
            "stt": "sind bei ihnen allergien bekannt etwa gegen metalle",
        }),
        (["smoking"], "Rauchen Sie, und wenn ja, seit wann?", {
            "verb_form": "Raucht Sie, und wenn ja, seit wann?",
            "register": "Rauchst du, und wenn ja, seit wann?",
            "natural": "Sind Sie Raucherin? Seit wann?",
            "stt": "rauchen sie und wenn ja seit wann",
            "french": ("Vous fumez, und wenn ja, seit wann?", "Rauchen Sie"),
        }),
        (["family"], "Gibt es Allergien in Ihrer Familie?", {
            "case": "Gibt es Allergien in Ihre Familie?",
            "gender": "Gibt es Allergien in Ihrem Familie?",
            "register": "Gibt es Allergien in deiner Familie?",
            "natural": "In der Familie, hat da jemand Allergien?",
            "stt": "gibt es allergien in ihrer familie",
        }),
        (["occupation"], "Kommen Sie bei der Arbeit mit Chemikalien in Kontakt?", {
            "case": "Kommen Sie bei die Arbeit mit Chemikalien in Kontakt?",
            "gender": "Kommen Sie bei dem Arbeit mit Chemikalien in Kontakt?",
            "verb_form": "Kommt Sie bei der Arbeit mit Chemikalien in Kontakt?",
            "word_choice": "Kommen Sie bei der Arbeit mit Chemikalien in Anschluss?",
            "register": "Kommst du bei der Arbeit mit Chemikalien in Kontakt?",
            "natural": "Haben Sie beruflich mit Chemikalien zu tun?",
            "stt": "kommen sie bei der arbeit mit chemikalien in kontakt",
        }),
    ],
}


def language_items(family: str) -> list[dict[str, Any]]:
    """Same construction as version 2, own seed, on the new families."""
    spec = CASES[family]
    phrases = {f["id"]: f["patient_phrase"] for f in spec["facts"]}
    section_of = {f: s["id"] for s in spec["anamnesis_sections"] for f in s["fact_ids"]}
    optional = [s["id"] for s in spec["anamnesis_sections"] if s["id"] in OPTIONAL_SECTIONS]
    all_slots = NEW_SLOTS[family]
    items = []
    for index in range(1, TRANSCRIPTS_PER_FAMILY + 1):
        rng = random.Random(f"evaluation-language@3:{family}:{index}")
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


def main(out: Path) -> None:
    directory = out / "evaluation-language" / "3"
    directory.mkdir(parents=True, exist_ok=True)
    jsonl = V2["_jsonl"]
    dev = [row for f in V2_DEV for row in V2["language_items"](f)]
    dev += [row for f in V2_HOLDOUT for row in V2["language_items"](f)]
    files = {
        "cases.json": json.dumps({"cases": list(CASES.values())}, ensure_ascii=False, indent=2)
        + "\n",
        "dev.jsonl": jsonl(dev),
        "holdout.jsonl": jsonl([row for f in HOLDOUT_FAMILIES for row in language_items(f)]),
    }
    for name, content in files.items():
        (directory / name).write_text(content, encoding="utf-8")
    manifest = {
        "suite": "evaluation-language",
        "version": "3",
        "description": (
            "Version 2 (développement et ancien holdout) en développement, et un holdout neuf de "
            "trois familles : transcripts synthétiques à erreurs injectées (genre, cas, forme "
            "verbale, ordre des mots, choix du mot, registre, mot français, artefact STT, section "
            "oubliée) et à formulations correctes mais inhabituelles : vérité connue par "
            "construction."
        ),
        "synthetic": True,
        "source": "Cas et énoncés inventés par l'agent, générés par evals/datasets/build_synthetic_v3.py",
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
            "Le holdout de la version 2 a été lancé une fois (1er novembre 2026, échec) : ses "
            "familles sont en développement dans cette version. Le holdout est neuf.",
            "Règle d'annotation genre / cas : voir l'en-tête de build_synthetic_v2.py.",
        ],
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
