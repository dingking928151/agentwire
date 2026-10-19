"""Behavior tests: accounting, preservation, strict input, and offline tokenization."""
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

from agentcodec.codec import FallbackRequired
from agentcodec.schema import MessageV1

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "bench" / "tasks.v1.jsonl"


def module(name="agentcodec.benchmark"):
    try:
        if name == "bench.baselines":
            spec = importlib.util.spec_from_file_location(name, ROOT / "bench" / "baselines.py")
            result = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(result)
            return result
        return importlib.import_module(name)
    except ModuleNotFoundError as error:
        pytest.fail(f"Required benchmark implementation is missing: {error}")


def message(**changes):
    values = dict(message_id="t1", message_type="delegate", task="Review the local report.",
                  status="pending", blocker=None, next_step="Ask before sending.",
                  evidence=["fixture.txt"], constraints=["Do not publish.", "Budget <= $25."],
                  approval_required=True, user_id="demo-user", dictionary_version=None)
    return MessageV1(**(values | changes))


class CharacterEncoding:
    def encode_ordinary(self, text):
        return list(text)


def stub_tokens(monkeypatch, benchmark):
    monkeypatch.setattr(benchmark, "_get_encoding", lambda name: CharacterEncoding())


def test_load_tasks_preserves_unicode_nulls_false_and_order(tmp_path):
    b = module()
    path = tmp_path / "tasks.v1.jsonl"
    path.write_text(message(task="检查 [预算] | 20%\nDo not send.").model_dump_json() + "\n\n" +
                    message(message_id="t2", approval_required=False).model_dump_json() + "\n", encoding="utf-8")
    tasks = b.load_tasks(path)
    assert [m.message_id for m in tasks] == ["t1", "t2"]
    assert tasks[0].task == "检查 [预算] | 20%\nDo not send."
    assert tasks[0].blocker is None
    assert tasks[1].approval_required is False


@pytest.mark.parametrize("text", ["{bad}", "[]", '{"message_id":"x"}',
                                  message().model_dump_json().replace('"pending"', '"unknown"'),
                                  message().model_dump_json().replace('"approval_required":true', '"approval_required":"true"'),
                                  message().model_dump_json().replace('"message_id":"t1"', '"message_id":"t1","message_id":"evil"'),
                                  message().model_dump_json() + "\n" + message().model_dump_json(), "\n"])
def test_load_tasks_rejects_invalid_ambiguous_or_empty_data(tmp_path, text):
    b = module()
    path = tmp_path / "bad.jsonl"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="task|line|empty|duplicate"):
        b.load_tasks(path)


def test_dataset_has_30_unique_synthetic_messages_and_safety_cases():
    b = module()
    tasks = b.load_tasks(DATA)
    assert len(tasks) == len({m.message_id for m in tasks}) == 30
    assert {m.message_type for m in tasks} == {"delegate", "status", "result"}
    assert {m.status for m in tasks} == {"pending", "running", "blocked", "completed", "failed", "cancelled"}
    assert {m.approval_required for m in tasks} == {False, True}
    text = DATA.read_text(encoding="utf-8")
    assert "2026-11-01" in text and "$25" in text and "Do not publish." in text
    assert any("检查" in m.task for m in tasks)
    assert any("[" in m.task and "|" in m.task for m in tasks)


def test_count_tokens_uses_ordinary_text_and_selected_stub(monkeypatch):
    b = module()
    stub_tokens(monkeypatch, b)
    assert b.count_tokens("a中\n<|endoftext|>", "stub") == 16
    assert b.count_tokens("", "stub") == 0


def test_real_tokenizer_counts_known_text_and_special_literal():
    b = module()
    for name in ("o200k_base", "cl100k_base"):
        assert b.count_tokens("hello world", name) == 2
        assert b.count_tokens("<|endoftext|>", name) > 1


@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_or_corrupt_vocab_fails_without_network(tmp_path, monkeypatch, corrupt):
    b = module()
    import hashlib
    import tiktoken.load
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    if corrupt:
        url = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
        (tmp_path / hashlib.sha1(url.encode()).hexdigest()).write_bytes(b"corrupt")
    def forbid_network(*args, **kwargs):
        pytest.fail("tokenizer attempted network access")
    monkeypatch.setattr(tiktoken.load, "read_file", forbid_network)
    b._get_encoding.cache_clear()
    with pytest.raises(RuntimeError, match="local|cache|vocab"):
        b.count_tokens("hello", "o200k_base")
    b._get_encoding.cache_clear()


def test_unknown_encoding_is_explicitly_unavailable():
    b = module()
    with pytest.raises(RuntimeError, match="encoding|tokenizer"):
        b.count_tokens("hello", "not-an-encoding")


def test_renderers_retain_every_field_and_drixl_bracket_boundaries():
    baselines = module("bench.baselines")
    m = message(task='Check [x] | 20%\n"quoted" 中文')
    assert json.loads(baselines.render_json(m)) == m.model_dump()
    natural = baselines.render_natural_language(m)
    assert 'Check [x] | 20%' in natural and 'Do not publish.' in natural and 'Approval required: true' in natural
    rendered = baselines.render_drixl(m)
    assert rendered.startswith("@to:WORKER @fr:COORD @t:REQ @p:MED\nROUT ")
    params = rendered.split("\n", 1)[1].removeprefix("ROUT ").split(" ")
    from urllib.parse import unquote
    fields = {}
    for param in params:
        assert param.startswith("[") and param.endswith("]")
        assert "[" not in param[1:-1] and "]" not in param[1:-1]
        key, value = param[1:-1].split(":", 1)
        fields[key] = json.loads(unquote(value))
    assert fields == m.model_dump()


@pytest.mark.parametrize("kind,expected", [("status", "RES"), ("result", "FIN")])
def test_drixl_message_type_mapping(kind, expected):
    baselines = module("bench.baselines")
    assert f"@t:{expected}" in baselines.render_drixl(message(message_type=kind))


def test_five_conditions_have_equivalent_fields_and_separate_quality_metrics(monkeypatch):
    b = module()
    stub_tokens(monkeypatch, b)
    report = b.run_benchmark([message()], ["stub"])
    assert report.task_count == 1
    rows = report.results["stub"]
    assert set(rows) == {"natural_language", "json", "drixl", "codec", "codec_dictionary"}
    for row in rows.values():
        assert len(row.messages) == 1
        assert row.fallback_count == row.fallback_tokens == 0
        assert row.total_tokens == row.messages[0].total_tokens
    for name in ("codec", "codec_dictionary"):
        assert rows[name].round_trip_equal_count == 1
        assert rows[name].critical_constraint_loss_count == 0
        assert rows[name].messages[0].fields_equal is True
    for name in ("natural_language", "json", "drixl"):
        assert rows[name].round_trip_equal_count is None  # no baseline decoder claim
        assert rows[name].critical_constraint_loss_count is None
    assert report.results["stub"]["codec_dictionary"].dictionary_tokens > 0
    assert report.results["stub"]["codec"].dictionary_tokens == 0
    assert report.results["stub"]["json"].setup_tokens == 0


def test_amortization_charges_setup_once_and_repeats_whole_corpus(monkeypatch):
    b = module()
    stub_tokens(monkeypatch, b)
    row = b.run_benchmark([message(), message(message_id="t2")], ["stub"]).results["stub"]["codec_dictionary"]
    assert row.setup_tokens == row.protocol_tokens + row.dictionary_tokens
    assert row.total_tokens == sum(m.payload_tokens + m.fallback_tokens for m in row.messages)
    for reuse in (1, 10, 50):
        cost = row.amortized[reuse]
        assert cost.total_tokens == row.setup_tokens + reuse * row.total_tokens
        assert cost.tokens_per_corpus == pytest.approx(row.total_tokens + row.setup_tokens / reuse)
        assert cost.tokens_per_message == pytest.approx(cost.total_tokens / (2 * reuse))


def test_literal_accounting_math_and_fallback_resend(monkeypatch):
    b = module()
    def token_count(text, encoding):
        if text.startswith("AC1|"):
            return 7
        if text.startswith('{"message_id"'):
            return 11
        return 13
    monkeypatch.setattr(b, "count_tokens", token_count)
    def reject(*args, **kwargs):
        raise FallbackRequired("simulated unavailable receiver")
    monkeypatch.setattr(b, "decode_message", reject)
    row = b.run_benchmark([message()], ["stub"]).results["stub"]["codec"]
    assert (row.total_tokens, row.fallback_tokens, row.fallback_count) == (18, 11, 1)
    assert row.protocol_tokens == row.setup_tokens == 13
    assert row.messages[0].fallback_reason == "simulated unavailable receiver"
    assert row.round_trip_equal_count == 0
    assert row.critical_constraint_loss_count == 6  # six critical fields unrecovered
    assert row.amortized[1].total_tokens == 31
    assert row.amortized[10].total_tokens == 193
    assert row.amortized[50].total_tokens == 913
    assert row.amortized[10].tokens_per_message == pytest.approx(19.3)


def test_encode_rejection_counts_only_original_resend(monkeypatch):
    b = module()
    stub_tokens(monkeypatch, b)
    def reject(*args, **kwargs):
        raise FallbackRequired("cannot encode")
    monkeypatch.setattr(b, "encode_message", reject)
    row = b.run_benchmark([message()], ["stub"]).results["stub"]["codec"]
    assert row.messages[0].payload_tokens == 0
    assert row.total_tokens == row.fallback_tokens > 0
    assert row.fallback_count == 1


def test_lossy_decode_is_a_fallback_and_records_lost_critical_fields(monkeypatch):
    b = module()
    stub_tokens(monkeypatch, b)
    real_decode = b.decode_message
    def lose_constraints(payload, dictionary=None):
        decoded = real_decode(payload, dictionary)
        return decoded.model_copy(update={"constraints": [], "approval_required": False})
    monkeypatch.setattr(b, "decode_message", lose_constraints)
    row = b.run_benchmark([message()], ["stub"]).results["stub"]["codec"]
    assert row.round_trip_equal_count == 0
    assert row.critical_constraint_loss_count == 2
    assert row.fallback_count == 1 and row.fallback_tokens > 0


def test_dictionary_does_not_cross_user_namespace(monkeypatch):
    b = module()
    stub_tokens(monkeypatch, b)
    row = b.run_benchmark([message(user_id="another-user")], ["stub"]).results["stub"]["codec_dictionary"]
    assert row.fallback_count == 1
    assert row.messages[0].payload_tokens == 0
    assert "match" in row.messages[0].fallback_reason


def test_run_is_deterministic_and_serializable_without_mutating_inputs(monkeypatch):
    b = module()
    stub_tokens(monkeypatch, b)
    tasks = [message()]
    before = tasks[0].model_dump()
    first = b.run_benchmark(iter(tasks), ["stub"])
    second = b.run_benchmark(tasks, ["stub"])
    assert first.model_dump_json() == second.model_dump_json()
    assert tasks[0].model_dump() == before
    assert json.loads(first.model_dump_json())["task_count"] == 1


@pytest.mark.parametrize("tasks,encodings,reuses", [([], ["stub"], (1,)), ([message()], [], (1,)),
    ([message()], ["stub"], (0,)), ([message()], ["stub"], (-1,)),
    ([message()], ["stub"], (True,)), ([message()], ["stub"], (1.5,)),
    ([message()], ["stub"], ()), ([message()], ["stub", "stub"], (1,)),
    ([message(), message()], ["stub"], (1,)), ([message()], ["stub"], (1, 1))])
def test_invalid_run_parameters_are_rejected(tasks, encodings, reuses, monkeypatch):
    b = module()
    stub_tokens(monkeypatch, b)
    with pytest.raises(ValueError):
        b.run_benchmark(tasks, encodings, reuse_counts=reuses)


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
@pytest.mark.parametrize("record_delimiter", ["\n", "\r\n"])
@pytest.mark.parametrize("record_count", [1, 2])
def test_load_tasks_preserves_unicode_line_separators(
    tmp_path, separator, record_delimiter, record_count,
):
    # Catches treating Unicode string content as JSONL record boundaries.
    text = f"before{separator}after"
    expected = [message(message_id=f"t{index}", task=text, blocker=text,
                        next_step=text, evidence=[text], constraints=[text]).model_dump()
                for index in range(record_count)]
    serialized = record_delimiter.join(json.dumps(row, ensure_ascii=False) for row in expected)
    path = tmp_path / "unicode.jsonl"
    path.write_bytes((serialized + record_delimiter).encode("utf-8"))
    assert [task.model_dump() for task in module().load_tasks(path)] == expected


def test_load_tasks_does_not_treat_bare_carriage_return_as_record_delimiter(tmp_path):
    # Catches file newline translation inventing a JSONL LF boundary.
    path = tmp_path / "bare-cr.jsonl"
    path.write_bytes((message().model_dump_json() + "\r" +
                      message(message_id="t2").model_dump_json()).encode("utf-8"))
    with pytest.raises(ValueError, match="line 1"):
        module().load_tasks(path)
