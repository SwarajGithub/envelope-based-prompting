import pytest

from fenced_retry.llm.client import LLMSettings
from fenced_retry.llm.sampling import SamplingError, load_sampling


def _write(tmp_path, text):
    p = tmp_path / "s.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_reads_allowed_stages(tmp_path):
    p = _write(tmp_path, "[single_shot]\ntemperature = 0.3\n[naive_retry]\ntemperature = 0.7\n")
    assert load_sampling(p) == {"single_shot": 0.3, "naive_retry": 0.7}


def test_executor_is_governed_by_envelope(tmp_path):
    with pytest.raises(SamplingError, match="governed"):
        load_sampling(_write(tmp_path, "[executor]\ntemperature = 0.5\n"))


def test_unknown_stage_rejected(tmp_path):
    with pytest.raises(SamplingError, match="unknown stage"):
        load_sampling(_write(tmp_path, "[bogus]\ntemperature = 0.1\n"))


def test_out_of_range_rejected(tmp_path):
    with pytest.raises(SamplingError, match="outside"):
        load_sampling(_write(tmp_path, "[judge]\ntemperature = 3.0\n"))


def test_executor_uses_envelope_temperature_others_use_config():
    s = LLMSettings(temperature=0.0, stage_temperatures=(("naive_retry", 0.8),))
    assert s.temperature_for("executor") == 0.0
    assert s.temperature_for("naive_retry") == 0.8
    assert s.temperature_for("single_shot") == 0.0  # not configured: falls back to theta


def test_client_sends_and_records_stage_temperature(tmp_path):
    import json
    import sqlite3

    import httpx

    from fenced_retry.llm.client import LLMClient

    digest = "500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41"
    seen = {}

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b", "model": "qwen3:8b", "digest": digest}]})
        seen["options"] = json.loads(request.content)["options"]
        return httpx.Response(200, json={
            "model": "qwen3:8b", "message": {"role": "assistant", "content": "OK"}, "done": True,
            "prompt_eval_count": 20, "eval_count": 3,
        })

    cfg = LLMSettings(usage_db=tmp_path / "usage.sqlite", expected_digest=digest,
                      stage_temperatures=(("naive_retry", 0.8),))
    http = httpx.Client(base_url=cfg.endpoint, transport=httpx.MockTransport(handler))
    client = LLMClient(cfg, http=http)
    client.chat([{"role": "user", "content": "hi"}], project_name="t", stage="naive_retry")
    client.close()
    assert seen["options"]["temperature"] == 0.8
    conn = sqlite3.connect(tmp_path / "usage.sqlite")
    assert conn.execute("select stage, temperature from llm_calls").fetchall() == [("naive_retry", 0.8)]
