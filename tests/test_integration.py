"""End-to-end wiring tests against a mocked transport (no network, no API key)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deepseek_harness import Config, DeepSeekClient, Thinking  # noqa: E402
from deepseek_harness import messages as M  # noqa: E402
from deepseek_harness.agents import CodingAgent, ReasoningAgent  # noqa: E402
from deepseek_harness.errors import RefusalUnresolved  # noqa: E402
from deepseek_harness.refusal import RefusalHandler  # noqa: E402
from deepseek_harness.runner import Harness  # noqa: E402


class MockTransport:
    """Scripted transport. Replies are routed by call label, not by position.

    Ordering assertions on a pipeline are brittle: a stage such as ``distill``
    legitimately skips its call when there is nothing worth distilling, which
    silently shifts every later reply. Routing by label tests what each stage
    asks for instead.
    """

    def __init__(self, replies=None, by_label=None):
        self.replies = list(replies or [])
        self.by_label = dict(by_label or {})
        self.payloads: list[dict] = []
        self.labels: list[str] = []
        self.default = {"content": "ok"}

    def _pick(self, label: str) -> dict:
        for prefix, reply in sorted(
                self.by_label.items(), key=lambda kv: -len(kv[0])):
            if label.startswith(prefix):
                return reply() if callable(reply) else reply
        if self.replies:
            return self.replies.pop(0)
        return dict(self.default)

    def post_json(self, path, payload, *, label=""):
        self.payloads.append(payload)
        self.labels.append(label)
        reply = self._pick(label)
        message = {"role": "assistant", "content": reply.get("content", "ok")}
        if reply.get("reasoning"):
            message["reasoning_content"] = reply["reasoning"]
        if reply.get("tool_calls"):
            message["tool_calls"] = reply["tool_calls"]
        return {
            "choices": [{"message": message, "finish_reason": reply.get("finish", "stop")}],
            "usage": {"prompt_tokens": 120, "prompt_cache_hit_tokens": 64,
                      "prompt_cache_miss_tokens": 56, "completion_tokens": 40},
        }

    def close(self):
        pass


def make_client(replies=None, by_label=None, **cfg):
    config = Config(api_key="test-key", **cfg)
    client = DeepSeekClient(config)
    client.transport = MockTransport(replies, by_label)
    return client


# --------------------------------------------------------------------------

def test_thinking_payload_carries_effort_and_replays_reasoning():
    client = make_client([{"content": "first", "reasoning": "step one"},
                          {"content": "second"}])
    hist = [M.user("q1")]
    c1 = client.complete(hist, thinking=Thinking(True, "max"), label="t1")
    assert client.transport.payloads[0]["reasoning_effort"] == "max"
    assert c1.reasoning == "step one"

    hist += [c1.message, M.user("q2")]
    client.complete(hist, thinking=Thinking(True, "high"))
    replayed = client.transport.payloads[1]["messages"]
    assistant_turns = [m for m in replayed if m["role"] == "assistant"]
    assert assistant_turns[0]["reasoning_content"] == "step one"


def test_thinking_disabled_sends_the_disabled_flag():
    client = make_client([{"content": "x"}])
    client.complete([M.user("q")], thinking=Thinking.OFF)
    assert client.transport.payloads[0]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in client.transport.payloads[0]


def test_temperature_is_dropped_while_thinking_is_active():
    client = make_client([{"content": "x"}])
    client.complete([M.user("q")], thinking=Thinking(True, "high"), temperature=0.7)
    assert "temperature" not in client.transport.payloads[0]


def test_telemetry_accumulates_cache_stats():
    client = make_client([{"content": "a"}, {"content": "b"}])
    client.complete([M.user("q")], thinking=Thinking.OFF)
    client.complete([M.user("q")], thinking=Thinking.OFF)
    assert client.telemetry.usage.prompt_tokens == 240
    assert client.telemetry.usage.cache_hit_rate == pytest.approx(64 / 120)
    assert client.telemetry.cost > 0


# --------------------------------------------------------------------------

def test_refusal_triggers_a_bounded_re_request_that_succeeds():
    client = make_client([
        {"content": "I'm sorry, but I can't help with that."},
        {"content": "none"},                       # classifier verdict on the refusal
        {"content": "Here is the implementation: def f(): ..."},
    ])
    # The classifier reply above is consumed for the refusal; force heuristic-only
    # so the script stays readable.
    handler = RefusalHandler(client, use_classifier=False)
    client.transport.replies = [
        {"content": "I'm sorry, but I can't help with that."},
        {"content": "Here is the implementation: def f(): return 1"},
    ]
    result = handler.complete([M.user("write f")], thinking=Thinking.OFF)
    assert result.resolved
    assert len(result.attempts) == 2
    assert result.attempts[1].strategy == "clarify"
    assert "implementation" in result.text


def test_exhausted_refusal_escalates_instead_of_looping():
    client = make_client([], refusal_max_attempts=2)
    client.transport.default = {"content": "I'm sorry, but I can't help with that."}
    handler = RefusalHandler(client, use_classifier=False)
    with pytest.raises(RefusalUnresolved) as exc:
        handler.complete([M.user("anything")], thinking=Thinking.OFF)
    # initial + exactly 2 bounded retries, then stop.
    assert len(exc.value.attempts) == 3


def test_refusal_retry_count_is_configurable_and_respected():
    client = make_client([], refusal_max_attempts=1)
    client.transport.default = {"content": "I cannot assist with this request."}
    handler = RefusalHandler(client, use_classifier=False)
    with pytest.raises(RefusalUnresolved) as exc:
        handler.complete([M.user("anything")], thinking=Thinking.OFF)
    assert len(exc.value.attempts) == 2


def test_purpose_context_is_attached_on_retry():
    client = make_client([])
    client.transport.default = {"content": "I'm sorry, but I can't help with that."}
    handler = RefusalHandler(client, use_classifier=False,
                             purpose_context="Authorised internal audit, ticket SEC-1")
    try:
        handler.complete([M.user("scan this parser")], thinking=Thinking.OFF)
    except RefusalUnresolved as exc:
        assert "SEC-1" in exc.attempts[1].prompt


# --------------------------------------------------------------------------

def test_reasoning_pipeline_runs_all_stages():
    client = make_client(by_label={
        "reason:frame": {"content": "framing", "reasoning": "r1"},
        "reason:hypotheses": {"content": "two hypotheses", "reasoning": "r2"},
        "reason:solve": {"content": "the answer is 42", "reasoning": "r3"},
        "reason:verify": {"content": '{"verdict": "correct", "problems": [], '
                                     '"weakest_claim": "x"}'},
        "reason:final": {"content": "Final: 42."},
        "distill": {"content": "{}"},
    })
    agent = ReasoningAgent(client)
    agent.refusals.use_classifier = False
    result = agent.solve("what is 6*7?", verify=True)

    assert result.ok
    assert result.output == "Final: 42."
    assert [s["step"] for s in result.steps] == ["frame", "hypotheses", "solve",
                                                 "verify", "synthesis"]


def test_verification_runs_on_a_clean_context():
    """The verifier must not inherit the solver's framing, or it rubber-stamps it."""
    client = make_client(by_label={
        "reason:verify": {"content": '{"verdict": "correct", "problems": []}'},
        "distill": {"content": "{}"},
    })
    agent = ReasoningAgent(client)
    agent.refusals.use_classifier = False
    agent.solve("prove X", verify=True)

    idx = client.transport.labels.index("reason:verify")
    verify_msgs = client.transport.payloads[idx]["messages"]
    assert len(verify_msgs) == 1
    assert all(m["role"] == "user" for m in verify_msgs)


def test_reasoning_repairs_a_wrong_answer():
    client = make_client(by_label={
        "reason:solve": {"content": "the answer is 41"},
        "reason:verify": {"content": '{"verdict": "wrong", "problems": ["off by one"], '
                                     '"corrected_answer": "42"}'},
        "reason:repair": {"content": "Final: 42."},
        "distill": {"content": "{}"},
    })
    agent = ReasoningAgent(client)
    agent.refusals.use_classifier = False
    result = agent.solve("what is 6*7?", verify=True)

    assert "repair" in [s["step"] for s in result.steps]
    assert result.output == "Final: 42."
    assert any("off by one" in f.text for f in result.memory.facts)


def test_best_of_n_consults_a_judge():
    client = make_client(by_label={
        "reason:final": {"content": "candidate answer"},
        "reason:verify": {"content": '{"verdict": "correct", "problems": []}'},
        "reason:judge": {"content": '{"best_index": 1, "why": "verifies cleanly"}'},
        "distill": {"content": "{}"},
    })
    agent = ReasoningAgent(client)
    agent.refusals.use_classifier = False
    result = agent.solve("hard problem", samples=3, verify=False)

    assert "best-of-3" in result.note
    assert result.steps[0]["chosen"] == 1
    assert len(result.steps[0]["candidates"]) == 3


# --------------------------------------------------------------------------

def test_coding_agent_edits_verifies_and_reports(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    edit_block = """calc.py
<<<<<<< SEARCH
def add(a, b):
    return a - b
=======
def add(a, b):
    return a + b
>>>>>>> REPLACE"""

    client = make_client(by_label={
        "code:explore": {"content": '{"files": [{"path": "calc.py", "why": "defines add"}]}'},
        "code:plan": {"content": "plan: the operator is inverted"},
        "code:implement": {"content": edit_block},
        "code:review": {"content": '{"verdict": "ship", "findings": []}'},
        "distill": {"content": "{}"},
    })
    agent = CodingAgent(
        client, root=tmp_path,
        verify_command='python3 -c "import calc; assert calc.add(2,2)==4"',
    )
    agent.refusals.use_classifier = False
    result = agent.run("fix add()", review=True)

    assert "return a + b" in (tmp_path / "calc.py").read_text()
    assert result.verification is not None and result.verification.ok
    assert result.files_changed == ["calc.py"]
    assert result.ok


def test_coding_agent_repairs_a_malformed_edit_block(tmp_path):
    (tmp_path / "m.py").write_text("VALUE = 1\n")
    bad = """m.py
<<<<<<< SEARCH
VALUE = 999
=======
VALUE = 2
>>>>>>> REPLACE"""
    good = """m.py
<<<<<<< SEARCH
VALUE = 1
=======
VALUE = 2
>>>>>>> REPLACE"""

    attempts = iter([bad, good])
    client = make_client(by_label={
        "code:explore": {"content": '{"files": [{"path": "m.py", "why": "x"}]}'},
        "code:plan": {"content": "plan"},
        "code:implement": lambda: {"content": next(attempts)},
        "code:repair-patch": lambda: {"content": next(attempts)},
        "code:review": {"content": '{"verdict": "ship", "findings": []}'},
        "distill": {"content": "{}"},
    })
    agent = CodingAgent(client, root=tmp_path)
    agent.refusals.use_classifier = False
    result = agent.run("set VALUE to 2", review=True)

    assert (tmp_path / "m.py").read_text() == "VALUE = 2\n"
    assert any(d.text.startswith("edit block failed") for d in result.memory.dead_ends)


def test_coding_agent_repairs_a_failing_test(tmp_path):
    (tmp_path / "v.py").write_text("VALUE = 1\n")
    fix = """v.py
<<<<<<< SEARCH
VALUE = 1
=======
VALUE = 2
>>>>>>> REPLACE"""
    noop = """v.py
<<<<<<< SEARCH
VALUE = 1
=======
VALUE = 1
>>>>>>> REPLACE"""

    client = make_client(by_label={
        "code:explore": {"content": '{"files": [{"path": "v.py", "why": "x"}]}'},
        "code:plan": {"content": "plan"},
        "code:implement": {"content": noop},
        "code:repair-test": {"content": fix},
        "code:review": {"content": '{"verdict": "ship", "findings": []}'},
        "distill": {"content": "{}"},
    })
    agent = CodingAgent(
        client, root=tmp_path,
        verify_command='python3 -c "import v; assert v.VALUE == 2"',
    )
    agent.refusals.use_classifier = False
    result = agent.run("make VALUE 2", review=False)

    assert result.verification.ok
    assert (tmp_path / "v.py").read_text() == "VALUE = 2\n"
    assert any("still failed" in d.text for d in result.memory.dead_ends)


def test_coding_agent_reports_failure_when_no_edit_applies(tmp_path):
    (tmp_path / "z.py").write_text("A = 1\n")
    client = make_client(by_label={
        "code:explore": {"content": '{"files": []}'},
        "code:plan": {"content": "plan"},
        "code:implement": {"content": "I thought about it but produced no blocks."},
        "distill": {"content": "{}"},
    })
    agent = CodingAgent(client, root=tmp_path)
    agent.refusals.use_classifier = False
    result = agent.run("do something", review=True)
    assert not result.ok
    assert "No edits could be applied" in result.output


# --------------------------------------------------------------------------

def test_harness_routes_and_runs_the_direct_lane():
    client = make_client([{"content": "direct"}, {"content": "the answer"}])
    h = Harness(Config(api_key="k"))
    h.client = client
    h.telemetry = client.telemetry
    outcome = h.run("what is 2+2?")
    assert outcome.lane == "direct"
    assert outcome.output == "the answer"


def test_tool_loop_executes_a_tool_then_finishes(tmp_path):
    (tmp_path / "hello.txt").write_text("world\n")
    client = make_client([
        {"content": None, "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "hello.txt"}'}}]},
        {"content": "The file says: world"},
    ])
    h = Harness(Config(api_key="k"), root=tmp_path)
    h.client = client
    h.telemetry = client.telemetry
    result = h.tool_loop("read hello.txt")
    assert "world" in result.output
    assert result.steps[0]["tool"] == "read_file"


# --------------------------------------------------------------------------
# Model alias probing
# --------------------------------------------------------------------------

def test_renamed_model_is_probed_and_cached():
    """A retired model ID should cost one probe, not a failed run."""
    from deepseek_harness.errors import APIError

    client = make_client(by_label={"": {"content": "answer"}})
    real = client.transport.post_json
    rejected: list[str] = []

    def flaky(path, payload, *, label=""):
        if payload["model"] == "deepseek-flash":
            rejected.append(payload["model"])
            raise APIError("HTTP 404", 404, '{"error": "model not found"}')
        return real(path, payload, label=label)

    client.transport.post_json = flaky
    out = client.complete([M.user("q")], model="deepseek-flash", thinking=Thinking.OFF)

    assert out.text == "answer"
    assert out.model in ("deepseek-v4-flash", "deepseek-v4.1-flash", "deepseek-chat")
    # Second call goes straight to the discovered alias.
    before = len(rejected)
    client.complete([M.user("q2")], model="deepseek-flash", thinking=Thinking.OFF)
    assert len(rejected) == before


def test_unrelated_api_error_is_not_treated_as_a_rename():
    from deepseek_harness.errors import APIError

    client = make_client()
    def always_400(path, payload, *, label=""):
        raise APIError("HTTP 400", 400, '{"error": "context length exceeded"}')
    client.transport.post_json = always_400

    with pytest.raises(APIError):
        client.complete([M.user("q")], model="deepseek-flash", thinking=Thinking.OFF)


def test_thinking_has_no_phantom_constructor_field():
    import dataclasses
    assert [f.name for f in dataclasses.fields(Thinking)] == ["enabled", "effort"]
    assert Thinking.OFF.enabled is False


# --------------------------------------------------------------------------
# Compaction -- the load-bearing feature that previously had no coverage
# --------------------------------------------------------------------------

def _fill(convo, n, prefix="t"):
    for i in range(n):
        convo.add_user(f"{prefix} user {i}")
        convo.add(M.assistant(f"{prefix} assistant {i}", reasoning=f"cot {i}"))


def test_compaction_folds_the_head_and_keeps_the_live_tail():
    from deepseek_harness.context import Conversation

    convo = Conversation(Config(keep_live_turns=4), system_prompt="sys")
    client = make_client(by_label={"compact": {"content": "DIGEST"}})
    _fill(convo, 12)
    assert len(convo.transcript) == 24

    convo.compact(client)
    assert len(convo.transcript) == 4
    assert convo.digest == "DIGEST"
    # The surviving tail keeps its verbatim chain of thought.
    assert all(m.reasoning_content for m in convo.transcript if m.role == "assistant")


def test_compacted_digest_is_rendered_as_user_role_not_assistant():
    """A digest has no reasoning_content, so it must never occupy an assistant turn."""
    from deepseek_harness.context import Conversation

    convo = Conversation(Config(keep_live_turns=2), system_prompt="sys")
    client = make_client(by_label={"compact": {"content": "DIGEST"}})
    _fill(convo, 8)
    convo.compact(client)

    rendered = M.render(convo.render(), thinking=True)
    digest_turns = [m for m in rendered if "prior_context_digest" in (m.get("content") or "")]
    assert len(digest_turns) == 1
    assert digest_turns[0]["role"] == "user"
    for m in rendered:
        if m["role"] == "assistant":
            assert m.get("reasoning_content")


def test_compaction_never_orphans_a_tool_result():
    from deepseek_harness.context import Conversation

    convo = Conversation(Config(keep_live_turns=2), system_prompt="s")
    client = make_client(by_label={"compact": {"content": "D"}})
    call = M.ToolCall("id1", "read_file", '{"path": "a"}')
    for i in range(6):
        convo.add_user(f"u{i}")
        convo.add(M.Msg("assistant", None, reasoning_content="r", tool_calls=[call]))
        convo.add(M.tool_result("id1", f"result {i}"))

    convo.compact(client)
    assert convo.transcript[0].role != "tool", "a tool result was split from its call"


def test_digest_stays_bounded_across_many_compactions():
    """Regression: the digest is re-sent every request, so it cannot grow forever."""
    from deepseek_harness.context import Conversation

    cap = 2_000
    convo = Conversation(Config(keep_live_turns=2, max_digest_chars=cap), system_prompt="s")
    chunk = "SUMMARY " * 120  # ~960 chars per compaction
    client = make_client(by_label={
        "compact:digest": {"content": "FOLDED"},
        "compact": {"content": chunk},
    })
    for n in range(10):
        _fill(convo, 6, prefix=f"r{n}")
        convo.compact(client)

    assert len(convo.digest) <= cap, f"digest grew to {len(convo.digest)} chars"


def test_should_compact_fires_on_a_full_context():
    from deepseek_harness.context import Conversation

    convo = Conversation(Config(compact_at_fraction=0.5), system_prompt="s")
    assert not convo.should_compact("deepseek-flash")
    convo.add_user("x" * 4_000_000)   # well past half of a 1M-token window
    assert convo.should_compact("deepseek-flash")


def test_korean_is_not_counted_as_cheaply_as_english():
    """A blended chars/token constant underestimates Hangul by 2-3x."""
    korean = M.approx_tokens([M.user("한" * 1000)])
    english = M.approx_tokens([M.user("a" * 1000)])
    assert korean > english * 2


# --------------------------------------------------------------------------
# Truncated responses
# --------------------------------------------------------------------------

def test_truncated_response_is_resumed_and_stitched():
    parts = iter([
        {"content": "def f():\n    ret", "finish": "length"},
        {"content": "urn 1\n", "finish": "stop"},
    ])
    client = make_client(by_label={"": lambda: next(parts)})
    out = client.complete([M.user("write f")], thinking=Thinking.OFF)

    assert out.text == "def f():\n    return 1\n"
    assert not out.truncated
    # Usage is summed across both segments, not just the last.
    assert out.usage.completion_tokens == 80


def test_continuation_is_bounded():
    client = make_client()
    client.transport.default = {"content": "chunk ", "finish": "length"}
    out = client.complete([M.user("q")], thinking=Thinking.OFF, auto_continue=2)

    assert out.truncated                      # still truncated, but we stopped
    assert len(client.transport.labels) == 3  # initial + exactly 2 continuations


def test_auto_continue_can_be_disabled():
    client = make_client()
    client.transport.default = {"content": "partial", "finish": "length"}
    out = client.complete([M.user("q")], thinking=Thinking.OFF, auto_continue=0)
    assert out.truncated and len(client.transport.labels) == 1


def test_continuation_replays_the_partial_turn_with_its_reasoning():
    parts = iter([
        {"content": "half", "reasoning": "my cot", "finish": "length"},
        {"content": " done", "finish": "stop"},
    ])
    client = make_client(by_label={"": lambda: next(parts)})
    client.complete([M.user("q")], thinking=Thinking(True, "high"))

    second = client.transport.payloads[1]["messages"]
    assistant_turns = [m for m in second if m["role"] == "assistant"]
    assert assistant_turns[0]["reasoning_content"] == "my cot"


# --------------------------------------------------------------------------
# Execution-guided candidate selection
# --------------------------------------------------------------------------

def _block(path, search, replace):
    return f"{path}\n<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE"


def test_selection_picks_the_candidate_that_passes_the_tests(tmp_path):
    """The whole point: the test suite chooses, not the model's self-assessment."""
    from deepseek_harness import selection

    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    wrong = _block("calc.py", "    return a - b", "    return a * b")
    right = _block("calc.py", "    return a - b", "    return a + b")
    also_wrong = _block("calc.py", "    return a - b", "    return a - b - 1")

    responses = [wrong, right, also_wrong]
    result = selection.select(
        tmp_path, lambda i: responses[i],
        # 2+2 cannot separate + from *; the oracle has to discriminate.
        'python3 -c "import calc; assert calc.add(2,3)==5"', n=3,
    )
    try:
        assert result.any_passed
        assert result.winner.index == 1, result.render()
        assert selection.adopt(result.winner, tmp_path) == ["calc.py"]
        assert "return a + b" in (tmp_path / "calc.py").read_text()
    finally:
        selection.cleanup(result)


def test_a_weak_verification_command_admits_a_wrong_patch(tmp_path):
    """Pins the ceiling: selection rejects only what the test actually catches."""
    from deepseek_harness import selection

    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    multiply = _block("calc.py", "    return a - b", "    return a * b")

    result = selection.select(
        tmp_path, lambda i: multiply,
        'python3 -c "import calc; assert calc.add(2,2)==4"', n=1,
    )
    try:
        # Wrong code, passing suite. The gain is bounded by the oracle.
        assert result.winner.passed
    finally:
        selection.cleanup(result)


def test_candidates_are_isolated_from_the_real_workspace(tmp_path):
    from deepseek_harness import selection

    (tmp_path / "m.py").write_text("V = 1\n")
    result = selection.select(
        tmp_path, lambda i: _block("m.py", "V = 1", f"V = {i + 10}"),
        "python3 -c \"import m; assert m.V == 11\"", n=3,
    )
    try:
        # Nothing is written back until adopt() is called explicitly.
        assert (tmp_path / "m.py").read_text() == "V = 1\n"
        assert result.winner.index == 1
    finally:
        selection.cleanup(result)


def test_selection_survives_a_candidate_that_raises(tmp_path):
    from deepseek_harness import selection

    (tmp_path / "m.py").write_text("V = 1\n")

    def generate(i):
        if i == 0:
            raise RuntimeError("model call blew up")
        return _block("m.py", "V = 1", "V = 2")

    result = selection.select(
        tmp_path, generate, 'python3 -c "import m; assert m.V == 2"', n=2)
    try:
        assert result.candidates[0].error.startswith("RuntimeError")
        assert result.winner.index == 1 and result.winner.passed
    finally:
        selection.cleanup(result)


def test_when_none_pass_the_smallest_applied_patch_wins(tmp_path):
    from deepseek_harness import selection

    (tmp_path / "m.py").write_text("V = 1\nW = 2\n")
    big = (_block("m.py", "V = 1", "V = 99  # a much longer replacement line here")
           + "\n\n" + _block("m.py", "W = 2", "W = 98  # and another long one"))
    small = _block("m.py", "V = 1", "V = 3")

    result = selection.select(
        tmp_path, lambda i: [big, small][i], 'python3 -c "assert False"', n=2)
    try:
        assert not result.any_passed
        assert result.winner.index == 1, result.render()
    finally:
        selection.cleanup(result)


def test_workspace_size_guard(tmp_path):
    from deepseek_harness import selection

    (tmp_path / "m.py").write_text("V = 1\n" + "# padding\n" * 500)
    result = selection.select(
        tmp_path, lambda i: _block("m.py", "V = 1", "V = 2"),
        None, n=1, max_workspace_bytes=100,
    )
    try:
        assert "exceeds 100 bytes" in result.candidates[0].error
        assert result.winner is None
    finally:
        selection.cleanup(result)


def test_cleanup_removes_every_workspace(tmp_path):
    from deepseek_harness import selection

    (tmp_path / "m.py").write_text("V = 1\n")
    result = selection.select(tmp_path, lambda i: _block("m.py", "V = 1", "V = 2"),
                              None, n=2)
    workspaces = [c.workspace for c in result.candidates if c.workspace]
    assert workspaces and all(w.exists() for w in workspaces)
    selection.cleanup(result)
    assert not any(w.exists() for w in workspaces)
    selection.cleanup(result)  # idempotent


# --------------------------------------------------------------------------
# Repo map
# --------------------------------------------------------------------------

def test_repomap_extracts_python_signatures(tmp_path):
    from deepseek_harness import repomap

    (tmp_path / "svc.py").write_text(
        "TIMEOUT = 30\n\n"
        "class Uploader:\n"
        "    def upload(self, path: str, retries: int = 3) -> bool:\n"
        "        return True\n\n"
        "async def fetch(url: str) -> dict:\n"
        "    return {}\n"
    )
    rendered = repomap.build(tmp_path, "upload retries").render()
    assert "class Uploader" in rendered
    assert "def upload(self, path: str, retries: int = 3) -> bool" in rendered
    assert "async def fetch(url: str) -> dict" in rendered
    assert "TIMEOUT" in rendered


def test_repomap_ranks_task_relevant_files_first(tmp_path):
    from deepseek_harness import repomap

    (tmp_path / "uploader.py").write_text("def upload_chunk(data):\n    pass\n")
    (tmp_path / "unrelated.py").write_text("def render_template(name):\n    pass\n")
    ranked = repomap.build(tmp_path, "fix upload_chunk retry handling").top_paths(2)
    assert ranked[0] == "uploader.py"


def test_repomap_ranks_central_modules_above_leaves(tmp_path):
    from deepseek_harness import repomap

    (tmp_path / "core.py").write_text("def shared_helper():\n    pass\n")
    for i in range(4):
        (tmp_path / f"leaf{i}.py").write_text(
            f"from core import shared_helper\n\ndef leaf_{i}():\n    shared_helper()\n")
    # The task names nothing, so only centrality can order these.
    ranked = repomap.build(tmp_path, "general cleanup").top_paths(1)
    assert ranked[0] == "core.py"


def test_repomap_respects_its_character_budget(tmp_path):
    from deepseek_harness import repomap

    for i in range(30):
        (tmp_path / f"m{i}.py").write_text(
            "".join(f"def fn_{i}_{j}(a, b, c):\n    pass\n" for j in range(20)))
    rendered = repomap.build(tmp_path, "x").render(max_chars=2_000)
    assert len(rendered) <= 2_200
    assert "not shown" in rendered


def test_repomap_handles_unparseable_and_binary_gracefully(tmp_path):
    from deepseek_harness import repomap

    (tmp_path / "broken.py").write_text("def f(:\n  syntax error\n")
    (tmp_path / "ok.py").write_text("def good():\n    pass\n")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")
    assert "def good" in repomap.build(tmp_path, "x").render()
