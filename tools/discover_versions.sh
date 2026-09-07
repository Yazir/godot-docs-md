#!/usr/bin/env bash
# List the latest version branch of each major line of godot-docs
# (e.g. "3.6" and "4.7"; "5.0" once it appears). One tracked branch per major.
set -euo pipefail

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

gh api "repos/godotengine/godot-docs/branches?per_page=100" --paginate --jq '.[].name' \
	| grep -E '^[0-9]+\.[0-9]+$' | awk -F. '$1 >= 3' | sort -u > "$tmp/branches" || true

if [ ! -s "$tmp/branches" ]; then
	exit 0
fi

awk -F. '{ if ($2 + 0 > max[$1] + 0) max[$1] = $2 } END { for (m in max) print m "." max[m] }' "$tmp/branches" \
	| sort -t. -k1,1n -k2,2n
