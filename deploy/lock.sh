#!/usr/bin/env bash
# Source this file, then call acquire_deploy_lock with an absolute lock path.  The file descriptor
# stays open for the caller's lifetime, so concurrent deploy/restore processes fail immediately.
acquire_deploy_lock() {
	local lock_path="$1"
	command -v flock >/dev/null 2>&1 || {
		echo "flock is required for deployment safety" >&2
		return 1
	}
	exec 9>"$lock_path"
	flock -n 9 || {
		echo "another deployment or live restore holds $lock_path" >&2
		return 1
	}
}
