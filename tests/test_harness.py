"""Offline tests: everything that does not need a live API key."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deepseek_harness import messages as M  # noqa: E402
from deepseek_harness.config import Config, is_peak, spec_for  # noqa: E402
from deepseek_harness.context import Conversation  # noqa: E402
from deepseek_harness.memory import WorkingMemory  # noqa: E402
from deepseek_harness.patch import Edit, apply_edits, parse_edits  # noqa: E402
from deepseek_harness.refusal import RefusalKind, classify  # noqa: E402
from deepseek_harness.telemetry import Telemetry, Usage, cost_usd  # noqa: E402


# --------------------------------------------------------------------------
# reasoning_content round-trip -- the rule most clients get wrong
# --------------------------------------------------------------------------

def test_reasoning_content_is_replayed_verbatim_in_thinking_mode():
    hist = [M.user("q"), M.assistant("a", reasoning="because X")]
    out = M.render(hist, thinking=True)
    assert out[1]["reasoning_content"] == "because X"


def test_reasoning_content_is_omitted_when_thinking_is_off():
    hist = [M.user("q"), M.assistant("a", reasoning="because X")]
    out = M.render(hist, thinking=False)
    assert "reasoning_content" not in out[1]


def test_blank_reasoning_is_never_sent_as_empty_string():
    """An empty reasoning_content is rejected by the API exactly like a missing one."""
    hist = [M.assistant("a", reasoning="")]
    out = M.render(hist, thinking=True)
    assert "reasoning_content" not in out[0]


def test_unreplayable_turn_is_downgraded_not_sent_malformed():
    lost = M.Msg("assistant", "earlier conclusion", reasoning_lost=True)
    out = M.render([M.user("q"), lost], thinking=True)
    assert out[1]["role"] == "user"
    assert "earlier conclusion" in out[1]["content"]
    assert all("reasoning_content" not in m for m in out)


def test_tool_calls_survive_rendering():
    call = M.ToolCall("id1", "read_file", '{"path": "a.py"}')
    hist = [M.Msg("assistant", None, tool_calls=[call]), M.tool_result("id1", "contents")]
    out = M.render(hist, thinking=True)
    assert out[0]["tool_calls"][0]["function"]["name"] == "read_file"
    assert out[1]["tool_call_id"] == "id1"


# --------------------------------------------------------------------------
# Cache-optimal ordering
# --------------------------------------------------------------------------

def test_working_memory_renders_last_so_the_prefix_stays_cacheable():
    convo = Conversation(Config(), system_prompt="sys", task_context="task")
    convo.add_user("hello")
    convo.memory.add_fact("the parser is in parse.py")
    rendered = convo.render()
    assert rendered[0].role == "system"
    assert "task_context" in (rendered[1].content or "")
    assert "working_memory" in (rendered[-1].content or "")
    assert rendered[-1].ephemeral is True


def test_ephemeral_memory_block_is_not_persisted_into_transcript():
    convo = Conversation(Config(), system_prompt="sys")
    convo.add_user("hello")
    convo.memory.add_fact("fact one")
    convo.render()
    convo.render()
    assert len(convo.transcript) == 1
    assert all(not m.ephemeral for m in convo.transcript)


# --------------------------------------------------------------------------
# Patch engine
# --------------------------------------------------------------------------

SAMPLE = """def add(a, b):
    return a + b


def sub(a, b):
    return a - b
"""


def test_parse_and_apply_roundtrip(tmp_path):
    (tmp_path / "m.py").write_text(SAMPLE)
    blocks = """m.py
<<<<<<< SEARCH
def add(a, b):
    return a + b
=======
def add(a, b):
    \"\"\"Sum two numbers.\"\"\"
    return a + b
>>>>>>> REPLACE"""
    edits = parse_edits(blocks)
    assert len(edits) == 1
    report = apply_edits(edits, tmp_path)
    assert report.ok, report.render()
    assert "Sum two numbers" in (tmp_path / "m.py").read_text()


def test_ambiguous_block_is_rejected_not_applied_to_first_hit(tmp_path):
    (tmp_path / "d.py").write_text("x = 1\ny = 2\nx = 1\n")
    report = apply_edits([Edit("d.py", "x = 1\n", "x = 99\n")], tmp_path)
    assert not report.ok
    assert "matches 2 places" in report.failed[0].detail
    assert (tmp_path / "d.py").read_text() == "x = 1\ny = 2\nx = 1\n"


def test_missing_block_reports_closest_real_lines(tmp_path):
    (tmp_path / "n.py").write_text(SAMPLE)
    report = apply_edits([Edit("n.py", "def add(a, b, c):\n", "pass\n")], tmp_path)
    assert not report.ok
    assert "closest lines actually in the file" in report.failed[0].detail


def test_whitespace_drift_still_applies(tmp_path):
    (tmp_path / "w.py").write_text("def f():\n    return 1   \n")
    report = apply_edits([Edit("w.py", "def f():\n    return 1\n", "def f():\n    return 2\n")],
                         tmp_path)
    assert report.ok, report.render()
    assert "return 2" in (tmp_path / "w.py").read_text()


def test_new_file_creation_with_empty_search(tmp_path):
    report = apply_edits([Edit("pkg/new.py", "", "VALUE = 1\n")], tmp_path)
    assert report.ok, report.render()
    assert (tmp_path / "pkg" / "new.py").read_text() == "VALUE = 1\n"


def test_creation_refuses_to_clobber_existing_content(tmp_path):
    (tmp_path / "exists.py").write_text("important = True\n")
    report = apply_edits([Edit("exists.py", "", "wiped = True\n")], tmp_path)
    assert not report.ok
    assert (tmp_path / "exists.py").read_text() == "important = True\n"


def test_path_escaping_workspace_is_refused(tmp_path):
    report = apply_edits([Edit("../escape.py", "", "bad = 1\n")], tmp_path)
    assert not report.ok
    assert not (tmp_path.parent / "escape.py").exists()


def test_sequential_edits_to_one_file_see_each_other(tmp_path):
    (tmp_path / "s.py").write_text(SAMPLE)
    report = apply_edits([
        Edit("s.py", "    return a + b\n", "    return a + b + 0\n"),
        Edit("s.py", "    return a - b\n", "    return a - b - 0\n"),
    ], tmp_path)
    assert report.ok, report.render()
    text = (tmp_path / "s.py").read_text()
    assert "a + b + 0" in text and "a - b - 0" in text


def test_dry_run_does_not_write(tmp_path):
    (tmp_path / "dr.py").write_text(SAMPLE)
    report = apply_edits([Edit("dr.py", "    return a + b\n", "    return 0\n")],
                         tmp_path, dry_run=True)
    assert report.ok
    assert "return a + b" in (tmp_path / "dr.py").read_text()


# --------------------------------------------------------------------------
# Refusal classification
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "I'm sorry, but I can't help with that.",
    "I cannot assist with this request.",
    "죄송하지만 도와드릴 수 없습니다.",
    "抱歉，我不能帮助你完成这个请求。",
])
def test_refusals_are_detected_across_languages(text):
    assert classify(text) is RefusalKind.SPURIOUS


@pytest.mark.parametrize("text", [
    "I don't have access to that file.",
    "Could you share the contents of config.py?",
    "파일을 찾을 수 없습니다.",
])
def test_capability_gaps_are_distinguished_from_refusals(text):
    assert classify(text) is RefusalKind.CAPABILITY


def test_normal_answers_are_not_flagged():
    assert classify("Here is the fix:\n\ndef f(): return 1") is RefusalKind.NONE
    assert classify("The bug is an off-by-one; the loop cannot reach the last index.") \
        is RefusalKind.NONE


def test_a_caveat_inside_a_long_answer_is_not_a_refusal():
    long_answer = ("I can't help with the deployment step, but here is the parser.\n"
                   + "x = 1\n" * 400)
    assert classify(long_answer) is RefusalKind.NONE


# --------------------------------------------------------------------------
# Working memory
# --------------------------------------------------------------------------

def test_memory_renders_dead_ends_prominently():
    wm = WorkingMemory(goal="fix the parser")
    wm.add_dead_end("tried regex split -- breaks on nested quotes")
    assert "DEAD ENDS" in wm.render()
    assert "nested quotes" in wm.render()


def test_memory_deduplicates():
    wm = WorkingMemory()
    wm.add_fact("same")
    wm.add_fact("same")
    assert len(wm.facts) == 1


def test_memory_apply_merges_a_model_update():
    wm = WorkingMemory()
    wm.apply({
        "facts": ["parser lives in parse.py"],
        "dead_ends": ["regex approach failed"],
        "hypotheses": [{"text": "encoding bug", "status": "refuted", "evidence": "utf-8 confirmed"}],
        "artifacts": {"parse.py": "the parser"},
    })
    assert wm.facts[0].text == "parser lives in parse.py"
    assert wm.hypotheses[0].status == "refuted"
    assert wm.artifacts["parse.py"] == "the parser"
    assert "refuted" in wm.render()


def test_empty_memory_renders_nothing():
    assert WorkingMemory().render() == ""


def test_memory_render_respects_char_cap():
    wm = WorkingMemory()
    for i in range(500):
        wm.add_fact(f"fact number {i} with some padding text to take up room")
    assert len(wm.render(max_chars=2000)) <= 2100


# --------------------------------------------------------------------------
# Pricing / telemetry
# --------------------------------------------------------------------------

def test_cache_hits_are_far_cheaper_than_misses():
    hit = Usage(prompt_tokens=1_000_000, cache_hit_tokens=1_000_000, cache_miss_tokens=0)
    miss = Usage(prompt_tokens=1_000_000, cache_hit_tokens=0, cache_miss_tokens=1_000_000)
    peak = dt.datetime(2026, 9, 9, 2, 0, tzinfo=dt.timezone.utc)  # Wed 02:00 UTC
    assert cost_usd(hit, "deepseek-flash", peak) * 10 < cost_usd(miss, "deepseek-flash", peak)


def test_usage_from_api_derives_missing_cache_miss_field():
    u = Usage.from_api({"prompt_tokens": 100, "prompt_cache_hit_tokens": 30,
                        "completion_tokens": 10})
    assert u.cache_miss_tokens == 70
    assert u.cache_hit_rate == pytest.approx(0.3)


def test_off_peak_is_half_price():
    peak = dt.datetime(2026, 9, 9, 2, 0, tzinfo=dt.timezone.utc)     # Wed 02:00
    off = dt.datetime(2026, 9, 9, 20, 0, tzinfo=dt.timezone.utc)     # Wed 20:00
    u = Usage(prompt_tokens=1000, cache_miss_tokens=1000, completion_tokens=500)
    assert cost_usd(u, "deepseek-flash", off) == pytest.approx(
        cost_usd(u, "deepseek-flash", peak) / 2)


def test_peak_windows():
    assert is_peak(dt.datetime(2026, 9, 9, 2, 0, tzinfo=dt.timezone.utc))      # Wed 02:00
    assert is_peak(dt.datetime(2026, 9, 9, 7, 0, tzinfo=dt.timezone.utc))      # Wed 07:00
    assert not is_peak(dt.datetime(2026, 9, 9, 5, 0, tzinfo=dt.timezone.utc))  # Wed 05:00 gap
    assert not is_peak(dt.datetime(2026, 9, 12, 2, 0, tzinfo=dt.timezone.utc)) # Sat


def test_budget_is_enforced():
    from deepseek_harness.config import Budget
    cfg = Config(budget=Budget(max_tokens=100))
    t = Telemetry(cfg)
    assert t.budget_exceeded() is None
    t.record("deepseek-flash", Usage(prompt_tokens=200, completion_tokens=10), 1.0)
    assert "tokens" in (t.budget_exceeded() or "")


def test_unknown_model_gets_a_permissive_spec():
    spec = spec_for("some-future-model")
    assert spec.supports_thinking and spec.requires_reasoning_roundtrip


# --------------------------------------------------------------------------
# Stale bytecode: a same-size edit must not be masked by a cached .pyc
# --------------------------------------------------------------------------

def test_same_size_edit_invalidates_stale_bytecode(tmp_path):
    """Regression: `VALUE = 1` -> `VALUE = 2` is byte-identical in length.

    CPython validates cached bytecode on (mtime, size), so without explicit
    invalidation the next subprocess imports the pre-edit module and the agent
    sees its own correct fix fail.
    """
    import subprocess

    (tmp_path / "v.py").write_text("VALUE = 1\n")
    run = lambda: subprocess.run(
        [sys.executable, "-c", "import v; print(v.VALUE)"],
        cwd=tmp_path, capture_output=True, text=True,
    ).stdout.strip()

    assert run() == "1"
    assert (tmp_path / "__pycache__").is_dir(), "precondition: bytecode was cached"

    report = apply_edits([Edit("v.py", "VALUE = 1\n", "VALUE = 2\n")], tmp_path)
    assert report.ok, report.render()
    assert run() == "2"


def test_bytecode_invalidation_ignores_non_python_files(tmp_path):
    (tmp_path / "a.txt").write_text("one\n")
    report = apply_edits([Edit("a.txt", "one\n", "two\n")], tmp_path)
    assert report.ok
    assert (tmp_path / "a.txt").read_text() == "two\n"
