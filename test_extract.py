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


def test_extract_usage_reads_responses_api_token_names():
    # The Responses API reports input_tokens/output_tokens for the same counts
    # Chat Completions calls prompt_tokens/completion_tokens.
    body = (b'{"usage": {"input_tokens": 8, "output_tokens": 114, '
            b'"total_tokens": 122, "output_tokens_details": {"reasoning_tokens": 64}}}')
    assert extract_usage(body) == {
        "prompt_tokens": 8, "completion_tokens": 114, "total_tokens": 122}


def test_extract_usage_prefers_chat_completions_names_when_both_present():
    body = (b'{"usage": {"prompt_tokens": 1, "completion_tokens": 2, '
            b'"input_tokens": 99, "output_tokens": 99, "total_tokens": 3}}')
    assert extract_usage(body) == {
        "prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}


def test_extract_usage_keeps_zero_counts_instead_of_falling_through():
    # 0 is a real count, not "missing" -- it must not resolve to the alias.
    body = b'{"usage": {"prompt_tokens": 0, "input_tokens": 42, "total_tokens": 5}}'
    assert extract_usage(body)["prompt_tokens"] == 0


def test_extract_usage_handles_embeddings_without_a_completion_count():
    body = b'{"usage": {"prompt_tokens": 8, "total_tokens": 8}}'
    assert extract_usage(body) == {
        "prompt_tokens": 8, "completion_tokens": None, "total_tokens": 8}


def test_extract_model_survives_a_binary_multipart_body():
    # File uploads (audio transcription) aren't JSON and aren't even UTF-8.
    assert extract_model(b'--boundary\r\n\xff\xd8\xff\xe0binary\r\n') is None


RESPONSES_STREAM = (
    b'event: response.output_text.delta\n'
    b'data: {"type":"response.output_text.delta","delta":"Mount"}\n\n'
    b'event: response.completed\n'
    b'data: {"type":"response.completed","response":{"id":"resp_1","usage":'
    b'{"input_tokens":14,"output_tokens":653,"total_tokens":667}}}\n\n'
)

CHAT_STREAM = (
    b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
    b'data: {"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":21,'
    b'"total_tokens":30}}\n\n'
    b'data: [DONE]\n\n'
)


def test_extract_usage_reads_the_responses_api_completed_frame():
    # The terminal frame nests the whole response object, usage included.
    assert extract_usage(RESPONSES_STREAM) == {
        "prompt_tokens": 14, "completion_tokens": 653, "total_tokens": 667}


def test_extract_usage_reads_a_chat_completions_usage_chunk():
    assert extract_usage(CHAT_STREAM) == {
        "prompt_tokens": 9, "completion_tokens": 21, "total_tokens": 30}


def test_extract_usage_ignores_the_done_sentinel():
    assert extract_usage(b'data: [DONE]\n\n') is None


def test_extract_usage_returns_none_for_a_stream_carrying_no_usage():
    body = (b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
            b'data: [DONE]\n\n')
    assert extract_usage(body) is None
