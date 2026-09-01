"""The agent: system prompt assembly, the tool loop, and SSE framing."""
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

from chat import config
from chat.schema_tool import TOOL_NAME, render_fields, tool_definition

log = logging.getLogger(__name__)

PERSONA = """\
You are the assistant for the Delft-FEWS Configuration Guide at
https://df-docs.streamflows.org. You help people configure Delft-FEWS, the
streamflow forecasting and time series management system.

The complete documentation for this site is provided below. Answer from it.

Rules:
- Ground every answer in the provided documentation. When you state how FEWS
  behaves, it should be traceable to something in that text.
- Link to the relevant page using the URL given in its header, so the reader can
  go deeper.
- The documentation explains concepts but does NOT contain the field and
  attribute tables for config files. When a question needs that level of detail —
  which fields exist, what type they are, whether they are required, what enum
  values are allowed — call the lookup_config_fields tool. Do not guess at field
  names.
- If the documentation does not cover something, say so plainly and suggest where
  the reader might look. Your audience is configuring a live forecasting system,
  where a confident wrong answer is far worse than an admitted gap.
- Be concise and concrete. Prefer a short XML example over a long explanation.

Formatting. Your answers render in a narrow (420px) side panel that supports a
limited subset of Markdown. Use it, and stay inside it:
- Put every config example in a fenced code block with a language tag —
  ```xml, ```csv or ```text. Never present XML as indented prose.
- Use ## or ### headings only when an answer has genuinely separate sections.
- Use - bullets and 1. numbered lists for steps and field lists.
- Use `backticks` for element, attribute and file names.
- Tables are supported and are good for field references. Keep them to three
  or four narrow columns; the panel is narrow and wide tables must scroll.
- Nested lists, blockquotes, images and raw HTML do NOT render — they appear
  as literal characters. Do not use them.
"""


def sse(event: str, data: dict) -> str:
    """One Server-Sent Event. JSON encoding keeps newlines out of the framing."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class Agent:
    def __init__(self, corpus: str, schema_dir: Path, client):
        self.corpus = corpus
        self.schema_dir = Path(schema_dir)
        self.client = client
        self._tools = [tool_definition(self.schema_dir)]
        # Measured against a live call: this corpus is 282,885 chars and
        # billed 112,090 input tokens, i.e. ~2.5 chars/token — technical prose
        # full of XML and identifiers tokenizes far denser than the usual ~4.
        # Divide by 2 so the figure errs HIGH: it drives the pre-dispatch
        # reservation, where under-estimating is what lets spend outrun the
        # ceiling, and the abandoned-request floor charge.
        self._corpus_tokens = len(corpus) // 2

    def minimum_usage(self):
        """A conservative floor for a request that upstream billed but whose
        usage object we never saw, because the browser hung up mid-stream.
        Without this, disconnecting early every time evades the daily ceiling
        entirely while Anthropic still charges for the call."""
        return SimpleNamespace(
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=self._corpus_tokens,
        )

    def system_blocks(self) -> list[dict]:
        """Persona first, corpus last. The cache breakpoint sits on the final
        block so it covers the tools and the persona as well as the corpus."""
        return [
            {"type": "text", "text": PERSONA},
            {
                "type": "text",
                "text": self.corpus,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ]

    def tools(self) -> list[dict]:
        return self._tools

    def _handle_tool_use(self, block) -> dict:
        if block.name != TOOL_NAME:
            body = f"Unknown tool {block.name!r}."
        else:
            body = render_fields(
                self.schema_dir, (block.input or {}).get("config_file", "")
            )
        return {"type": "tool_result", "tool_use_id": block.id, "content": body}

    def estimated_cost(self, budget) -> float:
        """Worst case for ONE call: a cold cache write plus a full-length
        answer. Reserved before dispatch and settled to the real figure after,
        so the reservation is held only for the duration of that call. Erring
        high is the correct direction for a spend ceiling — it refuses near the
        cap rather than sailing past it."""
        return budget.cost_of(
            SimpleNamespace(
                input_tokens=0,
                output_tokens=config.MAX_TOKENS,
                cache_creation_input_tokens=self._corpus_tokens,
                cache_read_input_tokens=0,
            )
        )

    def run(self, messages: list[dict], on_usage=None, on_reserve=None) -> Iterator[str]:
        """Stream one answer, transparently resolving tool calls along the way.

        on_usage is invoked with each completed response's usage object, as soon
        as that API call finishes. It is NOT deferred to the end of the stream:
        a generator abandoned when the browser disconnects never runs its tail,
        so end-of-stream accounting would let a client hang up mid-answer every
        time and never be charged against the daily ceiling, while Anthropic
        bills the account regardless.
        """
        convo = list(messages)
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        # Counters, not a boolean. A single `charged` flag is set by the first
        # completed call and never reset, so a disconnect during round 2 of a
        # tool loop left that call — already billed upstream — recorded
        # nowhere. Deterministic, not a race: it fired on every disconnect in
        # any second-or-later round.
        dispatched = 0
        recorded = 0

        try:
            for _ in range(config.MAX_TOOL_CALLS + 1):
                if on_reserve is not None and not on_reserve():
                    yield sse(
                        "error",
                        {
                            "message": "The assistant has reached its daily "
                            "limit and is resting until tomorrow. The "
                            "documentation is still all here."
                        },
                    )
                    return

                dispatched += 1
                with self.client.messages.stream(
                    model=config.MODEL,
                    max_tokens=config.MAX_TOKENS,
                    output_config={"effort": config.EFFORT},
                    system=self.system_blocks(),
                    tools=self.tools(),
                    messages=convo,
                ) as stream:
                    for text in stream.text_stream:
                        if text:
                            yield sse("delta", {"text": text})
                    final = stream.get_final_message()

                # Charge for this call before anything else can go wrong.
                if on_usage is not None:
                    on_usage(final.usage)
                    recorded += 1
                for key in totals:
                    value = getattr(final.usage, key, 0)
                    totals[key] += value if isinstance(value, int) else 0

                if final.stop_reason == "refusal":
                    yield sse(
                        "error",
                        {
                            "message": "The assistant declined to answer that "
                            "one. Try rephrasing, or ask something else."
                        },
                    )
                    return

                if final.stop_reason != "tool_use":
                    break

                tool_blocks = [
                    b for b in final.content if getattr(b, "type", None) == "tool_use"
                ]
                if not tool_blocks:
                    break

                convo.append({"role": "assistant", "content": final.content})
                convo.append(
                    {
                        "role": "user",
                        "content": [self._handle_tool_use(b) for b in tool_blocks],
                    }
                )
            else:
                # Loop ran to its cap with the model still asking for tools.
                yield sse(
                    "delta",
                    {
                        "text": "\n\n(I looked up as many config files as I can "
                        "in one go. Ask a follow-up if you need more.)"
                    },
                )

            yield sse("done", {"usage": totals})

        except Exception:
            # Log the detail server-side; show the user a sentence, not a stack.
            log.exception("chat completion failed")
            yield sse(
                "error",
                {
                    "message": "The assistant is unreachable right now. "
                    "Please try again in a moment."
                },
            )

        finally:
            # Runs on GeneratorExit too, which is what a browser disconnect
            # looks like from here. If we never saw a usage object the request
            # was still billed upstream, so charge the floor rather than let a
            # client evade the ceiling by hanging up every time.
            # One floor charge per call that was dispatched but never recorded,
            # not one per run. Each was billed upstream regardless.
            for _ in range(max(0, dispatched - recorded)) if on_usage else ():
                try:
                    on_usage(self.minimum_usage())
                except Exception:
                    # Nothing above catches this one — an exception escaping a
                    # finally block leaves the client with a committed 200 and
                    # a reset connection instead of an error frame.
                    log.exception("could not record the floor charge")
