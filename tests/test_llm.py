import json
import logging

import pytest

from agent_team import core


class Resp:
    """A minimal OpenAI-compatible chat completion response."""
    def __init__(self, content, finish="stop"):
        self.status_code, self.reason_phrase = 200, "OK"
        self._d = {"choices": [{"message": {"content": content}, "finish_reason": finish}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        self.text = json.dumps(self._d)

    def json(self):
        return self._d


@pytest.fixture
def llm(monkeypatch):
    for k, v in {"LLM_BASE_URL": "https://llm.example/v1", "LLM_API_KEY": "k", "LLM_MODEL": "m"}.items():
        monkeypatch.setenv(k, v)
    for k in ("LLM_FALLBACK_BASE_URL", "LLM_FALLBACK_API_KEY", "LLM_FALLBACK_MODEL"):
        monkeypatch.delenv(k, raising=False)
    sent, replies = [], []

    def post(url, timeout, headers, json):
        sent.append(json)
        return replies.pop(0)
    monkeypatch.setattr(core.httpx, "post", post)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    return sent, replies


def test_untruncated_reply_is_one_call_with_unchanged_payload(llm):
    sent, replies = llm
    replies.append(Resp("hi"))
    assert core._chat("sys", "user", 4000) == "hi"
    assert sent == [{"model": "m", "max_tokens": 4000, "temperature": 0.4,
                     "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}]}]


def test_truncated_reply_is_retried_with_the_same_max_tokens(llm):
    sent, replies = llm
    replies += [Resp("cut", "length"), Resp("full")]
    assert core._chat("sys", "user", 4000) == "full"
    assert [s["max_tokens"] for s in sent] == [4000, 4000]


def test_truncated_twice_raises_llm_truncated(llm):
    sent, replies = llm
    replies += [Resp("cut", "length"), Resp("cut again", "length")]
    with pytest.raises(core.LLMTruncated):
        core._chat("sys", "user", 4000)
    assert [s["max_tokens"] for s in sent] == [4000, 4000]


def test_empty_reply_cut_off_for_length_is_retried_with_the_same_max_tokens(llm):
    sent, replies = llm
    replies += [Resp("", "length"), Resp("full")]       # all budget spent on hidden reasoning, then a real reply
    assert core._chat("sys", "user", 4000) == "full"
    assert [s["max_tokens"] for s in sent] == [4000, 4000]


def test_empty_reply_that_was_not_cut_off_is_still_an_error(llm):
    sent, replies = llm
    replies.append(Resp("", "stop"))
    with pytest.raises(core.LLMError, match="empty reply"):
        core._chat("sys", "user", 4000)
    assert len(sent) == 1


def test_anthropic_without_a_key_is_a_clear_llm_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(core, "_client", None)
    with pytest.raises(core.LLMError, match="ANTHROPIC_API_KEY"):
        core.ask("sys", "user", provider="anthropic")


def test_llm_options_apply_to_that_call_only(llm):
    sent, replies = llm
    replies += [Resp("a"), Resp("b")]
    core.ask("sys", "user", llm_options={"reasoning_effort": "low"})
    core.ask("sys", "user")
    assert sent[0]["reasoning_effort"] == "low" and "reasoning_effort" not in sent[1]


def test_each_call_is_logged_with_part_tokens_finish_and_duration(llm):
    sent, replies = llm
    replies += [Resp("cut", "length"), Resp("full")]
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    core.llm_log.addHandler(handler)
    try:
        core.ask("sys", "user", label="explainer video")
    finally:
        core.llm_log.removeHandler(handler)
    lines = [r.getMessage() for r in records]
    assert len(lines) == 2
    for line in lines:
        assert "part=explainer video" in line and "prompt_tokens=10" in line and "completion_tokens=5" in line
        assert "duration=" in line
    assert "finish_reason=length" in lines[0]
    assert "finish_reason=stop" in lines[1] and "(truncation retry)" in lines[1]
