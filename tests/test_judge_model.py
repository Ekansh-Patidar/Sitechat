"""eval_judge_model: the evaluation judge can use a stronger model than the
bot; the bot never does."""

from unittest.mock import MagicMock, patch

from app.agent import llm
from app.config import get_settings
from evaluation.faithfulness import _judge, judge_model


def test_judge_uses_the_bots_model_by_default(monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_judge_model", "")
    assert judge_model() == get_settings().openai_model


def test_judge_uses_its_own_model_when_set(monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_judge_model", "gpt-4o")
    with patch("evaluation.faithfulness.complete", return_value='{"supported": true}') as complete:
        assert _judge("system", "content") == {"supported": True}
    assert complete.call_args.kwargs["model"] == "gpt-4o"


def test_the_bot_keeps_its_model_whatever_the_judge_uses(monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_judge_model", "gpt-4o")
    client = MagicMock()
    client.chat.completions.create.return_value.choices = [MagicMock()]
    client.chat.completions.create.return_value.usage = None
    with patch("app.agent.llm._get_client", return_value=client):
        llm.complete([{"role": "user", "content": "hi"}])
    assert client.chat.completions.create.call_args.kwargs["model"] == get_settings().openai_model
