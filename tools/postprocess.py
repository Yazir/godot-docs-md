#!/usr/bin/env python3
"""Post-process converted Markdown into the distributable package.

Generates INDEX.md, VERSION, .gdignore and LICENSE-PACKAGE.md, assembles the
package tree and produces a byte-deterministic tar.gz plus SHA256SUMS.
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import io
import os
import tarfile

TAR_MTIME = 946684800  # fixed timestamp for deterministic archives

LICENSE_PACKAGE = """\
# License and attribution for this documentation package

This package contains documentation derived from two upstream sources:

- **Class reference** (`classes/`): generated from `doc/classes/*.xml`,
  `modules/*/doc_classes/*.xml` and `platform/*/doc_classes/*.xml` of the
  [godotengine/godot](https://github.com/godotengine/godot) repository,
  licensed under the
  [MIT license](https://github.com/godotengine/godot/blob/master/LICENSE.txt).
  Copyright (c) Juan Linietsky, Ariel Manzur and the Godot community.
- **Manual** (`manual/`): converted from the `getting_started`, `tutorials`
  and `engine_details` trees of the
  [godotengine/godot-docs](https://github.com/godotengine/godot-docs)
  repository, licensed under the
  [Creative Commons Attribution 3.0 Unported license (CC BY 3.0)](https://github.com/godotengine/godot-docs/blob/master/LICENSE.txt).
  Copyright (c) Juan Linietsky, Ariel Manzur and the Godot community.

The conversion tooling that produced this package is maintained separately and
does not modify upstream content. See the `godot-docs-md` repository for the
pipeline and its own MIT-licensed tooling.
"""


def parse_frontmatter(path: str) -> tuple[dict[str, str], list[str]]:
	with open(path, "r", encoding="utf-8") as f:
		lines = f.read().split("\n")
	keys: dict[str, str] = {}
	if not lines or lines[0].strip() != "---":
		return keys, lines
	for i in range(1, len(lines)):
		line = lines[i]
		if line.strip() == "---":
			return keys, lines[i + 1:]
		if ":" in line:
			k, v = line.split(":", 1)
			keys[k.strip()] = v.strip()
	return keys, lines


def file_summary(path: str) -> str:
	keys, body = parse_frontmatter(path)
	summary = keys.get("summary")
	if summary:
		return summary
	for line in body:
		s = line.strip()
		if s == "":
			continue
		if s.startswith(("#", "_", "-", "|", ">", "**", "```", "~~~", "![", "<")):
			continue
		return s
	if keys.get("title"):
		return keys["title"]
	return "(no summary)"


def write_file(path: str, content: str) -> None:
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w", encoding="utf-8", newline="\n") as f:
		f.write(content)


def build_index(files: list[str], input_dir: str, version: str, docs_sha: str) -> str:
	out = []
	out.append(f"# Godot {version} documentation index")
	out.append("")
	out.append(f"{len(files)} files. Built from godot-docs@{docs_sha}.")
	out.append("")
	for rel in files:
		out.append(f"- {rel} — {file_summary(os.path.join(input_dir, rel))}")
	return "\n".join(out) + "\n"


def make_tar_gz(tar_path: str, pkg_root: str, root_name: str) -> None:
	entries: list[tuple[str, str]] = []  # (archive_name, absolute_path)
	for dirpath, dirnames, filenames in os.walk(pkg_root):
		dirnames.sort()
		for name in sorted(filenames):
			abs_path = os.path.join(dirpath, name)
			arc_name = os.path.join(root_name, os.path.relpath(abs_path, pkg_root))
			entries.append((arc_name.replace(os.sep, "/"), abs_path))
	entries.sort(key=lambda e: e[0])

	dirs: set[str] = set()
	for arc_name, _ in entries:
		parts = arc_name.split("/")
		for i in range(1, len(parts)):
			dirs.add("/".join(parts[:i]))

	with open(tar_path, "wb") as raw:
		with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as gz:
			with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar:
				for dir_name in sorted(dirs):
					info = tarfile.TarInfo(dir_name)
					info.type = tarfile.DIRTYPE
					info.mode = 0o755
					info.mtime = TAR_MTIME
					info.uid = 0
					info.gid = 0
					info.uname = ""
					info.gname = ""
					tar.addfile(info)
				for arc_name, abs_path in entries:
					with open(abs_path, "rb") as f:
						data = f.read()
					info = tarfile.TarInfo(arc_name)
					info.type = tarfile.REGTYPE
					info.mode = 0o644
					info.size = len(data)
					info.mtime = TAR_MTIME
					info.uid = 0
					info.gid = 0
					info.uname = ""
					info.gname = ""
					tar.addfile(info, io.BytesIO(data))


def main() -> None:
	parser = argparse.ArgumentParser(description="Assemble the godot-docs-md package.")
	parser.add_argument("--input", "-i", required=True, help="Directory containing classes/ and manual/.")
	parser.add_argument("--output", "-o", required=True, help="Directory for the assembled package tree.")
	parser.add_argument("--tar", required=True, help="Path of the resulting .tar.gz package.")
	parser.add_argument("--version", "-v", default="4.7")
	parser.add_argument("--docs-sha", required=True)
	args = parser.parse_args()

	version = args.version
	release = f"godot-{version}-md-{args.docs_sha[:8]}"

	src_classes = os.path.join(args.input, "classes")
	src_manual = os.path.join(args.input, "manual")

	markdown_files: list[str] = []
	for base in ("classes", "manual"):
		root = os.path.join(args.input, base)
		if not os.path.isdir(root):
			continue
		for dirpath, dirnames, filenames in os.walk(root):
			dirnames.sort()
			for name in sorted(filenames):
				if name.endswith(".md"):
					markdown_files.append(os.path.relpath(os.path.join(dirpath, name), args.input))
	markdown_files.sort()

	# Package tree
	pkg_root = os.path.join(args.output, f"godot-{version}-md")
	if os.path.isdir(pkg_root):
		import shutil

		shutil.rmtree(pkg_root)
	os.makedirs(pkg_root, exist_ok=True)

	# Copy converted trees byte-for-byte
	for base in ("classes", "manual"):
		src = os.path.join(args.input, base)
		if not os.path.isdir(src):
			continue
		for dirpath, dirnames, filenames in os.walk(src):
			dirnames.sort()
			rel_dir = os.path.relpath(dirpath, args.input)
			os.makedirs(os.path.join(pkg_root, rel_dir), exist_ok=True)
			for name in sorted(filenames):
				dst = os.path.join(pkg_root, rel_dir, name)
				os.makedirs(os.path.dirname(dst), exist_ok=True)
				with open(os.path.join(dirpath, name), "rb") as fr, open(dst, "wb") as fw:
					fw.write(fr.read())

	write_file(os.path.join(pkg_root, "INDEX.md"), build_index(markdown_files, args.input, version, args.docs_sha))

	built_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
	version_md = "\n".join(
		[
			f"engine: {version}",
			f"godot_docs_sha: {args.docs_sha}",
			f"release: {release}",
			f"built_at: {built_at}",
		]
	) + "\n"
	write_file(os.path.join(pkg_root, "VERSION"), version_md)

	# .gdignore: prevents the Godot editor from scanning ~1500 generated files
	open(os.path.join(pkg_root, ".gdignore"), "wb").close()

	write_file(os.path.join(pkg_root, "LICENSE-PACKAGE.md"), LICENSE_PACKAGE)

	os.makedirs(os.path.dirname(args.tar) or ".", exist_ok=True)
	make_tar_gz(args.tar, pkg_root, f"godot-{version}-md")

	with open(args.tar, "rb") as f:
		digest = hashlib.sha256(f.read()).hexdigest()
	sums_path = os.path.join(os.path.dirname(args.tar), "SHA256SUMS")
	write_file(sums_path, f"{digest}  {os.path.basename(args.tar)}\n")

	print(f"postprocess: package {args.tar} ({len(markdown_files)} markdown files), {sums_path}")


if __name__ == "__main__":
	main()
