"""Data shapes shared by the agents, the graph, and the output renderers."""

from typing import Literal

from pydantic import BaseModel, Field, computed_field

Severity = Literal['critical', 'high', 'medium', 'low']
ReviewerName = Literal['logic', 'security', 'lint']
Verdict = Literal['approve', 'comment', 'request_changes']

SEVERITY_ORDER: dict[Severity, int] = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}


class PullRequest(BaseModel):
	"""Platform-neutral PR/MR metadata. GitHub and GitLab adapters both produce this."""

	title: str
	description: str = ''
	url: str | None = None
	base_sha: str | None = None
	head_sha: str | None = None


class Finding(BaseModel):
	"""One issue reported by a reviewer agent. The field descriptions are shown to the LLM."""

	file: str = Field(description='Path relative to the repository root, exactly as in the diff.')
	line: int | None = Field(
		description=(
			'Line number in the NEW version of the file: the L<n> marker in the patch, or the line '
			'number of the file in /repo/. Null if the issue is not tied to one line.'
		),
	)
	severity: Severity = Field(
		description=(
			'critical: exploitable or data-losing, must not merge. high: a real bug or '
			'vulnerability. medium: likely problem or risky pattern. low: minor issue.'
		),
	)
	title: str = Field(description='One-line summary of the issue.')
	explanation: str = Field(
		description='What is wrong, why it matters, and evidence from the code.'
	)
	suggestion: str | None = Field(
		default=None,
		description='A concrete fix. May include a short code snippet.',
	)


class ReviewerOutput(BaseModel):
	"""What each reviewer agent must return (its `response_format`)."""

	summary: str = Field(description='Two or three sentences on what you checked and found.')
	findings: list[Finding] = Field(
		description='Issues found. An empty list is a valid answer when nothing is wrong.',
	)


class ReviewerReport(ReviewerOutput):
	"""A reviewer's output, tagged with who produced it. This is what the graph stores."""

	reviewer: ReviewerName
	error: str | None = None


class ReviewComment(Finding):
	"""A de-duplicated finding in the final review."""

	reviewers: list[ReviewerName] = Field(description='Every reviewer that reported this issue.')


class SynthesizerOutput(BaseModel):
	"""What the synthesizer LLM must return."""

	summary: str = Field(description='A short overall summary of the review for the PR author.')
	comments: list[ReviewComment] = Field(
		description='De-duplicated findings, ordered from most to least severe.',
	)


class FinalReview(SynthesizerOutput):
	"""The synthesizer's output plus fields computed in code rather than by the LLM."""

	failed_reviewers: list[ReviewerName] = []

	@computed_field
	@property
	def verdict(self) -> Verdict:
		severities = {c.severity for c in self.comments}
		if severities & {'critical', 'high'}:
			return 'request_changes'
		# Never approve when a reviewer failed: part of the PR went unchecked.
		return 'comment' if severities or self.failed_reviewers else 'approve'
