from proxy import extract_model, extract_usage


def test_extract_model_reads_model_field():
    assert extract_model(b'{"model": "gpt-5-mini", "messages": []}') == "gpt-5-mini"


def test_extract_model_returns_none_when_field_missing():
    assert extract_model(b'{"messages": []}') is None


def test_extract_model_returns_none_for_invalid_json():
    assert extract_model(b'not json') is None


def test_extract_model_returns_none_for_empty_body():
    assert extract_model(None) is None
    assert extract_model(b"") is None


def test_extract_usage_reads_token_counts():
    body = b'{"usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}'
    assert extract_usage(body) == {
        "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}


def test_extract_usage_returns_none_when_usage_missing():
    assert extract_usage(b'{"id": "chatcmpl-1"}') is None


def test_extract_usage_returns_none_for_streamed_sse_body():
    # Multiple concatenated SSE frames aren't valid single-document JSON.
    body = b'data: {"id": "chatcmpl-1"}\n\ndata: {"id": "chatcmpl-2"}\n\ndata: [DONE]\n\n'
    assert extract_usage(body) is None


def test_extract_usage_returns_none_for_empty_body():
    assert extract_usage(None) is None
    assert extract_usage(b"") is None
