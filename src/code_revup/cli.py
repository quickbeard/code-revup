import argparse
import asyncio
import json
import logging
from pathlib import Path

from langchain_core.messages import AIMessage
from pydantic import TypeAdapter

from code_revup.graph import graph
from code_revup.models import FinalReview, PullRequest, ReviewerReport
from code_revup.platforms.github import fetch_pr
from code_revup.render import render_markdown


def _print_tool_calls(namespace: tuple[str, ...], update: dict) -> None:
	"""Print the tool calls an agent makes, as they stream out of the nested agent graphs."""
	# namespace is the path of nested graphs, e.g. ('security:<id>',) for a reviewer's own
	# steps, or ('security:<id>', 'tools:<id>') for a subagent it started with the task tool.
	label = namespace[0].split(':')[0] + ('/subagent' if len(namespace) > 1 else '')
	for node_update in update.values():
		messages = node_update.get('messages') if isinstance(node_update, dict) else None
		for message in messages if isinstance(messages, list) else []:
			if isinstance(message, AIMessage):
				for call in message.tool_calls:
					args = json.dumps(call['args'], ensure_ascii=False)
					print(f'  [{label}] {call["name"]} {args[:100]}')


async def _review(sample_dir: Path) -> None:
	repo_dir = sample_dir / 'repo'
	inputs = {
		'pr': PullRequest.model_validate_json((sample_dir / 'pr.json').read_text()),
		'diff': (sample_dir / 'pr.diff').read_text(),
		'repo_dir': str(repo_dir) if repo_dir.is_dir() else None,
	}
	reports: list[ReviewerReport] = []
	review: FinalReview | None = None
	# stream_mode='updates' yields each node's output as it finishes; subgraphs=True also
	# yields updates from inside the reviewer agents, so you can watch them work.
	async for namespace, update in graph.astream(inputs, stream_mode='updates', subgraphs=True):
		if namespace:
			_print_tool_calls(namespace, update)
			continue
		for node, output in update.items():
			if node == 'prepare':
				print(f'Reviewing {len(output["patches"])} files...')
			elif node == 'synthesize':
				review = output['review']
			else:
				report = output['reports'][0]
				reports.append(report)
				status = (
					f'failed: {report.error}'
					if report.error
					else f'{len(report.findings)} findings'
				)
				print(f'✓ {node} reviewer done ({status})')

	assert review is not None
	markdown = render_markdown(review)
	(sample_dir / 'review.md').write_text(markdown)
	(sample_dir / 'review.json').write_text(review.model_dump_json(indent=2))
	reports_json = TypeAdapter(list[ReviewerReport]).dump_json(reports, indent=2)
	(sample_dir / 'reports.json').write_bytes(reports_json)
	print(f'\n{markdown}\nSaved review.md, review.json, and reports.json to {sample_dir}')


def main() -> None:
	logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')
	parser = argparse.ArgumentParser(prog='code-revup')
	commands = parser.add_subparsers(dest='command', required=True)

	fetch = commands.add_parser('fetch', help='Download a GitHub PR into a sample directory.')
	fetch.add_argument('pr_url')
	fetch.add_argument('--out', type=Path, help='Default: samples/<owner>-<repo>-pr<number>')

	review = commands.add_parser('review', help='Review a sample directory created by fetch.')
	review.add_argument('sample_dir', type=Path)

	args = parser.parse_args()
	if args.command == 'fetch':
		out_dir = fetch_pr(args.pr_url, args.out)
		print(f'Saved to {out_dir}')
	elif args.command == 'review':
		asyncio.run(_review(args.sample_dir))
