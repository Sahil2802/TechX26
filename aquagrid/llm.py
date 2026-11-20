"""One OpenAI-compatible client with an ordered provider fallback chain (Ollama / Groq / Gemini)."""
import logging
import os
import re

from openai import APITimeoutError, OpenAI

from aquagrid import config

log = logging.getLogger(__name__)

def _groq_extra(model: str) -> dict:
    """gpt-oss models reason before answering: cap it so the reasoning cannot use up max_tokens.
    Non-reasoning models reject the parameter, so only send it where it applies."""
    return {"reasoning_effort": "low"} if "gpt-oss" in model else {}


PROVIDERS = {
    "ollama": {"base_url": "http://localhost:11434/v1", "key_env": None, "model": config.OLLAMA_MODEL},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
             "model": config.GROQ_MODEL, "extra": _groq_extra(config.GROQ_MODEL)},
    # Gemini thinking counts against max_tokens, so cap it (reasoning_effort maps to Gemini's thinking level)
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "key_env": "GEMINI_API_KEY", "model": config.GEMINI_MODEL, "extra": {"reasoning_effort": "low"}},
}
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class AllProvidersFailed(Exception):
    """Every configured provider errored or timed out."""


# Documented placeholder for tool calls Gemini 3 did not generate itself (e.g. history from another provider)
GEMINI_PLACEHOLDER_SIGNATURE = "context_engineering_is_the_way_to_go"


def _gemini_history(messages: list[dict]) -> list[dict]:
    """Gemini 3 rejects (HTTP 400) a replayed step whose first tool call lacks a thought_signature.
    Keep real signatures untouched; add the documented placeholder only where one is missing.
    Returns a copy; the caller's messages are not modified."""
    out = []
    for m in messages:
        calls = m.get("tool_calls")
        if m.get("role") == "assistant" and calls and not calls[0].get("extra_content"):
            first = {**calls[0], "extra_content": {"google": {"thought_signature": GEMINI_PLACEHOLDER_SIGNATURE}}}
            m = {**m, "tool_calls": [first, *calls[1:]]}
        out.append(m)
    return out


def _brief_error(e: Exception) -> str:
    """Short, safe reason for the operator-facing brief. Full details (which can include account ids)
    go to the log only."""
    status = getattr(e, "status_code", None)
    if status:
        return f"HTTP {status}"
    return "timed out" if isinstance(e, APITimeoutError) else str(e)[:120]


def strip_think(text: str | None) -> str:
    return _THINK.sub("", text or "").strip()


def _api_key(name: str) -> str | None:
    env = PROVIDERS[name]["key_env"]
    return "ollama" if env is None else os.getenv(env)  # Ollama ignores the key but the SDK needs one


def available() -> list[str]:
    """Providers in LLM_CHAIN order that are configured (Ollama is assumed local, not probed)."""
    return [p for p in config.LLM_CHAIN if p in PROVIDERS and _api_key(p)]


def chat(messages: list[dict], tools: list[dict] | None = None):
    """Try each available provider in order. Returns (message, "provider/model")."""
    errors: list[str] = []
    for name in available():
        p = PROVIDERS[name]
        try:
            client = OpenAI(base_url=p["base_url"], api_key=_api_key(name),
                            timeout=config.LLM_TIMEOUT_S[name], max_retries=config.LLM_MAX_RETRIES)
            sent = _gemini_history(messages) if name == "gemini" else messages
            kwargs = {"model": p["model"], "messages": sent, "max_tokens": config.LLM_MAX_TOKENS,
                      **p.get("extra", {})}
            if tools:
                kwargs["tools"] = tools
            choice = client.chat.completions.create(**kwargs).choices[0]
            message = choice.message
            message.content = strip_think(message.content)
            if not message.tool_calls and (choice.finish_reason == "length" or not message.content):
                # truncated or empty and no tool call: unusable, so treat it like a provider failure
                raise ValueError(f"unusable reply (finish_reason={choice.finish_reason}, "
                                 f"{len(message.content)} chars after stripping)")
        except Exception as e:  # provider failure is expected; logged, then the next one is tried
            log.warning("LLM provider %s failed: %s", name, e)
            errors.append(f"{name}: {_brief_error(e)}")
            continue
        return message, f"{name}/{p['model']}"
    raise AllProvidersFailed("; ".join(errors) or "no LLM provider configured")
