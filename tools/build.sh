#!/usr/bin/env bash
# Build the godot-docs-md package for one Godot version branch.
#
# Usage: build.sh <version> [--force]
#
# Steps:
#   1. Resolve upstream SHAs (godot: last commit touching doc/, godot-docs: tip).
#   2. Skip if versions/<v>.md already records the same SHAs (unless --force).
#   3. Sparse-checkout both upstream repos at those exact SHAs.
#   4. Run the converters, post-process and package the result.
#   5. Record versions/<v>.md.

set -euo pipefail

VERSION="${1:-4.7}"
FORCE="${2:-}"

if [ "$FORCE" != "" ] && [ "$FORCE" != "--force" ]; then
	echo "usage: build.sh <version> [--force]" >&2
	exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${GDM_WORK:-$ROOT/.work/$VERSION}"
DIST="${GDM_DIST:-$ROOT/dist}"
UPSTREAM="$WORK/upstream"
OUT="$WORK/out"

GODOT_REPO="godotengine/godot"
DOCS_REPO="godotengine/godot-docs"

log() { echo "build: $*"; }

# ---------------------------------------------------------------- 1. SHAs ---
command -v gh >/dev/null || { echo "error: gh CLI is required" >&2; exit 1; }

GODOT_SHA="$(gh api "repos/$GODOT_REPO/commits/$VERSION?path=doc" --jq .sha)"
DOCS_SHA="$(gh api "repos/$DOCS_REPO/commits/$VERSION" --jq .sha)"
SHA8="${GODOT_SHA:0:8}"
TAG="godot-$VERSION-md-$SHA8"

# ---------------------------------------------------------- 2. skip check ---
if [ "$FORCE" != "--force" ] && [ -f "$ROOT/versions/$VERSION.md" ]; then
	if grep -qx "godot_sha: $GODOT_SHA" "$ROOT/versions/$VERSION.md" \
		&& grep -qx "godot_docs_sha: $DOCS_SHA" "$ROOT/versions/$VERSION.md"; then
		log "skipped: $VERSION.md already records godot@$GODOT_SHA godot-docs@$DOCS_SHA"
		exit 0
	fi
fi

log "building version $VERSION (godot@$GODOT_SHA, godot-docs@$DOCS_SHA)"

# ------------------------------------------------------ 3. sparse checkout ---
rm -rf "$UPSTREAM"
mkdir -p "$UPSTREAM"

checkout() {
	local dir="$1" repo="$2" sha="$3"
	shift 3
	log "checking out $repo at $sha"
	git init -q "$dir"
	git -C "$dir" remote add origin "https://github.com/$repo.git"
	git -C "$dir" sparse-checkout init
	git -C "$dir" sparse-checkout set --no-cone "$@"
	git -C "$dir" fetch --depth 1 --filter=blob:none origin "$sha"
	git -C "$dir" checkout --detach --quiet FETCH_HEAD
}

checkout "$UPSTREAM/godot" "$GODOT_REPO" "$GODOT_SHA" \
	'/doc/**' '/modules/*/doc_classes/**' '/platform/*/doc_classes/**'

checkout "$UPSTREAM/godot-docs" "$DOCS_REPO" "$DOCS_SHA" \
	'/getting_started/**' '/tutorials/**' '/engine_details/**'

log "upstreams checked out at $SHA8 / ${DOCS_SHA:0:8}"

# ------------------------------------------------------------ 4. convert ---
rm -rf "$OUT"
mkdir -p "$OUT/classes" "$OUT/manual"

log "converting class reference..."
python3 "$ROOT/tools/convert_classref.py" \
	--version "$VERSION" \
	--output "$OUT/classes" \
	"$UPSTREAM/godot/doc/classes" \
	"$UPSTREAM/godot/modules" \
	"$UPSTREAM/godot/platform"

log "converting manual..."
python3 "$ROOT/tools/convert_manual.py" \
	--version "$VERSION" \
	--docs-root "$UPSTREAM/godot-docs" \
	--output "$OUT/manual" \
	getting_started tutorials engine_details

log "post-processing and packaging..."
TARBALL="$DIST/godot-$VERSION-md.tar.gz"
python3 "$ROOT/tools/postprocess.py" \
	--input "$OUT" \
	--output "$DIST/package-$VERSION" \
	--tar "$TARBALL" \
	--version "$VERSION" \
	--godot-sha "$GODOT_SHA" \
	--godot-docs-sha "$DOCS_SHA"

# ------------------------------------------------- 5. record build state ---
mkdir -p "$ROOT/versions"
cat > "$ROOT/versions/$VERSION.md" <<EOF
version: $VERSION
godot_sha: $GODOT_SHA
godot_docs_sha: $DOCS_SHA
release: $TAG
EOF

log "done: $TARBALL"
