# godot-docs-md

Automated pipeline that converts the official Godot documentation into
per-file Markdown, optimized for AI consumption (semantic search, RAG,
LLM context). Published as GitHub Releases — no expiring CI artifacts.

## Why this exists

The official Godot documentation lives in two upstream repos:

| Source | Repo | Content |
|---|---|---|
| Class reference source | [`godotengine/godot`](https://github.com/godotengine/godot) | `doc/classes/*.xml`, `modules/*/doc_classes/*.xml`, `platform/*/doc_classes/*.xml` |
| Manual | [`godotengine/godot-docs`](https://github.com/godotengine/godot-docs) | hand-written RST (`getting_started/`, `tutorials/`, `engine_details/`) |

The class reference shown on docs.godotengine.org is generated at build time
from the engine repo's XML. The godot-docs repo also carries that reference as
*generated* RST files committed to its `classes/` directory (produced by
upstream automation from the same XML), so a docs-only build would not be
empty — but we deliberately track the XML as well, for two reasons:

- **It is the source of truth.** The committed RST only follows the engine
  after upstream's sync bot runs; converting the XML means class-reference
  changes are picked up as soon as they land in the engine repo.
- **Cleaner conversion.** Converting structured XML directly yields semantic
  Markdown; converting the generated RST instead would mean stripping the
  RST artifacts make_rst.py emits (`.. rst-class::` directives,
  `|virtual|`-style abbreviation substitutions, `:ref:` anchors, grid
  tables) only to reconstruct the same content.

This is a follower repo, not a fork: no upstream content is modified, and
rebuilds are triggered automatically whenever upstream changes.

## How the SHA-poll works

The build tracks one or more version branches. Upstream **commit SHAs are the
change-detector** — release tags and branch names are never polled:

- `gh api repos/godotengine/godot/commits/4.7?path=doc --jq .sha` — last commit
  that touched the engine's `doc/` tree (the class reference source).
- `gh api repos/godotengine/godot-docs/commits/4.7 --jq .sha` — manual tip.

A weekly scheduled workflow (cron `0 4 * * 1`) does two things:

1. **Drift check**: resolves both SHAs for every recorded version and runs
   `tools/build.sh <version>`. If `versions/<version>.md` already records the
   same pair of SHAs, the build exits early ("skipped") and nothing is
   published — so patch drift of tracked branches (doc fixes, patch releases)
   triggers a rebuild automatically, and quiet branches cost one API call.
2. **Adopts new versions**: lists the version-named branches (e.g. `4.7`,
   `4.8`) present in **both** upstream repos and builds any that is not yet
   recorded. Only the highest major line is adopted (`2.1`/`3.x` are ignored);
   when a new major appears (e.g. `5.0`), adoption switches to that line
   automatically. Already-tracked versions keep their drift checks regardless.

## Triggering a build manually

Run the **build** workflow from the Actions tab (`workflow_dispatch`):

- **version** — leave empty to run the automatic flow above, or set a branch
  (e.g. `4.7`) to build exactly that version.
- **force** — rebuild even when the recorded SHAs are unchanged.

The same command works locally (needs `python3` 3.12+, `pandoc`, `git`, `gh`):

```sh
tools/build.sh 4.7          # rebuild if upstream changed
tools/build.sh 4.7 --force  # rebuild unconditionally
```

Outputs land in `dist/` and the build record in `versions/4.7.md`.

## Release tag scheme

| Tag | Meaning |
|---|---|
| `godot-4.7-md-<sha8>` | immutable release built from godot commit `<sha8>` |
| `godot-4.7-md-latest` | rolling tag, re-pointed to the newest `4.7` build |

Assets on every release: `godot-<version>-md.tar.gz` and `SHA256SUMS` (sha256
of the archive). Each successful build publishes the pinned release, re-creates
the rolling one, and commits `versions/<version>.md`
(`[skip ci] record build <tag>`) so subsequent runs can skip if nothing
changed upstream.

## Docs browser (GitHub Pages)

Every successful build also regenerates a lightweight static browser for the
produced Markdown and publishes it to GitHub Pages (one subdirectory per
version, e.g. `/4.7/`):

- sidebar legend with the full file tree (classes flat-list, manual nested by
  section), highlighting the current page;
- live title filtering plus full-text search with ranked results and snippets
  (prebuilt JSON index, fetched lazily, no external JS);
- all pages are pre-rendered HTML with working cross-links between pages.

Local inspection without Pages:

```sh
tools/build_site.py build --input dist/package-4.7/godot-4.7-md --output site/4.7
tools/build_site.py root  --output site
python3 -m http.server -d site   # then open http://localhost:8000/
```

## Package layout

```
godot-4.7-md/
├── .gdignore            # stops the Godot editor from scanning ~1,500 files
├── INDEX.md             # one line per file: path — summary line
├── VERSION              # engine version, upstream SHAs, release, build time
├── LICENSE-PACKAGE.md   # upstream license attribution
├── classes/             # one .md per engine class (Node2D.md, @GlobalScope.md, ...)
└── manual/              # getting_started/, tutorials/, engine_details/ mirrored
```

- Every file starts with YAML frontmatter (`title`, `engine`, `category`,
  `source`, plus `summary` where a document summary exists).
- Class pages keep the full reference: description, tutorials, properties,
  constructors, methods, operators, signals, enumerations, constants,
  annotations and theme properties — each member gets its own `###` heading
  with the signature in backticks. GDScript and C# code examples are kept
  verbatim. Deprecated/experimental notices are preserved.
- Manual pages keep all content, drop images/videos (alt text is kept as a
  line), admonitions become `> **Note:**` blockquotes, and sphinx `:ref:`
  targets are replaced by their link text.
- No navigation chrome, no relative `.html` links, no `img/` references.

## Consumer integration

Download the rolling latest release and unpack it into your Godot project —
the included `.gdignore` makes the editor ignore the folder:

```sh
curl -L -o godot-4.7-md.tar.gz \
  https://github.com/Yazir/godot-docs-md/releases/download/godot-4.7-md-latest/godot-4.7-md.tar.gz
tar -xzf godot-4.7-md.tar.gz -C my_project/
```

Verify the download:

```sh
sha256sum -c <(grep 'godot-4.7-md.tar.gz' SHA256SUMS)
```

Feed `INDEX.md` to your retrieval layer as the entry point, then load
individual `classes/*.md` / `manual/**/*.md` files on demand — every file is
self-contained and free of cross-file link requirements.

## One-time setup

Enable GitHub Pages once so the docs browser is served
(**Settings → Pages → Source: Deploy from a branch → `gh-pages` / root**).
The `gh-pages` branch is created and updated automatically by the workflow.

## Licenses

- The **tooling in this repo** (`tools/`, workflow) is MIT-licensed — see
  [LICENSE](LICENSE).
- The **generated documentation content** belongs to upstream Godot:
  the class reference XML is MIT-licensed and the manual is
  CC BY 3.0, both © Juan Linietsky, Ariel Manzur and the Godot community.
  Each package ships a `LICENSE-PACKAGE.md` with the full attribution.
