from __future__ import annotations

import pytest

from decider_lab.adapters import AdapterError, BedrockAdapter, ChatAdapter, StrandsAdapter, llm, make_adapter
from decider_lab.rows import YES_NO, check


def row(kind="choice", n=3, label=0, **extra):
    if kind == "noul":
        r = {"kind": "noul", "state": "s", "instructions": "q?", "options": YES_NO, "label": label}
    elif kind == "score":
        r = {"kind": "score", "state": "s", "instructions": "how?", "options": [[str(i), f"l{i}"] for i in range(n)],
             "label": label}
    else:
        r = {"kind": "choice", "state": "s", "instructions": "which?", "options": [[f"o{i}", f"d{i}"] for i in range(n)],
             "label": label}
    return check([{**r, **extra}])[0]


def test_prompt_maps_letters_to_canonical_options():
    text, letters = llm.build_prompt(row("noul"))
    assert letters == ["A", "B"] and "A. No" in text and "B. Yes" in text
    text, letters = llm.build_prompt(row("score", 4))
    assert letters == list("ABCD") and "level 3 of 3" in text


@pytest.mark.parametrize("reply, want", [
    ('```json\n{"A": 0.7, "B": 0.3}\n```', [0.7, 0.3]),
    ('thinking... {"a.": 2, "b": 2}', [0.5, 0.5]),
    ('{"A": 0.9}', [1.0, 0.0]),
])
def test_parse_reply(reply, want):
    assert llm.parse_reply(reply, ["A", "B"]) == pytest.approx(want)


@pytest.mark.parametrize("reply", ["no json here", '{"A": "high"}', '{"A": 0, "B": 0}'])
def test_parse_reply_rejects(reply):
    with pytest.raises(AdapterError):
        llm.parse_reply(reply, ["A", "B"])


class FakeBedrock:
    def __init__(self, replies, errors=()):
        self.replies, self.errors, self.calls = list(replies), list(errors), []

    def converse(self, **kw):
        self.calls.append(kw)
        if self.errors:
            raise self.errors.pop(0)
        return {"output": {"message": {"content": [{"text": self.replies.pop(0)}]}}}


class ClientError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


def test_bedrock_converse_text_and_images(tmp_path, monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG")
    fake = FakeBedrock(['{"A": 0.2, "B": 0.8}', '{"A": 0.6, "B": 0.3, "C": 0.1}'])
    a = BedrockAdapter("us.amazon.nova-pro-v1:0", client=fake, base_dir=str(tmp_path))
    assert a.predict_one(row("noul", images=["a.png"])) == pytest.approx([0.2, 0.8])
    content = fake.calls[0]["messages"][0]["content"]
    assert content[0]["image"]["format"] == "png" and content[0]["image"]["source"]["bytes"] == b"\x89PNG"
    assert fake.calls[0]["modelId"] == "us.amazon.nova-pro-v1:0" and fake.calls[0]["inferenceConfig"]["temperature"] == 0
    assert a.predict_one(row("choice", 3)) == pytest.approx([0.6, 0.3, 0.1])
    assert a.describe() == {"adapter": "bedrock", "model": "us.amazon.nova-pro-v1:0", "region": "us-east-1",
                            "credentials": "default chain"}


def test_bedrock_retries_throttling_but_not_access_denied(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    fake = FakeBedrock(['{"A": 1, "B": 0}'], errors=[ClientError("ThrottlingException")])
    assert BedrockAdapter(client=fake).predict_one(row("noul")) == pytest.approx([1.0, 0.0])
    fake = FakeBedrock([], errors=[ClientError("AccessDeniedException")])
    with pytest.raises(AdapterError, match="ClientError|AccessDenied"):
        BedrockAdapter(client=fake).predict_one(row("noul"))
    assert len(fake.calls) == 1


def test_bedrock_credentials_from_named_env_vars(monkeypatch):
    import boto3

    seen = {}
    monkeypatch.setattr(boto3, "Session", lambda **kw: seen.update(kw) or object())
    monkeypatch.setenv("MY_KEY", "AKIAEXAMPLE")
    monkeypatch.setenv("MY_SECRET", "s3cr3t")
    llm.aws_session("us-west-2", None, "MY_KEY", "MY_SECRET", None)
    assert seen == {"aws_access_key_id": "AKIAEXAMPLE", "aws_secret_access_key": "s3cr3t", "aws_session_token": None,
                    "region_name": "us-west-2"}
    a = BedrockAdapter(access_key_id_env="MY_KEY", secret_access_key_env="MY_SECRET", client=object())
    assert "s3cr3t" not in str(a.describe()) and a.describe()["credentials"] == "env keys"
    monkeypatch.delenv("MY_SECRET")
    with pytest.raises(ValueError, match="MY_SECRET"):
        llm.aws_session("us-west-2", None, "MY_KEY", "MY_SECRET", None)


def test_strands_adapter_uses_a_fresh_agent(monkeypatch):
    calls = []

    class FakeAgent:
        def __init__(self, **kw):
            calls.append(kw)

        def __call__(self, prompt):
            return '{"A": 0.25, "B": 0.75}'

    import strands

    monkeypatch.setattr(strands, "Agent", FakeAgent)
    built = []
    a = StrandsAdapter(model_factory=lambda: built.append(1) or "model")
    assert a.predict_one(row("noul")) == pytest.approx([0.25, 0.75])
    assert a.predict_one(row("noul")) == pytest.approx([0.25, 0.75])
    assert len(calls) == 2 and calls[0]["tools"] == [] and calls[0]["model"] == "model"
    assert built == [1], "the model is built once and reused; only the agent is per row"


def test_make_adapter_llm_specs():
    assert isinstance(make_adapter({"bedrock": "us.amazon.nova-pro-v1:0", "region": "us-east-1", "workers": 4}),
                      BedrockAdapter)
    s = make_adapter({"strands": {"provider": "openai", "model_id": "gpt-x"}})
    assert isinstance(s, StrandsAdapter) and s.provider == "openai"
    assert isinstance(make_adapter({"chat": "m", "base_url": "http://x/v1", "api_key_env": ""}), ChatAdapter)


def test_chat_adapter_against_fake_endpoint(monkeypatch):
    import io
    import json

    def fake_open(req, timeout):
        body = json.loads(req.data)
        assert body["messages"][0]["content"].startswith("You are a decision model")
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": '{"A": 0.1, "B": 0.9}'}}]}).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_open)
    assert ChatAdapter("m", api_key_env="").predict_one(row("noul")) == pytest.approx([0.1, 0.9])
