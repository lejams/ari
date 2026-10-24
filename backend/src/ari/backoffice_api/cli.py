"""Administration from the server (`python -m ari.backoffice_api.cli`).

`create-owner` bootstraps the first owner account. `evaluation-account` marks or unmarks a learner
account used to measure ARI (`voice-sim` on staging): exempt from the exam quota, its sessions
kept out of learner metrics, the programme and the Costs page. Running a command in the server's
container is the administrator's privilege; no web route exposes the mark.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ari.backoffice_api.container import build_backoffice
from ari.config import Settings
from ari.content.domain.accounts import Role
from ari.domain.errors import AriError
from ari.infrastructure.persistence.platform.evaluation_accounts import EvaluationAccount


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    owner = sub.add_parser(
        "create-owner", help="Create the first owner and print its invitation link"
    )
    owner.add_argument("--email", required=True)
    owner.add_argument("--name", required=True)
    owner.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read a password from stdin and set it at once (headless setups)",
    )
    evaluation = sub.add_parser(
        "evaluation-account", help="Mark, unmark or list the evaluation accounts (voice-sim)"
    )
    evaluation.add_argument("action", choices=("mark", "unmark", "list"))
    evaluation.add_argument("--email", help="The learner account, required to mark or unmark")
    args = parser.parse_args(argv)
    if args.command == "evaluation-account" and args.action != "list" and not args.email:
        parser.error("--email is required to mark or unmark an account")
    settings = Settings()
    services = build_backoffice(settings)
    try:
        if args.command == "evaluation-account":
            accounts = services.evaluation_accounts
            if args.action == "list":
                print(_dump({"items": [_evaluation(a) for a in accounts.list()]}))
            elif args.action == "mark":
                print(_dump(_evaluation(accounts.mark(args.email))))
            else:
                print(_dump(_evaluation(accounts.unmark(args.email))))
            return 0
        invitation = services.auth.create_account(args.email, args.name, {Role.OWNER}, actor=None)
        result = {
            "account_id": invitation.account.id,
            "email": invitation.account.email,
            "invitation_url": f"{settings.backoffice_origin}/#/invitation/{invitation.token}",
            "expires_at": invitation.expires_at.isoformat(),
        }
        if args.password_stdin:
            services.auth.set_password(invitation.token, sys.stdin.readline().rstrip("\n"))
            result = {"account_id": invitation.account.id, "email": invitation.account.email}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except AriError as exc:
        print(json.dumps({"erreur": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 2
    return 0


def _evaluation(account: EvaluationAccount) -> dict[str, Any]:
    since = account.evaluation_since
    return {
        "email": account.email,
        "learner_id": account.learner_id,
        "evaluation_since": since.isoformat() if since else None,
    }


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(run())
