"""Deterministic analyzers the reviewer agents can call.

Static analyzers find issues reliably; the agent's job is to run them on the right files, discard
noise, and explain what matters. Each factory binds the tools to one PR's checkout directory.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from langchain_core.tools import BaseTool, tool

_MAX_OUTPUT_CHARS = 20_000
_RUFF_CONFIG_FILES = ('ruff.toml', '.ruff.toml', 'pyproject.toml')


def _resolve(repo_dir: Path, paths: list[str]) -> list[str]:
	"""Map agent-supplied paths ('/repo/src/a.py' or 'src/a.py') to existing files in repo_dir."""
	resolved = []
	for path in paths:
		relative = path.removeprefix('/repo/').lstrip('/')
		full = (repo_dir / relative).resolve()
		if full.is_relative_to(repo_dir) and full.is_file():
			resolved.append(relative)
	return resolved


def _run(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
	return subprocess.run(
		cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
	)


def _truncate(text: str) -> str:
	if len(text) <= _MAX_OUTPUT_CHARS:
		return text
	return text[:_MAX_OUTPUT_CHARS] + '\n... (output truncated)'


def make_lint_tools(repo_dir: Path) -> list[BaseTool]:
	repo_dir = repo_dir.resolve()
	# Use the reviewed repo's own Ruff config if it has one; otherwise ignore any config Ruff
	# would find by walking up the directory tree (such as this project's own pyproject.toml).
	has_config = any((repo_dir / name).is_file() for name in _RUFF_CONFIG_FILES)
	config_args = [] if has_config else ['--isolated']

	@tool
	def run_ruff(paths: list[str]) -> str:
		"""Lint and format-check Python files with Ruff.

		Args:
			paths: Python files to check, e.g. ["/repo/src/app.py"]. Non-Python files are ignored.
		"""
		files = [p for p in _resolve(repo_dir, paths) if p.endswith(('.py', '.pyi'))]
		if not files:
			return 'No Python files to check. Ruff only supports Python.'
		ruff = [sys.executable, '-m', 'ruff']
		check = _run(
			[*ruff, 'check', '--output-format=concise', '--no-cache', *config_args, *files],
			repo_dir,
			timeout=120,
		)
		fmt = _run([*ruff, 'format', '--diff', '--no-cache', *config_args, *files], repo_dir, 120)
		return _truncate(
			f'## ruff check\n{check.stdout or check.stderr or "No issues."}\n\n'
			f'## ruff format --diff\n{fmt.stdout or "Already formatted."}\n{fmt.stderr}'
		)

	return [run_ruff]


def make_security_tools(repo_dir: Path) -> list[BaseTool]:
	repo_dir = repo_dir.resolve()

	@tool
	def run_semgrep(paths: list[str]) -> str:
		"""Scan files for security issues with Semgrep's default ruleset (many languages).

		Args:
			paths: Files to scan, e.g. ["/repo/src/server.ts"].
		"""
		semgrep = shutil.which('semgrep')
		if semgrep is None:
			return 'Semgrep is not installed. Review the code manually.'
		files = _resolve(repo_dir, paths)
		if not files:
			return 'None of the given paths exist in /repo/.'
		result = _run(
			[semgrep, 'scan', '--config=p/default', '--json', '--quiet', '--metrics=off', *files],
			repo_dir,
			timeout=300,
		)
		try:
			report = json.loads(result.stdout)
		except json.JSONDecodeError:
			return _truncate(f'Semgrep failed (exit {result.returncode}):\n{result.stderr}')
		hits = [
			f'{r["path"]}:{r["start"]["line"]} [{r["extra"]["severity"]}] {r["check_id"]}\n'
			f'  {r["extra"]["message"].strip()}'
			for r in report.get('results', [])
		]
		return _truncate('\n'.join(hits) or f'No findings in {len(files)} file(s).')

	return [run_semgrep]
