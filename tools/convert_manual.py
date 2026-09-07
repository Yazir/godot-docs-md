#!/usr/bin/env python3
"""Convert the curated godot-docs RST trees to Markdown mirroring the source
directory structure.

Approach: a pre-pass rewrites Sphinx constructs that pandoc mishandles
(admonitions, tabs/code-tabs, images/figures/videos, toctrees, substitutions),
then each file is converted with `pandoc -f rst -t gfm`, followed by a
fence-aware cleanup pass. Python stdlib only (pandoc is a subprocess).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

CURATED_TREES = ("getting_started", "tutorials", "engine_details")

DIRECTIVE_RE = re.compile(r"^(\s*)\.\.\s+([a-zA-Z0-9_\-|]+)::\s*(.*)$")

ADMONITION_LABELS = {
	"note": "Note",
	"warning": "Warning",
	"seealso": "See also",
	"tip": "Tip",
	"important": "Important",
	"attention": "Attention",
	"danger": "Danger",
	"caution": "Caution",
}

DROP_BLOCKS = {"toctree", "index", "highlight", "rst-class", "meta", "contents", "raw", "include"}
DROP_IMAGES = {"image", "figure"}

# :role:`Text <target>` -> Text (targets are useless without a Sphinx site map)
ROLE_TARGET_RE = re.compile(r":([a-zA-Z]+):`([^`]*?)\s*<[^>`]+>`")
# top-level docinfo fields like ":orphan:" / ":allow_comments: False"
FIELD_LINE_RE = re.compile(r"^:[a-zA-Z][a-zA-Z0-9_\-]*:(\s|$)")


def indent_of(line: str) -> int:
	return len(line) - len(line.lstrip(" "))


def render_inline(rst: str) -> str:
	"""Reduce an RST inline substitution value to plain text."""
	rst = rst.strip()
	abbr = re.match(r":abbr:`([^`]*)`", rst)
	if abbr:
		inner = abbr.group(1)
		return re.sub(r"\s*\([^)]*\)\s*$", "", inner).strip()
	link = re.match(r"`([^`<]+?)\s*<[^>]*>`_{1,2}", rst)
	if link:
		return link.group(1).strip()
	return rst


def block_end(lines: list[str], start: int, directive_indent: int) -> int:
	"""Return the index after the last line belonging to the directive block."""
	j = start + 1
	while j < len(lines):
		line = lines[j]
		if line.strip() == "":
			j += 1
			continue
		if indent_of(line) <= directive_indent:
			break
		j += 1
	while j > start + 1 and lines[j - 1].strip() == "":
		j -= 1
	return j


def deindent(block: list[str]) -> list[str]:
	indents = [indent_of(line) for line in block if line.strip()]
	if not indents:
		return list(block)
	cut = min(indents)
	return [line[cut:] if len(line) >= cut else line.lstrip() for line in block]


def ensure_blank(out: list[str]) -> None:
	if out and out[-1].strip() != "":
		out.append("")


def pre_pass(lines: list[str]) -> list[str]:
	# Collect substitution definitions (|name| -> plain text or alt text).
	subs: dict[str, str] = {}
	for i, line in enumerate(lines):
		m = DIRECTIVE_RE.match(line)
		if not m:
			continue
		name = m.group(2)
		if not (name.startswith("|") and name.endswith("|")):
			continue
		key = name[1:-1].strip()
		arg = m.group(3)
		parts = arg.split(None, 1)
		kind = parts[0] if parts else "replace"
		rest = parts[1] if len(parts) > 1 else ""
		if kind == "replace":
			subs[key] = render_inline(rest)
		elif kind == "image":
			alt = ""
			for opt in lines[i + 1:]:
				if opt.strip() == "" or not opt.startswith(" "):
					break
				om = re.match(r"\s+:alt:\s*(.*)", opt)
				if om:
					alt = om.group(1).strip()
			subs[key] = alt

	def substitute(line: str) -> str:
		if "|" not in line and ":`" not in line:
			return line
		for key, value in subs.items():
			token = "|" + key + "|"
			if token in line:
				line = line.replace(token, value)
		# keep link text, drop role target: :ref:`Text <doc_target>` -> Text
		return ROLE_TARGET_RE.sub(r"\1", line)

	out: list[str] = []
	i = 0
	code_indent: int | None = None  # inside a raw RST code-block: pass through verbatim
	while i < len(lines):
		line = lines[i]

		if code_indent is not None:
			if line.strip() != "" and indent_of(line) <= code_indent:
				code_indent = None
			else:
				out.append(line)
				i += 1
				continue

		m = DIRECTIVE_RE.match(line)
		if not m:
			if FIELD_LINE_RE.match(line):
				i += 1
				continue
			out.append(substitute(line))
			i += 1
			continue

		directive_indent = indent_of(line)
		name = m.group(2)
		name_l = name.lower()
		arg = m.group(3)
		end = block_end(lines, i, directive_indent)
		block = lines[i + 1:end]

		if name_l in DROP_IMAGES:
			alt = ""
			for opt in block:
				om = re.match(r"\s+:alt:\s*(.*)", opt)
				if om:
					alt = om.group(1).strip()
			if alt:
				ensure_blank(out)
				out.append("*Image: " + alt + "*")
				out.append("")
			# keep the figure caption (content after options) as a paragraph
			caption: list[str] = []
			seen_options = False
			for opt in block:
				if opt.strip() == "":
					seen_options = True
					continue
				if not seen_options and opt.strip().startswith(":"):
					continue
				caption.append(opt)
			while caption and caption[0].strip() == "":
				caption.pop(0)
			for cap_line in deindent(caption):
				out.append(substitute(cap_line))
			if caption:
				out.append("")
			i = end
			continue

		if name_l in DROP_BLOCKS:
			i = end
			continue

		if name_l == "video":
			alt = ""
			for opt in block:
				om = re.match(r"\s+:alt:\s*(.*)", opt)
				if om:
					alt = om.group(1).strip()
			label = alt if alt else (arg.strip() if arg.strip().startswith("http") else "")
			if label:
				ensure_blank(out)
				out.append("*Video: " + label + "*")
				out.append("")
			i = end
			continue

		if name_l == "tabs":
			ensure_blank(out)
			out.append("")
			i += 1
			continue

		if name_l == "code-tab":
			lang = arg.split()[0] if arg.split() else "text"
			content = deindent(block)
			while content and content[-1].strip() == "":
				content.pop()
			ensure_blank(out)
			out.append(".. code-block:: " + lang)
			out.append("")
			out.extend("    " + cl if cl.strip() else "" for cl in content)
			out.append("")
			i = end
			continue

		if name_l == "tab":
			content = deindent(block)
			ensure_blank(out)
			if arg.strip():
				out.append("**" + arg.strip() + "**")
				out.append("")
			out.extend(substitute(cl) for cl in content)
			out.append("")
			i = end
			continue

		if name_l in ADMONITION_LABELS:
			# pass through: pandoc parses admonitions natively, the post pass
			# converts the emitted <div class="..."> into a labeled blockquote
			out.append(line)
			i += 1
			continue

		if name_l == "versionadded":
			out.append("**Added in " + arg.strip() + ".**")
			out.append("")
			i = end
			continue

		if name_l == "versionchanged":
			out.append("**Changed in " + arg.strip() + ".**")
			out.append("")
			i = end
			continue

		if name_l == "deprecated":
			out.append("**Deprecated in " + arg.strip() + ".**")
			out.append("")
			i = end
			continue

		if name_l in ("code-block", "code"):
			code_indent = directive_indent
			out.append(line)
			i += 1
			continue

		out.append(line)
		i += 1

	return out


ROLE_RE = re.compile(r":([a-zA-Z_]+):`([^`]*)`")
CODE_SPAN_REF_RE = re.compile(r"`([^`<>]+?)\s+<([a-zA-Z0-9_./:\-#]+)>`")
REL_HTML_LINK_RE = re.compile(r"\[([^\]]+)\]\(((?!https?://)[^)\s]+\.html[^)]*)\)")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]*)\)")
DIRECTIVE_LINE_RE = re.compile(r"^\s*\.\.\s+[a-zA-Z0-9_\-]+::.*$")
DIV_OPEN_RE = re.compile(r"^\s*<div\b[^>]*>\s*$")
DIV_CLOSE_RE = re.compile(r"^\s*</div>\s*$")
ADMONITION_DIV_RE = re.compile(r'^\s*<div class="([a-zA-Z0-9\-_]+)">\s*$')
SPAN_RE = re.compile(r"<span\b[^>]*>(.*?)</span>")
IMG_TAG_RE = re.compile(r"<img\b[^>]*/?>")
MEDIA_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+\.(?:png|jpe?g|webp|gif|webm|svg|avif|mp4))\)")


def map_outside_fences(lines: list[str], fn) -> list[str]:
	out: list[str] = []
	in_fence = False
	for line in lines:
		if line.startswith("```"):
			if line.startswith("``` ") and not in_fence:
				line = "```" + line[4:]
			in_fence = not in_fence
			out.append(line)
			continue
		out.append(line if in_fence else fn(line))
	return out


def admonition_div_pass(lines: list[str]) -> list[str]:
	"""Convert pandoc's admonition <div> wrappers into labeled blockquotes."""
	out: list[str] = []
	i = 0
	n = len(lines)
	while i < n:
		line = lines[i]
		m = ADMONITION_DIV_RE.match(line)
		if not m or m.group(1) not in ADMONITION_LABELS:
			out.append(line)
			i += 1
			continue
		cls = m.group(1)
		j = i + 1
		label = None
		while j < n and lines[j].strip() == "":
			j += 1
		if j < n and re.match(r'^\s*<div class="title">\s*$', lines[j]):
			title_lines: list[str] = []
			j += 1
			while j < n and not DIV_CLOSE_RE.match(lines[j]):
				title_lines.append(lines[j])
				j += 1
			j += 1  # skip </div>
			label = " ".join(s.strip() for s in title_lines if s.strip()) or None
		content: list[str] = []
		depth = 1
		while j < n:
			if DIV_OPEN_RE.match(lines[j]):
				depth += 1
			elif DIV_CLOSE_RE.match(lines[j]):
				depth -= 1
				if depth == 0:
					break
			content.append(lines[j])
			j += 1
		while content and content[0].strip() == "":
			content.pop(0)
		while content and content[-1].strip() == "":
			content.pop()
		if not label:
			label = ADMONITION_LABELS.get(cls, cls.capitalize())
		first = "> **" + label + ":**"
		merge = bool(content) and not content[0].startswith(
			(">", "-", "*", "```", "~~~", "|", "#", "!", "<", "1.", " ")
		)
		if merge:
			out.append(first + " " + content[0].strip())
			content = content[1:]
		else:
			out.append(first)
		for cl in content:
			out.append("> " + cl if cl.strip() else ">")
		out.append("")
		i = j + 1
	return out


def post_pass(text: str) -> str:
	lines = text.split("\n")
	lines = admonition_div_pass(lines)

	def clean_line(line: str) -> str:
		if DIV_OPEN_RE.match(line) or DIV_CLOSE_RE.match(line):
			return ""
		if DIRECTIVE_LINE_RE.match(line):
			return ""
		line = ROLE_RE.sub(lambda m: "`" + m.group(2).strip() + "`", line)
		line = CODE_SPAN_REF_RE.sub(lambda m: m.group(1).strip(), line)

		def sub_html_link(m: re.Match) -> str:
			return m.group(1)

		def sub_image(m: re.Match) -> str:
			alt = m.group(1).strip()
			return ("_Image: " + alt + "_") if alt else ""

		def sub_media_link(m: re.Match) -> str:
			return m.group(1)

		line = SPAN_RE.sub(lambda m: m.group(1), line)
		line = IMG_TAG_RE.sub("", line)
		if MEDIA_LINK_RE.search(line):
			only_media = MEDIA_LINK_RE.sub(sub_media_link, line).strip() == ""
			if only_media:
				return ""
			line = MEDIA_LINK_RE.sub(sub_media_link, line)
		line = REL_HTML_LINK_RE.sub(sub_html_link, line)
		line = IMAGE_RE.sub(sub_image, line)
		if line.startswith("``` "):
			line = "```" + line[4:]
		return line

	lines = map_outside_fences(lines, clean_line)

	final: list[str] = []
	in_fence = False
	blank = 0
	for line in lines:
		if line.startswith("```"):
			in_fence = not in_fence
			blank = 0
			final.append(line)
			continue
		if in_fence:
			final.append(line)
			continue
		if line.strip() == "":
			blank += 1
			if blank <= 1:
				final.append(line)
		else:
			blank = 0
			final.append(line)

	return "\n".join(final)


def first_sentence(paragraph: str) -> str | None:
	text = " ".join(paragraph.split())
	if not text:
		return None
	m = re.match(r"^(.{2,}?[.!?])(\s|$)", text)
	if m:
		return m.group(1)
	return text if len(text) <= 400 else text[:400].rstrip() + "…"


def extract_summary(body_lines: list[str]) -> str | None:
	for line in body_lines:
		s = line.strip()
		if s == "":
			continue
		if s.startswith("#"):
			continue
		if s.startswith(("```", "~~~", "|", "<", "> ", "- ", "* ", "![")):
			continue
		return s
	return None


def convert_file(rst_path: str, md_path: str, rel: str, version: str) -> None:
	with open(rst_path, "r", encoding="utf-8") as f:
		text = f.read()

	# multi-line roles first, then line-based pre-pass
	text = ROLE_TARGET_RE.sub(lambda m: m.group(1), text)
	lines = pre_pass(text.split("\n"))
	rst_text = "\n".join(lines)

	proc = subprocess.run(
		["pandoc", "-f", "rst", "-t", "gfm", "--wrap=none"],
		input=rst_text.encode("utf-8"),
		capture_output=True,
	)
	if proc.returncode != 0:
		sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
		raise RuntimeError(f"pandoc failed for {rst_path}")

	body = post_pass(proc.stdout.decode("utf-8"))
	body_lines = [ln.rstrip() for ln in body.split("\n")]
	while body_lines and body_lines[0].strip() == "":
		body_lines.pop(0)
	while body_lines and body_lines[-1].strip() == "":
		body_lines.pop()

	title = None
	for line in body_lines:
		if line.startswith("# "):
			title = line[2:].strip()
			break

	out: list[str] = []
	out.append("---")
	out.append("title: " + (title if title else os.path.splitext(os.path.basename(rst_path))[0]))
	out.append("engine: " + version)
	category_dir = os.path.dirname(rel)
	out.append("category: manual/" + category_dir if category_dir else "category: manual")
	out.append("source: " + rel)
	sentence = first_sentence(extract_summary(body_lines) or "")
	if sentence:
		out.append("summary: " + sentence)
	out.append("---")
	out.append("")
	out.extend(body_lines)

	while out and out[-1].strip() == "":
		out.pop()

	os.makedirs(os.path.dirname(md_path), exist_ok=True)
	with open(md_path, "w", encoding="utf-8", newline="\n") as f:
		f.write("\n".join(out) + "\n")


def main() -> None:
	parser = argparse.ArgumentParser(description="Convert godot-docs RST trees to Markdown.")
	parser.add_argument("--docs-root", required=True, help="Path to the godot-docs checkout.")
	parser.add_argument("--output", "-o", required=True, help="Output directory (mirrors tree structure).")
	parser.add_argument("--version", "-v", default="4.7", help="Engine version recorded in frontmatter.")
	parser.add_argument("trees", nargs="*", default=list(CURATED_TREES), help="Top-level RST trees to convert.")
	args = parser.parse_args()

	count = 0
	for tree in args.trees:
		root = os.path.join(args.docs_root, tree)
		if not os.path.isdir(root):
			print(f"convert_manual: missing tree {tree}, skipping", file=sys.stderr)
			continue
		for dirpath, dirnames, filenames in os.walk(root):
			dirnames.sort()
			for filename in sorted(filenames):
				if not filename.endswith(".rst"):
					continue
				rst_path = os.path.join(dirpath, filename)
				rel = os.path.relpath(rst_path, args.docs_root).replace(os.sep, "/")
				md_path = os.path.join(args.output, os.path.splitext(rel)[0] + ".md")
				convert_file(rst_path, md_path, rel, args.version)
				count += 1
	print(f"convert_manual: wrote {count} files to {args.output}")


if __name__ == "__main__":
	main()
