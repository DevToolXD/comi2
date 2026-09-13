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
