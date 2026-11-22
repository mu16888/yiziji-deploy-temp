"""DeepSeek LLM client: JSON mode, injection, retry and key-safety contracts."""

import httpx
import pytest

from app.adapters.llm import LLMClient
from app.adapters.llm import deepseek as ds
from app.adapters.llm.deepseek import (
    LLMError,
    LLMBadResponse,
    LLMConfigError,
    LLMTimeout,
    DeepSeekClient,
)


# --- fake HTTP client (no real network, ever) ---------------------------------


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class ScriptedClient:
    """httpx-like client: returns queued steps; the last step repeats."""

    def __init__(self, steps):
        self._steps = list(steps)
        self.calls = []  # list of (url, kwargs)

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        step = self._steps[0] if len(self._steps) == 1 else self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _completion(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def fake_client(*steps):
    """Accept raw content strings (200 completion) or FakeResponse/exception steps."""
    normalized = []
    for step in steps:
        if isinstance(step, str):
            normalized.append(FakeResponse(200, _completion(step)))
        else:
            normalized.append(step)
    return ScriptedClient(normalized)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    """Zero-delay retry so tests stay instant (no production sleep)."""
    monkeypatch.setattr(ds, "RETRY_BACKOFF_SECONDS", 0)


@pytest.fixture(autouse=True)
def no_env_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


# --- protocol / error hierarchy -----------------------------------------------


def test_client_satisfies_llm_protocol():
    client = DeepSeekClient(api_key="k", http_client=fake_client('{"a": 1}'))
    assert isinstance(client, LLMClient)


def test_error_subclasses():
    assert issubclass(LLMConfigError, LLMError)
    assert issubclass(LLMTimeout, LLMError)
    assert issubclass(LLMBadResponse, LLMError)


# --- JSON mode ----------------------------------------------------------------


def test_complete_json_parses_fenced_json():
    client = DeepSeekClient(api_key="k", http_client=fake_client('```json\n{"a": 1}\n```'))
    assert client.complete_json("sys", "usr") == {"a": 1}


def test_complete_json_parses_plain_json():
    client = DeepSeekClient(api_key="k", http_client=fake_client('  {"a": 1, "b": [2]}  '))
    assert client.complete_json("sys", "usr") == {"a": 1, "b": [2]}


def test_complete_json_sends_expected_request():
    fake = fake_client('{"ok": true}')
    client = DeepSeekClient(api_key="k", http_client=fake)
    assert client.complete_json("SYS", "USR", temperature=0.2) == {"ok": True}

    url, kwargs = fake.calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    body = kwargs["json"]
    assert body["model"] == "deepseek-chat"
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.2
    assert kwargs["headers"]["Authorization"] == "Bearer k"


def test_custom_base_url_and_model():
    fake = fake_client('{"a": 1}')
    client = DeepSeekClient(
        api_key="k",
        base_url="http://localhost:9999/v1/",
        model="custom-model",
        http_client=fake,
    )
    client.complete_json("s", "u")
    url, kwargs = fake.calls[0]
    assert url == "http://localhost:9999/v1/chat/completions"
    assert kwargs["json"]["model"] == "custom-model"


# --- retries ------------------------------------------------------------------


def test_retry_on_429_then_success():
    fake = fake_client(
        FakeResponse(429, {"error": "rate limited"}),
        FakeResponse(200, _completion('{"a": 1}')),
    )
    client = DeepSeekClient(api_key="k", http_client=fake)
    assert client.complete_json("s", "u") == {"a": 1}
    assert len(fake.calls) == 2


def test_retry_on_5xx_then_success():
    fake = fake_client(
        FakeResponse(503, {}),
        FakeResponse(500, {}),
        FakeResponse(200, _completion('{"ok": true}')),
    )
    client = DeepSeekClient(api_key="k", http_client=fake)
    assert client.complete_json("s", "u") == {"ok": True}
    assert len(fake.calls) == 3


def test_retry_on_timeout_then_success():
    fake = fake_client(
        httpx.TimeoutException("timed out"),
        FakeResponse(200, _completion('{"a": 1}')),
    )
    client = DeepSeekClient(api_key="k", http_client=fake)
    assert client.complete_json("s", "u") == {"a": 1}
    assert len(fake.calls) == 2


def test_gives_up_after_two_retries():
    fake = fake_client(FakeResponse(429, {}))
    client = DeepSeekClient(api_key="k", http_client=fake)
    with pytest.raises(LLMError):
        client.complete_json("s", "u")
    assert len(fake.calls) == 3  # 1 initial + 2 retries


def test_timeout_exhausted_raises_llm_timeout():
    fake = fake_client(httpx.TimeoutException("timed out"))
    client = DeepSeekClient(api_key="k", http_client=fake)
    with pytest.raises(LLMTimeout):
        client.complete_json("s", "u")
    assert len(fake.calls) == 3


def test_non_retryable_4xx_raises_without_retry():
    fake = fake_client(FakeResponse(400, {}))
    client = DeepSeekClient(api_key="k", http_client=fake)
    with pytest.raises(LLMBadResponse):
        client.complete_json("s", "u")
    assert len(fake.calls) == 1


# --- malformed responses ------------------------------------------------------


def test_bad_json_raises_llmbadresponse():
    client = DeepSeekClient(api_key="k", http_client=fake_client("not json at all"))
    with pytest.raises(LLMBadResponse):
        client.complete_json("s", "u")


def test_non_object_json_raises_llmbadresponse():
    client = DeepSeekClient(api_key="k", http_client=fake_client("[1, 2, 3]"))
    with pytest.raises(LLMBadResponse):
        client.complete_json("s", "u")


def test_missing_content_raises_llmbadresponse():
    fake = fake_client(FakeResponse(200, {"choices": [{"message": {}}]}))
    client = DeepSeekClient(api_key="k", http_client=fake)
    with pytest.raises(LLMBadResponse):
        client.complete_json("s", "u")


# --- review focus: never leak the key -----------------------------------------


def test_no_key_in_errors():
    secret = "SUPERSECRET"
    # Missing key -> config error before any network call; secret not echoed.
    with pytest.raises(LLMConfigError) as missing:
        DeepSeekClient(api_key="").complete_json("s", "u")
    assert secret not in str(missing.value)

    # No injected http_client -> config error, not a real network call, no key leak.
    with pytest.raises(LLMConfigError) as unconfigured:
        DeepSeekClient(api_key=secret).complete_json("s", "u")
    assert secret not in str(unconfigured.value)

    # Invalid key (401) via injected fake -> error still must not echo the key.
    bad = DeepSeekClient(api_key=secret, http_client=fake_client(FakeResponse(401, {})))
    with pytest.raises(LLMError) as invalid:
        bad.complete_json("s", "u")
    assert secret not in str(invalid.value)
