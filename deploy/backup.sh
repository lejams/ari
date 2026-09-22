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

set -a
# shellcheck disable=SC1091
. ./.env
set +a

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
docker compose exec -T postgres pg_dump -Fc -U postgres ari_platform >"$work/platform.dump"
docker compose exec -T postgres pg_dump -Fc -U postgres ari_content >"$work/content.dump"
docker compose exec -T postgres pg_dumpall --globals-only -U postgres >"$work/globals.sql"

# Uploaded PDFs and rendered pages are SHA-addressed and immutable, so a tar taken after the
# dumps is always a superset of what the dumps reference.
docker run --rm -v ari_content_storage:/data:ro -v "$work":/backup alpine \
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
