"""Download a GitHub PR into a local sample directory, for running reviews without a webhook.

Layout of the sample directory:

    pr.json   PullRequest metadata
    pr.diff   the unified diff
    repo/     every changed file at the PR's head commit (deleted and binary files are skipped)
"""

import re
from pathlib import Path
from urllib.parse import quote

import httpx

from code_revup.config import get_settings
from code_revup.diff import split_diff
from code_revup.models import PullRequest

_PR_URL = re.compile(r'github\.com/([^/]+)/([^/]+)/pull/(\d+)')


def fetch_pr(pr_url: str, out_dir: Path | None = None) -> Path:
	match = _PR_URL.search(pr_url)
	if not match:
		raise ValueError(f'Not a GitHub pull request URL: {pr_url}')
	owner, repo, number = match.groups()
	out_dir = out_dir or Path('samples') / f'{owner}-{repo}-pr{number}'

	headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
	if token := get_settings().github_token:
		headers['Authorization'] = f'Bearer {token.get_secret_value()}'

	with httpx.Client(headers=headers, timeout=30, follow_redirects=True) as client:
		api_url = f'https://api.github.com/repos/{owner}/{repo}/pulls/{number}'
		meta = client.get(api_url).raise_for_status().json()
		diff = (
			client.get(api_url, headers={'Accept': 'application/vnd.github.diff'})
			.raise_for_status()
			.text
		)
		pr = PullRequest(
			title=meta['title'],
			description=meta['body'] or '',
			url=meta['html_url'],
			base_sha=meta['base']['sha'],
			head_sha=meta['head']['sha'],
		)

		repo_dir = (out_dir / 'repo').resolve()
		repo_dir.mkdir(parents=True, exist_ok=True)
		# Fetch from the head repo, which differs from the base repo when the PR comes from a fork.
		raw_base = (
			f'https://raw.githubusercontent.com/{meta["head"]["repo"]["full_name"]}/{pr.head_sha}'
		)
		for patch in split_diff(diff):
			if patch.status == 'deleted':
				continue
			target = (repo_dir / patch.path).resolve()
			if not target.is_relative_to(repo_dir):
				raise ValueError(f'Refusing to write outside {repo_dir}: {patch.path}')
			response = client.get(f'{raw_base}/{quote(patch.path)}').raise_for_status()
			try:
				content = response.content.decode()
			except UnicodeDecodeError:
				continue  # binary file
			target.parent.mkdir(parents=True, exist_ok=True)
			target.write_text(content)

	(out_dir / 'pr.diff').write_text(diff)
	(out_dir / 'pr.json').write_text(pr.model_dump_json(indent=2))
	return out_dir
