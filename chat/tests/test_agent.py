import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chat import config
from chat.agent import Agent, sse


class FakeStream:
    """Stands in for the SDK's streaming context manager."""

    def __init__(self, chunks, final):
        self.text_stream = iter(chunks)
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._final


def message(stop_reason="end_turn", content=None, usage=None):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content or [],
        usage=usage
        or SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


def tool_use_block(name="lookup_config_fields", cfg="locations", block_id="tu_1"):
    return SimpleNamespace(type="tool_use", id=block_id, name=name,
                           input={"config_file": cfg})


@pytest.fixture
def client():
    return MagicMock()


@pytest.fixture
def agent(client, tmp_path):
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    (schema_dir / "locations.json").write_text(json.dumps({
        "element": "locations",
        "types": {"LocationComplexType": {
            "doc": "A location.",
            "attributes": [{"name": "id", "type": "string", "use": "required"}],
            "fields": [],
        }},
    }))
    return Agent(corpus="THE CORPUS", schema_dir=schema_dir, client=client)


def collect(gen):
    return "".join(gen)


def test_sse_frames_are_well_formed():
    frame = sse("delta", {"text": "hi"})
    assert frame.startswith("event: delta\ndata: ")
    assert frame.endswith("\n\n")
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload == {"text": "hi"}


def test_sse_escapes_newlines_via_json():
    frame = sse("delta", {"text": "a\nb"})
    assert frame.count("\n\n") == 1


def test_system_blocks_put_the_corpus_last_with_a_cache_breakpoint(agent):
    blocks = agent.system_blocks()
    assert blocks[-1]["text"] == "THE CORPUS"
    assert blocks[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "cache_control" not in blocks[0]


def test_tools_are_stable_across_calls(agent):
    assert agent.tools() == agent.tools()


def test_plain_answer_streams_text_and_ends(agent, client):
    client.messages.stream.return_value = FakeStream(["Hello ", "world"], message())
    out = collect(agent.run([{"role": "user", "content": "hi"}]))
    assert "Hello " in out and "world" in out
    assert "event: done" in out
    assert client.messages.stream.call_count == 1


def test_request_uses_the_configured_model_and_effort(agent, client):
    client.messages.stream.return_value = FakeStream(["x"], message())
    collect(agent.run([{"role": "user", "content": "hi"}]))
    kwargs = client.messages.stream.call_args.kwargs
    assert kwargs["model"] == config.MODEL
    assert kwargs["output_config"] == {"effort": config.EFFORT}
    assert kwargs["max_tokens"] == config.MAX_TOKENS
    assert "thinking" not in kwargs


def test_tool_use_triggers_a_second_request_in_the_same_stream(agent, client):
    first = message(stop_reason="tool_use", content=[tool_use_block()])
    client.messages.stream.side_effect = [
        FakeStream(["Let me check. "], first),
        FakeStream(["The id attribute is required."], message()),
    ]
    out = collect(agent.run([{"role": "user", "content": "fields?"}]))
    assert client.messages.stream.call_count == 2
    assert "The id attribute is required." in out
    assert "event: done" in out


def test_tool_result_is_appended_to_the_next_request(agent, client):
    first = message(stop_reason="tool_use", content=[tool_use_block()])
    client.messages.stream.side_effect = [
        FakeStream([""], first),
        FakeStream(["ok"], message()),
    ]
    collect(agent.run([{"role": "user", "content": "fields?"}]))
    sent = client.messages.stream.call_args_list[1].kwargs["messages"]
    tool_results = [
        block
        for m in sent
        if isinstance(m.get("content"), list)
        for block in m["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert len(tool_results) == 1
    assert "LocationComplexType" in tool_results[0]["content"]


def test_tool_loop_stops_at_the_cap(agent, client):
    looping = message(stop_reason="tool_use", content=[tool_use_block()])
    client.messages.stream.side_effect = [
        FakeStream([""], looping) for _ in range(config.MAX_TOOL_CALLS + 5)
    ]
    out = collect(agent.run([{"role": "user", "content": "loop"}]))
    assert client.messages.stream.call_count <= config.MAX_TOOL_CALLS + 1
    assert "event: done" in out


def test_api_failure_becomes_an_error_frame_not_an_exception(agent, client):
    client.messages.stream.side_effect = RuntimeError("upstream is down")
    out = collect(agent.run([{"role": "user", "content": "hi"}]))
    assert "event: error" in out
    assert "upstream is down" not in out  # no internals leaked to the browser


def test_usage_is_charged_per_api_call_not_at_the_end(agent, client):
    """Two API calls (one tool round trip) must produce two charges, each as
    soon as that call finishes."""
    first = message(stop_reason="tool_use", content=[tool_use_block()])
    client.messages.stream.side_effect = [
        FakeStream([""], first),
        FakeStream(["done"], message()),
    ]
    charges = []
    collect(agent.run([{"role": "user", "content": "fields?"}], on_usage=charges.append))
    assert len(charges) == 2


def test_abandoned_stream_is_still_charged(agent, client):
    """A generator abandoned when the browser disconnects never runs its tail.
    End-of-stream accounting would let a client hang up mid-answer every time
    and never be charged, while Anthropic bills the account regardless."""
    client.messages.stream.return_value = FakeStream(["partial ", "text"], message())
    charges = []
    gen = agent.run([{"role": "user", "content": "hi"}], on_usage=charges.append)
    next(gen)      # read one delta...
    gen.close()    # ...then disconnect
    assert len(charges) == 1
    assert charges[0].cache_read_input_tokens > 0


def test_a_failing_usage_callback_cannot_escape_the_generator(agent, client):
    """The finally block's floor charge is outside every except clause. If it
    raised, the client would get a committed 200 and a reset connection instead
    of an error frame."""
    client.messages.stream.side_effect = RuntimeError("upstream down")

    def boom(_usage):
        raise OSError("disk full")

    out = collect(agent.run([{"role": "user", "content": "hi"}], on_usage=boom))
    assert "event: error" in out


def test_refusal_surfaces_a_message_rather_than_a_blank_answer(agent, client):
    client.messages.stream.return_value = FakeStream([""], message(stop_reason="refusal"))
    out = collect(agent.run([{"role": "user", "content": "hi"}]))
    assert "event: error" in out
    assert "declined" in out


def test_refusal_is_still_charged(agent, client):
    client.messages.stream.return_value = FakeStream([""], message(stop_reason="refusal"))
    charges = []
    collect(agent.run([{"role": "user", "content": "hi"}], on_usage=charges.append))
    assert len(charges) == 1


def test_usage_is_reported_on_the_done_frame(agent, client):
    client.messages.stream.return_value = FakeStream(["x"], message())
    out = collect(agent.run([{"role": "user", "content": "hi"}]))
    done = [ln for ln in out.splitlines() if ln.startswith("data: ")][-1]
    payload = json.loads(done[len("data: "):])
    assert "usage" in payload
