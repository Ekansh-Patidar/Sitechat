"""A thin wrapper around the OpenAI chat API.

One place to configure the client, one place to turn API failures into
messages a visitor can read. Keeping this separate means the orchestration
layer never touches the OpenAI SDK directly, which makes it easy to test.
"""

import contextlib
import contextvars
import logging
import re
import time
from typing import Any, cast

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """Build the client once, on first use."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = OpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )
    return _client


# Below this much time left, a call is not worth starting: fail cleanly now.
MIN_CALL_SECONDS = 3.0

TOO_SLOW = "That took longer than it should. Please try again."


def _timeout_until(deadline: float | None) -> float:
    """The per-attempt timeout that keeps every attempt inside the deadline.

    Each attempt (the first and its retries) gets an equal share of the
    time left, capped at openai_timeout_seconds.
    """
    settings = get_settings()
    if deadline is None:
        return settings.openai_timeout_seconds
    remaining = deadline - time.monotonic()
    if remaining < MIN_CALL_SECONDS:
        logger.error("No time left for another model call")
        raise LLMError(TOO_SLOW)
    attempts = settings.openai_max_retries + 1
    return min(settings.openai_timeout_seconds, remaining / attempts)


def trim_to_sentence(text: str) -> str:
    """Cut a reply that hit the token limit back to its last whole sentence
    or paragraph, whichever ends later, so it does not stop mid-word.
    Unchanged if there is nowhere to cut."""
    paragraph = text.rfind("\n\n")
    # Not "1." in a numbered list.
    ends = [m.end() for m in re.finditer(r"(?<!\d)[.!?](?=\s|$)", text)]
    cut = max(paragraph, ends[-1] if ends else -1)
    return text[:cut].rstrip() if cut > 0 else text


# Tokens used by the calls made inside tracking_usage(), for the daily budget
# (app/usage/budget.py).
_usage: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "llm_usage", default=None
)


@contextlib.contextmanager
def tracking_usage():
    """Count the tokens of every model call inside the block: holder[0]."""
    holder = [0]
    token = _usage.set(holder)
    try:
        yield holder
    finally:
        _usage.reset(token)


def _count(response) -> None:
    holder = _usage.get()
    total = getattr(getattr(response, "usage", None), "total_tokens", None)
    if holder is not None and isinstance(total, int):
        holder[0] += total


class LLMError(Exception):
    """Raised when the model cannot be reached. Carries a visitor-safe message."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


def complete(
    messages: list[dict], json_mode: bool = False, model: str = ""
) -> str:
    """Send a conversation to the model and return its reply text.

    json_mode makes the model return a single JSON object. The messages
    must ask for JSON, or the API rejects the request. model: empty uses
    openai_model; the evaluation judge may use another (eval_judge_model).
    """
    settings = get_settings()
    extra: dict[str, Any] = {"response_format": {"type": "json_object"}} if json_mode else {}
    try:
        response = _get_client().chat.completions.create(
            model=model or settings.openai_model,
            messages=cast("list[ChatCompletionMessageParam]", messages),
            **extra,
        )
        _count(response)
        return response.choices[0].message.content or ""
    except RateLimitError as exc:
        logger.error("OpenAI rate limit hit")
        raise LLMError("We're a bit busy right now. Please try again in a moment.") from exc
    except APITimeoutError as exc:
        logger.error("OpenAI timed out")
        raise LLMError(TOO_SLOW) from exc
    except APIConnectionError as exc:
        logger.error("Could not reach OpenAI")
        raise LLMError("I couldn't reach the service. Please try again.") from exc
    except APIError as exc:
        logger.exception("OpenAI API error")
        raise LLMError("Something went wrong. Please try again.") from exc


def complete_with_tools(
    messages: list[dict], tools: list[dict], deadline: float | None = None
):
    """Send a conversation with tool definitions, returning the raw message.

    Unlike complete(), this returns the message object rather than text,
    because the caller needs to distinguish a tool request from a reply.

    deadline (a time.monotonic() value) bounds the call, retries included;
    a reply that hits reply_max_tokens is trimmed to its last sentence.
    """
    settings = get_settings()
    timeout = _timeout_until(deadline)
    try:
        response = _get_client().chat.completions.create(
            model=settings.openai_model,
            messages=cast("list[ChatCompletionMessageParam]", messages),
            tools=cast("list[ChatCompletionToolParam]", tools),
            max_tokens=settings.reply_max_tokens,
            timeout=timeout,
        )
        _count(response)
        choice = response.choices[0]
        message = choice.message
        if choice.finish_reason == "length":
            logger.warning("Reply hit the %d-token limit", settings.reply_max_tokens)
            if message.content and not message.tool_calls:
                message.content = trim_to_sentence(message.content)
        return message
    except RateLimitError as exc:
        logger.error("OpenAI rate limit hit")
        raise LLMError("We're a bit busy right now. Please try again in a moment.") from exc
    except APITimeoutError as exc:
        logger.error("OpenAI timed out")
        raise LLMError(TOO_SLOW) from exc
    except APIConnectionError as exc:
        logger.error("Could not reach OpenAI")
        raise LLMError("I couldn't reach the service. Please try again.") from exc
    except APIError as exc:
        logger.exception("OpenAI API error")
        raise LLMError("Something went wrong. Please try again.") from exc
