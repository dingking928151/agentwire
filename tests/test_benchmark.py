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


def test_natural_language_renders_all_eleven_fields_in_canonical_order():
    # Catches dropping/reordering a field or hiding explicit null/list/false values.
    baselines = module("bench.baselines")
    original = MessageV1(
        message_id="nl-11", message_type="result", task="检查 [x] | 20%",
        status="completed", blocker=None, next_step="Keep local; do not send.",
        evidence=["proof:17", "receipt:29"], constraints=["Budget <= $31.", "Never buy."],
        approval_required=False, user_id="nl-owner", dictionary_version="nl-v7",
    )
    assert baselines.render_natural_language(original) == (
        'ID: "nl-11"; Type: "result"; Task: "检查 [x] | 20%"; Status: "completed"; '
        'Blocker: null; Next step: "Keep local; do not send."; '
        'Evidence: ["proof:17","receipt:29"]; Constraints: ["Budget <= $31.","Never buy."]; '
        'Approval required: false; User: "nl-owner"; Dictionary version: "nl-v7".'
    )


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


# Task 2: catch dictionary mismatches, omitted costs, and diagnostic/payload mixing.
REPETITIVE = ROOT / "bench" / "tasks-repetitive.v1.jsonl"
REPEAT_DICTIONARY = ROOT / "bench" / "dictionaries" / "repeat-v1.json"
SEVEN_FORMATS = {"natural_language", "json", "drixl", "ac1", "ac1_dictionary", "ac2", "ac2_dictionary"}


def suite_api():
    b = module()
    assert hasattr(b, "run_benchmark_suite"), "paired benchmark suite API is missing"
    assert hasattr(b, "BenchmarkScenario"), "explicit scenario input is missing"
    return b


def test_repetitive_fixture_is_versioned_repeated_and_diverse_source_unchanged():
    import hashlib
    from collections import Counter
    from agentcodec.dictionary import load_dictionary
    assert hashlib.sha256(DATA.read_bytes()).hexdigest() == "9284dc0b7a878355c6e8ddc0195cd7adcade9b4966dc3a07aeb58f2f1f2ad7b8"
    assert REPETITIVE.exists(), "separate repetitive v1 corpus is missing"
    assert REPEAT_DICTIONARY.exists(), "manual repeat-v1 dictionary is missing"
    tasks = module().load_tasks(REPETITIVE)
    assert [m.message_id for m in tasks] == [f"repeat-v1-{i:02d}" for i in range(1, 31)]
    assert {m.user_id for m in tasks} == {"repeat-user"}
    assert {m.dictionary_version for m in tasks} == {None}
    dictionary = load_dictionary(REPEAT_DICTIONARY, "repeat-user")
    assert dictionary.version == "repeat-v1"
    with pytest.raises(FallbackRequired, match="user"):
        load_dictionary(REPEAT_DICTIONARY, "other-user")
    counts = Counter(m.task for m in tasks)
    assert max(counts.values()) >= 10
    assert sum(len(phrase) >= 80 for phrase in dictionary.aliases.values()) >= 4
    for phrase in dictionary.aliases.values():
        assert any(phrase == value or isinstance(value, list) and phrase in value
                   for m in tasks for value in m.model_dump().values())
    assert {m.message_type for m in tasks} == {"delegate", "status", "result"}
    assert {m.approval_required for m in tasks} == {True, False}
    text = " ".join(m.task + " ".join(m.constraints) for m in tasks)
    assert "Do not" in text and "$25" in text and "2026-11-01" in text
    assert "检查" in text and "|" in text and "[" in text and "%" in text


def test_suite_keeps_scenarios_separate_and_uses_matched_dictionary_payloads():
    from agentcodec.codec import encode_message
    from agentcodec.ac2 import encode_message_ac2
    from agentcodec.dictionary import load_dictionary
    b = suite_api()
    tasks = b.load_tasks(DATA)
    repetitive = b.load_tasks(REPETITIVE)
    repeat_dictionary = load_dictionary(REPEAT_DICTIONARY, "repeat-user")
    scenarios = {"diverse": b.BenchmarkScenario(tasks=tasks, dictionary=b.DEMO_DICTIONARY),
                 "repetitive": b.BenchmarkScenario(tasks=repetitive, dictionary=repeat_dictionary)}
    before = [m.model_dump() for m in tasks + repetitive]
    first = b.run_benchmark_suite(scenarios, ["o200k_base", "cl100k_base"])
    second = b.run_benchmark_suite(scenarios, ["o200k_base", "cl100k_base"])
    assert first.model_dump_json() == second.model_dump_json()
    assert first.report_version == "suite-v1"
    assert first.reuse_counts == [1, 10, 50]
    assert set(first.scenarios) == {"diverse", "repetitive"}
    for name, scenario in first.scenarios.items():
        source = scenarios[name]
        assert scenario.task_count == 30
        assert scenario.dictionary == source.dictionary
        for encoding, rows in scenario.results.items():
            assert set(rows) == SEVEN_FORMATS
            for fmt, row in rows.items():
                assert len(row.messages) == 30
                assert row.fallback_count == row.fallback_tokens == 0
                assert row.total_tokens == row.payload_tokens == sum(m.payload_tokens for m in row.messages)
                assert row.setup_tokens == row.protocol_tokens + row.dictionary_tokens
                assert row.protocol_tokens == b.count_tokens(scenario.protocol_instructions[fmt], encoding)
                expected_dict_cost = b.count_tokens(source.dictionary.model_dump_json(), encoding) if fmt.endswith("_dictionary") else 0
                assert row.dictionary_tokens == expected_dict_cost
                for n in (1, 10, 50):
                    assert row.amortized[n].total_tokens == row.setup_tokens + n * row.total_tokens
                    assert row.amortized[n].tokens_per_corpus == pytest.approx(row.total_tokens + row.setup_tokens / n)
                    assert row.amortized[n].tokens_per_message == pytest.approx((row.setup_tokens + n * row.total_tokens) / (30 * n))
                if fmt.startswith(("ac1", "ac2")):
                    assert row.round_trip_equal_count == 30 and row.round_trip_equal_rate == 1
                    assert row.critical_constraint_loss_count == 0
                    encoder = encode_message if fmt.startswith("ac1") else encode_message_ac2
                    dictionary = source.dictionary if fmt.endswith("_dictionary") else None
                    for original, cost in zip(source.tasks, row.messages):
                        selected = original.model_copy(update={"dictionary_version": dictionary.version}) if dictionary else original
                        assert cost.payload_tokens == b.count_tokens(encoder(selected, dictionary), encoding)
                else:
                    assert row.round_trip_equal_rate is row.critical_constraint_loss_count is None
            diagnostic = scenario.ac1_diagnostics[encoding]
            from urllib.parse import quote
            from statistics import mean, median
            for field, costs in diagnostic.non_enum_fields.items():
                for original, sample in zip(source.tasks, costs.samples):
                    literal = json.dumps(getattr(original, field), ensure_ascii=False, separators=(",", ":"))
                    escaped = quote(literal, safe="")
                    assert sample.json_tokens == b.count_tokens(literal, encoding)
                    assert sample.ac1_tokens == b.count_tokens(escaped, encoding)
                    assert sample.token_delta == sample.ac1_tokens - sample.json_tokens
                    raw_bytes = literal.encode("utf-8")
                    escaped_bytes = sum(byte not in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-~" for byte in raw_bytes)
                    assert sample.original_utf8_bytes == len(raw_bytes)
                    assert sample.escaped_utf8_bytes == escaped_bytes
                    assert sample.escaped_utf8_byte_ratio == escaped_bytes / len(raw_bytes)
                assert costs.token_delta_mean == mean(s.token_delta for s in costs.samples)
                assert costs.token_delta_median == median(s.token_delta for s in costs.samples)
    assert [m.model_dump() for m in tasks + repetitive] == before
    # Existing report stays five-format, version 1, with its original dictionary metadata.
    legacy = b.run_benchmark(tasks, ["o200k_base"])
    assert legacy.report_version == "1"
    assert set(legacy.results["o200k_base"]) == {"natural_language", "json", "drixl", "codec", "codec_dictionary"}
    assert legacy.demo_dictionary == b.DEMO_DICTIONARY
    for old, new in {"codec": "ac1", "codec_dictionary": "ac1_dictionary", "json": "json",
                     "natural_language": "natural_language", "drixl": "drixl"}.items():
        assert legacy.results["o200k_base"][old] == first.scenarios["diverse"].results["o200k_base"][new]


def test_one_message_suite_preserves_explicit_fields_and_only_selects_dictionary_version(monkeypatch):
    # Catches omissions shared by encoders/decoders, altered metadata, or unmatched dictionaries.
    from urllib.parse import unquote
    from agentcodec.schema import UserDictionary

    b = suite_api()
    stub_tokens(monkeypatch, b)
    expected = {
        "message_id": "paired-11", "message_type": "status", "task": "检查 [budget] | 35%",
        "status": "blocked", "blocker": "Waiting for local evidence.",
        "next_step": "Review; do not send.", "evidence": ["proof:41", "receipt:53"],
        "constraints": ["Do not buy before 2026-11-09.", "Budget <= $47."],
        "approval_required": False, "user_id": "paired-owner", "dictionary_version": None,
    }
    expected_selected = {**expected, "dictionary_version": "paired-v3"}
    expected_dictionary = {
        "user_id": "paired-owner", "version": "paired-v3",
        "aliases": {"TASK": "检查 [budget] | 35%", "WAIT": "Waiting for local evidence.",
                    "NEXT": "Review; do not send.", "PROOF": "proof:41",
                    "HOLD": "Do not buy before 2026-11-09.", "LIMIT": "Budget <= $47."},
    }
    original = MessageV1(**expected)
    dictionary = UserDictionary(**expected_dictionary)
    rendered = {}
    decoded = {"ac1": [], "ac2": []}

    # Observe real boundary outputs; wrappers never synthesize a renderer/codec result.
    def observe_renderer(name, renderer):
        def capture(task):
            text = renderer(task)
            rendered[name] = text
            return text
        return capture

    def observe_decoder(name, decoder):
        def capture(payload, dictionary=None):
            result = decoder(payload, dictionary)
            decoded[name].append((payload, dictionary, result.model_dump()))
            return result
        return capture

    for name in ("natural_language", "json", "drixl"):
        attribute = "render_" + name
        monkeypatch.setattr(b, attribute, observe_renderer(name, getattr(b, attribute)))
    monkeypatch.setattr(b, "decode_message", observe_decoder("ac1", b.decode_message))
    monkeypatch.setattr(b, "decode_message_ac2", observe_decoder("ac2", b.decode_message_ac2))

    report = b.run_benchmark_suite(
        {"explicit": b.BenchmarkScenario(tasks=[original], dictionary=dictionary)}, ["stub"],
    )
    assert set(report.scenarios) == {"explicit"}
    scenario = report.scenarios["explicit"]
    assert scenario.task_count == 1
    assert scenario.dictionary.model_dump() == expected_dictionary
    assert rendered["natural_language"] == (
        'ID: "paired-11"; Type: "status"; Task: "检查 [budget] | 35%"; Status: "blocked"; '
        'Blocker: "Waiting for local evidence."; Next step: "Review; do not send."; '
        'Evidence: ["proof:41","receipt:53"]; '
        'Constraints: ["Do not buy before 2026-11-09.","Budget <= $47."]; '
        'Approval required: false; User: "paired-owner"; Dictionary version: null.'
    )
    assert json.loads(rendered["json"]) == expected
    envelope, body = rendered["drixl"].split("\n", 1)
    assert envelope == "@to:WORKER @fr:COORD @t:RES @p:MED"
    assert body.startswith("ROUT ")
    params = body.removeprefix("ROUT ").split(" ")
    drixl_fields = {}
    for param in params:
        assert param.startswith("[") and param.endswith("]")
        key, value = param[1:-1].split(":", 1)
        assert key not in drixl_fields
        drixl_fields[key] = json.loads(unquote(value))
    assert drixl_fields == expected

    rows = scenario.results["stub"]
    assert set(rows) == SEVEN_FORMATS
    for name, row in rows.items():
        assert row.fallback_count == row.fallback_tokens == 0
        assert len(row.messages) == 1
        cost = row.messages[0]
        assert cost.message_id == "paired-11"
        assert cost.fallback_reason is None and cost.fallback_tokens == 0
        if name in rendered:
            assert cost.payload_tokens == len(rendered[name])
        else:
            assert row.round_trip_equal_count == 1
            assert row.critical_constraint_loss_count == 0
            assert cost.fields_equal is True

    for name in ("ac1", "ac2"):
        assert len(decoded[name]) == 2
        plain_wire, no_dictionary, plain_fields = decoded[name][0]
        selected_wire, selected_dictionary, selected_fields = decoded[name][1]
        assert no_dictionary is None
        assert plain_fields == expected
        assert selected_fields == expected_selected
        assert selected_dictionary is scenario.dictionary
        assert selected_dictionary.model_dump() == expected_dictionary
        assert rows[name].messages[0].payload_tokens == len(plain_wire)
        assert rows[name + "_dictionary"].messages[0].payload_tokens == len(selected_wire)
    assert decoded["ac1"][1][1] is decoded["ac2"][1][1]
    assert original.model_dump() == expected
    assert dictionary.model_dump() == expected_dictionary


def test_ac1_diagnostics_use_unaliased_values_and_utf8_byte_denominator(monkeypatch):
    b = suite_api()
    stub_tokens(monkeypatch, b)
    tasks = [message(task="中|", constraints=["Do not publish."]),
             message(message_id="t2", task="ab"), message(message_id="t3", task="abc")]
    report = b.run_benchmark_suite({"sample": b.BenchmarkScenario(tasks=tasks, dictionary=b.DEMO_DICTIONARY)}, ["stub"])
    scenario = report.scenarios["sample"]
    diagnostic = scenario.ac1_diagnostics["stub"]
    assert diagnostic.diagnostic_only is True
    assert diagnostic.condition == "ac1_no_dictionary"
    assert set(diagnostic.non_enum_fields) == set(b.FIELDS) - {"message_type", "status"}
    assert set(diagnostic.enum_fields) == {"message_type", "status"}
    field = diagnostic.non_enum_fields["task"]
    # '"中|"' is 6 UTF-8 bytes, all 6 escaped: %22%E4%B8%AD%7C%22.
    sample = field.samples[0]
    assert (sample.json_tokens, sample.ac1_tokens, sample.token_delta) == (4, 18, 14)
    assert (sample.original_utf8_bytes, sample.escaped_utf8_bytes, sample.escaped_utf8_byte_ratio) == (6, 6, 1)
    assert field.samples[1].token_delta == 4  # '"ab"' -> '%22ab%22'
    assert field.token_delta_mean == pytest.approx(22 / 3)
    assert field.token_delta_median == 4
    assert field.json_tokens == 13 and field.ac1_tokens == 35 and field.token_delta == 22
    assert field.escaped_utf8_byte_ratio == pytest.approx(10 / 15)  # byte-weighted, not mean of ratios
    alias_field = diagnostic.non_enum_fields["constraints"].samples[0]
    assert alias_field.json_tokens == len('["Do not publish."]')
    assert alias_field.ac1_tokens == len('%5B%22Do%20not%20publish.%22%5D')
    enum = diagnostic.enum_fields["message_type"]
    assert (enum.samples[0].json_tokens, enum.samples[0].ac1_tokens, enum.samples[0].token_delta) == (10, 1, -9)
    assert enum.escaped_utf8_byte_ratio is None
    assert diagnostic.enum_fields["status"].token_delta_mean == -8
    components = sum(f.ac1_tokens for f in diagnostic.non_enum_fields.values()) + sum(f.ac1_tokens for f in diagnostic.enum_fields.values())
    actual = scenario.results["stub"]["ac1"].payload_tokens
    assert actual == components + 14 * len(tasks)  # AC1 prefix + eleven separators
    assert actual != components


@pytest.mark.parametrize("codec", ["ac1", "ac2"])
@pytest.mark.parametrize("failure", ["encode", "decode", "loss"])
def test_suite_codec_fallback_keeps_transmitted_payload_and_original_resend(monkeypatch, codec, failure):
    b = suite_api()
    stub_tokens(monkeypatch, b)
    encoder_name = "encode_message" if codec == "ac1" else "encode_message_ac2"
    decoder_name = "decode_message" if codec == "ac1" else "decode_message_ac2"
    real_decode = getattr(b, decoder_name)
    def reject(*args, **kwargs):
        raise FallbackRequired("simulated failure")
    def lossy(payload, dictionary=None):
        return real_decode(payload, dictionary).model_copy(update={"constraints": []})
    monkeypatch.setattr(b, encoder_name if failure == "encode" else decoder_name,
                        lossy if failure == "loss" else reject)
    original = message()
    scenario = b.run_benchmark_suite({"sample": b.BenchmarkScenario(tasks=[original], dictionary=b.DEMO_DICTIONARY)}, ["stub"]).scenarios["sample"]
    for fmt in (codec, codec + "_dictionary"):
        row = scenario.results["stub"][fmt]
        assert row.fallback_count == 1 and row.round_trip_equal_count == 0
        assert row.fallback_tokens == len(b.render_json(original))
        assert (row.payload_tokens == 0) is (failure == "encode")
        assert row.total_tokens == row.payload_tokens + row.fallback_tokens
        assert row.critical_constraint_loss_count == (1 if failure == "loss" else 6)
        assert row.amortized[10].total_tokens == row.setup_tokens + 10 * row.total_tokens


@pytest.mark.parametrize("owner,version", [("other-user", None), ("demo-user", "unknown-v1")])
def test_suite_mismatched_metadata_falls_back_without_silent_reselection(owner, version, monkeypatch):
    b = suite_api()
    stub_tokens(monkeypatch, b)
    scenario = b.run_benchmark_suite({"sample": b.BenchmarkScenario(
        tasks=[message(user_id=owner, dictionary_version=version)], dictionary=b.DEMO_DICTIONARY)}, ["stub"]).scenarios["sample"]
    for name in ("ac1_dictionary", "ac2_dictionary"):
        row = scenario.results["stub"][name]
        assert row.fallback_count == 1 and row.payload_tokens == 0
        assert row.fallback_tokens > 0


@pytest.mark.parametrize("kind", ["empty_scenarios", "empty_tasks", "duplicate_ids", "empty_encodings", "duplicate_encodings", "empty_reuses", "bad_reuse", "duplicate_reuses", "mutated_task", "mutated_dictionary"])
def test_suite_rejects_invalid_inputs(kind, monkeypatch):
    b = suite_api()
    stub_tokens(monkeypatch, b)
    task = message()
    dictionary = b.DEMO_DICTIONARY.model_copy(deep=True)
    scenarios = {"sample": b.BenchmarkScenario(tasks=[task], dictionary=dictionary)}
    encodings, reuses = ["stub"], (1, 10, 50)
    if kind == "empty_scenarios": scenarios = {}
    if kind == "empty_tasks": scenarios["sample"].tasks.clear()
    if kind == "duplicate_ids": scenarios["sample"].tasks.append(task)
    if kind == "empty_encodings": encodings = []
    if kind == "duplicate_encodings": encodings = ["stub", "stub"]
    if kind == "empty_reuses": reuses = ()
    if kind == "bad_reuse": reuses = (True,)
    if kind == "duplicate_reuses": reuses = (1, 1)
    if kind == "mutated_task": scenarios["sample"].tasks[0].approval_required = "false"
    if kind == "mutated_dictionary": scenarios["sample"].dictionary.aliases["duplicate"] = next(iter(dictionary.aliases.values()))
    with pytest.raises(ValueError):
        b.run_benchmark_suite(scenarios, encodings, reuse_counts=reuses)
