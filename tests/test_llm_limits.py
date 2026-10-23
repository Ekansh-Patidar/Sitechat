"""Model calls fit inside Cloud Run's 60 s request (backlog 85): per-attempt
timeouts, one retry, one deadline for the whole reply, and a reply length cap.
"""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from openai import OpenAI
from pydantic import SecretStr

import app.agent.llm as llm
import app.retrieval.embeddings as embeddings
from app.agent.orchestrator import _run_with_tools
from app.config import get_settings
from app.tenants.models import Tenant


def fake_response(content="Hi.", finish_reason="stop", tool_calls=None):
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason
    response = MagicMock()
    response.choices = [choice]
    return response


@pytest.fixture
def create():
    """The OpenAI create() call, faked; returns what the test sets."""
    client = MagicMock()
    with patch.object(llm, "_get_client", return_value=client):
        yield client.chat.completions.create


@pytest.fixture
def clock(monkeypatch):
    """time.monotonic() in llm, set by the test."""
    now = {"t": 1000.0}
    monkeypatch.setattr(llm.time, "monotonic", lambda: now["t"])
    return now


# --- what each call is given ---------------------------------------------------


def test_reply_calls_carry_the_token_cap_and_a_timeout(create):
    create.return_value = fake_response()
    llm.complete_with_tools([{"role": "user", "content": "hi"}], [])
    kwargs = create.call_args.kwargs
    assert kwargs["max_tokens"] == get_settings().reply_max_tokens
    assert kwargs["timeout"] == get_settings().openai_timeout_seconds


def test_with_plenty_of_time_each_attempt_gets_the_full_timeout(create, clock):
    create.return_value = fake_response()
    llm.complete_with_tools([], [], deadline=clock["t"] + 40)
    assert create.call_args.kwargs["timeout"] == 15.0


def test_near_the_deadline_attempts_share_what_is_left(create, clock):
    create.return_value = fake_response()
    llm.complete_with_tools([], [], deadline=clock["t"] + 10)
    # One retry: two attempts, 5 s each, so both fit in the 10 s left.
    assert create.call_args.kwargs["timeout"] == 5.0


def test_past_the_deadline_no_call_is_made(create, clock):
    with pytest.raises(llm.LLMError) as error:
        llm.complete_with_tools([], [], deadline=clock["t"] + 1)
    assert error.value.user_message == llm.TOO_SLOW
    create.assert_not_called()


def test_every_tool_round_shares_one_deadline():
    call = MagicMock()
    call.id = "c1"
    call.function.name = "contact_team"
    call.function.arguments = json.dumps({"reason": "x"})
    call.model_dump.return_value = {"id": "c1", "type": "function",
                                    "function": {"name": "contact_team", "arguments": "{}"}}
    asked, answered = MagicMock(), MagicMock()
    asked.content, asked.tool_calls = None, [call]
    answered.content, answered.tool_calls = "Done.", None
    tenant = Tenant(tenant_id="t1", site_key="k", display_name="School")

    with patch("app.agent.orchestrator.complete_with_tools",
               side_effect=[asked, answered]) as model:
        assert _run_with_tools([], tenant, "c1", None, "contact me") == "Done."

    deadlines = [c.kwargs["deadline"] for c in model.call_args_list]
    assert len(deadlines) == 2 and deadlines[0] == deadlines[1]


# --- the real SDK: retries and timeouts -----------------------------------------


def test_a_timed_out_call_is_tried_twice_then_fails_cleanly(monkeypatch):
    attempts = []

    def network(request):
        attempts.append(request)
        raise httpx.ReadTimeout("slow", request=request)

    client = OpenAI(
        api_key="sk-test", max_retries=get_settings().openai_max_retries,
        http_client=httpx.Client(transport=httpx.MockTransport(network)),
    )
    monkeypatch.setattr(llm, "_client", client)
    monkeypatch.setattr(client, "_calculate_retry_timeout", lambda *a, **k: 0)

    with pytest.raises(llm.LLMError) as error:
        llm.complete_with_tools([{"role": "user", "content": "hi"}], [])
    assert error.value.user_message == llm.TOO_SLOW
    assert len(attempts) == 2


def test_clients_are_built_with_short_timeouts(monkeypatch):
    settings = get_settings()
    # The SDK refuses to build a client without a key (CI has no .env).
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("sk-test"))
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(embeddings, "_client", None)
    chat, embed = llm._get_client(), embeddings._get_client()
    assert (chat.timeout, chat.max_retries) == (15.0, 1)
    assert (embed.timeout, embed.max_retries) == (settings.embedding_timeout_seconds, 1)
    # The whole budget: embedding (two attempts) plus the reply's deadline.
    assert 2 * settings.embedding_timeout_seconds + settings.reply_deadline_seconds < 55


# --- the length cap -------------------------------------------------------------


def test_a_reply_cut_off_by_the_cap_ends_on_a_whole_sentence(create):
    create.return_value = fake_response(
        "Our 200-hour course runs in March. It costs $1,800 and inclu",
        finish_reason="length",
    )
    message = llm.complete_with_tools([], [])
    assert message.content == "Our 200-hour course runs in March."


@pytest.mark.parametrize("text, expected", [
    ("One. Two thr", "One."),
    # A list cut part-way goes, rather than ending on half an item.
    ("Para one.\n\nWe offer:\n- Hatha\n- Vin", "Para one."),
    ("Courses:\n\n1. Hatha\n2. Vinyasa\n3. Yin is", "Courses:"),
    ("Is it open? Yes! It opens in Ma", "Is it open? Yes!"),
    ("no sentence end at all", "no sentence end at all"),
])
def test_trim_to_sentence(text, expected):
    assert llm.trim_to_sentence(text) == expected


def test_a_complete_reply_is_left_alone(create):
    create.return_value = fake_response("Short answer. No cut", finish_reason="stop")
    assert llm.complete_with_tools([], []).content == "Short answer. No cut"
