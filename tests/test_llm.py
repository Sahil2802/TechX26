from types import SimpleNamespace

import pytest

from aquagrid import config, llm


class FakeOpenAI:
    """Stands in for openai.OpenAI. Providers listed in `failing` raise; others answer."""
    failing: set[str] = set()
    truncating: set[str] = set()  # these providers hit max_tokens before answering
    inits: list[dict] = []
    calls: list[tuple] = []

    def __init__(self, base_url, api_key, timeout, max_retries):
        self.kw = dict(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=max_retries)
        FakeOpenAI.inits.append(self.kw)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        FakeOpenAI.calls.append((self.kw["base_url"], kwargs))
        if any(name in self.kw["base_url"] for name in FakeOpenAI.failing):
            raise ConnectionError("provider down")
        if any(name in self.kw["base_url"] for name in FakeOpenAI.truncating):
            msg = SimpleNamespace(content="Okay, let me think about this", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="length")])
        msg = SimpleNamespace(content="<think>hmm</think>ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    FakeOpenAI.failing, FakeOpenAI.truncating, FakeOpenAI.inits, FakeOpenAI.calls = set(), set(), [], []
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(config, "LLM_CHAIN", ["ollama", "groq", "gemini"])
    monkeypatch.setenv("GROQ_API_KEY", "g-key")
    monkeypatch.setenv("GEMINI_API_KEY", "m-key")


def test_strip_think():
    assert llm.strip_think("<think>a\nb</think>answer") == "answer"
    assert llm.strip_think(None) == ""


def test_first_provider_answers_and_think_is_stripped():
    msg, who = llm.chat([{"role": "user", "content": "hi"}])
    assert who == f"ollama/{config.OLLAMA_MODEL}" and msg.content == "ok"


def test_falls_back_to_next_provider():
    FakeOpenAI.failing = {"11434"}
    _, who = llm.chat([])
    assert who == f"groq/{llm.PROVIDERS['groq']['model']}"


def test_providers_without_keys_are_skipped(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    FakeOpenAI.failing = {"11434"}
    _, who = llm.chat([])
    assert who == f"gemini/{llm.PROVIDERS['gemini']['model']}"
    assert llm.available() == ["ollama", "gemini"]


def test_all_providers_failed_raises():
    FakeOpenAI.failing = {"11434", "groq", "googleapis"}
    with pytest.raises(llm.AllProvidersFailed, match="ollama"):
        llm.chat([])


def test_truncated_reply_without_a_tool_call_counts_as_a_provider_failure():
    FakeOpenAI.truncating = {"11434"}  # e.g. qwen3:4b spending its whole budget reasoning
    _, who = llm.chat([])
    assert who == f"groq/{llm.PROVIDERS['groq']['model']}"


def test_every_provider_truncating_raises_instead_of_returning_garbage():
    FakeOpenAI.truncating = {"11434", "groq", "googleapis"}
    with pytest.raises(llm.AllProvidersFailed, match="unusable reply"):
        llm.chat([])


def test_a_reply_that_is_only_a_think_block_is_unusable(monkeypatch):
    class OnlyThink(FakeOpenAI):
        def _create(self, **kwargs):
            msg = SimpleNamespace(content="<think>all of it</think>", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])
    monkeypatch.setattr(llm, "OpenAI", OnlyThink)
    with pytest.raises(llm.AllProvidersFailed, match="0 chars"):
        llm.chat([])


def test_a_tool_call_with_no_text_is_a_valid_reply(monkeypatch):
    class ToolOnly(FakeOpenAI):
        def _create(self, **kwargs):
            msg = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(id="c")])
            return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")])
    monkeypatch.setattr(llm, "OpenAI", ToolOnly)
    msg, _ = llm.chat([])
    assert msg.tool_calls and msg.content == ""


def test_cloud_providers_use_the_documented_openai_compatible_endpoints():
    assert llm.PROVIDERS["groq"]["base_url"] == "https://api.groq.com/openai/v1"
    assert llm.PROVIDERS["gemini"]["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert llm.PROVIDERS["groq"]["key_env"] == "GROQ_API_KEY" and llm.PROVIDERS["gemini"]["key_env"] == "GEMINI_API_KEY"


def test_reasoning_is_capped_only_where_the_model_reasons():
    assert llm._groq_extra("openai/gpt-oss-120b") == {"reasoning_effort": "low"}
    assert llm._groq_extra("llama-3.1-8b-instant") == {}  # non-reasoning models reject the parameter
    assert llm.PROVIDERS["gemini"]["extra"] == {"reasoning_effort": "low"}
    assert "extra" not in llm.PROVIDERS["ollama"]


def test_extra_params_reach_the_request_alongside_tools_and_the_token_cap(monkeypatch):
    monkeypatch.setattr(config, "LLM_CHAIN", ["groq", "gemini"])
    FakeOpenAI.failing = {"groq"}
    llm.chat([], tools=[{"type": "function"}])
    (_, groq_kw), (_, gemini_kw) = FakeOpenAI.calls
    assert groq_kw.get("reasoning_effort") == llm.PROVIDERS["groq"]["extra"].get("reasoning_effort")
    assert gemini_kw["reasoning_effort"] == "low"
    assert gemini_kw["max_tokens"] == config.LLM_MAX_TOKENS and gemini_kw["tools"] == [{"type": "function"}]


def test_a_chain_with_only_cloud_keys_never_touches_ollama(monkeypatch):
    monkeypatch.setattr(config, "LLM_CHAIN", ["groq", "gemini", "ollama"])
    llm.chat([])
    assert [c[0] for c in FakeOpenAI.calls] == [llm.PROVIDERS["groq"]["base_url"]]


def test_provider_error_details_do_not_reach_the_operator_facing_message(monkeypatch):
    class RateLimited(FakeOpenAI):
        def _create(self, **kwargs):
            err = RuntimeError("Rate limit reached for organization org_SECRET123 on tokens per minute")
            err.status_code = 429
            raise err
    monkeypatch.setattr(llm, "OpenAI", RateLimited)
    with pytest.raises(llm.AllProvidersFailed) as exc:
        llm.chat([])
    assert "HTTP 429" in str(exc.value) and "org_SECRET123" not in str(exc.value)


def test_a_timeout_is_reported_briefly(monkeypatch):
    from openai import APITimeoutError

    class Slow(FakeOpenAI):
        def _create(self, **kwargs):
            raise APITimeoutError(request=SimpleNamespace())
    monkeypatch.setattr(llm, "OpenAI", Slow)
    with pytest.raises(llm.AllProvidersFailed, match="timed out"):
        llm.chat([])


def test_nothing_configured_raises(monkeypatch):
    monkeypatch.setattr(config, "LLM_CHAIN", ["groq"])
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(llm.AllProvidersFailed):
        llm.chat([])


def test_timeout_and_retries_come_from_config():
    llm.chat([])
    assert FakeOpenAI.inits[0]["timeout"] == config.LLM_TIMEOUT_S["ollama"]
    assert FakeOpenAI.inits[0]["max_retries"] == config.LLM_MAX_RETRIES


# ---- Gemini 3 thought signatures ---------------------------------------------------------------------

def _history(signature=None):
    call = {"id": "c1", "type": "function", "function": {"name": "read_network_telemetry", "arguments": "{}"}}
    second = {"id": "c2", "type": "function", "function": {"name": "read_tariff_and_quality_feeds", "arguments": "{}"}}
    if signature:
        call["extra_content"] = {"google": {"thought_signature": signature}}
    return [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
            {"role": "assistant", "content": "", "tool_calls": [call, second]},
            {"role": "tool", "tool_call_id": "c1", "content": "{}"}]


def test_gemini_history_gets_the_placeholder_only_where_a_signature_is_missing():
    sent = llm._gemini_history(_history())
    first, second = sent[2]["tool_calls"]
    assert first["extra_content"]["google"]["thought_signature"] == llm.GEMINI_PLACEHOLDER_SIGNATURE
    assert "extra_content" not in second  # only the first call of a step needs one


def test_a_real_gemini_signature_is_replayed_untouched():
    sent = llm._gemini_history(_history(signature="REAL-SIGNATURE"))
    assert sent[2]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "REAL-SIGNATURE"


def test_gemini_history_is_a_copy_and_leaves_other_messages_alone():
    original = _history()
    sent = llm._gemini_history(original)
    assert "extra_content" not in original[2]["tool_calls"][0]  # caller's messages not mutated
    assert [sent[i] for i in (0, 1, 3)] == [original[i] for i in (0, 1, 3)]


def test_only_gemini_receives_the_placeholder(monkeypatch):
    monkeypatch.setattr(config, "LLM_CHAIN", ["groq", "gemini"])
    FakeOpenAI.failing = {"groq"}
    llm.chat(_history())
    (_, groq_kw), (_, gemini_kw) = FakeOpenAI.calls
    assert "extra_content" not in groq_kw["messages"][2]["tool_calls"][0]
    assert gemini_kw["messages"][2]["tool_calls"][0]["extra_content"]
