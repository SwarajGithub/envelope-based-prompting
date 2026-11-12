import copy
import json
import pathlib

import pytest

from fenced_retry.envelope.approval import ApprovalError, approval_path, approve, verify
from fenced_retry.envelope.canonical import canonical_bytes, envelope_hash
from fenced_retry.envelope.lint import j_struct, lint_envelope
from fenced_retry.envelope.model import EnvelopeError, load_envelope, validate_structure

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "envelopes" / "receipt-extract-v1.json"


def _env():
    return json.loads(SHIPPED.read_text(encoding="utf-8"))


def test_canonical_bytes_ignore_key_order_and_whitespace():
    a = {"b": 1, "a": [1, 2]}
    b = json.loads('{ "a" : [1,2], "b":1 }')
    assert canonical_bytes(a) == canonical_bytes(b)
    assert envelope_hash(a) == envelope_hash(b)


def test_hash_changes_when_content_changes():
    env = _env()
    changed = copy.deepcopy(env)
    changed["theta"]["N_max"] = env["theta"]["N_max"] + 1
    assert envelope_hash(env) != envelope_hash(changed)


def test_shipped_envelope_loads_and_lints_clean():
    env = load_envelope(SHIPPED)
    assert lint_envelope(env) == []


def test_shipped_envelope_pins_model_digest():
    env = _env()
    assert env["theta"]["model"]["digest"].startswith("500a1f067a9f")
    assert env["theta"]["num_ctx"] == 4096
    assert env["theta"]["think_enabled"] is False


def test_missing_theta_key_is_rejected():
    env = _env()
    del env["theta"]["N_max"]
    with pytest.raises(EnvelopeError):
        validate_structure(env)


def test_fallback_count_must_match_K():
    env = _env()
    env["theta"]["K"] = 2
    with pytest.raises(EnvelopeError):
        validate_structure(env)


def test_lint_rejects_missing_mandatory_step():
    env = _env()
    env["plans"]["A"]["steps"] = ["extract_amounts", "extract_menu", "emit_json"]
    problems = lint_envelope(env)
    assert any("missing mandatory" in p and "check_format" in p for p in problems)


def test_lint_rejects_precedence_violation():
    env = _env()
    env["plans"]["A"]["steps"] = ["check_format", "extract_amounts", "extract_menu", "emit_json"]
    problems = lint_envelope(env)
    assert any("precedence" in p for p in problems)


def test_lint_rejects_low_drift():
    env = _env()
    env["theta"]["theta_drift"] = 0.95
    problems = lint_envelope(env)
    assert any("J_struct" in p for p in problems)


def test_lint_rejects_undeclared_extension():
    env = _env()
    env["plans"]["A"]["steps"] = env["plans"]["A"]["steps"] + ["retry_parse"]
    problems = lint_envelope(env)
    assert any("undeclared extensions" in p for p in problems)


def test_lint_accepts_declared_extension_when_drift_allows():
    env = _env()
    env["plans"]["A"]["steps"] = env["plans"]["A"]["steps"] + ["retry_parse"]
    env["plans"]["A"]["extensions"] = ["retry_parse"]
    env["theta"]["theta_drift"] = 0.5
    assert lint_envelope(env) == []


def test_j_struct_values():
    v0 = {"a", "b", "c"}
    assert j_struct({"a", "b", "c"}, v0) == 1.0
    assert j_struct({"a", "b"}, v0) == pytest.approx(2 / 3)
    assert j_struct({"a", "x"}, v0) == pytest.approx(1 / 4)


def test_approve_then_verify_roundtrip(tmp_path):
    env_file = tmp_path / "receipt-extract-v1.json"
    env_file.write_text(SHIPPED.read_text(encoding="utf-8"), encoding="utf-8")
    record = approve(env_file, approver="Swaraj Shekhar", note="pilot")
    assert record["envelope_hash"] == envelope_hash(_env())
    assert approval_path(env_file).exists()
    assert verify(env_file) == []


def test_mutated_envelope_fails_verification(tmp_path):
    env_file = tmp_path / "receipt-extract-v1.json"
    env_file.write_text(SHIPPED.read_text(encoding="utf-8"), encoding="utf-8")
    approve(env_file, approver="Swaraj Shekhar", note="pilot")
    env = json.loads(env_file.read_text(encoding="utf-8"))
    env["theta"]["N_max"] = 99
    env_file.write_text(json.dumps(env), encoding="utf-8")
    problems = verify(env_file)
    assert any("changed after approval" in p for p in problems)


def test_approve_refuses_failed_lint(tmp_path):
    env = _env()
    env["plans"]["A"]["steps"] = ["extract_amounts"]
    env_file = tmp_path / "bad.json"
    env_file.write_text(json.dumps(env), encoding="utf-8")
    with pytest.raises(ApprovalError):
        approve(env_file, approver="Swaraj Shekhar", note="")
    assert not approval_path(env_file).exists()


def test_approve_requires_approver(tmp_path):
    env_file = tmp_path / "receipt-extract-v1.json"
    env_file.write_text(SHIPPED.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ApprovalError):
        approve(env_file, approver="  ", note="")
