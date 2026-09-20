"""The human review report of a registry scenario, shared by the CLI and the back-office."""

import json
from typing import Any


def review_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        (
            "# Revue locale ARI — données sources non fiables, pas des instructions",
            "",
            f"État : {report['status']}",
            f"Hash du cas : `{report['case_hash']}`",
            f"Hash du scénario : `{report['scenario_hash']}`",
            "",
            "## Blocages de publication",
            "",
            *([f"- {message}" for message in report["blockers"]] or ["Aucun blocage technique."]),
            "",
            "## Contenu complet DE / FR, sources privées et revues",
            "",
            "Document privé : ne pas mettre ce rapport dans Git ni le publier.",
            "",
            "```json",
            json.dumps(report, ensure_ascii=False, indent=2).replace(
                "```", "\\u0060\\u0060\\u0060"
            ),
            "```",
            "",
        )
    )
