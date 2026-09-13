# deepseek-harness

A harness for DeepSeek V4 built around one premise: on multi-turn work the
dominant variable is not the prompt, it is **whether reasoning state survives
between turns, and what happens to it when the context fills.**

That premise is not a guess. On ARC-AGI-3, the same GPT-6 Astra weights scored
62.71% through ARC Prize's provider-neutral harness and 98.55% at the same
reasoning effort through OpenAI's Provider Adapter — a ~36 point swing from
nothing but reasoning-state continuity and compaction. The adapter also used
49% fewer tokens and ran 3.66x faster, which is the tell that it removed waste
rather than bought compute.

DeepSeek V4 turns the first half of that into a hard requirement, and leaves
the second half to you. This harness handles both.

## The correctness problem most clients have

In thinking mode, DeepSeek requires the assistant turn's `reasoning_content` to
be replayed **verbatim** on the next request. Omitting it is a 400. Sending an
empty string is a 400. This is the reverse of the old R1 behaviour, where
`reasoning_content` had to be stripped, and it is currently open as a bug
against OpenClaw, OpenCode, LiteLLM and qwen-code.

`messages.py` encodes the rule, and its consequence:

| situation | what this harness sends |
|---|---|
| assistant turn with CoT, thinking on | `reasoning_content` verbatim |
| assistant turn with CoT, thinking off | field omitted |
| CoT is blank | field omitted (never `""`) |
| CoT was dropped by compaction | turn downgraded to a `user`-role digest |

That last row is the one that matters. A turn whose chain of thought is gone
**cannot** be replayed as an assistant message, so it is never sent as one.

## Layout

```
config.py      model registry, peak/off-peak pricing, budgets
transport.py   HTTP + retry/backoff (httpx if present, else stdlib)
messages.py    message types, reasoning round-trip, safe degradation
client.py      param negotiation, model alias probing, telemetry
context.py     conversation assembly + compaction
memory.py      durable working memory (facts / decisions / dead ends)
refusal.py     refusal detection + bounded re-request
patch.py       SEARCH/REPLACE edit engine
tools.py       tool registry + workspace-scoped builtins
agents/        reasoning.py, coding.py
runner.py      routing, budget enforcement, tool loop
```

## Cache-optimal ordering

DeepSeek's context cache is a **prefix** cache and hits are ~50x cheaper than
misses, so `Conversation.render()` runs most stable to most volatile:

```
system -> task context -> digest -> append-only transcript -> working memory
```

Working memory changes every turn, so it goes last and is marked `ephemeral`:
it is re-rendered fresh each call and never written into the transcript.
Persisting it would invalidate the prefix on every turn *and* bury stale
snapshots in history.

## Compact, never truncate

Dropping the oldest turns silently deletes the decisions and dead ends that
keep a long run from looping. `context.py` instead folds the middle of the
transcript into a digest that is explicitly required to carry forward:
decisions and why, dead ends and their failure mode, verified facts, current
state, open questions.

`memory.py` holds the same information in structured form so it survives even
that. `dead_ends` earns its place: the dominant failure mode of a long agent
loop is re-attempting what already failed forty turns ago.

## The two flagship lanes

**Reasoning** (`agents/reasoning.py`) — frame → hypotheses → solve → verify →
synthesise. Each stage is a place where the model's initial framing can be
rejected: `hypotheses` forces at least two genuinely distinct candidates and
asks what evidence discriminates between them; `verify` runs on a **clean
context** holding only the problem and the answer, so it does not inherit the
solver's blind spots. `samples > 1` adds best-of-N with a judge.

**Coding** (`agents/coding.py`) — explore → plan → edit → apply → verify →
repair → review. Planning and editing are separate calls, often on separate
models: thinking-mode depth pays when planning and actively hurts when the job
is transcribing exact SEARCH text. A failed patch is *repaired* with the
specific reason ("matched 3 places", plus the closest real lines), not
regenerated. Every failure is written to dead-end memory.

Edits use anchored SEARCH/REPLACE (`patch.py`) because a mismatch is
*detectable*: a block matching twice is rejected rather than applied to the
first hit, and a block matching nowhere comes back with the closest lines that
actually exist so the model can correct it.

That is a reliability argument, **not an accuracy one**. Published comparisons
put edit formats within a few points of each other for strong models, and one
study measured search/replace 1.3 points *below* whole-file for DeepSeek-V3.
With V4's 384K output cap, whole-file rewriting is also far more viable than it
was. Treat the format as a tunable to A/B on your own tasks, not a settled win.

## Refusal handling

Configurable via `refusal_max_attempts` (default 3). Detection is multilingual
(EN/KO/ZH) and separates three cases that look alike in text: a **capability**
gap ("I don't have access to that file"), a **spurious** refusal (ordinary
technical work misread as harmful), and a **principled** one.

Retries work by supplying information — restating the operator's real context,
making the ask concrete, decomposing it, switching model. They deliberately do
**not** apply escalating pressure, inject a persona, or forge an assistant
prefix. Those do not fix a correct refusal, they only launder it, and on the
far more common spurious case they measurably degrade the output you get. When
the bound is spent the refusal is handed back with the full attempt trail
rather than looped on.

`purpose_context` is operator-supplied text (engagement scope, ticket,
authorisation) attached on retry. You assert it; the harness does not invent it.

## Usage

```bash
export DEEPSEEK_API_KEY=sk-...

python -m deepseek_harness "why does the retry loop drop the last error?"
python -m deepseek_harness --lane coding --verify "pytest -q" "fix the off-by-one in parse_range"
python -m deepseek_harness --lane reasoning --samples 3 --effort max "<hard problem>"
python -m deepseek_harness --max-usd 5 --verbose --json "<task>"
```

```python
from deepseek_harness import Harness, Config, Budget

with Harness(Config(budget=Budget(max_usd=5.0)), root=".",
             verify_command="pytest -q") as h:
    outcome = h.run("add retry to the uploader", lane="coding")
    print(outcome.output, outcome.report())
```

## Measuring whether a change helped

`Telemetry` tracks cache hit rate, reasoning tokens and cost per call, because
the useful question is not "did quality go up" but **"did quality go up while
tokens went down"**. Quality up *and* cost up usually means you bought compute,
and the next model generation will take that back for free.

```
calls=11  in=284,102 (cache 71%)  out=18,340  think=42,110  $0.2214  96.3s
```

## Notes

- Model IDs move. `deepseek-chat` / `deepseek-reasoner` were retired after
  2026-07-24; the current lineup is `deepseek-flash` and `deepseek-v4-pro`.
  Override with `DSH_MODEL_FLASH` / `DSH_MODEL_PRO`, and `client._resolve()`
  probes known aliases once before giving up.
- Prices in `config.py` are peak rates and approximate; off-peak is half, from
  01:00–04:00 and 06:00–10:00 UTC Mon–Fri being the only peak windows. Verify
  against DeepSeek's current pricing page before trusting a cost ceiling.
- Zero required dependencies. `pip install httpx` for connection pooling.

## Tests

```bash
python -m pytest tests/ -q     # 69 tests, no network, no API key
```

Unit coverage for the round-trip rule, cache ordering, compaction, the patch
engine, refusal classification, pricing and budgets; end-to-end wiring for both
agents and the tool loop against a mocked transport.

## Known gaps

Be aware of these before trusting it in production:

- **It has never run against the live API.** Every wire-level assumption --
  parameter names, the exact 400 for a missing round-trip, usage field names,
  model IDs -- comes from documentation summaries, not from a real call. First
  contact may need fixes.
- **No streaming.** `stream` is always false, so a long generation gives no
  progress and cannot be cancelled early. Truncation is handled by resuming
  (`auto_continue`) rather than by streaming.
- **The token estimator is unvalidated** against DeepSeek's tokeniser. It is
  script-aware and tuned to over-count, but the compaction threshold rests on it.
- **The cache-ordering claim is unmeasured.** The layout follows from how prefix
  caches work; nobody has yet watched `cache_hit_rate` on a real run to confirm
  the win.
- **`run_command` is `shell=True` with no allowlist or sandbox.** Paths are
  confined to the workspace root; commands are not. Run it where you would run
  an untrusted script.
