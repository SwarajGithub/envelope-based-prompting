import httpx
import pytest

from fenced_retry.llm.client import (
    BudgetExceeded,
    DigestMismatch,
    LLMClient,
    LLMSettings,
    OllamaUnavailable,
)

DIGEST = "500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41"
MESSAGES = [{"role": "user", "content": "Reply with OK."}]


def _tags(digest=DIGEST):
    return httpx.Response(200, json={"models": [{"name": "qwen3:8b", "model": "qwen3:8b", "digest": digest}]})


def _chat_ok(**extra):
    body = {
        "model": "qwen3:8b",
        "created_at": "2026-10-19T00:00:00Z",
        "message": {"role": "assistant", "content": "OK"},
        "done": True,
        "load_duration": 2_000_000_000,
        "prompt_eval_count": 20,
        "eval_count": 3,
    }
    body.update(extra)
    return httpx.Response(200, json=body)


def _client(tmp_path, handler, **settings):
    cfg = LLMSettings(usage_db=tmp_path / "usage.sqlite", **settings)
    http = httpx.Client(base_url=cfg.endpoint, transport=httpx.MockTransport(handler))
    return LLMClient(cfg, http=http)


def test_ok_call_is_recorded_and_hashes_only_by_default(tmp_path):
    def handler(request):
        if request.url.path == "/api/tags":
            return _tags()
        return _chat_ok()

    client = _client(tmp_path, handler, expected_digest=DIGEST)
    result = client.chat(MESSAGES, project_name=None)
    totals = client.tracker.totals()
    client.close()
    assert result.status == "ok"
    assert result.text == "OK"
    assert result.thinking is None
    assert result.input_tokens == 20 and result.output_tokens == 3
    assert result.load_ms == 2000.0
    assert totals["calls"] == 1


def test_stored_row_has_hashes_and_no_text_by_default(tmp_path):
    import sqlite3

    def handler(request):
        return _tags() if request.url.path == "/api/tags" else _chat_ok()

    client = _client(tmp_path, handler)
    client.chat(MESSAGES, project_name="pilot")
    client.close()
    conn = sqlite3.connect(tmp_path / "usage.sqlite")
    row = conn.execute(
        "SELECT project_name, model_digest, prompt_hash, output_hash, output_text, input_text, store_full_text FROM llm_calls"
    ).fetchone()
    conn.close()
    project, digest, prompt_hash, output_hash, output_text, input_text, full = row
    assert project == "pilot"
    assert digest == DIGEST
    assert len(prompt_hash) == 64 and len(output_hash) == 64
    assert output_text is None and input_text is None and full == 0


def test_store_full_text_switch_keeps_text(tmp_path):
    import sqlite3

    def handler(request):
        return _tags() if request.url.path == "/api/tags" else _chat_ok()

    client = _client(tmp_path, handler, store_full_text=True)
    client.chat(MESSAGES)
    client.close()
    conn = sqlite3.connect(tmp_path / "usage.sqlite")
    output_text, input_text = conn.execute("SELECT output_text, input_text FROM llm_calls").fetchone()
    conn.close()
    assert output_text == "OK"
    assert "Reply with OK." in input_text


def test_thinking_returned_only_when_enabled(tmp_path):
    def handler(request):
        if request.url.path == "/api/tags":
            return _tags()
        return _chat_ok(message={"role": "assistant", "content": "OK", "thinking": "reasoning"})

    off = _client(tmp_path, handler, think=False).chat(MESSAGES)
    on = _client(tmp_path / "on", handler, think=True).chat(MESSAGES)
    assert off.thinking is None
    assert on.thinking == "reasoning"


def test_digest_mismatch_halts(tmp_path):
    def handler(request):
        return _tags("deadbeef") if request.url.path == "/api/tags" else _chat_ok()

    client = _client(tmp_path, handler, expected_digest=DIGEST)
    with pytest.raises(DigestMismatch):
        client.chat(MESSAGES)
    client.close()


def test_connection_refused_halts_as_unavailable(tmp_path):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    client = _client(tmp_path, handler)
    with pytest.raises(OllamaUnavailable):
        client.chat(MESSAGES)
    client.close()


def test_timeout_is_recorded_as_failed_attempt(tmp_path):
    def handler(request):
        if request.url.path == "/api/tags":
            return _tags()
        raise httpx.ReadTimeout("slow", request=request)

    client = _client(tmp_path, handler)
    result = client.chat(MESSAGES)
    client.close()
    assert result.status == "timeout"


def test_truncation_at_context_is_flagged(tmp_path):
    def handler(request):
        if request.url.path == "/api/tags":
            return _tags()
        return _chat_ok(prompt_eval_count=4096)

    client = _client(tmp_path, handler, num_ctx=4096)
    result = client.chat(MESSAGES)
    client.close()
    assert result.status == "truncated"


def test_oversize_prompt_is_not_sent(tmp_path):
    sent = []

    def handler(request):
        if request.url.path == "/api/tags":
            return _tags()
        sent.append(request)
        return _chat_ok()

    big = [{"role": "user", "content": "x" * 20000}]
    client = _client(tmp_path, handler, num_ctx=4096, num_predict=512)
    result = client.chat(big)
    client.close()
    assert result.status == "too_large"
    assert sent == []


def test_http_error_is_recorded(tmp_path):
    def handler(request):
        if request.url.path == "/api/tags":
            return _tags()
        return httpx.Response(500, text="model failed to load")

    client = _client(tmp_path, handler)
    result = client.chat(MESSAGES)
    client.close()
    assert result.status == "error"
    assert "500" in result.error_text


def test_malformed_response_is_recorded(tmp_path):
    def handler(request):
        if request.url.path == "/api/tags":
            return _tags()
        return httpx.Response(200, json={"done": True})

    client = _client(tmp_path, handler)
    result = client.chat(MESSAGES)
    client.close()
    assert result.status == "error"


def test_token_budget_stops_next_call(tmp_path):
    def handler(request):
        return _tags() if request.url.path == "/api/tags" else _chat_ok()

    client = _client(tmp_path, handler, max_total_tokens=10)
    first = client.chat(MESSAGES)  # 23 tokens used
    assert first.status == "ok"
    with pytest.raises(BudgetExceeded):
        client.chat(MESSAGES)
    client.close()
