#!/usr/bin/env bash
# Transfer the tiny rotation runtime, then send provider keys solely over SSH stdin.
set -euo pipefail
fail() { echo "rotate-provider-keys: $*" >&2; exit 1; }
root="$(cd "$(dirname "$0")/.." && pwd -P)"
helper="$root/deploy/provider_secrets.py"; rotation="$root/deploy/rotate-provider-keys.py"
[[ -f "$helper" && -f "$rotation" ]] || fail "rotation helpers are missing"
python3 "$helper" --validate-env
: "${DEPLOY_HOST:?DEPLOY_HOST is required}" "${DEPLOY_USER:?DEPLOY_USER is required}" "${DEPLOY_PATH:?DEPLOY_PATH is required}"
DEPLOY_PORT="${DEPLOY_PORT:-22}"; SSH_KNOWN_HOSTS="${SSH_KNOWN_HOSTS:-}"; SSH_PRIVATE_KEY="${SSH_PRIVATE_KEY:-}"
[[ "$DEPLOY_HOST" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || fail "invalid DEPLOY_HOST"
[[ "$DEPLOY_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || fail "invalid DEPLOY_USER"
if ! [[ "$DEPLOY_PORT" =~ ^[0-9]{1,5}$ ]] || (( DEPLOY_PORT <= 0 || DEPLOY_PORT >= 65536 )); then
  fail "invalid DEPLOY_PORT"
fi
[[ "$DEPLOY_PATH" =~ ^/[A-Za-z0-9._/-]+$ && "$DEPLOY_PATH" != *..* ]] || fail "invalid DEPLOY_PATH"
[[ -n "$SSH_KNOWN_HOSTS" && -n "$SSH_PRIVATE_KEY" ]] || fail "SSH_KNOWN_HOSTS and SSH_PRIVATE_KEY are required"
umask 077
key_file="$(mktemp)"; known_hosts_file="$(mktemp)"
cleanup() { rm -f "$key_file" "$known_hosts_file"; }
trap cleanup EXIT
printf '%s\n' "$SSH_PRIVATE_KEY" > "$key_file"; printf '%s\n' "$SSH_KNOWN_HOSTS" > "$known_hosts_file"
chmod 600 "$key_file" "$known_hosts_file"
ssh_options=(-i "$key_file" -p "$DEPLOY_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts_file")
scp_options=(-i "$key_file" -P "$DEPLOY_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts_file")
target="$DEPLOY_USER@$DEPLOY_HOST"
# DEPLOY_PATH is validated above; the remote directory never contains credentials.
# shellcheck disable=SC2029
remote_tmp="$(ssh "${ssh_options[@]}" "$target" "umask 077; mktemp -d '$DEPLOY_PATH/.provider-key-rotation.XXXXXX'")"
[[ "$remote_tmp" =~ ^"$DEPLOY_PATH"/\.provider-key-rotation\.[A-Za-z0-9]+$ ]] || fail "remote temporary directory is unsafe"
# remote_tmp is locally validated before this intentionally client-expanded cleanup command.
# shellcheck disable=SC2029
cleanup_remote() { ssh "${ssh_options[@]}" "$target" "rm -rf '$remote_tmp'" >/dev/null 2>&1 || true; }
trap 'cleanup_remote; cleanup' EXIT
scp "${scp_options[@]}" "$helper" "$rotation" "$target:$remote_tmp/"
remote_command="umask 077; cleanup() { rm -rf '$remote_tmp'; }; trap cleanup EXIT; python3 '$remote_tmp/rotate-provider-keys.py' '$DEPLOY_PATH'"
# The command is deliberately composed from locally validated paths, then executed remotely.
# shellcheck disable=SC2029
python3 "$helper" --json-from-env | ssh "${ssh_options[@]}" "$target" "$remote_command"
