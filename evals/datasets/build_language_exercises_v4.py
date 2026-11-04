"""Builds `language-exercises@4`: a holdout reviewed by an outside reader, and a dev made of what was burnt.

Usage, from the repository root:
    PYTHONPATH=backend/src python3 evals/datasets/build_language_exercises_v4.py [out_dir]

Why a fourth version. The holdout of version 3 (run 41912108307, 4 November 2026) passed the
safety metric (no defective exercise accepted, near-miss typos included) but failed the gate on
false rejection (0.40 against 0.25). Its content is hidden by design, so the cause cannot be read;
the dev diagnosis of the same suite shows the judge refusing references that are defensible. A
holdout written by the agent alone keeps repeating that.

What changed.
1. The development split is everything already measured: the 28 families of version 2 and the 14
   families of the version 3 holdout, with every defect of version 3 (`near_miss_key` included).
2. The holdout is new: 14 good exercises (`e-m01` to `e-m14`) and 2 medically absurd ones
   (`e-m15-absurd`). They were written by the agent, then read by an outside model on the owner's
   request; the corrections of that reading (an explanation that called "der" a feminine
   nominative, distractors that were already correct, variants that changed the meaning, two
   exercises that did not isolate their point) are in them. No verdict of the real models was read
   while writing them, and the checks, prompts and thresholds are those of version 3.
3. The `no_correct` variant of a vocabulary exercise is a well-spelt word that does not fit (not a
   misspelling), so that it is not the same fault as its `near_miss_key`.

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
from build_language_exercises_v3 import (  # noqa: E402
    NEAR_MISS as V3_NEAR_MISS,
)
from build_language_exercises_v3 import (  # noqa: E402
    NEW_HOLDOUT_ABSURD as V3_HOLDOUT_ABSURD,
)
from build_language_exercises_v3 import (  # noqa: E402
    NEW_HOLDOUT_BASES as V3_HOLDOUT_BASES,
)

CREATED_ON = "2026-11-04"

NEAR_MISS: dict[str, str] = {
    **V3_NEAR_MISS,
    "e-m01-gender-cloze": "dei",
    "e-m02-case-cloze": "dm",
    "e-m03-verb-cloze": "habbe",
    "e-m04-order-correction": "Rufen Sie mich an, wenn die Schmerzen stärker werdn.",
    "e-m05-case-cloze": "dse",
    "e-m06-verb-cloze": "wurte",
    "e-m07-verb-cloze": "häte",
    "e-m08-choice-cloze": "ausgestelt",
    "e-m09-order-correction": (
        "Obwohl der Patient starke Schmerzn hatte, wollte er nicht im Krankenhaus bleiben."
    ),
    "e-m11-case-cloze": "weiterer Untersuchngen",
    "e-m12-choice-cloze": "hervorgehobn",
    "e-m13-verb-cloze": "häte",
}

HOLDOUT_BASES: list[Base] = [
    base(
        "e-m01-gender-cloze",
        "B1",
        "gender",
        "cloze",
        stem="Die Krankenschwester legt ___ Decke über den Patienten.",
        key="die",
        distractors=["den", "das"],
        explanation_fr="« Decke » est féminin : à l'accusatif, l'article défini féminin est « die ». « den » est masculin et « das » neutre.",
        setting="soins",
        second_correct="eine",
        wrong_variant="dem",
        bad_explanation="« Decke » est neutre : l'article défini neutre à l'accusatif est « die ».",
        other_category="verb_form",
    ),
    base(
        "e-m02-case-cloze",
        "B1",
        "case",
        "cloze",
        stem="Die Patientin dankt ___ Arzt für die gute Behandlung.",
        key="dem",
        distractors=["den", "der"],
        explanation_fr="« danken » demande le datif. Au masculin singulier, on dit « dem Arzt ». « den Arzt » est l'accusatif et « der Arzt » le nominatif : ni l'un ni l'autre ne s'emploie après « danken ».",
        setting="consultation",
        second_correct="diesem",
        wrong_variant="demm",
        bad_explanation="« danken » se construit avec l'accusatif : « den Arzt ».",
        other_category="gender",
    ),
    base(
        "e-m03-verb-cloze",
        "B1",
        "verb_form",
        "cloze",
        stem="Gestern ___ ich lange im Wartezimmer gewartet.",
        key="habe",
        distractors=["bin", "war"],
        explanation_fr="« warten » au sens d'attendre forme son parfait avec « haben » : « habe gewartet ». « bin gewartet » et « war gewartet » n'existent pas dans ce sens.",
        setting="salle d'attente",
        second_correct=None,
        wrong_variant="haben",
        bad_explanation="« warten » forme son parfait avec « sein » : « bin gewartet ».",
        other_category="case",
    ),
    base(
        "e-m04-order-correction",
        "B1",
        "word_order",
        "correct_sentence",
        stem="Rufen Sie mich an, wenn die Schmerzen werden stärker.",
        key="Rufen Sie mich an, wenn die Schmerzen stärker werden.",
        distractors=[
            "Rufen Sie mich an, wenn werden die Schmerzen stärker.",
            "Rufen Sie mich an, wenn werden stärker die Schmerzen.",
        ],
        explanation_fr="Dans une subordonnée introduite par « wenn », le verbe conjugué se place en dernière position : « wenn die Schmerzen stärker werden ». Les deux autres versions mettent « werden » trop tôt, juste après « wenn ».",
        setting="suivi",
        second_correct=None,
        wrong_variant="Rufen Sie mich an, wenn die Schmerzen stärker wird.",
        bad_explanation="Dans une subordonnée introduite par « wenn », le verbe conjugué reste en deuxième position.",
        other_category="verb_form",
    ),
    base(
        "e-m05-case-cloze",
        "B2",
        "case",
        "cloze",
        stem="Trotz ___ hohen Fiebers ging der Patient zur Arbeit.",
        key="des",
        distractors=["dem", "den"],
        explanation_fr="Dans cette phrase, la forme « Fiebers » marque le génitif singulier. Avec ce nom neutre, on obtient « des hohen Fiebers ». « dem » est un datif et « den » un accusatif : ils ne vont pas avec « Fiebers ».",
        setting="arrêt de travail",
        second_correct="dieses",
        wrong_variant="dess",
        bad_explanation="« trotz » est suivi de l'accusatif : « den ».",
        other_category="gender",
    ),
    base(
        "e-m06-verb-cloze",
        "B2",
        "verb_form",
        "cloze",
        stem="Die Wunde ___ gestern von der Ärztin genäht.",
        key="wurde",
        distractors=["wurden", "hat"],
        explanation_fr="Passif au prétérit : « wurde » + participe passé. « wurden » est le pluriel et ne s'accorde pas avec « Wunde » ; « hat » ne forme pas de passif.",
        setting="chirurgie",
        second_correct=None,
        wrong_variant="worde",
        bad_explanation="Le passif se forme avec « haben » et le participe passé : « hat genäht ».",
        other_category="case",
    ),
    base(
        "e-m07-verb-cloze",
        "B2",
        "verb_form",
        "cloze",
        stem="Wenn ich mehr Zeit ___, würde ich öfter zur Vorsorge gehen.",
        key="hätte",
        distractors=["hätten", "hat"],
        explanation_fr="Dans la lecture hypothétique proposée, on emploie le subjonctif II de « haben ». À la première personne du singulier, la forme est « hätte ». « hätten » est la forme du pluriel (wir, sie) et « hat » est l'indicatif de la 3e personne : ni l'un ni l'autre ne s'accorde avec « ich ».",
        setting="prévention",
        second_correct=None,
        wrong_variant="hättest",
        bad_explanation="Une condition irréelle au présent demande l'indicatif : « habe ».",
        other_category="word_choice",
    ),
    base(
        "e-m08-choice-cloze",
        "B2",
        "word_choice",
        "cloze",
        stem="Die Hausärztin hat dem Patienten eine Krankschreibung für eine Woche ___.",
        key="ausgestellt",
        distractors=["eingestellt", "angestellt"],
        explanation_fr="On établit un certificat avec « ausstellen ». « einstellen » signifie engager ou arrêter, « anstellen » employer : aucun des deux ne se dit d'un certificat.",
        setting="certificat",
        second_correct="ausgehändigt",
        wrong_variant="ausgestellen",
        bad_explanation="« ausstellen » ne s'emploie que pour exposer des objets, jamais pour un document.",
        other_category="verb_form",
    ),
    base(
        "e-m09-order-correction",
        "B1",
        "word_order",
        "correct_sentence",
        stem="Obwohl der Patient hatte starke Schmerzen, wollte er nicht im Krankenhaus bleiben.",
        key="Obwohl der Patient starke Schmerzen hatte, wollte er nicht im Krankenhaus bleiben.",
        distractors=[
            "Obwohl hatte der Patient starke Schmerzen, wollte er nicht im Krankenhaus bleiben.",
            "Obwohl der Patient starke Schmerzen hatte, er wollte nicht im Krankenhaus bleiben.",
        ],
        explanation_fr="Après « obwohl », le verbe conjugué va en fin de subordonnée (« Schmerzen hatte »). Quand la subordonnée ouvre la phrase, la principale commence par le verbe : « wollte er », pas « er wollte ».",
        setting="hospitalisation",
        second_correct=None,
        wrong_variant="Obwohl der Patient starke Schmerzen hattest, wollte er nicht im Krankenhaus bleiben.",
        bad_explanation="Après « obwohl », le verbe conjugué reste en deuxième position.",
        other_category="verb_form",
    ),
    base(
        "e-m10-register-choice",
        "B2",
        "register",
        "register_choice",
        context_fr="Premier courriel à un confrère d'un autre hôpital que vous ne connaissez pas, pour lui demander poliment un avis non urgent sur un patient.",
        stem="Moin! Guck dir mal die Akte an, ich weiß nicht weiter.",
        key="Sehr geehrter Herr Kollege, könnten Sie sich bitte die Akte ansehen? Ich bin bei der Einschätzung unsicher und wäre für Ihren Rat dankbar.",
        distractors=[
            "Hallo, kannst du mal kurz in die Akte schauen? Bin unsicher, was ich machen soll.",
            "Hiermit wird Ihnen die Akte zur umgehenden Prüfung vorgelegt; eine Rückmeldung wird erwartet.",
        ],
        explanation_fr="Dans un premier message à un confrère inconnu, on vouvoie et on demande poliment. La version familière tutoie quelqu'un qu'on ne connaît pas et reste relâchée ; la version administrative est impersonnelle et exige une réponse au lieu de la demander, ce qui ne convient pas à un avis non urgent.",
        setting="échange entre médecins",
        second_correct="Guten Tag, könnten Sie sich bitte die Akte ansehen? Ich bin unsicher und freue mich über Ihre Einschätzung.",
        wrong_variant="Mach mal die Akte auf.",
        bad_explanation="Entre médecins d'hôpitaux différents, le tutoiement familier est la norme et le vouvoiement serait déplacé.",
        other_category=None,
    ),
    base(
        "e-m11-case-cloze",
        "C1",
        "case",
        "cloze",
        stem="Der unklare Befund der Röntgenaufnahme bedarf dringend ___.",
        key="weiterer Untersuchungen",
        distractors=["weiteren Untersuchungen", "weitere Untersuchungen"],
        explanation_fr="« bedürfen » régit le génitif. Ici, sans article, l'adjectif prend la terminaison forte « -er » au génitif pluriel : « weiterer Untersuchungen ». « weiteren Untersuchungen » est un datif pluriel et « weitere Untersuchungen » un nominatif ou un accusatif : « bedürfen » n'en admet aucun.",
        setting="diagnostic",
        second_correct="mehrerer Untersuchungen",
        wrong_variant="weiteres Untersuchungen",
        bad_explanation="« bedürfen » régit le datif : « weiteren Untersuchungen ».",
        other_category="word_choice",
    ),
    base(
        "e-m12-choice-cloze",
        "C1",
        "word_choice",
        "cloze",
        stem="Der Chefarzt hat in der Besprechung die Bedeutung einer frühen Diagnose ___.",
        key="hervorgehoben",
        distractors=["hervorgebracht", "hervorgerufen"],
        explanation_fr="« die Bedeutung hervorheben » signifie souligner son importance. « hervorbringen » signifie produire, « hervorrufen » provoquer : aucun ne se dit de l'importance d'une chose. Les trois options ont la même construction, seul le sens les distingue.",
        setting="réunion d'équipe",
        second_correct="betont",
        wrong_variant="fotokopiert",
        bad_explanation="« hervorheben » signifie provoquer une réaction ; on souligne une importance avec « hervorrufen ».",
        other_category="verb_form",
    ),
    base(
        "e-m13-verb-cloze",
        "C1",
        "verb_form",
        "cloze",
        stem="Die Komplikation ___ vermieden werden können, wenn man früher operiert hätte.",
        key="hätte",
        distractors=["wäre", "wurde"],
        explanation_fr="Irréel du passé au passif avec un verbe modal : « hätte vermieden werden können ». « wäre » convient au passif sans modal (« wäre vermieden worden ») mais ne peut pas précéder « vermieden werden können » ; « wurde » forme un prétérit passif, qui ne s'enchaîne pas non plus avec cette suite d'infinitifs.",
        setting="analyse de cas",
        second_correct=None,
        wrong_variant="hätten",
        bad_explanation="L'irréel du passé se forme avec « sein » à l'indicatif : « ist vermieden worden ».",
        other_category="case",
    ),
    base(
        "e-m14-register-choice",
        "C1",
        "register",
        "register_choice",
        context_fr="Vous répondez par écrit à la réclamation d'un patient sur le temps d'attente. Objectif : présenter des excuses, reconnaître l'attente et expliquer la situation sans rejeter la responsabilité.",
        stem="Das mit der Wartezeit tut mir echt leid, aber wir hatten voll viel zu tun.",
        key="Wir bitten Sie, die lange Wartezeit zu entschuldigen; an diesem Tag war die Praxis außergewöhnlich stark ausgelastet.",
        distractors=[
            "Sorry wegen der Warterei, war einfach mega viel los bei uns.",
            "Die Wartezeit ist ausschließlich auf die hohe Auslastung zurückzuführen, für welche keinerlei Verantwortung seitens der Praxis besteht.",
        ],
        explanation_fr="La réponse doit s'excuser, reconnaître l'attente et expliquer sans rejeter la faute, dans un registre écrit soigné. La formulation « Sorry wegen der Warterei » est trop familière. La formulation « Die Wartezeit ist ausschließlich… » est défensive et décline toute responsabilité au lieu de s'excuser.",
        setting="réclamation",
        second_correct="Es tut uns aufrichtig leid, dass Sie lange warten mussten; an diesem Tag waren außergewöhnlich viele Patienten in der Praxis.",
        wrong_variant="Pech gehabt.",
        bad_explanation="Une réponse écrite à une plainte doit rester familière pour rassurer le patient.",
        other_category=None,
    ),
]

HOLDOUT_ABSURD: list[Base] = [
    base(
        "e-m15-absurd",
        "B1",
        "case",
        "cloze",
        stem="Gegen den Husten bekommt der Patient einen Verband um ___ Knie.",
        key="das",
        distractors=["dem", "der"],
        explanation_fr="« um » est suivi de l'accusatif ; « Knie » est neutre : « das Knie ».",
        setting="traitement",
    ),
    base(
        "e-m15-absurd",
        "B2",
        "gender",
        "cloze",
        stem="Zur Behandlung der Migräne wird der Patientin ___ Blinddarm entfernt.",
        key="der",
        distractors=["die", "das"],
        explanation_fr="« Blinddarm » est masculin et sujet du passif : au nominatif, l'article défini masculin est « der ».",
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
    dev_bases = [*V2_DEV_BASES, *V2_HOLDOUT_BASES, *V3_HOLDOUT_BASES]
    dev_absurd = [*V2_DEV_ABSURD, *V2_HOLDOUT_ABSURD, *V3_HOLDOUT_ABSURD]
    for b in dev_bases + HOLDOUT_BASES:
        if b["format"] != "register_choice":
            assert b["family"] in NEAR_MISS, f"no near miss for {b['family']}"
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
        "version": "4",
        "description": (
            "Exercices de langue dont le défaut est connu par construction : 56 exercices "
            "corrects écrits à la main (42 en développement, repris des versions 2 et 3, et 14 "
            "nouveaux en holdout, relus par un lecteur extérieur) et leurs variantes défectueuses "
            "(mauvaise clé, deux réponses justes, aucune réponse juste, réponse presque juste, "
            "explication fausse, mauvaise catégorie, mauvais niveau, options en double, format "
            "cassé, décor médical absurde)."
        ),
        "synthetic": True,
        "source": "Exercices inventés par l'agent, générés par evals/datasets/build_language_exercises_v4.py",
        "created_on": CREATED_ON,
        "consent": "not_applicable:synthetic",
        "annotators": [{**AGENT, "annotated_on": CREATED_ON}],
        "splits": {
            "dev": [b["family"] for b in dev_bases]
            + ["e-d15-absurd", "e-h15-absurd", "e-k15-absurd"],
            "holdout": [b["family"] for b in HOLDOUT_BASES] + ["e-m15-absurd"],
        },
        "files": files,
        "limitations": [
            "L'allemand a été écrit par l'agent et n'a pas été relu par une personne germanophone : le holdout a été relu par un lecteur extérieur (un modèle), à la demande du propriétaire, et corrigé, mais cela ne remplace pas un germanophone. Un item « valide » refusé est à regarder avant de conclure que le contrôle se trompe.",
            "Quatorze bases valides en holdout : un exercice juste refusé vaut 7 points de fausse rejection ; un zéro fausse acceptation sur ce jeu n'est pas une garantie.",
            "Le développement contient tout ce qui a déjà été mesuré (versions 2 et 3, dont deux holdouts lancés une fois chacun) : il ne sert plus de test indépendant. Le holdout de la version 4 est neuf et n'a été lu par aucun modèle des contrôles avant leur gel.",
            "Les défauts sont fabriqués à partir d'exercices corrects ; les fautes subtiles qu'un modèle auteur produit vraiment y sont moins bien représentées. Les fautes de frappe du défaut `near_miss_key` sont choisies une par famille.",
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "language-exercises" / "4")
