"""Merge the reviewers' reports into one de-duplicated review.

This is a single structured-output LLM call, not a Deep Agent: merging a list of findings needs
no planning, files, or tools, so a deep agent would only add cost and latency.
"""

from langchain_core.messages import HumanMessage, SystemMessage

from code_revup.llm import TRANSIENT_ERRORS, get_model
from code_revup.models import (
	SEVERITY_ORDER,
	FinalReview,
	PullRequest,
	ReviewComment,
	ReviewerReport,
	SynthesizerOutput,
)

_SYSTEM_PROMPT = """\
You merge the reports of several code reviewers (logic, security, lint) into one review for the
author of a pull request.

Rules:
- Merge findings that describe the same underlying problem, even when reviewers worded them
  differently or pointed at nearby lines. A merged comment lists every reviewer that reported
  it, keeps the highest severity, and combines the clearest explanation and suggestion.
- Never invent findings. Keep file paths and line numbers exactly as the reviewers gave them.
- Keep every finding that is not a duplicate. Do not judge whether it is correct; the reviewers
  had the code and you don't.
- summary: 2-4 sentences for the author, covering the overall assessment and the most
  important issues. If a reviewer failed, say its area was not reviewed."""


def _sort_key(comment: ReviewComment) -> tuple[int, str, int]:
	return SEVERITY_ORDER[comment.severity], comment.file, comment.line or 0


async def synthesize(pr: PullRequest, reports: list[ReviewerReport]) -> FinalReview:
	failed = [r.reviewer for r in reports if r.error]
	if not any(r.findings for r in reports):
		summary = 'No issues found.'
		if failed:
			summary += f' Not reviewed because the reviewer failed: {", ".join(failed)}.'
		return FinalReview(summary=summary, comments=[], failed_reviewers=failed)

	reports_json = '\n\n'.join(r.model_dump_json(indent=2) for r in reports)
	# json_schema = native structured output; the default (function_calling) forces a tool call,
	# which newer Claude models reject.
	model = get_model().with_structured_output(SynthesizerOutput, method='json_schema')
	model = model.with_retry(retry_if_exception_type=TRANSIENT_ERRORS, stop_after_attempt=4)
	output = await model.ainvoke(
		[
			SystemMessage(_SYSTEM_PROMPT),
			HumanMessage(f'PR: {pr.title}\n\nReviewer reports:\n\n{reports_json}'),
		],
	)
	assert isinstance(output, SynthesizerOutput)
	# Ordering is deterministic, so do it in code instead of trusting the LLM with it.
	comments = sorted(output.comments, key=_sort_key)
	return FinalReview(summary=output.summary, comments=comments, failed_reviewers=failed)
