"""Regression tests for provider error redaction.

Gemini API credentials are carried in a URL query parameter. HTTP client
exceptions can include the complete URL, so exception strings must never be
persisted into mission state or printed in strict-mode errors.
"""

from __future__ import annotations

import httpx
import pytest

from mcp_and_crews import llm_factory


@pytest.mark.parametrize("strict_mode", [False, True])
def test_gemini_provider_errors_do_not_leak_query_credentials(monkeypatch, strict_mode: bool) -> None:
    secret = "example-secret-do-not-log"
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("VERITY_OFFLINE", "0")
    monkeypatch.setenv("VERITY_USE_CREWAI", "0")
    monkeypatch.setenv("VERITY_STRICT_LLM", "1" if strict_mode else "0")

    request = httpx.Request(
        "POST",
        "https://generativelanguage.googleapis.com/v1beta/models/test:generateContent?key=" + secret,
    )
    response = httpx.Response(403, request=request)

    def fail_with_http_error(prompt: str) -> str:
        raise httpx.HTTPStatusError("provider rejected request", request=request, response=response)

    monkeypatch.setattr(llm_factory, "_gemini", fail_with_http_error)

    if strict_mode:
        with pytest.raises(RuntimeError) as caught:
            llm_factory.complete_text("safe test prompt", purpose="planner_notes")
        message = str(caught.value)
    else:
        result = llm_factory.complete_text("safe test prompt", purpose="planner_notes")
        assert result.provider == "deterministic"
        assert result.warning is not None
        message = result.warning

    assert secret not in message
    assert "HTTP 403" in message
    assert "generativelanguage.googleapis.com" not in message
