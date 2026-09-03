from kakehashi.protocol_openai import (
    build_openai_sse_reasoning,
    extract_openai_stream_deltas,
)


def test_extract_content_and_reasoning():
    obj = {"choices": [{"delta": {"content": "hi", "reasoning_content": "think"}}]}
    assert extract_openai_stream_deltas(obj) == ("hi", "think")


def test_extract_reasoning_only():
    obj = {"choices": [{"delta": {"reasoning_content": "step1"}}]}
    assert extract_openai_stream_deltas(obj) == ("", "step1")


def test_extract_empty_delta():
    assert extract_openai_stream_deltas({"choices": [{"delta": {}}]}) == ("", "")
    assert extract_openai_stream_deltas({}) == ("", "")


def test_reasoning_chunk_format():
    import json
    line = build_openai_sse_reasoning("think", "m", "cid", 1)
    obj = json.loads(line[len("data: "):].strip())
    assert obj["choices"][0]["delta"] == {"reasoning_content": "think"}
