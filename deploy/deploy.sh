#!/usr/bin/env bash
# Idempotent production deploy: fetch main, tag the image by git SHA, back up, rebuild, migrate,
# restart, wait for health. Run from the server, inside the repository (e.g. /opt/ari).
#
#   deploy/deploy.sh
#
# Rollback (application code): ARI_IMAGE_TAG=<previous-sha> docker compose --profile serve up -d --no-build
# Rollback (data, if a migration ran): restore the predeploy-<sha> backup set (see restore.sh).
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose --profile serve "$@"; }

has_migrated_schema() {
	if ! compose ps --all --services | grep -qx postgres; then
		return 1
	fi
	if ! compose ps --status running --services | grep -qx postgres; then
		echo "Postgres exists but is not running; refusing to deploy without a backup." >&2
		exit 1
	fi

	local platform_version content_version
	if ! platform_version="$(docker compose exec -T postgres psql -U postgres -d ari_platform -tAc \
		"SELECT to_regclass('public.alembic_version')" | tr -d '[:space:]')"; then
		echo "Could not inspect ari_platform; refusing to deploy without a backup." >&2
		exit 1
	fi
	if ! content_version="$(docker compose exec -T postgres psql -U postgres -d ari_content -tAc \
		"SELECT to_regclass('public.alembic_version')" | tr -d '[:space:]')"; then
		echo "Could not inspect ari_content; refusing to deploy without a backup." >&2
		exit 1
	fi
	if [ "$platform_version" = "alembic_version" ] && [ "$content_version" = "alembic_version" ]; then
		return 0
	fi
	if [ -n "$platform_version" ] || [ -n "$content_version" ]; then
		echo "Database state is inconsistent; refusing to deploy without a backup." >&2
		exit 1
	fi
	return 1
}

# Refuse to deploy anything but a clean main checkout.
if [ -n "$(git status --porcelain)" ]; then
	echo "Working tree is dirty; commit or stash before deploying." >&2
	exit 1
fi
branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "$branch" != "main" ]; then
	echo "On branch '$branch'; production deploys from main only." >&2
	exit 1
fi

# compose >= 2.20 is required for `up --wait` to accept the one-shot migrate service.
cver="$(docker compose version --short | sed 's/^v//')"
if ! printf '%s\n' "$cver" | awk -F. '{ exit !($1 > 2 || ($1 == 2 && $2 >= 20)) }'; then
	echo "docker compose >= 2.20 required; found $cver." >&2
	exit 1
fi

prev="$(git rev-parse --short HEAD)"
git pull --ff-only origin main
tag="$(git rev-parse --short HEAD)"
export ARI_IMAGE_TAG="$tag"
echo "Deploying $tag (was $prev)."

# The rollback point: a full backup taken before any migration runs. On the first deployment,
# the databases have been created by Postgres but have no Alembic schema to back up yet.
if has_migrated_schema; then
	deploy/backup.sh "predeploy-$tag"
else
	echo "No previous deployment detected; skipping pre-deploy backup."
fi

compose build --pull
# up re-runs migrate (both Alembic chains) then starts the rest; --wait blocks on healthchecks.
compose up -d --no-build --wait --wait-timeout 180

compose ps
echo "---- migrate log ----"
docker compose logs --tail 20 migrate || true
cat <<EOF

Deployed $tag (previous $prev).
Rollback code: ARI_IMAGE_TAG=$prev docker compose --profile serve up -d --no-build
Rollback data: deploy/restore.sh <predeploy-$tag set> ari --live  (only if a migration ran)
EOF
