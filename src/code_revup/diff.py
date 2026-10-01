"""Split a unified git diff into per-file patches, and number their lines for the LLM."""

import re
from typing import Literal

from pydantic import BaseModel

FileStatus = Literal['added', 'modified', 'deleted', 'renamed']

_DIFF_HEADER = re.compile(r'^diff --git a/(.+) b/(.+)$')
_HUNK_HEADER = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@')


class FilePatch(BaseModel):
	path: str
	status: FileStatus
	patch: str


def split_diff(diff: str) -> list[FilePatch]:
	"""Split `git diff` output into one patch per file."""
	patches = []
	for chunk in re.split(r'(?m)^(?=diff --git )', diff):
		if not chunk.startswith('diff --git '):
			continue
		lines = chunk.splitlines()
		header = _DIFF_HEADER.match(lines[0])
		old_path, new_path = header.groups() if header else (None, None)
		status: FileStatus = 'modified'
		for line in lines[1:]:
			if line.startswith('@@'):
				break
			if line.startswith('new file mode'):
				status = 'added'
			elif line.startswith('deleted file mode'):
				status = 'deleted'
			elif line.startswith('rename to '):
				status, new_path = 'renamed', line.removeprefix('rename to ')
			elif line.startswith('+++ b/'):
				new_path = line.removeprefix('+++ b/')
			elif line.startswith('--- a/'):
				old_path = line.removeprefix('--- a/')
		path = old_path if status == 'deleted' else new_path
		if path:
			patches.append(FilePatch(path=path, status=status, patch=chunk))
	return patches


def annotate_patch(patch: str) -> str:
	"""Prefix each hunk line with its line number in the new file.

	LLMs are bad at counting lines from `@@ -a,b +c,d @@` headers, and review comments need
	exact line numbers, so the numbers are computed here. Context and added lines get an `L<n>`
	marker; removed lines get none because they don't exist in the new file. The `L` keeps these
	apart from the line numbers Deep Agents' `read_file` adds on its own.
	"""
	out = []
	new_line: int | None = None
	for line in patch.splitlines():
		if hunk := _HUNK_HEADER.match(line):
			new_line = int(hunk.group(1))
			out.append(line)
		elif new_line is None or line.startswith('\\'):
			out.append(line)
		elif line.startswith('-'):
			out.append(f'{"":>7} | {line}')
		else:
			# '+' lines and context lines (' ') both exist in the new file.
			out.append(f'{f"L{new_line}":>7} | {line}')
			new_line += 1
	return '\n'.join(out) + '\n'
