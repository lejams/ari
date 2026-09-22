#!/usr/bin/env bash
# Restore a backup set produced by backup.sh. By default it restores into a throwaway compose
# project on an alternate port so a monthly drill never touches the live stack.
#
#   AGE_IDENTITY_FILE=~/ari-backup.key deploy/restore.sh <set.tar.age> [project]
#   AGE_IDENTITY_FILE=~/ari-backup.key deploy/restore.sh <set.tar.age> ari --live
#
# The private age key is scp'd in for the drill and deleted afterwards; it never lives on the VPS.
# Promotion to live requires app/backoffice/worker to be stopped first (see docs/DEPLOYMENT.md).
set -euo pipefail
cd "$(dirname "$0")/.."

archive="${1:?usage: restore.sh <set.tar.age> [project] [--live]}"
project="${2:-ari-restore}"
live="${3:-}"

set -a
# shellcheck disable=SC1091
. ./.env
set +a

: "${AGE_IDENTITY_FILE:?set AGE_IDENTITY_FILE to the age private key path}"

if [ "$project" = "ari" ] && [ "$live" != "--live" ]; then
	echo "Refusing to overwrite the live project without --live." >&2
	exit 1
fi

if [ "$project" = "ari" ]; then
	running="$(docker compose -p ari ps --services --status running)"
	if echo "$running" | grep -qE '^(app|backoffice|worker)$'; then
		echo "Stop app, backoffice and worker before a live restore:" >&2
		echo "  docker compose --profile serve stop app backoffice worker" >&2
		exit 1
	fi
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
age -d -i "$AGE_IDENTITY_FILE" "$archive" | tar xf - -C "$tmp"

if [ "$project" != "ari" ]; then
	# Fresh, project-prefixed postgres on an alternate port; init.sh recreates the roles.
	ARI_POSTGRES_PORT=55432 docker compose -p "$project" up -d --wait postgres
fi

psql() { docker compose -p "$project" exec -T postgres psql -U postgres "$@"; }

# Roles already exist (init.sh / the live cluster); tolerate the duplicate errors.
psql -v ON_ERROR_STOP=0 -d postgres <"$tmp/globals.sql" >/dev/null 2>&1 || true

restore_db() {
	local db="$1" role="$2" dump="$3"
	psql -v ON_ERROR_STOP=1 -d postgres -c "DROP DATABASE IF EXISTS $db WITH (FORCE)"
	psql -v ON_ERROR_STOP=1 -d postgres -c "CREATE DATABASE $db OWNER $role"
	docker compose -p "$project" exec -T postgres \
		pg_restore -U postgres -d "$db" --no-owner --role="$role" <"$dump"
}
restore_db ari_platform ari_platform "$tmp/platform.dump"
restore_db ari_content ari_content "$tmp/content.dump"

# Content storage volume: replace its contents from the archive.
volume="${project}_content_storage"
docker volume create "$volume" >/dev/null
docker run --rm -v "$volume":/data -v "$tmp":/backup:ro alpine \
	sh -c 'rm -rf /data/* && tar xzf /backup/content.tar.gz -C /data'

echo "---- smoke checks ($project) ----"
psql -tAc "SELECT 'learners='||count(*) FROM learners" -d ari_platform
psql -tAc "SELECT 'sessions='||count(*) FROM sessions" -d ari_platform
psql -tAc "SELECT 'documents='||count(*) FROM documents" -d ari_content
psql -tAc "SELECT 'accounts='||count(*) FROM accounts" -d ari_content
files="$(docker run --rm -v "$volume":/data:ro alpine sh -c 'find /data -type f | wc -l')"
echo "content_storage files=$files"

if [ "$project" != "ari" ]; then
	echo
	echo "Drill restore complete. Tear down with: docker compose -p $project down -v"
fi
