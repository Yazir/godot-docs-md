# godot-docs-md

Automated pipeline that converts the official Godot documentation into
per-file Markdown, optimized for AI consumption (semantic search, RAG,
LLM context). Published as GitHub Releases — no expiring CI artifacts.

## Why this exists

The pipeline has a **single upstream source**:
[`godotengine/godot-docs`](https://github.com/godotengine/godot-docs), which
carries both halves of the official documentation on every version branch:

| Content | Location in godot-docs |
|---|---|
| Class reference | `classes/class_*.rst` — generated upstream by `make_rst.py` from the engine's XML and committed by automation |
| Manual | hand-written RST: `getting_started/`, `tutorials/`, `engine_details/` |

Tracking godot-docs alone keeps the two halves consistent (one commit, one
SHA) and stays a follower repo, not a fork: no upstream content is modified,
and rebuilds are triggered automatically whenever upstream changes.

The converters never touch the built HTML site: the class reference is parsed
directly from the generated RST (its structure is fully regular), and the
manual goes through `pandoc` plus a cleanup pass. Everything is optimized for
AI retrieval: dense headings, one self-contained file per class or manual
page, zero navigation chrome.

## How the SHA-poll works

Upstream **commit SHAs are the change-detector** — release tags and branch
names are never polled. The pipeline tracks **one branch per major line** —
the newest one (today: `3.6` and `4.7`; `5.0` once it appears):

- `gh api repos/godotengine/godot-docs/commits/4.7 --jq .sha` — tip of the
  version branch. Any commit to the branch (manual edits **or** the automated
  class-reference sync) changes the SHA.

A weekly scheduled workflow (cron `0 4 * * 1`) resolves the SHA of each
tracked branch and runs `tools/build.sh <branch>`:

- if `versions/<major>.x.md` records the same branch and SHA, the build exits
  early ("skipped") and nothing is published;
- when a new minor branch appears (e.g. `4.8`), the `4.x` line automatically
  moves to it and rebuilds; new major lines (`5.x`) are adopted the same way.

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

Outputs land in `dist/` and the build record in `versions/4.x.md`.

## Release scheme

One rolling release per major line — no pinned-SHA releases:

| Tag | Contents |
|---|---|
| `godot-3.x-md-latest` | latest `3.x` build (assets: `godot-3.x-md.tar.gz`, `SHA256SUMS`) |
| `godot-4.x-md-latest` | latest `4.x` build (assets: `godot-4.x-md.tar.gz`, `SHA256SUMS`) |
| `godot-5.x-md-latest` | appears automatically when Godot 5 branches |

The tag names are stable: when the tracked branch of a line moves (e.g.
`4.7` → `4.8`), the same release is deleted and re-created with the new
package — consumer URLs never change. The exact upstream commit of every
build is recorded inside the archive (`VERSION`) and in `versions/<major>.x.md`
(committed with `[skip ci] record build <tag>`).

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
tools/build_site.py build --input dist/package-4.7/godot-4.x-md --output site/4.x
tools/build_site.py root  --output site
python3 -m http.server -d site   # then open http://localhost:8000/
```

## Package layout

```
godot-4.x-md/
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
curl -L -o godot-4.x-md.tar.gz \
  https://github.com/Yazir/godot-docs-md/releases/download/godot-4.x-md-latest/godot-4.x-md.tar.gz
tar -xzf godot-4.x-md.tar.gz -C my_project/
```

Verify the download:

```sh
sha256sum -c <(grep 'godot-4.x-md.tar.gz' SHA256SUMS)
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
