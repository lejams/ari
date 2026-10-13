#!/usr/bin/env bash
# Transfers a pre-built release bundle and invokes its checked-in runtime script.
# Usage: transfer-release.sh <staging|production> <bundle-dir> <image-digest-ref> <commit-sha>
set -euo pipefail
fail() { echo "transfer-release: $*" >&2; exit 1; }
environment="${1:-}"; bundle_dir="${2:-}"; image_ref="${3:-}"; commit_sha="${4:-}"
case "$environment" in staging|production) ;; *) fail "environment must be staging or production";; esac
[[ "$commit_sha" =~ ^[0-9a-f]{40}$ ]] || fail "commit SHA must be 40 lowercase hex characters"
[[ "$image_ref" =~ ^ghcr\.io/lejams/ari@sha256:[0-9a-f]{64}$ ]] || fail "image must be the ARI GHCR digest reference"
[[ -d "$bundle_dir" && -f "$bundle_dir/release.json" ]] || fail "bundle is missing release.json"
DEPLOY_HOST="${DEPLOY_HOST:-}"; DEPLOY_USER="${DEPLOY_USER:-}"; DEPLOY_PORT="${DEPLOY_PORT:-22}"; DEPLOY_PATH="${DEPLOY_PATH:-}"
SSH_KNOWN_HOSTS="${SSH_KNOWN_HOSTS:-}"; SSH_PRIVATE_KEY="${SSH_PRIVATE_KEY:-}"
[[ "$DEPLOY_HOST" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || fail "invalid DEPLOY_HOST"
[[ "$DEPLOY_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || fail "invalid DEPLOY_USER"
if ! [[ "$DEPLOY_PORT" =~ ^[0-9]{1,5}$ ]] || (( DEPLOY_PORT <= 0 || DEPLOY_PORT >= 65536 )); then
  fail "invalid DEPLOY_PORT"
fi
[[ "$DEPLOY_PATH" =~ ^/[A-Za-z0-9._/-]+$ && "$DEPLOY_PATH" != *..* ]] || fail "invalid DEPLOY_PATH"
[[ -n "$SSH_KNOWN_HOSTS" && -n "$SSH_PRIVATE_KEY" ]] || fail "SSH_KNOWN_HOSTS and SSH_PRIVATE_KEY are required"
python3 - "$bundle_dir/release.json" "$commit_sha" "$image_ref" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as file:
    release = json.load(file)
if release != {"sha": sys.argv[2], "image": sys.argv[3]}:
    raise SystemExit("release.json does not match the requested immutable release")
PY
bundle_dir="$(cd "$bundle_dir" && pwd -P)"
chmod +x "$bundle_dir/deploy/release.sh"
key_file="$(mktemp)"; known_hosts_file="$(mktemp)"
cleanup() { rm -f "$key_file" "$known_hosts_file"; }
trap cleanup EXIT
umask 077
printf '%s\n' "$SSH_PRIVATE_KEY" > "$key_file"
printf '%s\n' "$SSH_KNOWN_HOSTS" > "$known_hosts_file"
chmod 600 "$key_file" "$known_hosts_file"
ssh_options=(-i "$key_file" -p "$DEPLOY_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts_file")
scp_options=(-i "$key_file" -P "$DEPLOY_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts_file")
target="$DEPLOY_USER@$DEPLOY_HOST"; release_dir="$DEPLOY_PATH/releases/$commit_sha"; environment_dir="$DEPLOY_PATH"
# release_dir is validated and intentionally expanded locally.
# shellcheck disable=SC2029
ssh "${ssh_options[@]}" "$target" "mkdir -p '$release_dir'"
scp "${scp_options[@]}" -r "$bundle_dir/." "$target:$release_dir/"
# release_dir is validated and intentionally expanded locally.
# shellcheck disable=SC2029
ssh "${ssh_options[@]}" "$target" "chmod +x '$release_dir'/deploy/*.sh"
# release_dir is validated and intentionally expanded locally.
# shellcheck disable=SC2029
ssh "${ssh_options[@]}" "$target" "$release_dir/deploy/release.sh" "$environment" "$environment_dir" "$image_ref" "$commit_sha"
if [ "$environment" = staging ]; then
  scp "${scp_options[@]}" "$target:$environment_dir/.staging-success.json" "$bundle_dir/staging-success.json"
  python3 - "$bundle_dir/staging-success.json" "$commit_sha" "$image_ref" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as file:
    marker = json.load(file)
if marker != {"sha": sys.argv[2], "image": sys.argv[3]}:
    raise SystemExit("staging success proof does not match this release")
PY
fi
