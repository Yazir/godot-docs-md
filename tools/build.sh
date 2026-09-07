#!/usr/bin/env bash
# Build the godot-docs-md package for one Godot version branch.
#
# Usage: build.sh <version> [--force]
#
# Single upstream source: godotengine/godot-docs (manual trees + the generated
# class reference RST committed to its classes/ directory).
#
# Steps:
#   1. Resolve the upstream SHA (tip of the version branch).
#   2. Skip if versions/<major>.x.md records the same branch+SHA (unless --force).
#   3. Sparse-checkout godot-docs at that exact SHA.
#   4. Run the converters, post-process and package the result.
#   5. Record versions/<major>.x.md.

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

DOCS_REPO="godotengine/godot-docs"

log() { echo "build: $*"; }

# ---------------------------------------------------------------- 1. SHA ----
command -v gh >/dev/null || { echo "error: gh CLI is required" >&2; exit 1; }

DOCS_SHA="$(gh api "repos/$DOCS_REPO/commits/$VERSION" --jq .sha)"
MAJOR_LINE="${VERSION%%.*}.x"
RECORD="$ROOT/versions/$MAJOR_LINE.md"

# ---------------------------------------------------------- 2. skip check ---
if [ "$FORCE" != "--force" ] && [ -f "$RECORD" ] \
	&& grep -qx "branch: $VERSION" "$RECORD" \
	&& grep -qx "godot_docs_sha: $DOCS_SHA" "$RECORD"; then
	log "skipped: $MAJOR_LINE.md already records branch $VERSION at godot-docs@$DOCS_SHA"
	exit 0
fi

log "building version $VERSION (godot-docs@$DOCS_SHA)"

# ------------------------------------------------------ 3. sparse checkout ---
rm -rf "$UPSTREAM"
mkdir -p "$UPSTREAM"

log "checking out $DOCS_REPO at $DOCS_SHA"
git init -q "$UPSTREAM/godot-docs"
git -C "$UPSTREAM/godot-docs" remote add origin "https://github.com/$DOCS_REPO.git"
git -C "$UPSTREAM/godot-docs" sparse-checkout init
git -C "$UPSTREAM/godot-docs" sparse-checkout set --no-cone \
	'/classes/**' '/getting_started/**' '/tutorials/**' '/engine_details/**'
git -C "$UPSTREAM/godot-docs" fetch --depth 1 --filter=blob:none origin "$DOCS_SHA"
git -C "$UPSTREAM/godot-docs" checkout --detach --quiet FETCH_HEAD

# ------------------------------------------------------------ 4. convert ---
rm -rf "$OUT"
mkdir -p "$OUT/classes" "$OUT/manual"

log "converting class reference..."
python3 "$ROOT/tools/convert_classref.py" \
	--version "$VERSION" \
	--output "$OUT/classes" \
	"$UPSTREAM/godot-docs/classes"

log "converting manual..."
python3 "$ROOT/tools/convert_manual.py" \
	--version "$VERSION" \
	--docs-root "$UPSTREAM/godot-docs" \
	--output "$OUT/manual" \
	getting_started tutorials engine_details

log "post-processing and packaging..."
TARBALL="$DIST/godot-$MAJOR_LINE-md.tar.gz"
python3 "$ROOT/tools/postprocess.py" \
	--input "$OUT" \
	--output "$DIST/package-$VERSION" \
	--tar "$TARBALL" \
	--version "$VERSION" \
	--docs-sha "$DOCS_SHA"

# ------------------------------------------------- 5. record build state ---
mkdir -p "$ROOT/versions"
cat > "$RECORD" <<EOF
major: $MAJOR_LINE
branch: $VERSION
godot_docs_sha: $DOCS_SHA
release: godot-$MAJOR_LINE-md-latest
EOF

log "done: $TARBALL"
