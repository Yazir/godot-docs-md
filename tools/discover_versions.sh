#!/usr/bin/env bash
# List Godot version branches (e.g. "4.7") that exist in BOTH upstream repos.
# Only the highest major line is adopted (new majors take over automatically);
# already-recorded versions keep their drift checks regardless.
set -euo pipefail

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

gh api "repos/godotengine/godot/branches?per_page=100" --paginate --jq '.[].name' \
	| grep -E '^[0-9]+\.[0-9]+$' | sort -u > "$tmp/godot" || true
gh api "repos/godotengine/godot-docs/branches?per_page=100" --paginate --jq '.[].name' \
	| grep -E '^[0-9]+\.[0-9]+$' | sort -u > "$tmp/docs" || true

comm -12 "$tmp/godot" "$tmp/docs" \
	| awk -F. 'NR==FNR{if ($1 > m) m = $1; next} $1 == m' "$tmp/godot" - \
	| sort -t. -k1,1n -k2,2n
