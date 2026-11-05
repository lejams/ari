"""Builds `language-exercises@6`: the holdout of version 5, corrected after an outside read.

Usage, from the repository root:
    PYTHONPATH=backend/src python3 evals/datasets/build_language_exercises_v6.py [out_dir]

Why a sixth version. The holdout of version 5 was never run on the real models. An outside model
read its 16 exercises on 5 November 2026 (the 14 keys are correct and unique, the 2 absurd
exercises are to be refused) and found defects of wording and calibration. A published version
never changes, so the corrections make a new version. Nothing else moves:

1. The development split is exactly the one of version 5 (the file is copied byte for byte).
2. In the holdout, `e-n01` and `e-n05` get explanations that name every case an article can take
   (« der » is also a feminine dative, « die » also a plural, « die » is a nominative or an
   accusative, not only an accusative).
3. `e-n13-order-correction`: one distractor put the subject after the infinitive (« darf beginnen
   die Untersuchung »), a marginal but attested order; it is replaced by a clearly wrong one (the
   finite verb rejected to the end of the main clause). The explanation named the wrong word of
   each distractor and is rewritten without ordinals. The rule tested is a B2 rule, so the label
   goes from C1 to B2.
4. `e-n15-absurd` (first one): the exercise trains gender (« das Bein »), not case; its category
   becomes `gender`.
5. The checks, the prompts, the rubric and the thresholds are those of version 5 (`gates-v12` only
   adds the block of this version).

The output is deterministic and a test rebuilds it.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_language_exercises_v4 as v4
import build_language_exercises_v5 as v5
from build_language_exercises_v1 import AGENT, Base, candidate, jsonl, row

CREATED_ON = "2026-11-05"
V5_DIR = HERE / "language-exercises" / "5"

_FIXES: dict[str, dict[str, object]] = {
    "e-n01-gender-cloze": {
        "explanation_fr": (
            "« Formular » est neutre : à l'accusatif, l'article défini neutre est « das ». « der » "
            "(masculin, ou datif féminin) et « die » (féminin ou pluriel) ne s'accordent pas avec "
            "un nom neutre à l'accusatif."
        ),
    },
    "e-n05-case-cloze": {
        "explanation_fr": (
            "« vertrauen » se construit avec le datif. Le pronom relatif renvoie à « Ärztin » "
            "(féminin singulier) et se met donc au datif féminin : « der ». « die » est un "
            "nominatif ou un accusatif féminin, jamais un datif, et « deren » un génitif : "
            "« vertrauen » n'admet ni l'un ni l'autre."
        ),
    },
    "e-n13-order-correction": {
        "level": "B2",
        "distractors": [
            "Nur wenn der Patient ist nüchtern, darf die Untersuchung beginnen.",
            "Nur wenn der Patient nüchtern ist, die Untersuchung beginnen darf.",
        ],
        "explanation_fr": (
            "Quand la subordonnée ouvre la phrase, la principale commence par le verbe conjugué : "
            "« darf die Untersuchung beginnen ». Dans la subordonnée, « ist » reste en dernière "
            "position. Dans l'une des autres versions « ist » est placé trop tôt, dans l'autre "
            "« darf » est rejeté à la fin de la principale."
        ),
    },
}


def _fixed(b: Base) -> Base:
    fix = _FIXES.get(b["family"])
    return {**b, **fix} if fix else b  # type: ignore[typeddict-item,return-value]


HOLDOUT_BASES: list[Base] = [_fixed(b) for b in v5.HOLDOUT_BASES]
HOLDOUT_ABSURD: list[Base] = [
    {**b, "category": "gender"} if n == 0 else b  # type: ignore[typeddict-item,misc]
    for n, b in enumerate(v5.HOLDOUT_ABSURD)
]


def build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(V5_DIR / "dev.jsonl", out / "dev.jsonl")
    v4.NEAR_MISS = v5.NEAR_MISS
    holdout = v4.with_near_misses(HOLDOUT_BASES)
    holdout += [
        row(b, "medical_absurd", candidate(b), n)
        for n, b in enumerate(HOLDOUT_ABSURD, start=1)
    ]
    ids = [r["id"] for r in holdout]
    assert len(ids) == len(set(ids)), "duplicate ids in holdout.jsonl"
    (out / "holdout.jsonl").write_text(jsonl(holdout), encoding="utf-8")
    manifest5 = json.loads((V5_DIR / "manifest.json").read_text(encoding="utf-8"))
    files = {
        name: hashlib.sha256((out / name).read_bytes()).hexdigest()
        for name in sorted(("dev.jsonl", "holdout.jsonl"))
    }
    manifest = {
        "suite": "language-exercises",
        "version": "6",
        "description": (
            "Exercices de langue dont le défaut est connu par construction : mêmes exercices "
            "et mêmes défauts que la version 5, dont le holdout est corrigé après une relecture "
            "extérieure (explications de e-n01, e-n05 et e-n13, distracteur ambigu et niveau "
            "de e-n13, catégorie du premier exercice absurde)."
        ),
        "synthetic": True,
        "source": "Exercices inventés par l'agent, générés par evals/datasets/build_language_exercises_v6.py",
        "created_on": CREATED_ON,
        "consent": "not_applicable:synthetic",
        "annotators": [{**AGENT, "annotated_on": CREATED_ON}],
        "splits": {
            "dev": manifest5["splits"]["dev"],
            "holdout": [b["family"] for b in HOLDOUT_BASES] + ["e-n15-absurd"],
        },
        "files": files,
        "limitations": [
            "L'allemand a été écrit par l'agent et n'a pas été relu par une personne germanophone. Le holdout de la version 6 a été relu par un modèle extérieur (5 novembre 2026) : les quatorze clés sont justes et uniques, les deux exercices absurdes sont à refuser ; les défauts relevés sont corrigés ici. Un item « valide » refusé reste à regarder avant de conclure que le contrôle se trompe.",
            "Quatorze bases valides en holdout : un exercice juste refusé vaut 7 points de fausse rejection ; un zéro fausse acceptation sur ce jeu n'est pas une garantie.",
            "Le holdout est facile pour le niveau C1 (relecture extérieure) : il mesure la détection de fautes et d'absurdités, pas une validation exigeante des exercices C1. Le niveau de e-n13 est ramené à B2.",
            "Le développement est celui de la version 5 : tout ce qui a déjà été mesuré (versions 2, 3 et 4), sans les familles e-k12-choice-cloze et e-k13-order-correction, dont les contrôles jugent les références défendables. Le holdout de la version 6 est neuf et n'a été lu par aucun modèle des contrôles.",
            "Les défauts sont fabriqués à partir d'exercices corrects ; les fautes subtiles qu'un modèle auteur produit vraiment y sont moins bien représentées. Les fautes de frappe du défaut `near_miss_key` sont choisies une par famille.",
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "language-exercises" / "6")
