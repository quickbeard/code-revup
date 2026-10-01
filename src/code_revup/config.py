from functools import cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
	"""App settings, read from environment variables or `.env`."""

	# env_ignore_empty: treat `KEY=` (as copied from .env.example) as unset, not as ''.
	model_config = SettingsConfigDict(env_file='.env', extra='ignore', env_ignore_empty=True)

	api_key: SecretStr
	base_url: str = 'https://openrouter.ai/api/v1'
	# OpenRouter model id used by every agent, e.g. anthropic/claude-sonnet-5.5. Required, so the
	# model choice lives in .env instead of being hidden in code.
	model: str
	# Optional: raises GitHub's API rate limit when fetching sample PRs.
	github_token: SecretStr | None = None

	@field_validator('base_url')
	@classmethod
	def _strip_endpoint(cls, url: str) -> str:
		# The client appends `/chat/completions` itself, so accept a full endpoint URL too.
		return url.rstrip('/').removesuffix('/chat/completions')


@cache
def get_settings() -> Settings:
	return Settings()  # pyright: ignore[reportCallIssue]  # fields come from env, not args
