"""Bootstrap of the back-office (`python -m ari.backoffice_api.cli`): the first owner account."""

from __future__ import annotations

import argparse
import json
import sys

from ari.backoffice_api.container import build_backoffice
from ari.config import Settings
from ari.content.domain.accounts import Role
from ari.domain.errors import AriError


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
    args = parser.parse_args(argv)
    settings = Settings()
    services = build_backoffice(settings)
    try:
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


if __name__ == "__main__":
    raise SystemExit(run())
