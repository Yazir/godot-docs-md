#!/usr/bin/env python3
"""Build a lightweight static Markdown browser for GitHub Pages.

Sub-commands:
  build  Render one version's package (classes/ + manual/) into a static site
         with a sidebar legend (file tree) and client-side full-text search.
  root   Generate the multi-version landing page for the site root.

Pages are pre-rendered with pandoc; search is a lazily-fetched JSON index.
Python stdlib only (pandoc is a subprocess).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import os
import re
import subprocess
import sys
from urllib.parse import unquote

MAX_SEARCH_CHARS = 80_000

HREF_MD_RE = re.compile(r'href="([^"]+\.md)"')


def parse_version_file(path: str) -> dict[str, str]:
	meta = {}
	if os.path.isfile(path):
		with open(path, "r", encoding="utf-8") as f:
			for line in f:
				if ":" in line:
					k, v = line.split(":", 1)
					meta[k.strip()] = v.strip()
	return meta


def parse_markdown(path: str) -> tuple[dict[str, str], str]:
	with open(path, "r", encoding="utf-8") as f:
		text = f.read()
	keys: dict[str, str] = {}
	if text.startswith("---\n"):
		end = text.find("\n---", 4)
		if end != -1:
			for line in text[4:end].split("\n"):
				if ":" in line:
					k, v = line.split(":", 1)
					keys[k.strip()] = v.strip()
			text = text[end + 4:]
	return keys, text


def md_to_text(text: str) -> str:
	"""Reduce Markdown to lowercased plain text for the search index."""
	text = re.sub(r"```[a-zA-Z0-9_+#.\-]*\n?", " ", text)
	text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
	text = re.sub(r"[*_#>`]", " ", text)
	text = re.sub(r"\s+", " ", text)
	return text.strip().lower()[:MAX_SEARCH_CHARS]


def pandoc_html(md_text: str) -> str:
	proc = subprocess.run(
		["pandoc", "-f", "gfm", "-t", "html", "--wrap=none"],
		input=md_text.encode("utf-8"),
		capture_output=True,
	)
	if proc.returncode != 0:
		sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
		raise RuntimeError("pandoc failed")
	return proc.stdout.decode("utf-8")


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Godot {version} docs (MD)</title>
<link rel="stylesheet" href="{root}assets/style.css">
</head>
<body>
<aside id="sidebar">
  <div id="side-head">
    <a id="home" href="{root}index.html">godot-docs-md</a>
    <div id="meta">Godot {version}<span id="meta-shas"></span></div>
    <input id="search" type="search" placeholder="Search documentation…" autocomplete="off" spellcheck="false">
    <div id="results" hidden></div>
  </div>
  <nav id="tree" aria-label="Table of contents"></nav>
</aside>
<main id="content">
{body}
</main>
<script>window.SITE_ROOT = "{root}";</script>
<script src="{root}assets/app.js" defer></script>
</body>
</html>
"""

ROOT_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>godot-docs-md — Godot documentation in Markdown</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 46rem; margin: 3rem auto; padding: 0 1rem; line-height: 1.6; }}
h1 {{ font-size: 1.4rem; }}
li {{ margin: 0.3rem 0; }}
code {{ background: #f2f2f2; padding: 0.1rem 0.3rem; border-radius: 3px; }}
.muted {{ color: #666; font-size: 0.9rem; }}
</style>
</head>
<body>
<h1>godot-docs-md</h1>
<p>Browsable Markdown builds of the official Godot documentation, generated
from the upstream repositories. Pick a version:</p>
<ul>
{items}
</ul>
<p class="muted">Packages are published as GitHub release assets; the sites
here are regenerated on every build.</p>
</body>
</html>
"""


def collect_docs(input_dir: str) -> list[tuple[str, str, dict[str, str], str]]:
	"""Return (rel_md, abs_path, frontmatter, body) sorted by path."""
	docs = []
	for base in ("classes", "manual"):
		root = os.path.join(input_dir, base)
		if not os.path.isdir(root):
			continue
		for dirpath, dirnames, filenames in os.walk(root):
			dirnames.sort()
			for name in sorted(filenames):
				if name.endswith(".md"):
					abs_path = os.path.join(dirpath, name)
					rel = os.path.relpath(abs_path, input_dir).replace(os.sep, "/")
					keys, body = parse_markdown(abs_path)
					docs.append((rel, abs_path, keys, body))
	docs.sort(key=lambda d: d[0])
	return docs


def make_link_fixer(page_rel: str, known_pages: set[str]):
	"""Rewrite relative .md links to the rendered .html pages."""
	base_dir = os.path.dirname(page_rel)

	def repl(m: re.Match) -> str:
		href = m.group(1)
		if href.startswith(("http://", "https://", "#", "mailto:")):
			return m.group(0)
		target = os.path.normpath(os.path.join(base_dir, unquote(href)))
		if target in known_pages:
			rel = os.path.relpath(target[:-3] + ".html", base_dir)
			return 'href="' + rel.replace(os.sep, "/") + '"'
		return 'href="#"'

	return lambda text: HREF_MD_RE.sub(repl, text)


def build_site(input_dir: str, output_dir: str) -> None:
	meta = parse_version_file(os.path.join(input_dir, "VERSION"))
	version = meta.get("engine", "4.7")
	docs_sha = meta.get("godot_docs_sha", "")

	docs = collect_docs(input_dir)
	known_pages = {d[0] for d in docs}
	print(f"build_site: rendering {len(docs)} pages for Godot {version}")

	def render(doc: tuple[str, str, dict[str, str], str]) -> tuple[str, str, str, dict[str, str]]:
		rel, _, keys, body = doc
		root = "../" * rel.count("/")
		page_html = pandoc_html(body)
		page_html = make_link_fixer(rel, known_pages)(page_html)
		page = PAGE_TEMPLATE.format(
			title=html.escape(keys.get("title", os.path.basename(rel))),
			version=html.escape(version),
			root=root,
			body=page_html,
		)
		search_text = md_to_text(body)
		entry = {
			"p": rel,
			"t": keys.get("title", os.path.basename(rel)),
			"c": keys.get("category", ""),
			"s": keys.get("summary", ""),
		}
		return rel, page, search_text, entry

	with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
		rendered = list(pool.map(render, docs))

	os.makedirs(os.path.join(output_dir, "pages"), exist_ok=True)
	index_entries = []
	search_entries = []
	for rel, page_html, search_text, entry in rendered:
		page_path = os.path.join(output_dir, "pages", rel[:-3] + ".html")
		os.makedirs(os.path.dirname(page_path), exist_ok=True)
		with open(page_path, "w", encoding="utf-8", newline="\n") as f:
			f.write(page_html)
		index_entries.append(entry)
		search_entries.append({"p": rel, "t": entry["t"], "x": (entry["t"] + ". " + search_text).lower()})

	index_entries.sort(key=lambda e: e["p"])
	search_entries.sort(key=lambda e: e["p"])

	with open(os.path.join(output_dir, "index.json"), "w", encoding="utf-8", newline="\n") as f:
		json.dump(
			{"version": version, "docs_sha": docs_sha, "docs": index_entries},
			f,
			ensure_ascii=False,
			separators=(",", ":"),
		)
	with open(os.path.join(output_dir, "search.json"), "w", encoding="utf-8", newline="\n") as f:
		json.dump(search_entries, f, ensure_ascii=True, separators=(",", ":"))
	with open(os.path.join(output_dir, ".nojekyll"), "wb"):
		pass

	# VERSION copy so the root landing page can enumerate versions
	version_src = os.path.join(input_dir, "VERSION")
	if os.path.isfile(version_src):
		with open(version_src, "rb") as fr, open(os.path.join(output_dir, "VERSION"), "wb") as fw:
			fw.write(fr.read())

	assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_assets")
	os.makedirs(os.path.join(output_dir, "assets"), exist_ok=True)
	for name in ("style.css", "app.js"):
		with open(os.path.join(assets, name), "r", encoding="utf-8") as fr, open(
			os.path.join(output_dir, "assets", name), "w", encoding="utf-8", newline="\n"
		) as fw:
			fw.write(fr.read())

	# landing page for this version
	welcome = [
		f"<h1>Godot {html.escape(version)} documentation</h1>",
		f"<p class=muted>Markdown build from godot-docs@<code>{html.escape(docs_sha[:8])}</code> "
		f"({meta.get('built_at', '')}). Use the sidebar to browse, or search.</p>",
	]
	root_prefix = ""
	page = PAGE_TEMPLATE.format(
		title=f"Godot {version} docs (MD)",
		version=version,
		root=root_prefix,
		body="\n".join(welcome),
	)
	with open(os.path.join(output_dir, "index.html"), "w", encoding="utf-8", newline="\n") as f:
		f.write(page)

	print(f"build_site: wrote site to {output_dir}")


def build_root_index(output_dir: str) -> None:
	items = []
	if os.path.isdir(output_dir):
		for name in sorted(os.listdir(output_dir), key=lambda n: [int(p) for p in n.split(".")] if re.fullmatch(r"\d+\.\d+", n) else [999]):
			version_file = os.path.join(output_dir, name, "VERSION")
			if not os.path.isfile(version_file):
				continue
			meta = parse_version_file(version_file)
			label = f"Godot {name}"
			sub = meta.get("built_at", "")
			items.append(f'<li><a href="{html.escape(name)}/index.html">{html.escape(label)}</a> '
						 f'<span class="muted">built {html.escape(meta.get("built_at", ""))} '
						 f'({html.escape(meta.get("release", ""))})</span></li>')
	page = ROOT_PAGE_TEMPLATE.format(items="\n".join(items) or "<li>(no versions yet)</li>")
	with open(os.path.join(output_dir, "index.html"), "w", encoding="utf-8", newline="\n") as f:
		f.write(page)
	print(f"build_site: root index written to {output_dir}/index.html")


def main() -> None:
	parser = argparse.ArgumentParser(description="Build the static Markdown browser.")
	sub = parser.add_subparsers(dest="command", required=True)

	p_build = sub.add_parser("build", help="build one version's site")
	p_build.add_argument("--input", "-i", required=True, help="package dir (godot-<v>-md)")
	p_build.add_argument("--output", "-o", required=True, help="output site dir")

	p_root = sub.add_parser("root", help="generate the multi-version landing page")
	p_root.add_argument("--output", "-o", required=True)

	args = parser.parse_args()
	if args.command == "build":
		build_site(args.input, args.output)
	else:
		build_root_index(args.output)


if __name__ == "__main__":
	main()
