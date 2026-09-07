#!/usr/bin/env python3
"""Convert the Godot class reference from godot-docs' generated RST
(classes/class_*.rst, produced upstream by make_rst.py) into one
self-contained Markdown file per class.

The generated RST is highly regular, so this parser is line-based over the
known make_rst.py emit patterns:

- `.. rst-class:: classref-*` section/item markers and `----` separators
- reftable summary tables (dropped; the detailed sections carry all content)
- `|abbr|` substitution definitions from the file footer
- `:ref:`text <target>`` / `` `text <url>`__ `` / `:doc:` inline markup
- `::` literal blocks and `.. tabs::` / `.. code-tab::` code examples

Python stdlib only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.parse

UNDERLINE_RE = re.compile(r"^([=\-])\1*\s*$")
SUBST_RE = re.compile(r"^\.\. \|([^|]+)\| replace::\s*(.*)$")
REF_RE = re.compile(r":ref:`([^`]*)`")
NAMED_LINK_RE = re.compile(r"`([^`<>]+?)\s*<(https?://[^>`]+)>`_{1,2}")
CODE_RE = re.compile(r"``([^`]+)``|(?<!`)`([^`]+)`(?!`)")
BOLD_NAME_RE = re.compile(r"^(.*?)\*\*(.+?)\*\*(.*)$", re.S)
TRAILING_ANCHOR_RE = re.compile(r"\s*:ref:`🔗<[^>]*>`\s*$")
MEDIA_EXT_RE = re.compile(r"\.(png|jpe?g|webp|gif|webm|svg|avif|mp4)$", re.IGNORECASE)

SECTION_MAP = {
	"Description": "Description",
	"Tutorials": "Tutorials",
	"Property Descriptions": "Properties",
	"Constructor Descriptions": "Constructors",
	"Method Descriptions": "Methods",
	"Operator Descriptions": "Operators",
	"Theme Property Descriptions": "Theme Properties",
	"Signals": "Signals",
	"Enumerations": "Enumerations",
	"Constants": "Constants",
	"Annotations": "Annotations",
}

SECTION_ORDER = [
	("properties", "Properties"),
	("constructors", "Constructors"),
	("methods", "Methods"),
	("operators", "Operators"),
	("theme", "Theme Properties"),
	("signals", "Signals"),
	("enums", "Enumerations"),
	("constants", "Constants"),
	("annotations", "Annotations"),
]

ITEM_KINDS = {
	"classref-property": "property",
	"classref-constructor": "constructor",
	"classref-method": "method",
	"classref-operator": "operator",
	"classref-themeproperty": "themeproperty",
	"classref-signal": "signal",
	"classref-annotation": "annotation",
	"classref-constant": "constant",
	"classref-enumeration": "enumeration",
	"classref-enumeration-constant": "enumeration-constant",
}

RET_KINDS = {"property", "constructor", "method", "operator", "themeproperty", "enumeration-constant"}
PAREN_KINDS = {"method", "constructor", "operator", "annotation", "signal"}


def warn(msg: str) -> None:
	print(f"WARNING: {msg}", file=sys.stderr)


def class_link(name: str) -> str:
	target = urllib.parse.quote(name, safe="")
	return f"[{name}]({target}.md)"


def backtick_span(content: str) -> str:
	run = 0
	longest = 0
	for ch in content:
		if ch == "`":
			run += 1
			longest = max(longest, run)
		else:
			run = 0
	delim = "`" * (longest + 1)
	pad = " " if content != "" and (content.startswith("`") or content.endswith("`")) else ""
	return delim + pad + content + pad + delim


def render_subst(rst: str) -> str:
	m = re.match(r":abbr:`([^`]*)`", rst.strip())
	if m:
		return re.sub(r"\s*\([^)]*\)\s*$", "", m.group(1)).strip()
	return rst.strip()


def indent_of(line: str) -> int:
	return len(line) - len(line.lstrip(" \t"))


def collect_block(lines: list[str], i: int) -> tuple[list[str], int]:
	"""Collect the indented block following the directive at line i."""
	base = indent_of(lines[i])
	j = i + 1
	block: list[str] = []
	while j < len(lines):
		line = lines[j]
		if line.strip() == "":
			block.append("")
			j += 1
			continue
		if indent_of(line) <= base:
			break
		block.append(line)
		j += 1
	while block and block[-1].strip() == "":
		block.pop()
	return block, j


def deindent(block: list[str]) -> list[str]:
	indents = [indent_of(l) for l in block if l.strip()]
	if not indents:
		return list(block)
	cut = min(indents)
	return [l[cut:] if len(l) >= cut else l.lstrip() for l in block]


class ClassDoc:
	def __init__(self, name: str, version: str, source_rel: str):
		self.name = name
		self.version = version
		self.source_rel = source_rel
		self.subst: dict[str, str] = {}
		self.brief: list[str] = []
		self.inherits: list[str] = []
		self.inherited_by: list[str] = []
		self.description: list[str] = []
		self.tutorials: list[str] = []
		self.properties: list[str] = []
		self.constructors: list[str] = []
		self.methods: list[str] = []
		self.operators: list[str] = []
		self.theme: list[str] = []
		self.signals: list[str] = []
		self.enums: list[str] = []
		self.constants: list[str] = []
		self.annotations: list[str] = []
		self.num_warnings = 0

	# ------------------------------------------------------------ inline

	def _ref(self, text: str, target: str, plain: bool) -> str:
		target = target.strip()
		if text.strip() == "🔗":
			return ""
		if re.fullmatch(r"class_[^_]+", target):
			name = target[len("class_"):]
			if plain:
				return text
			if name == self.name:
				return f"**{text}**"
			return class_link(name)
		if target.startswith("enum_"):
			cls, _, _enum = target[len("enum_"):].partition("_")
			display = text
			if cls != self.name and "." not in text:
				display = f"{cls}.{text}"
			return backtick_span(display)
		if target.startswith("doc_"):
			return backtick_span(target[len("doc_"):])
		return backtick_span(text)

	def inline(self, text: str, plain: bool = False) -> str:
		def sub_subst(m: re.Match) -> str:
			return self.subst.get(m.group(1), m.group(0))

		text = re.sub(r"\|([a-zA-Z_]+)\|", sub_subst, text)

		def sub_link(m: re.Match) -> str:
			label, url = m.group(1), m.group(2)
			if MEDIA_EXT_RE.search(url.split("#")[0].split("?")[0]):
				return label
			return f"[{label}]({url})"

		text = NAMED_LINK_RE.sub(sub_link, text)

		def sub_doc(m: re.Match) -> str:
			rel = m.group(2)
			while rel.startswith("../"):
				rel = rel[3:]
			label = m.group(1).strip()
			if plain:
				return label
			return f"[{label}](../manual/{rel}.md)"

		text = re.sub(r":doc:`([^`]*?)\s+<([^>`]+)>`", sub_doc, text)

		def sub_ref(m: re.Match) -> str:
			inner = m.group(1)
			tm = re.match(r"^(.*?)\s*<([^>`]+)>$", inner)
			if tm:
				return self._ref(tm.group(1).strip(), tm.group(2).strip(), plain)
			return self._ref(inner.strip(), inner.strip(), plain)

		text = REF_RE.sub(sub_ref, text)

		spans: list[str] = []

		def stash_code(m: re.Match) -> str:
			content = m.group(1) if m.group(1) is not None else m.group(2)
			spans.append(content)
			return f"\x00{len(spans) - 1}\x00"

		text = CODE_RE.sub(stash_code, text)

		text = text.replace("\\ ", " ")
		for ch in ":,()":
			text = text.replace("\\" + ch, ch)

		def unstash(m: re.Match) -> str:
			return backtick_span(spans[int(m.group(1))])

		text = re.sub("\x00([0-9]+)\x00", unstash, text)
		return text

	# -------------------------------------------------------- signatures

	def parse_args(self, args_raw: str) -> str:
		if args_raw.strip() == "":
			return ""
		vararg = False
		mtail = re.search(r",?\s*\.\.\.\s*$", args_raw)
		if mtail:
			vararg = True
			args_raw = args_raw[: mtail.start()]

		parts: list[str] = []
		depth = 0
		cur = ""
		i = 0
		while i < len(args_raw):
			tok = args_raw[i:i + 2]
			if tok == "\\," and depth == 0:
				parts.append(cur)
				cur = ""
				i += 2
				continue
			if tok == "\\[":
				depth += 1
			elif tok == "\\]":
				depth = max(0, depth - 1)
			cur += args_raw[i]
			i += 1
		parts.append(cur)

		out = []
		for part in parts:
			part = part.strip()
			if part == "":
				continue
			m = re.match(r"^(.*?)\\:\s*(.*)$", part)
			if not m:
				out.append(self.inline(part, plain=True).replace("\\", "").strip())
				continue
			pname = self.inline(m.group(1), plain=True).strip()
			rest = m.group(2).strip()
			default = None
			md = re.search(r"\s*=\s*(.+)$", rest)
			if md:
				default = self.inline(md.group(1), plain=True).strip().strip("`")
				rest = rest[: md.start()].strip()
			ptype = self.inline(rest, plain=True).strip()
			sig = f"{pname}: {ptype}"
			if default is not None:
				sig += f" = {default}"
			out.append(sig)
		if vararg:
			out.append("...")
		return ", ".join(out)

	def parse_sig(self, raw: str, kind: str) -> dict | None:
		line = TRAILING_ANCHOR_RE.sub("", raw.strip()).strip()
		m = BOLD_NAME_RE.match(line)
		if not m:
			self.num_warnings += 1
			warn(f"{self.name}: unparsable {kind} signature: {line[:120]}")
			return None
		ret_raw, name, rest = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()

		item: dict = {
			"kind": kind,
			"name": name,
			"ret": "",
			"args": "",
			"quals": [],
			"default": None,
			"setter": None,
			"getter": None,
			"desc": [],
			"prefix": ("flags" if (kind == "enumeration" and ret_raw == "flags") else "enum") if kind == "enumeration" else None,
		}

		if kind == "enumeration":
			return item

		if kind in PAREN_KINDS:
			rest_n = rest.replace("\\ ", " ").strip()
			m2 = re.match(r"\((.*)\)\s*(.*)$", rest_n)
			if m2:
				args_raw, qual_raw = m2.group(1), m2.group(2)
			else:
				args_raw, qual_raw = rest_n, ""
			item["ret"] = self.inline(ret_raw, plain=True).strip()
			item["args"] = self.parse_args(args_raw)
			for q in re.findall(r"\|([a-zA-Z_]+)\|", qual_raw):
				if q not in ("vararg", "void", "bitfield") and q not in item["quals"]:
					item["quals"].append(q)
			return item

		if kind in RET_KINDS:
			item["ret"] = self.inline(ret_raw, plain=True).strip()
			if kind == "property" and item["ret"] == "":
				item["ret"] = "Variant"
		md = re.search(r"=\s*``([^`]*)``", rest)
		if md:
			item["default"] = md.group(1)
		return item

	def parse_setget(self, raw: str) -> tuple[str, str] | None:
		line = raw.strip()
		if line.startswith("- "):
			line = line[2:].strip()
		line_n = line.replace("\\ ", " ").strip()
		m = re.match(r"^(.*?)\*\*(.+?)\*\*\s*\((.*)\)\s*$", line_n)
		if not m:
			return None
		name = m.group(2).strip()
		args = self.parse_args(m.group(3))
		key = "setter" if name.startswith("set_") else "getter"
		return (key, f"{name}({args})")

	def member_sig_md(self, item: dict) -> str:
		out = item["name"] + "(" + item["args"] + ")"
		if item["quals"]:
			out += " " + " ".join(item["quals"])
		return out

	def member_heading(self, item: dict) -> str:
		if item["kind"] in ("property", "themeproperty"):
			sig = (item["ret"] + " " if item["ret"] else "") + item["name"]
			if item["default"] is not None:
				sig += f" = {item['default']}"
			return f"### `{sig}`"
		if item["kind"] == "constant":
			sig = item["name"]
			if item["default"] is not None:
				sig += f" = {item['default']}"
			return f"### `{sig}`"
		if item["kind"] in ("signal", "annotation"):
			return f"### `{self.member_sig_md(item)}`"
		ret = (item["ret"] + " ") if item["ret"] else ""
		return f"### `{ret}{self.member_sig_md(item)}`"

	# ------------------------------------------------------------ output

	def emit(self) -> str:
		out: list[str] = []
		out.append("---")
		out.append(f"title: {self.name}")
		out.append(f"engine: {self.version}")
		out.append("category: classes")
		out.append(f"source: {self.source_rel}")
		out.append("---")
		out.append("")
		out.append(f"# {self.name}")
		out.append("")

		if self.inherits:
			out.append("_Inherits: " + " < ".join(class_link(c) for c in self.inherits) + "_")
			out.append("")
		if self.inherited_by:
			out.append("_Implemented by: " + ", ".join(class_link(c) for c in self.inherited_by) + "_")
			out.append("")

		if self.brief:
			out.append("\n\n".join(self.brief).strip())
			out.append("")

		if self.description:
			out.append("## Description")
			out.append("")
			out.append("\n\n".join(self.description).strip())
			out.append("")

		if self.tutorials:
			out.append("## Tutorials")
			out.append("")
			out.extend(self.tutorials)
			out.append("")

		for attr, title in SECTION_ORDER:
			data = getattr(self, attr)
			if not data:
				continue
			out.append(f"## {title}")
			out.append("")
			out.extend(data)
			out.append("")

		return "\n".join(out).rstrip() + "\n"


def convert_file(path: str, version: str, source_rel: str) -> ClassDoc:
	with open(path, "r", encoding="utf-8") as f:
		lines = f.read().split("\n")

	# pre-pass: substitution definitions live in the footer but are used earlier
	subst: dict[str, str] = {}
	for line in lines:
		ms = SUBST_RE.match(line.strip())
		if ms:
			subst[ms.group(1).strip()] = render_subst(ms.group(2))

	n = len(lines)
	i = 0
	while i < n and not lines[i].startswith(".. _class_"):
		i += 1
	if i >= n:
		raise RuntimeError("no class anchor found")
	i += 1  # skip the anchor line itself
	while i < n and lines[i].strip() == "":
		i += 1
	if i >= n:
		raise RuntimeError("no class title found")
	class_name = lines[i].strip()
	i += 1
	if i < n and UNDERLINE_RE.match(lines[i]):
		i += 1

	doc = ClassDoc(class_name, version, source_rel)
	doc.subst = subst

	ctx = "preamble"  # preamble | Description | Tutorials | member | setget | skip
	item: dict | None = None
	pending: list[str] = []

	def attach_paragraph() -> None:
		nonlocal pending
		if not pending:
			return
		text = "\n".join(doc.inline(p) for p in pending).strip()
		pending = []
		if text == "":
			return
		if item is not None and ctx in ("member", "setget", "enum-value"):
			if item["desc"]:
				item["desc"].append("")
			item["desc"].append(text)
		elif ctx == "Description":
			doc.description.append(text)
		elif ctx == "preamble":
			doc.brief.append(text)

	def flush_item() -> None:
		nonlocal item
		if item is None:
			return
		kind = item["kind"]
		desc = "\n\n".join(p for p in item["desc"] if p).strip()
		head = doc.member_heading(item)
		if kind == "property":
			doc.properties.extend([head, ""])
			if item["setter"]:
				doc.properties.append(f"- Setter: `{item['setter']}`")
			if item["getter"]:
				doc.properties.append(f"- Getter: `{item['getter']}`")
			if item["setter"] or item["getter"]:
				doc.properties.append("")
			if desc:
				doc.properties.extend([desc, ""])
		elif kind == "enumeration":
			doc.enums.extend([f"### `{item['prefix']} {item['name']}`", ""])
		elif kind == "enumeration-constant":
			entry = [f"- `{item['name']} = {item['default']}`"] if item["default"] is not None else [f"- `{item['name']}`"]
			if desc:
				entry.append("")
				for ln in desc.split("\n"):
					entry.append(("  " + ln) if ln.strip() else "")
			doc.enums.extend(entry)
			doc.enums.append("")
		elif kind == "constant":
			doc.constants.extend([head, ""])
			if desc:
				doc.constants.extend([desc, ""])
		elif kind == "themeproperty":
			doc.theme.extend([head, ""])
			if desc:
				doc.theme.extend([desc, ""])
		else:
			section = {
				"constructor": doc.constructors,
				"method": doc.methods,
				"operator": doc.operators,
				"signal": doc.signals,
				"annotation": doc.annotations,
			}[kind]
			section.extend([head, ""])
			if desc:
				section.extend([desc, ""])
		item = None

	while i < n:
		line = lines[i]
		s = line.strip()

		if s == "":
			attach_paragraph()
			i += 1
			continue

		if s == "::":
			attach_paragraph()
			block, j = collect_block(lines, i)
			target = item["desc"] if (item is not None and ctx in ("member", "setget", "enum-value")) else doc.description
			content = deindent(block)
			while content and content[0].strip() == "":
				content.pop(0)
			target.append("```gdscript\n" + "\n".join(content) + "\n```")
			i = j
			continue

		if s.startswith(".. "):
			attach_paragraph()
			if s.startswith(".. rst-class::"):
				kind = s.split("::", 1)[1].strip()
				if kind in ITEM_KINDS:
					flush_item()
					item = {"kind": ITEM_KINDS[kind], "desc": [], "setter": None, "getter": None,
							"name": "", "args": "", "quals": [], "default": None, "ret": "",
							"prefix": None, "sig_mode": True}
					ctx = "member"
				elif kind == "classref-property-setget":
					ctx = "setget"
				elif kind in ("classref-reftable-group", "classref-item-separator",
							  "classref-section-separator", "classref-descriptions-group",
							  "classref-introduction-group"):
					ctx = "skip"
				i += 1
				continue
			if s.startswith(".. table::") or s.startswith(".. container::"):
				_, j = collect_block(lines, i)
				i = j
				continue
			if s.startswith(".. tabs::"):
				i += 1
				continue
			if s == "::" or s.startswith(".. code-tab::") or s.startswith(".. code::") or s.startswith(".. code-block::"):
				if s == "::":
					lang = "gdscript"
				else:
					first = s.split("::", 1)[1].strip().split()
					lang = first[0] if first else "text"
				block, j = collect_block(lines, i)
				target = item["desc"] if (item is not None and ctx in ("member", "setget", "enum-value")) else doc.description
				content = deindent(block)
				while content and content[0].strip() == "":
					content.pop(0)
				target.append("```" + lang + "\n" + "\n".join(content) + "\n```")
				i = j
				continue
			if s.startswith((".. note::", ".. warning::", ".. tip::", ".. important::", ".. seealso::")):
				label = s.split("::", 1)[0].split("..", 1)[1].strip().capitalize()
				if label == "Tip":
					label = "Tip"
				arg = s.split("::", 1)[1].strip()
				block, j = collect_block(lines, i)
				target = item["desc"] if (item is not None and ctx in ("member", "setget", "enum-value")) else doc.description
				content = [doc.inline(cl) for cl in deindent(block)]
				while content and content[0].strip() == "":
					content.pop(0)
				first_line = "> **" + label + ":**" + ((" " + doc.inline(arg)) if arg else "")
				if content and not content[0].startswith((">", "-", "*", "```", "|", "#")):
					target.append(first_line + " " + content[0].strip())
					rest = content[1:]
				else:
					target.append(first_line)
					rest = content
				for cl in rest:
					target.append("> " + cl if cl.strip() else ">")
				target.append("")
				i = j
				continue
			if s.startswith(".. meta::"):
				_, j = collect_block(lines, i)
				i = j
				continue
			msub = SUBST_RE.match(s)
			if msub:
				doc.subst[msub.group(1).strip()] = render_subst(msub.group(2))
			i += 1
			continue

		# heading: text line followed by a ---- underline
		if i + 1 < n and re.match(r"^-+\s*$", lines[i + 1]) and not line.startswith((" ", "\t", "..", "|", "+")) and not UNDERLINE_RE.match(line):
			flush_item()
			pending = []
			ctx = SECTION_MAP.get(s, "skip")
			i += 2
			continue

		if UNDERLINE_RE.match(line):
			i += 1
			continue

		# plain content
		if ctx == "preamble":
			if s.startswith("**Inherits:**"):
				doc.inherits = re.findall(r":ref:`[^<]+<class_([^>]+)>`", s)
				i += 1
				continue
			if s.startswith("**Inherited By:**"):
				doc.inherited_by = re.findall(r":ref:`[^<]+<class_([^>]+)>`", s)
				i += 1
				continue
			pending.append(line)
			i += 1
			continue

		if ctx == "Tutorials":
			if s.startswith("- "):
				doc.tutorials.append("- " + doc.inline(s[2:]))
			elif doc.tutorials:
				doc.tutorials[-1] += " " + doc.inline(s)
			i += 1
			continue

		if ctx == "member" and item is not None:
			if item.get("sig_mode"):
				parsed = doc.parse_sig(line, item["kind"])
				if parsed is not None:
					item.update(parsed)
					item["sig_mode"] = False
					if item["kind"] == "enumeration":
						ctx = "enum-value"
			else:
				pending.append(line)
			i += 1
			continue

		if ctx == "enum-value" and item is not None:
			if item.get("sig_mode") and item["name"] == "":
				parsed = doc.parse_sig(line, item["kind"])
				if parsed is not None:
					item.update(parsed)
					item["sig_mode"] = False
			else:
				pending.append(line)
			i += 1
			continue

		if ctx == "setget" and item is not None:
			if s.startswith("- "):
				sg = doc.parse_setget(s)
				if sg:
					key, value = sg
					item[key] = value
			else:
				ctx = "member"
				pending.append(line)
			i += 1
			continue

		pending.append(line)
		i += 1

	flush_item()
	return doc


def main() -> None:
	parser = argparse.ArgumentParser(description="Convert generated class reference RST to Markdown.")
	parser.add_argument("input", nargs="+", help="classes/ directory with class_*.rst files (or individual files).")
	parser.add_argument("--output", "-o", required=True, help="Output directory for class .md files.")
	parser.add_argument("--version", "-v", default="4.7", help="Engine version recorded in frontmatter.")
	args = parser.parse_args()

	file_list: list[str] = []
	for path in args.input:
		if os.path.isdir(path):
			file_list += [os.path.join(path, f) for f in sorted(os.listdir(path))
						  if f.startswith("class_") and f.endswith(".rst")]
		else:
			file_list.append(path)
	file_list = sorted(file_list)

	warnings = 0
	count = 0
	os.makedirs(args.output, exist_ok=True)
	for cur_file in file_list:
		try:
			doc = convert_file(cur_file, args.version, "classes/" + os.path.basename(cur_file))
		except Exception as e:
			warnings += 1
			print(f"WARNING: {cur_file}: {e}", file=sys.stderr)
			continue
		out_path = os.path.join(args.output, doc.name.replace('"', "").replace("/", "--") + ".md")
		with open(out_path, "w", encoding="utf-8", newline="\n") as f:
			f.write(doc.emit())
		count += 1
		warnings += doc.num_warnings

	print(f"convert_classref: wrote {count} class files to {args.output}")
	if warnings:
		print(f"convert_classref: {warnings} warnings", file=sys.stderr)


if __name__ == "__main__":
	main()
