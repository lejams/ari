#!/usr/bin/env bash
# Deploy one immutable CI image from an extracted release bundle.  This deliberately has no
# git/build/down/reset path: the image argument is the exact artifact promoted by CI.
#
#   deploy/release.sh <staging|production> <absolute-environment-dir> <image@sha256:...> <commit-sha>
set -Eeuo pipefail

usage() { echo "usage: $0 <staging|production> <environment-dir> <image-ref> <commit-sha>" >&2; exit 2; }
[ "$#" -eq 4 ] || usage
target="$1" env_dir="$2" image_ref="$3" commit_sha="$4"
case "$target" in staging|production) ;; *) usage ;; esac
case "$env_dir" in /*) ;; *) echo "environment directory must be absolute" >&2; exit 2 ;; esac
[ -d "$env_dir" ] && [ -f "$env_dir/.env" ] || { echo "environment directory must contain .env" >&2; exit 2; }
[[ "$commit_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "commit SHA must be 40 lowercase hex characters" >&2; exit 2; }
[[ "$image_ref" =~ ^ghcr\.io/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$ ]] || { echo "image must be a lowercase ghcr.io repository pinned by sha256 digest" >&2; exit 2; }

bundle_root="$(cd "$(dirname "$0")/.." && pwd)"
base_compose="$bundle_root/docker-compose.yml"
[ -f "$base_compose" ] || { echo "release bundle is missing docker-compose.yml" >&2; exit 2; }
[ -f "$bundle_root/deploy/lock.sh" ] || { echo "release bundle is missing deploy/lock.sh" >&2; exit 2; }
# shellcheck disable=SC1091
. "$bundle_root/deploy/lock.sh"

compose_args=(--env-file "$env_dir/.env" -f "$base_compose")
project="ari"
services=(app backoffice worker proxy)
if [ "$target" = staging ]; then
  staging_compose="$bundle_root/deploy/staging.compose.yml"
  [ -f "$staging_compose" ] || { echo "release bundle is missing deploy/staging.compose.yml" >&2; exit 2; }
  compose_args+=(-f "$staging_compose")
  project="ari-staging"
  services=(app backoffice worker)
fi
compose() { ARI_IMAGE_REF="$image_ref" docker compose -p "$project" "${compose_args[@]}" "$@"; }

# Do not trust an arbitrary .env to choose a differently named production project.  `-p` also
# fixes the runtime project, but this guard catches a mis-mounted environment before Docker runs.
grep -qx "COMPOSE_PROJECT_NAME=$project" "$env_dir/.env" || {
  echo "environment .env must set COMPOSE_PROJECT_NAME=$project" >&2
  exit 1
}
acquire_deploy_lock "$env_dir/.deploy.lock" || exit 1

previous="none"
if [ -f "$env_dir/.release.json" ]; then
  previous="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("{} {}".format(d.get("image", "unknown"), d.get("sha", "unknown")))' "$env_dir/.release.json" 2>/dev/null || echo invalid-state)"
fi

validate_proof() {
  local proof="$bundle_root/staging-success.json"
  [ -f "$proof" ] || { echo "production requires release-bundle staging-success.json" >&2; exit 1; }
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d == {"sha": sys.argv[2], "image": sys.argv[3]} else 1)' "$proof" "$commit_sha" "$image_ref" \
    || { echo "staging success proof does not match this image and commit" >&2; exit 1; }
}

production_preflight() {
  docker volume inspect ari_postgres_data >/dev/null || { echo "missing ari_postgres_data; refusing production deploy" >&2; exit 1; }
  docker volume inspect ari_content_storage >/dev/null || { echo "missing ari_content_storage; refusing production deploy" >&2; exit 1; }
  compose ps --status running --services | grep -qx postgres || { echo "existing production postgres must be running" >&2; exit 1; }
  # Read-only inventory gives the operator a visible check that content data exists before its backup.
  compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d ari_content -tAc \
    "SELECT 'documents='||count(*) FROM documents; SELECT 'protocol_versions='||count(*) FROM protocols;" >&2
  local files
  files="$(docker run --rm -v ari_content_storage:/data:ro alpine sh -c 'find /data -type f | wc -l')"
  echo "content_storage_files=$files" >&2
  # A populated volume alone does not prove that the original PDFs referenced by the DB exist.
  local keys
  keys="$(compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d ari_content -tAc \
    'SELECT storage_key FROM documents')"
  printf '%s\n' "$keys" | docker run --rm -i -v ari_content_storage:/data:ro alpine sh -ec '
    while IFS= read -r key; do
      [ -n "$key" ] || continue
      case "$key" in documents/*/*.pdf) ;; *) exit 1 ;; esac
      case "$key" in *..*) exit 1 ;; esac
      [ -s "/data/$key" ] || { echo "A referenced source PDF is missing" >&2; exit 1; }
    done'
}

write_state() {
  local destination="$1" kind="$2"
  local temporary
  temporary="$(mktemp "$env_dir/.${kind}.XXXXXX")"
  python3 -c 'import json,sys; json.dump({"sha":sys.argv[1],"image":sys.argv[2]}, open(sys.argv[3], "w"), separators=(",", ":")); print()' "$commit_sha" "$image_ref" "$temporary"
  chmod 600 "$temporary"
  mv -f "$temporary" "$destination"
}

write_failure() {
  local failure_phase="$1" temporary
  temporary="$(mktemp "$env_dir/.release-failure.XXXXXX")"
  python3 -c 'import json,sys
try:
    previous=json.load(open(sys.argv[4]))
except (OSError, ValueError):
    previous=None
json.dump({"sha":sys.argv[1],"image":sys.argv[2],"phase":sys.argv[3],"previous":previous}, open(sys.argv[5], "w"), separators=(",", ":")); print()' \
    "$commit_sha" "$image_ref" "$failure_phase" "$env_dir/.release.json" "$temporary"
  chmod 600 "$temporary"
  mv -f "$temporary" "$env_dir/.release-failure.json"
}

failure_guard=0
phase="preflight"
on_release_error() {
  local status="$?"
  if [ "$failure_guard" = 1 ]; then
    write_failure "$phase" || true
    echo "Release failed during $phase. Inspect .release-failure.json; do not restart an older app until migration compatibility is assessed." >&2
  fi
  exit "$status"
}
trap on_release_error ERR

if [ "$target" = production ]; then
  validate_proof
fi

# !override in the staging ports configuration needs Compose 2.24.4 or newer.
compose_version="$(docker compose version --short | sed 's/^v//')"
printf '%s\n' "$compose_version" | awk -F. '{exit !(NF >= 3 && ($1 > 2 || ($1 == 2 && ($2 > 24 || ($2 == 24 && $3 >= 4)))))}' \
  || { echo "Docker Compose >= 2.24.4 is required" >&2; exit 1; }
if [ "$target" = production ]; then
  production_preflight
fi

# The registry pull is explicit; `up --no-build` below can never silently build a local image.
compose pull app backoffice worker
if [ "$target" = production ]; then
  # A backup failure stops here, before the worker is quiesced or either migration can run.
  ARI_COMPOSE_PROJECT="$project" ARI_COMPOSE_FILE="$base_compose" ARI_COMPOSE_ENV_FILE="$env_dir/.env" \
    "$bundle_root/deploy/backup.sh" "predeploy-${commit_sha}"
else
  compose up -d --no-build --wait --wait-timeout 180 postgres
fi

# No writes run while schema migrations execute.  Once this begins, leave an atomic diagnostic on
# any failure; automatic rollback/restart can be unsafe after a successful migration.
failure_guard=1
phase="quiesce"
compose stop app backoffice worker
phase="migrate"
compose up -d --no-build --wait --wait-timeout 180 migrate
phase="activate"
compose up -d --no-build --no-deps --wait --wait-timeout 180 "${services[@]}"

write_state "$env_dir/.release.json" release
if [ "$target" = staging ]; then
  write_state "$env_dir/.staging-success.json" staging-success
fi
rm -f "$env_dir/.release-failure.json"
failure_guard=0
echo "Deployed $target $image_ref ($commit_sha); previous successful release: $previous"
echo "Fallback: rerun this command with the previous immutable image and SHA after assessing migration compatibility; data is not rolled back automatically."
