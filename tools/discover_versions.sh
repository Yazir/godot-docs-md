#!/usr/bin/env bash
# List Godot version branches (e.g. "4.7") of the godot-docs repository.
# Only the highest major line is adopted (new majors take over automatically);
# already-recorded versions keep their drift checks regardless.
set -euo pipefail

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

gh api "repos/godotengine/godot-docs/branches?per_page=100" --paginate --jq '.[].name' \
	| grep -E '^[0-9]+\.[0-9]+$' | sort -u > "$tmp/branches" || true

if [ ! -s "$tmp/branches" ]; then
	exit 0
fi

awk -F. 'NR==FNR{if ($1 > m) m = $1; next} $1 == m' "$tmp/branches" "$tmp/branches" \
	| sort -t. -k1,1n -k2,2n | uniq
