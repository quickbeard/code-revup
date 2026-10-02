"""The LangGraph workflow: prepare -> 3 reviewers in parallel -> synthesize.

START -> prepare -+-> logic ----+
                  +-> security -+-> synthesize -> END
                  +-> lint -----+
"""

import logging
import operator
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from code_revup.agents.reviewers import REVIEWERS, run_reviewer
from code_revup.agents.synthesizer import synthesize
from code_revup.diff import FilePatch, split_diff
from code_revup.models import FinalReview, PullRequest, ReviewerName, ReviewerReport

logger = logging.getLogger(__name__)

# Generated files that are noise in a review.
_SKIPPED_FILES = {
	'package-lock.json',
	'yarn.lock',
	'pnpm-lock.yaml',
	'bun.lock',
	'uv.lock',
	'poetry.lock',
	'Cargo.lock',
	'go.sum',
	'composer.lock',
	'Gemfile.lock',
}
_SKIPPED_SUFFIXES = ('.min.js', '.min.css', '.map')


# The reviewers run in parallel and each returns {'reports': [its_report]}. The reducer
# (operator.add) tells LangGraph to concatenate those updates instead of overwriting.
Reports = Annotated[list[ReviewerReport], operator.add]


class ReviewInput(TypedDict):
	pr: PullRequest
	diff: str
	repo_dir: str | None


class ReviewOutput(TypedDict):
	reports: Reports
	review: FinalReview


class ReviewState(ReviewInput, ReviewOutput):
	patches: list[FilePatch]


def prepare(state: ReviewState) -> dict:
	patches = [
		p
		for p in split_diff(state['diff'])
		if Path(p.path).name not in _SKIPPED_FILES and not p.path.endswith(_SKIPPED_SUFFIXES)
	]
	return {'patches': patches}


def make_reviewer_node(name: ReviewerName):
	async def review(state: ReviewState) -> dict:
		repo_dir = Path(state['repo_dir']) if state['repo_dir'] else None
		try:
			report = await run_reviewer(name, state['pr'], state['patches'], repo_dir)
		except Exception as exc:
			# One failing reviewer shouldn't sink the whole review; the synthesizer reports it.
			logger.exception('%s reviewer failed', name)
			report = ReviewerReport(
				reviewer=name, summary='', findings=[], error=f'{type(exc).__name__}: {exc}'
			)
		return {'reports': [report]}

	return review


async def synthesize_node(state: ReviewState) -> dict:
	return {'review': await synthesize(state['pr'], state['reports'])}


def build_graph():
	builder = StateGraph(ReviewState, input_schema=ReviewInput, output_schema=ReviewOutput)
	builder.add_node('prepare', prepare)
	builder.add_node('synthesize', synthesize_node)
	builder.add_edge(START, 'prepare')
	for name in REVIEWERS:
		builder.add_node(name, make_reviewer_node(name))
		builder.add_edge('prepare', name)
	# Passing a list waits for ALL reviewers to finish before synthesize runs (a join).
	builder.add_edge(list(REVIEWERS), 'synthesize')
	builder.add_edge('synthesize', END)
	return builder.compile()


graph = build_graph()
