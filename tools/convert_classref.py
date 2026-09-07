#!/usr/bin/env python3
"""Convert Godot class reference XML (doc/classes, modules/*/doc_classes,
platform/*/doc_classes) into one self-contained Markdown file per class.

The parser mirrors doc/tools/make_rst.py from the godot repo (MIT), with the
RST emitter replaced by a Markdown emitter. Python stdlib only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET

GODOT_DOCS_PATTERN = re.compile(r"^\$DOCS_URL/(.*)\.html(#.*)?$")

RESERVED_CODEBLOCK_TAGS = ["codeblock", "gdscript", "csharp"]
RESERVED_CROSSLINK_TAGS = [
	"method",
	"constructor",
	"operator",
	"member",
	"signal",
	"constant",
	"enum",
	"annotation",
	"theme_item",
	"param",
]

PACKED_ARRAY_TYPES = [
	"PackedByteArray",
	"PackedColorArray",
	"PackedFloat32Array",
	"PackedFloat64Array",
	"PackedInt32Array",
	"PackedInt64Array",
	"PackedStringArray",
	"PackedVector2Array",
	"PackedVector3Array",
	"PackedVector4Array",
]

DOCS_URL_BASE = "https://docs.godotengine.org/en/stable/"


def warn(msg: str) -> None:
	print(f"WARNING: {msg}", file=sys.stderr)


def is_in_tagset(tag_text: str, tagset: list[str]) -> bool:
	for tag in tagset:
		if tag_text == tag:
			return True
		if tag_text.startswith(tag + " "):
			return True
		if tag_text.startswith(tag + "="):
			return True
	return False


def get_tag_and_args(tag_text: str):
	tag_name = tag_text
	arguments = ""
	delim_pos = -1
	space_pos = tag_text.find(" ")
	if space_pos >= 0:
		delim_pos = space_pos
	assign_pos = tag_text.find("=")
	if assign_pos >= 0 and (delim_pos < 0 or assign_pos < delim_pos):
		delim_pos = assign_pos
	if delim_pos >= 0:
		tag_name = tag_text[:delim_pos]
		arguments = tag_text[delim_pos + 1:].strip()
	closing = False
	if tag_name.startswith("/"):
		tag_name = tag_name[1:]
		closing = True
	return tag_name, arguments, closing


def md_escape(text: str) -> str:
	"""Escape plain (non-code) text for Markdown."""
	out = []
	for i, ch in enumerate(text):
		if ch in "`*[]":
			out.append("\\" + ch)
		elif ch == "<" and (text[i + 1:i + 2].isalpha() or text[i + 1:i + 2] in "/!?"):
			out.append("\\" + ch)
		elif ch == "_":
			nxt = text[i + 1:i + 2]
			if not nxt.isalnum():
				out.append("\\_")
			else:
				out.append(ch)
		else:
			out.append(ch)
	return "".join(out)


def backtick_span(content: str) -> str:
	"""Wrap content in an inline code span, choosing a delimiter that survives."""
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


def class_link(name: str) -> str:
	target = urllib.parse.quote(name, safe="")
	return f"[{name}]({target}.md)"


class TypeName:
	def __init__(self, type_name: str, enum: str | None = None, is_bitfield: bool = False):
		self.type_name = type_name
		self.enum = enum
		self.is_bitfield = is_bitfield

	@classmethod
	def from_element(cls, element: ET.Element) -> "TypeName":
		return cls(element.attrib["type"], element.get("enum"), element.get("is_bitfield") == "true")

	def display(self, state: "State") -> str:
		if self.enum is not None:
			return make_enum(self.enum, self.is_bitfield, state)
		return self.type_name


class DefinitionBase:
	def __init__(self, definition_name: str, name: str):
		self.definition_name = definition_name
		self.name = name
		self.deprecated: str | None = None
		self.experimental: str | None = None


class PropertyDef(DefinitionBase):
	def __init__(self, name, type_name, setter, getter, text, default_value, overrides):
		super().__init__("property", name)
		self.type_name = type_name
		self.setter = setter
		self.getter = getter
		self.text = text
		self.default_value = default_value
		self.overrides = overrides


class ParameterDef(DefinitionBase):
	def __init__(self, name, type_name, default_value):
		super().__init__("parameter", name)
		self.type_name = type_name
		self.default_value = default_value


class SignalDef(DefinitionBase):
	def __init__(self, name, parameters, description):
		super().__init__("signal", name)
		self.parameters = parameters
		self.description = description


class AnnotationDef(DefinitionBase):
	def __init__(self, name, parameters, description, qualifiers):
		super().__init__("annotation", name)
		self.parameters = parameters
		self.description = description
		self.qualifiers = qualifiers


class MethodDef(DefinitionBase):
	def __init__(self, name, return_type, parameters, description, qualifiers):
		super().__init__("method", name)
		self.return_type = return_type
		self.parameters = parameters
		self.description = description
		self.qualifiers = qualifiers


class ConstantDef(DefinitionBase):
	def __init__(self, name, value, text, bitfield):
		super().__init__("constant", name)
		self.value = value
		self.text = text
		self.is_bitfield = bitfield


class EnumDef(DefinitionBase):
	def __init__(self, name, type_name, bitfield):
		super().__init__("enum", name)
		self.type_name = type_name
		self.values: dict[str, ConstantDef] = {}
		self.is_bitfield = bitfield


class ThemeItemDef(DefinitionBase):
	def __init__(self, name, type_name, data_name, text, default_value):
		super().__init__("theme property", name)
		self.type_name = type_name
		self.data_name = data_name
		self.text = text
		self.default_value = default_value


class ClassDef(DefinitionBase):
	def __init__(self, name: str):
		super().__init__("class", name)
		self.constants: dict[str, ConstantDef] = {}
		self.enums: dict[str, EnumDef] = {}
		self.properties: dict[str, PropertyDef] = {}
		self.constructors: dict[str, list[MethodDef]] = {}
		self.methods: dict[str, list[MethodDef]] = {}
		self.operators: dict[str, list[MethodDef]] = {}
		self.signals: dict[str, SignalDef] = {}
		self.annotations: dict[str, list[AnnotationDef]] = {}
		self.theme_items: dict[str, ThemeItemDef] = {}
		self.inherits: str | None = None
		self.brief_description: str | None = None
		self.description: str | None = None
		self.tutorials: list[tuple[str, str]] = []
		self.filepath: str = ""


class State:
	def __init__(self):
		self.classes: dict[str, ClassDef] = {}
		self.current_class = ""
		self.num_warnings = 0

	def parse_class(self, class_root: ET.Element, filepath: str) -> None:
		class_name = class_root.attrib["name"]
		self.current_class = class_name
		class_def = ClassDef(class_name)
		self.classes[class_name] = class_def
		class_def.filepath = filepath
		class_def.inherits = class_root.get("inherits")
		class_def.deprecated = class_root.get("deprecated")
		class_def.experimental = class_root.get("experimental")

		brief_desc = class_root.find("brief_description")
		if brief_desc is not None and brief_desc.text:
			class_def.brief_description = brief_desc.text

		desc = class_root.find("description")
		if desc is not None and desc.text:
			class_def.description = desc.text

		properties = class_root.find("members")
		if properties is not None:
			for member in properties:
				name = member.attrib["name"]
				if name in class_def.properties:
					self.warn(f'{class_name}.xml: Duplicate property "{name}".')
					continue
				type_name = TypeName.from_element(member)
				setter = member.get("setter") or None
				getter = member.get("getter") or None
				default_value = member.get("default") or None
				overrides = member.get("overrides") or None
				prop = PropertyDef(name, type_name, setter, getter, member.text, default_value, overrides)
				prop.deprecated = member.get("deprecated")
				prop.experimental = member.get("experimental")
				class_def.properties[name] = prop

		for tag, kind, target in (
			("constructors", "constructor", class_def.constructors),
			("methods", "method", class_def.methods),
			("operators", "operator", class_def.operators),
		):
			container = class_root.find(tag)
			if container is None:
				continue
			for element in container:
				name = element.attrib["name"]
				qualifiers = element.get("qualifiers")
				return_element = element.find("return")
				if return_element is not None:
					return_type = TypeName.from_element(return_element)
				else:
					return_type = TypeName("void")
				params = self.parse_params(element)
				desc_element = element.find("description")
				method_desc = desc_element.text if desc_element is not None else None
				method_def = MethodDef(name, return_type, params, method_desc, qualifiers)
				method_def.deprecated = element.get("deprecated")
				method_def.experimental = element.get("experimental")
				target.setdefault(name, []).append(method_def)

		constants = class_root.find("constants")
		if constants is not None:
			for constant in constants:
				name = constant.attrib["name"]
				value = constant.attrib["value"]
				enum = constant.get("enum")
				is_bitfield = constant.get("is_bitfield") == "true"
				constant_def = ConstantDef(name, value, constant.text, is_bitfield)
				constant_def.deprecated = constant.get("deprecated")
				constant_def.experimental = constant.get("experimental")
				if enum is None:
					if name in class_def.constants:
						self.warn(f'{class_name}.xml: Duplicate constant "{name}".')
						continue
					class_def.constants[name] = constant_def
				else:
					if enum not in class_def.enums:
						class_def.enums[enum] = EnumDef(enum, TypeName("int", enum), is_bitfield)
					class_def.enums[enum].values[name] = constant_def

		annotations = class_root.find("annotations")
		if annotations is not None:
			for annotation in annotations:
				name = annotation.attrib["name"]
				qualifiers = annotation.get("qualifiers")
				params = self.parse_params(annotation)
				desc_element = annotation.find("description")
				annotation_desc = desc_element.text if desc_element is not None else None
				annotation_def = AnnotationDef(name, params, annotation_desc, qualifiers)
				class_def.annotations.setdefault(name, []).append(annotation_def)

		signals = class_root.find("signals")
		if signals is not None:
			for signal in signals:
				name = signal.attrib["name"]
				if name in class_def.signals:
					self.warn(f'{class_name}.xml: Duplicate signal "{name}".')
					continue
				params = self.parse_params(signal)
				desc_element = signal.find("description")
				signal_desc = desc_element.text if desc_element is not None else None
				signal_def = SignalDef(name, params, signal_desc)
				signal_def.deprecated = signal.get("deprecated")
				signal_def.experimental = signal.get("experimental")
				class_def.signals[name] = signal_def

		theme_items = class_root.find("theme_items")
		if theme_items is not None:
			for theme_item in theme_items:
				name = theme_item.attrib["name"]
				data_name = theme_item.attrib["data_type"]
				if name in class_def.theme_items:
					self.warn(f'{class_name}.xml: Duplicate theme property "{name}".')
					continue
				default_value = theme_item.get("default") or None
				item = ThemeItemDef(name, TypeName.from_element(theme_item), data_name, theme_item.text, default_value)
				item.deprecated = theme_item.get("deprecated")
				item.experimental = theme_item.get("experimental")
				class_def.theme_items[name] = item

		tutorials = class_root.find("tutorials")
		if tutorials is not None:
			for link in tutorials:
				if link.text is not None:
					class_def.tutorials.append((link.text.strip(), link.get("title", "")))

		self.current_class = ""

	def parse_params(self, root: ET.Element) -> list[ParameterDef]:
		param_elements = root.findall("param")
		params: list[ParameterDef | None] = [None] * len(param_elements)
		for param_element in param_elements:
			index = int(param_element.attrib["index"])
			type_name = TypeName.from_element(param_element)
			default = param_element.get("default")
			params[index] = ParameterDef(param_element.attrib["name"], type_name, default)
		return [p for p in params if p is not None]


def make_enum(t: str, is_bitfield: bool, state: State) -> str:
	p = t.rfind(".")
	if p >= 0:
		c = t[:p]
		e = t[p + 1:]
		if c == "Variant":
			c = "@GlobalScope"
			e = "Variant." + e
	else:
		c = state.current_class
		e = t
		if c in state.classes and e not in state.classes[c].enums:
			c = "@GlobalScope"

	if c in state.classes and e in state.classes[c].enums:
		if c == state.current_class:
			if is_bitfield:
				return f"BitField[{e}]"
			return e
		if is_bitfield:
			return f"BitField[{c}.{e}]"
		return f"{c}.{e}"
	return t


def method_signature_md(defn: MethodDef | SignalDef | AnnotationDef, state: State) -> str:
	name = defn.name
	qualifiers = getattr(defn, "qualifiers", None)

	if isinstance(defn, AnnotationDef):
		name = "@" + name

	out = name + "("
	parts = []
	for arg in defn.parameters:
		sig = f"{arg.name}: {arg.type_name.display(state)}"
		if arg.default_value is not None:
			sig += f" = {arg.default_value}"
		parts.append(sig)
	if qualifiers is not None and "vararg" in qualifiers.split():
		parts.append("...")
	out += ", ".join(parts)
	out += ")"

	if qualifiers is not None:
		rest = [q for q in qualifiers.split() if q != "vararg"]
		if rest:
			out += " " + " ".join(rest)
	return out


def setter_signature(class_def: ClassDef, prop: PropertyDef, state: State) -> str | None:
	if prop.setter is None or prop.setter.startswith("_"):
		return None
	if prop.setter in class_def.methods:
		m = class_def.methods[prop.setter][0]
		return method_signature_md(m, state)
	setter = MethodDef(prop.setter, TypeName("void"), [ParameterDef("value", prop.type_name, None)], None, None)
	return method_signature_md(setter, state)


def getter_signature(class_def: ClassDef, prop: PropertyDef, state: State) -> str | None:
	if prop.getter is None or prop.getter.startswith("_"):
		return None
	if prop.getter in class_def.methods:
		m = class_def.methods[prop.getter][0]
		return method_signature_md(m, state)
	getter = MethodDef(prop.getter, prop.type_name, [], None, None)
	return method_signature_md(getter, state)


def deprecated_experimental_md(item: DefinitionBase, state: State) -> str:
	parts = []
	if item.deprecated is not None:
		if item.deprecated.strip() == "":
			msg = f"This {item.definition_name} may be changed or removed in future versions."
		else:
			msg = format_text_block(item.deprecated.strip(), item, state)
		parts.append(f"**Deprecated:** {msg}")
	if item.experimental is not None:
		if item.experimental.strip() == "":
			msg = f"This {item.definition_name} may be changed or removed in future versions."
		else:
			msg = format_text_block(item.experimental.strip(), item, state)
		parts.append(f"**Experimental:** {msg}")
	if not parts:
		return ""
	return "\n\n".join(parts) + "\n"


def preformat_text_block(text: str, state: State) -> str | None:
	result = ""
	codeblock_tag = ""
	indent_level = 0

	for line in text.splitlines():
		stripped_line = line.lstrip("\t")
		tab_count = len(line) - len(stripped_line)

		if codeblock_tag:
			if line == "":
				result += "\n"
				continue
			if tab_count < indent_level:
				state.num_warnings += 1
				print(
					f"WARNING: {state.current_class}.xml: Invalid indentation in code block.",
					file=sys.stderr,
				)
				return None
			if stripped_line.startswith("[/" + codeblock_tag):
				result += stripped_line
				codeblock_tag = ""
			else:
				result += "\n" + "    " * max(0, tab_count - indent_level) + stripped_line
		else:
			if (
				stripped_line.startswith("[codeblock]")
				or stripped_line.startswith("[codeblock ")
				or stripped_line.startswith("[gdscript]")
				or stripped_line.startswith("[gdscript ")
				or stripped_line.startswith("[csharp]")
				or stripped_line.startswith("[csharp ")
			):
				if result:
					result += "\n\n"
				result += stripped_line
				tag_text = stripped_line[1:].split("]", 1)[0]
				codeblock_tag, _, _ = get_tag_and_args(tag_text)
				indent_level = tab_count
			else:
				if result:
					result += "\n\n"
				result += stripped_line

	return result


def format_text_block(text: str, context: DefinitionBase, state: State) -> str:
	pre = preformat_text_block(text, state)
	if pre is None:
		return ""

	parts: list[str] = []
	pos = 0
	inside_code = False
	inside_code_tag = ""

	def emit_plain(segment: str) -> None:
		parts.append(md_escape(segment) if not inside_code else segment)

	while True:
		brack = pre.find("[", pos)
		if brack == -1:
			emit_plain(pre[pos:])
			break
		if brack > pos:
			emit_plain(pre[pos:brack])

		endq = pre.find("]", brack + 1)
		if endq == -1:
			emit_plain(pre[brack:])
			break

		tag_text = pre[brack + 1:endq]

		# Bare class reference.
		if tag_text in state.classes and not inside_code:
			if tag_text == state.current_class:
				parts.append(f"**{tag_text}**")
			else:
				parts.append(class_link(tag_text))
			pos = endq + 1
			continue

		tag_name, arguments, closing = get_tag_and_args(tag_text)

		if inside_code:
			if closing and tag_name == inside_code_tag:
				if tag_name == "codeblock":
					parts.append("\n```")
				else:
					parts.append("\n```\n")
				inside_code = False
				inside_code_tag = ""
			else:
				parts.append(f"[{tag_text}]")
			pos = endq + 1
			continue

		if tag_name == "codeblocks":
			pos = endq + 1  # drop, structure comes from gdscript/csharp fences
			continue

		if not closing and is_in_tagset(tag_name, RESERVED_CODEBLOCK_TAGS):
			if tag_name == "csharp":
				lang = "csharp"
			elif tag_name == "gdscript":
				lang = "gdscript"
			elif "lang=text" in arguments.split(" "):
				lang = "text"
			else:
				m = re.search(r"lang=([A-Za-z0-9_+#.\-]+)", arguments)
				lang = m.group(1) if m else "gdscript"
			parts.append("```" + lang)
			inside_code = True
			inside_code_tag = tag_name
			pos = endq + 1
			continue

		if tag_name == "code" and not closing:
			endcode = pre.find("[/code]", endq + 1)
			if endcode == -1:
				state.num_warnings += 1
				print(f"WARNING: {state.current_class}.xml: No closing [/code] found.", file=sys.stderr)
				parts.append(f"[{tag_text}]")
				pos = endq + 1
				continue
			content = pre[endq + 1:endcode]
			parts.append(backtick_span(content))
			pos = endcode + len("[/code]")
			continue

		if tag_name == "kbd":
			if closing:
				parts.append("`")
			else:
				endkbd = pre.find("[/kbd]", endq + 1)
				if endkbd == -1:
					parts.append(f"[{tag_text}]")
					pos = endq + 1
					continue
				content = pre[endq + 1:endkbd]
				parts.append(backtick_span(content))
				pos = endkbd + len("[/kbd]")
			continue

		if tag_name == "url" and not closing:
			if arguments == "":
				state.num_warnings += 1
				print(f"WARNING: {state.current_class}.xml: Empty [url] tag.", file=sys.stderr)
				parts.append(f"[{tag_text}]")
				pos = endq + 1
				continue
			endurl = pre.find("[/url]", endq + 1)
			if endurl == -1:
				parts.append(f"[{tag_text}]")
				pos = endq + 1
				continue
			link_title = pre[endq + 1:endurl]
			parts.append(make_link_md(arguments, link_title))
			pos = endurl + len("[/url]")
			continue

		if tag_name == "br":
			parts.append("\n\n")
			pos = endq + 1
			continue

		if tag_name == "lb":
			parts.append("\\[")
			pos = endq + 1
			continue

		if tag_name == "rb":
			parts.append("\\]")
			pos = endq + 1
			continue

		if tag_name in ("i", "b"):
			marker = "*" if tag_name == "i" else "**"
			parts.append(marker)
			pos = endq + 1
			continue

		if tag_name in ("u", "center"):
			pos = endq + 1  # drop
			continue

		if not closing and tag_name in RESERVED_CROSSLINK_TAGS:
			link_target = arguments
			if link_target == "":
				state.num_warnings += 1
				print(
					f"WARNING: {state.current_class}.xml: Empty cross-reference link [{tag_text}].",
					file=sys.stderr,
				)
				pos = endq + 1
				continue
			if tag_name == "enum":
				parts.append(backtick_span(make_enum(link_target, False, state)))
			elif tag_name == "param":
				parts.append(backtick_span(link_target))
			else:
				if "." in link_target:
					target_class_name, target_name = link_target.split(".", 1)
				else:
					target_class_name, target_name = state.current_class, link_target
					if tag_name == "constant" and (
						target_class_name not in state.classes
						or (
							target_name not in state.classes[target_class_name].constants
							and not any(
								target_name in e.values for e in state.classes[target_class_name].enums.values()
							)
						)
					):
						fallback = "@GlobalScope"
						if fallback in state.classes and (
							target_name in state.classes[fallback].constants
							or any(target_name in e.values for e in state.classes[fallback].enums.values())
						):
							target_class_name = fallback
				repl_text = target_name
				if target_class_name != state.current_class:
					repl_text = f"{target_class_name}.{target_name}"
				if tag_name == "method":
					repl_text += "()"
				parts.append(backtick_span(repl_text))
			pos = endq + 1
			continue

		if closing:
			parts.append(f"[{tag_text}]")
		else:
			parts.append(backtick_span(tag_text))
		pos = endq + 1

	text = "".join(parts)
	return collapse_blank_runs(text)


def collapse_blank_runs(text: str) -> str:
	"""Collapse 3+ newlines outside of fenced code blocks."""
	lines = text.split("\n")
	out: list[str] = []
	in_fence = False
	blank_run = 0
	for line in lines:
		if not in_fence and (line.startswith("```") or line.startswith("~~~")):
			in_fence = True
			blank_run = 0
			out.append(line)
			continue
		if in_fence:
			if line.startswith("```") or line.startswith("~~~"):
				in_fence = False
			out.append(line)
			continue
		if line.strip() == "":
			blank_run += 1
			if blank_run <= 1:
				out.append(line)
		else:
			blank_run = 0
			out.append(line)
	return "\n".join(out)


MEDIA_EXT_RE = re.compile(r"\.(png|jpe?g|webp|gif|webm|svg|avif|mp4)$", re.IGNORECASE)


def make_link_md(url: str, title: str) -> str:
	if MEDIA_EXT_RE.search(url.split("#")[0].split("?")[0]):
		# media links carry no textual value; keep the title, drop the target
		return md_escape(title)
	match = GODOT_DOCS_PATTERN.search(url)
	if match:
		url = DOCS_URL_BASE + match.group(1) + ".html" + (match.group(2) or "")
	text = title if title else url
	return f"[{md_escape(text)}]({url})"


def section_heading(out: list[str], title: str) -> None:
	out.append(f"## {title}")
	out.append("")


def emit_member_deprecated(out: list[str], item: DefinitionBase, state: State) -> None:
	notices = deprecated_experimental_md(item, state)
	if notices:
		out.append(notices.rstrip())
		out.append("")


def emit_class(class_def: ClassDef, state: State, output_dir: str, version: str) -> None:
	class_name = class_def.name
	out: list[str] = []

	source_rel = os.path.relpath(class_def.filepath, state.input_root).replace(os.sep, "/")

	out.append("---")
	out.append(f"title: {class_name}")
	out.append(f"engine: {version}")
	out.append("category: classes")
	out.append(f"source: {source_rel}")
	out.append("---")
	out.append("")
	out.append(f"# {class_name}")
	out.append("")

	notices = deprecated_experimental_md(class_def, state)
	if notices:
		out.append(notices.rstrip())
		out.append("")

	if class_def.inherits:
		chain = []
		inherits = class_def.inherits.strip()
		while inherits:
			chain.append(inherits)
			if inherits not in state.classes:
				break
			inherits = state.classes[inherits].inherits or ""
		if chain:
			out.append("_Inherits: " + " < ".join(class_link(c) for c in chain) + "_")
			out.append("")

	inherited = sorted(c.name for c in state.classes.values() if c.inherits and c.inherits.strip() == class_name)
	if inherited:
		out.append("_Implemented by: " + ", ".join(class_link(c) for c in inherited) + "_")
		out.append("")

	if class_def.brief_description is not None and class_def.brief_description.strip():
		out.append(format_text_block(class_def.brief_description.strip(), class_def, state).strip())
		out.append("")

	if class_def.description is not None and class_def.description.strip():
		section_heading(out, "Description")
		out.append(format_text_block(class_def.description.strip(), class_def, state).strip())
		out.append("")

	if class_def.tutorials:
		section_heading(out, "Tutorials")
		for url, title in class_def.tutorials:
			out.append(f"- {make_link_md(url, title)}")
		out.append("")

	if class_def.properties:
		section_heading(out, "Properties")
		for prop in class_def.properties.values():
			sig = f"{prop.type_name.display(state)} {prop.name}"
			if prop.default_value is not None:
				sig += f" = {prop.default_value}"
			out.append(f"### `{sig}`")
			out.append("")
			if prop.overrides:
				out.append(f"_Overrides {prop.overrides}._")
				out.append("")
			setter = setter_signature(class_def, prop, state)
			getter = getter_signature(class_def, prop, state)
			if setter:
				out.append(f"- Setter: `{setter}`")
			if getter:
				out.append(f"- Getter: `{getter}`")
			if setter or getter:
				out.append("")
			emit_member_deprecated(out, prop, state)
			if prop.text is not None and prop.text.strip():
				out.append(format_text_block(prop.text.strip(), prop, state).strip())
				out.append("")
			if prop.type_name.type_name in PACKED_ARRAY_TYPES:
				out.append(
					"**Note:** The returned array is *copied* and any changes to it will not update the "
					"original property value. See "
					+ class_link(prop.type_name.type_name)
					+ " for more details."
				)
				out.append("")

	if class_def.constructors:
		section_heading(out, "Constructors")
		for method_list in class_def.constructors.values():
			for m in method_list:
				sig = f"{m.return_type.display(state)} {method_signature_md(m, state)}"
				out.append(f"### `{sig}`")
				out.append("")
				emit_member_deprecated(out, m, state)
				if m.description is not None and m.description.strip():
					out.append(format_text_block(m.description.strip(), m, state).strip())
					out.append("")

	if class_def.methods:
		section_heading(out, "Methods")
		for method_list in class_def.methods.values():
			for m in method_list:
				sig = f"{m.return_type.display(state)} {method_signature_md(m, state)}"
				out.append(f"### `{sig}`")
				out.append("")
				emit_member_deprecated(out, m, state)
				if m.description is not None and m.description.strip():
					out.append(format_text_block(m.description.strip(), m, state).strip())
					out.append("")

	if class_def.operators:
		section_heading(out, "Operators")
		for method_list in class_def.operators.values():
			for m in method_list:
				sig = f"{m.return_type.display(state)} {method_signature_md(m, state)}"
				out.append(f"### `{sig}`")
				out.append("")
				emit_member_deprecated(out, m, state)
				if m.description is not None and m.description.strip():
					out.append(format_text_block(m.description.strip(), m, state).strip())
					out.append("")

	if class_def.theme_items:
		section_heading(out, "Theme Properties")
		for item in class_def.theme_items.values():
			sig = f"{item.type_name.display(state)} {item.name}"
			if item.default_value is not None:
				sig += f" = {item.default_value}"
			out.append(f"### `{sig}`")
			out.append("")
			emit_member_deprecated(out, item, state)
			if item.text is not None and item.text.strip():
				out.append(format_text_block(item.text.strip(), item, state).strip())
				out.append("")

	if class_def.signals:
		section_heading(out, "Signals")
		for signal in class_def.signals.values():
			sig = method_signature_md(signal, state)
			out.append(f"### `{sig}`")
			out.append("")
			emit_member_deprecated(out, signal, state)
			if signal.description is not None and signal.description.strip():
				out.append(format_text_block(signal.description.strip(), signal, state).strip())
				out.append("")

	if class_def.enums:
		section_heading(out, "Enumerations")
		for enum in class_def.enums.values():
			prefix = "flags" if enum.is_bitfield else "enum"
			out.append(f"### `{prefix} {enum.name}`")
			out.append("")
			for value in enum.values.values():
				entry = [f"- `{value.name} = {value.value}`"]
				body: list[str] = []
				notices = deprecated_experimental_md(value, state)
				if notices:
					body.append(notices.rstrip())
				if value.text is not None and value.text.strip():
					body.append(format_text_block(value.text.strip(), value, state).strip())
				if body:
					entry.append("")
					for line in body:
						if line == "":
							entry.append("")
						else:
							entry.append("  " + line.replace("\n", "\n  "))
				out.append("\n".join(entry))
				out.append("")

	if class_def.constants:
		section_heading(out, "Constants")
		for constant in class_def.constants.values():
			out.append(f"### `{constant.name} = {constant.value}`")
			out.append("")
			emit_member_deprecated(out, constant, state)
			if constant.text is not None and constant.text.strip():
				out.append(format_text_block(constant.text.strip(), constant, state).strip())
				out.append("")

	if class_def.annotations:
		section_heading(out, "Annotations")
		for method_list in class_def.annotations.values():
			for annotation in method_list:
				sig = method_signature_md(annotation, state)
				out.append(f"### `{sig}`")
				out.append("")
				if annotation.description is not None and annotation.description.strip():
					out.append(format_text_block(annotation.description.strip(), annotation, state).strip())
					out.append("")

	content = "\n".join(out).rstrip() + "\n"
	file_name = class_name.replace('"', "").replace("/", "--")
	os.makedirs(output_dir, exist_ok=True)
	with open(os.path.join(output_dir, f"{file_name}.md"), "w", encoding="utf-8", newline="\n") as f:
		f.write(content)


def collect_files(path: str) -> list[str]:
	if os.path.basename(path) in ("modules", "platform"):
		files = []
		for subdir, dirs, _ in os.walk(path):
			if "doc_classes" in dirs:
				doc_dir = os.path.join(subdir, "doc_classes")
				files += [os.path.join(doc_dir, f) for f in sorted(os.listdir(doc_dir)) if f.endswith(".xml")]
		return files
	if os.path.isdir(path):
		return [os.path.join(path, f) for f in sorted(os.listdir(path)) if f.endswith(".xml")]
	return [path]


def main() -> None:
	parser = argparse.ArgumentParser(description="Convert Godot class reference XML to Markdown.")
	parser.add_argument("input", nargs="+", help="XML files or directories (doc/classes, modules, platform).")
	parser.add_argument("--output", "-o", required=True, help="Output directory for class .md files.")
	parser.add_argument("--version", "-v", default="4.7", help="Engine version recorded in frontmatter.")
	args = parser.parse_args()

	state = State()

	file_list: list[str] = []
	for path in args.input:
		file_list += collect_files(path)
	file_list = sorted(file_list)

	for cur_file in file_list:
		try:
			tree = ET.parse(cur_file)
		except ET.ParseError as e:
			state.num_warnings += 1
			print(f"WARNING: {cur_file}: XML parse error: {e}", file=sys.stderr)
			continue
		root = tree.getroot()
		name = root.attrib["name"]
		if name in state.classes:
			state.num_warnings += 1
			print(f"WARNING: {cur_file}: Duplicate class \"{name}\".", file=sys.stderr)
			continue
		try:
			state.parse_class(root, cur_file)
		except Exception as e:
			state.num_warnings += 1
			print(f"WARNING: {cur_file}: Exception while parsing class: {e}", file=sys.stderr)
			continue

	state.input_root = os.path.commonpath([os.path.abspath(p) for p in args.input]) if args.input else "."

	for class_name in sorted(state.classes, key=lambda n: n.lower()):
		emit_class(state.classes[class_name], state, args.output, args.version)

	print(f"convert_classref: wrote {len(state.classes)} class files to {args.output}")
	if state.num_warnings:
		print(f"convert_classref: {state.num_warnings} warnings", file=sys.stderr)


if __name__ == "__main__":
	main()
