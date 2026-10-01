"""The three reviewer agents. Each is a Deep Agent with its own focus and tools."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.backends.utils import create_file_data
from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool

from code_revup.diff import FilePatch, annotate_patch
from code_revup.llm import agent_middleware, get_model
from code_revup.models import PullRequest, ReviewerName, ReviewerOutput, ReviewerReport
from code_revup.tools import make_lint_tools, make_security_tools

_MAX_DESCRIPTION_CHARS = 4_000


@dataclass(frozen=True)
class ReviewerSpec:
	name: ReviewerName
	focus: str
	make_tools: Callable[[Path], list[BaseTool]] | None = None


REVIEWERS: dict[ReviewerName, ReviewerSpec] = {
	'logic': ReviewerSpec(
		name='logic',
		focus="""\
Correctness. Look for:
- bugs: wrong conditions, off-by-one errors, wrong variables, inverted logic
- unhandled cases: null/undefined/empty input, error paths, partial failures
- broken contracts: changed signatures or behavior that existing callers rely on
- state problems: race conditions, stale caches, resource leaks, non-idempotent retries
- tests that don't actually test what they claim, or miss the risky paths of the change""",
	),
	'security': ReviewerSpec(
		name='security',
		focus="""\
Security. Look for:
- injection: SQL, shell commands, path traversal, template/code injection
- missing or broken authentication and authorization checks
- secrets, tokens, or credentials in code, config, or logs
- unsafe deserialization, SSRF, XSS, open redirects
- weak crypto or randomness, unsafe file permissions, insecure defaults

Run `run_semgrep` on the changed source files early. Treat its results as leads, not verdicts:
report a hit only if it is on or caused by lines this PR changes AND you have confirmed by reading
the code that untrusted input can actually reach it.""",
		make_tools=make_security_tools,
	),
	'lint': ReviewerSpec(
		name='lint',
		focus="""\
Lint, formatting, and readability. Look for:
- unused imports, variables, parameters, and dead code
- naming, formatting, and structure that break the conventions of the surrounding code
- misleading or outdated comments and docs, typos in identifiers and user-facing text
- needless complexity: duplicated code, deep nesting, overly long functions

Run `run_ruff` on changed Python files. For other languages, infer the project's conventions
from the unchanged code around the change. Only cite a rule code (such as F401) if it appears
in the tool output. Lint issues are usually `low` severity; use `medium`
only when readability is seriously harmed. Group repeated instances of the same issue in one
file into a single finding.""",
		make_tools=make_lint_tools,
	),
}

_SYSTEM_PROMPT = """\
You are the {name} reviewer in a multi-agent code review system. The {others} reviewers cover
their own areas, so stay within your focus.

## Your focus
{focus}

## Workspace
- /diff/<path>.patch: one patch per changed file. Every line that exists in the new file is
  prefixed with `L<n> |`, its line number in the new file; removed lines have no number.
{repo_note}
- You may write scratch notes under /notes/.

## How to work
1. Read the PR description and the list of changed files in the task message.
2. If several files changed, use write_todos to plan which ones to review.
3. Read each relevant patch. When a change could affect code the patch doesn't show, read the
   full file in /repo/ (use offset and limit for long files) or grep for related code.
4. Run your analysis tools where they apply.
5. Report only problems in lines this PR adds or changes, or problems the change causes
   elsewhere. Pre-existing problems in untouched code are out of scope.
6. Re-check each finding against the code before reporting it. Precision matters more than
   recall: one confirmed bug is worth more than five guesses. If nothing is wrong, return no
   findings.

## Reporting
- `line` is the `L<n>` number from the patch, or the line number of the file in /repo/.
- Don't praise the code or restate what the PR does."""

_REPO_NOTE = """\
- /repo/: the changed files at the PR's head commit. Read-only. It may not contain the rest of
  the repository."""
_NO_REPO_NOTE = '- /repo/ is not available for this review. Work from the patches only.'


def build_reviewer(spec: ReviewerSpec, repo_dir: Path | None):
	"""Create one reviewer agent. Agents are built per PR because tools are bound to repo_dir."""
	# CompositeBackend routes file operations by path prefix: /repo/ reads the real checkout
	# on disk, and everything else (/diff/, /notes/) lives in the agent's in-memory state.
	routes = {}
	tools = []
	if repo_dir:
		routes['/repo/'] = FilesystemBackend(root_dir=repo_dir, virtual_mode=True)
		tools = spec.make_tools(repo_dir) if spec.make_tools else []

	others = ' and '.join(name for name in REVIEWERS if name != spec.name)
	system_prompt = _SYSTEM_PROMPT.format(
		name=spec.name,
		others=others,
		focus=spec.focus,
		repo_note=_REPO_NOTE if repo_dir else _NO_REPO_NOTE,
	)
	return create_deep_agent(
		model=get_model(),
		tools=tools,
		system_prompt=system_prompt,
		middleware=agent_middleware(),
		backend=CompositeBackend(default=StateBackend(), routes=routes),
		permissions=[
			FilesystemPermission(operations=['write'], paths=['/repo/**', '/diff/**'], mode='deny'),
		],
		# ProviderStrategy uses the provider's native structured output: the model's final answer
		# is JSON constrained to the schema. (ToolStrategy, the alternative, forces a tool call
		# with tool_choice="any", which newer Claude models reject.)
		response_format=ProviderStrategy(ReviewerOutput),
		name=f'{spec.name}-reviewer',
	)


def _task_message(pr: PullRequest, patches: list[FilePatch]) -> str:
	description = pr.description[:_MAX_DESCRIPTION_CHARS] or '(no description)'
	files = '\n'.join(f'- /diff/{p.path}.patch ({p.status})' for p in patches)
	return (
		f'Review this pull request.\n\n# {pr.title}\n\n{description}\n\n## Changed files\n{files}'
	)


async def run_reviewer(
	name: ReviewerName,
	pr: PullRequest,
	patches: list[FilePatch],
	repo_dir: Path | None,
) -> ReviewerReport:
	agent = build_reviewer(REVIEWERS[name], repo_dir)
	result = await agent.ainvoke(
		{
			'messages': [HumanMessage(_task_message(pr, patches))],
			# Seed the agent's virtual filesystem (StateBackend) with the annotated patches.
			'files': {
				f'/diff/{p.path}.patch': create_file_data(annotate_patch(p.patch)) for p in patches
			},
		},
	)
	output: ReviewerOutput = result['structured_response']
	return ReviewerReport(reviewer=name, **output.model_dump())
