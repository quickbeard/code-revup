from functools import cache

import httpx
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware
from langchain_openrouter import ChatOpenRouter
from openrouter import errors

from code_revup.config import get_settings

# Errors worth retrying. Anything else (bad request, auth, payment) fails the same way every time.
TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
	errors.TooManyRequestsResponseError,
	errors.InternalServerResponseError,
	errors.BadGatewayResponseError,
	errors.ServiceUnavailableResponseError,
	errors.ProviderOverloadedResponseError,
	errors.EdgeNetworkTimeoutResponseError,
	errors.RequestTimeoutResponseError,
	errors.NoResponseError,
	httpx.TimeoutException,
	httpx.NetworkError,
)

_REQUEST_TIMEOUT_MS = 180_000
_MAX_MODEL_CALLS_PER_AGENT = 60


@cache
def get_model() -> ChatOpenRouter:
	"""Return the chat model shared by every agent."""
	settings = get_settings()
	return ChatOpenRouter(
		model=settings.model,
		api_key=settings.api_key,
		base_url=settings.base_url,
		temperature=0,
		# Without an explicit timeout the SDK uses httpx's 5 s default, which long LLM responses
		# exceed. Its built-in retries are off: they also retry permanent errors and can stall
		# for minutes. Retries happen in agent_middleware() and the synthesizer instead.
		timeout=_REQUEST_TIMEOUT_MS,
		max_retries=0,
	)


def agent_middleware() -> list:
	"""Middleware for every reviewer agent: retry transient failures, and cap the model calls."""
	return [
		ModelRetryMiddleware(max_retries=3, retry_on=TRANSIENT_ERRORS, on_failure='error'),
		# A runaway agent fails loudly instead of quietly burning credits.
		ModelCallLimitMiddleware(run_limit=_MAX_MODEL_CALLS_PER_AGENT, exit_behavior='error'),
	]
