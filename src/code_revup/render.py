from code_revup.models import FinalReview, Severity, Verdict

_VERDICT_LABELS: dict[Verdict, str] = {
	'approve': '✅ No issues found',
	'comment': '💬 Needs attention',
	'request_changes': '⛔ Changes requested',
}
_SEVERITY_ICONS: dict[Severity, str] = {'critical': '🔴', 'high': '🟠', 'medium': '🟡', 'low': '⚪'}


def render_markdown(review: FinalReview) -> str:
	"""Render the review as the Markdown body of a PR comment."""
	lines = [
		'## code-revup review',
		'',
		f'**{_VERDICT_LABELS[review.verdict]}**',
		'',
		review.summary,
	]
	if review.failed_reviewers:
		failed = ', '.join(review.failed_reviewers)
		lines += ['', f'> ⚠️ Not reviewed because the reviewer failed: {failed}']

	for comment in review.comments:
		location = f'{comment.file}:{comment.line}' if comment.line else comment.file
		lines += [
			'',
			f'### {_SEVERITY_ICONS[comment.severity]} {comment.title}',
			f'`{location}` · {comment.severity} · found by {", ".join(comment.reviewers)}',
			'',
			comment.explanation,
		]
		if comment.suggestion:
			lines += ['', f'**Suggestion:** {comment.suggestion}']
	return '\n'.join(lines) + '\n'
