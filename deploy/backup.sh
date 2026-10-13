#!/usr/bin/env bash
# Nightly off-site backup: both databases, the global roles, and the content storage volume,
# encrypted with age and copied to an S3-compatible bucket via rclone. A Docker volume survives a
# container restart, not the loss of the server, so the reference copy must live off the VPS.
#
#   deploy/backup.sh [label]      # label is appended to the set name, e.g. predeploy-<sha>
#
# Retention is handled by bucket lifecycle rules on the daily/ weekly/ monthly/ prefixes.
set -euo pipefail
cd "$(dirname "$0")/.."

# release.sh supplies these to back up a production release bundle without copying secrets into
# that bundle.  Existing manual calls still use the project root .env and project name.
compose_project="${ARI_COMPOSE_PROJECT:-ari}"
compose_file="${ARI_COMPOSE_FILE:-$PWD/docker-compose.yml}"
compose_env_file="${ARI_COMPOSE_ENV_FILE:-$PWD/.env}"
[ -f "$compose_env_file" ] || { echo "missing compose environment file: $compose_env_file" >&2; exit 1; }

# Explicit process settings are useful for a one-off protected backup.  Keep them above values
# in .env, while preserving the ordinary .env-only behaviour for unattended backups.
backup_age_override="${BACKUP_AGE_RECIPIENT:-}"
backup_remote_override="${BACKUP_RCLONE_REMOTE:-}"
backup_local_override="${BACKUP_LOCAL_DIR:-}"
backup_keep_override="${BACKUP_KEEP_LOCAL:-}"
backup_health_override="${BACKUP_HEALTHCHECK_URL:-}"

set -a
# shellcheck source=/dev/null
# The environment file is selected by the caller and validated above.
. "$compose_env_file"
set +a

[ -z "$backup_age_override" ] || BACKUP_AGE_RECIPIENT="$backup_age_override"
[ -z "$backup_remote_override" ] || BACKUP_RCLONE_REMOTE="$backup_remote_override"
[ -z "$backup_local_override" ] || BACKUP_LOCAL_DIR="$backup_local_override"
[ -z "$backup_keep_override" ] || BACKUP_KEEP_LOCAL="$backup_keep_override"
[ -z "$backup_health_override" ] || BACKUP_HEALTHCHECK_URL="$backup_health_override"

label="${1:-}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
setname="${stamp}${label:+-$label}"
work="${BACKUP_LOCAL_DIR:?set BACKUP_LOCAL_DIR}/$setname"

ping_fail() {
	if [ -n "${BACKUP_HEALTHCHECK_URL:-}" ]; then
		curl -fsS -m 10 "${BACKUP_HEALTHCHECK_URL}/fail" >/dev/null 2>&1 || true
	fi
}
trap 'ping_fail' ERR

mkdir -p "$work"
chmod 700 "$work"

# pg_dump over the container's local socket; the postgres superuser needs no password there.
compose() { docker compose -p "$compose_project" --env-file "$compose_env_file" -f "$compose_file" "$@"; }

compose exec -T postgres pg_dump -Fc -U postgres ari_platform >"$work/platform.dump"
compose exec -T postgres pg_dump -Fc -U postgres ari_content >"$work/content.dump"
compose exec -T postgres pg_dumpall --globals-only -U postgres >"$work/globals.sql"

# Uploaded PDFs and rendered pages are SHA-addressed and immutable, so a tar taken after the
# dumps is always a superset of what the dumps reference.
docker run --rm -v "${compose_project}_content_storage":/data:ro -v "$work":/backup alpine \
	tar czf /backup/content.tar.gz -C /data .

# One encrypted archive per set. The age private key is never on the server.
tar cf - -C "$work" . | age -r "${BACKUP_AGE_RECIPIENT:?set BACKUP_AGE_RECIPIENT}" >"$work.tar.age"
rm -rf "$work"

remote="${BACKUP_RCLONE_REMOTE:?set BACKUP_RCLONE_REMOTE}"
rclone copyto "$work.tar.age" "$remote/daily/$setname.tar.age"
if [ "$(date -u +%u)" = "7" ]; then
	rclone copyto "$work.tar.age" "$remote/weekly/$setname.tar.age"
fi
if [ "$(date -u +%d)" = "01" ]; then
	rclone copyto "$work.tar.age" "$remote/monthly/$setname.tar.age"
fi

# Keep only the newest few encrypted sets on the server itself.
keep="${BACKUP_KEEP_LOCAL:-3}"
# shellcheck disable=SC2012  # filenames are timestamped, safe for ls|tail
ls -1t "${BACKUP_LOCAL_DIR}"/*.tar.age 2>/dev/null | tail -n +"$((keep + 1))" | xargs -r rm -f

if [ -n "${BACKUP_HEALTHCHECK_URL:-}" ]; then
	curl -fsS -m 10 --retry 3 "${BACKUP_HEALTHCHECK_URL}" >/dev/null || true
fi
echo "Backup $setname uploaded to $remote/daily/."
