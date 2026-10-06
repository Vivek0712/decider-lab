"""General LLMs as decision models: Amazon Bedrock, Strands Agents, OpenAI-compatible endpoints.

All three ask the same question the same way (one prompt, options as letters, a JSON reply with a
probability per letter) and differ only in how the text gets to the model:

    nova:    {bedrock: us.amazon.nova-pro-v1:0, region: us-east-1}               # any Bedrock model
    agent:   {strands: {provider: bedrock, model_id: us.amazon.nova-pro-v1:0}}    # any Strands provider
    vllm:    {chat: Qwen/Qwen3-4B, base_url: "http://localhost:8000/v1", api_key_env: ""}

An LLM *states* its probabilities; a decision head reads them off its output. The report's NLL,
ECE and yes/no band share show how far apart the two are, which is part of what you measure.

Credentials are read, never written: run.json records the model and region, not keys.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import string
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from ..rows import Row
from .base import Adapter, AdapterError, normalise

PROMPT = """You are a decision model. Read the input and answer the question by giving a probability
for every option. Use calibrated probabilities: say 0.5 when you are unsure.

Input:
{state}

Question: {instructions}

Options:
{options}

You may reason briefly first. End your reply with one JSON object: one probability per option
letter, summing to 1, for example {{{example}}}."""


def build_prompt(row: Row) -> tuple[str, list[str]]:
    """The prompt for a row, and the option letters in canonical order."""
    n = len(row["options"])
    if n > 26:
        raise AdapterError("LLM adapters support at most 26 options")
    letters = list(string.ascii_uppercase[:n])
    if row["kind"] == "noul":
        lines = [f"A. No - {row['options'][0][1]}", f"B. Yes - {row['options'][1][1]}"]
    elif row["kind"] == "score":
        lines = [f"{L}. level {i} of {n - 1} (low to high): {d}" for i, (L, (_, d)) in enumerate(zip(letters, row["options"], strict=True))]
    else:
        lines = [f"{L}. {d}" for L, (_, d) in zip(letters, row["options"], strict=True)]
    state = row["state"] if isinstance(row["state"], str) else json.dumps(row["state"], ensure_ascii=False)
    example = ", ".join(f'"{L}": 0.{i}' for i, L in enumerate(letters[:2], 3))
    return PROMPT.format(state=state, instructions=row["instructions"], options="\n".join(lines), example=example), letters


def parse_reply(text: str, letters: list[str]) -> list[float]:
    """Probabilities from a reply: the last JSON object in it, keyed by letter."""
    found = re.findall(r"\{[^{}]*\}", text, re.S)
    if not found:
        raise AdapterError(f"no JSON object in the reply: {text[:120]!r}")
    try:
        probs = json.loads(found[-1])
    except json.JSONDecodeError as e:
        raise AdapterError(f"reply JSON does not parse: {found[-1][:120]!r}") from e
    probs = {str(k).strip().upper().rstrip("."): v for k, v in probs.items()}
    try:
        return normalise([float(probs.get(L, 0.0)) for L in letters], len(letters))
    except (TypeError, ValueError) as e:
        raise AdapterError(f"reply probabilities are not numbers: {probs}") from e


def image_blocks(row: Row, base_dir: str = "") -> list[tuple[str, bytes]]:
    """(format, bytes) for each image of a row: png, jpeg, gif or webp."""
    out = []
    for ref in row.get("images") or []:
        if ref.startswith("data:"):
            head, _, data = ref.partition(",")
            fmt = head.split("/")[1].split(";")[0]
            out.append((fmt, base64.b64decode(data)))
            continue
        path = ref if os.path.isabs(ref) or not base_dir else os.path.join(base_dir, ref)
        fmt = (mimetypes.guess_type(path)[0] or "image/png").split("/")[1]
        with open(path, "rb") as fh:
            out.append((fmt, fh.read()))
    return [("jpeg" if f == "jpg" else f, b) for f, b in out]


class LLMAdapter(Adapter):
    """Prompt, call, parse, with retries and a cap on concurrent calls (APIs throttle)."""

    name = "llm"
    supports_images = False

    def __init__(self, *, retries: int = 4, max_concurrency: int = 5, max_tokens: int = 1024,
                 temperature: float = 0.0, base_dir: str = "") -> None:
        self.retries, self.max_tokens, self.temperature, self.base_dir = retries, max_tokens, temperature, base_dir
        self._slots = threading.Semaphore(max(1, max_concurrency))

    def complete(self, prompt: str, images: list[tuple[str, bytes]]) -> str:
        raise NotImplementedError

    def retryable(self, e: Exception) -> bool:
        return True

    def predict_one(self, row: Row) -> list[float]:
        prompt, letters = build_prompt(row)
        images = image_blocks(row, self.base_dir) if row.get("images") else []
        if images and not self.supports_images:
            raise AdapterError(f"{self.name} adapter does not send images; this row has {len(images)}")
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with self._slots:
                    text = self.complete(prompt, images)
                return parse_reply(text, letters)
            except AdapterError as e:  # the model answered, but not in the format asked for
                last = e
            except Exception as e:  # network, throttling, server errors
                if not self.retryable(e):
                    raise AdapterError(f"{type(e).__name__}: {e}") from e
                last = e
            time.sleep(min(2 ** attempt, 20))
        raise AdapterError(f"no usable answer after {self.retries + 1} attempts: {last}")


# ---- Amazon Bedrock ---------------------------------------------------------------------------

def aws_session(region: str | None, profile: str | None = None, access_key_id_env: str | None = None,
                secret_access_key_env: str | None = None, session_token_env: str | None = None) -> Any:
    """A boto3 session from, in order: keys in the named environment variables, a profile, or the
    default chain (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN, SSO, instance role)."""
    try:
        import boto3
    except ImportError as e:
        raise ImportError("Bedrock needs boto3: pip install 'decider-lab[bedrock]'") from e
    if access_key_id_env or secret_access_key_env:
        key, secret = os.environ.get(access_key_id_env or ""), os.environ.get(secret_access_key_env or "")
        if not key or not secret:
            raise ValueError(f"set {access_key_id_env} and {secret_access_key_env} in the environment")
        token = os.environ.get(session_token_env) if session_token_env else None
        return boto3.Session(aws_access_key_id=key, aws_secret_access_key=secret, aws_session_token=token,
                             region_name=region)
    return boto3.Session(profile_name=profile, region_name=region)


class BedrockAdapter(LLMAdapter):
    """Any Amazon Bedrock model through the Converse API: Amazon Nova, Anthropic Claude, Meta Llama,
    Mistral, and others your account has access to. Model ids or inference profiles
    (us.amazon.nova-pro-v1:0). Images are sent to models that accept them (Nova, Claude)."""

    name = "bedrock"
    supports_images = True

    def __init__(self, model: str = "us.amazon.nova-pro-v1:0", region: str = "us-east-1", profile: str | None = None,
                 access_key_id_env: str | None = None, secret_access_key_env: str | None = None,
                 session_token_env: str | None = None, client: Any = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.model, self.region, self.profile = model, region, profile
        self._env = (access_key_id_env, secret_access_key_env, session_token_env)
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from botocore.config import Config

            sess = aws_session(self.region, self.profile, *self._env)
            self._client = sess.client("bedrock-runtime", config=Config(retries={"max_attempts": 2, "mode": "adaptive"},
                                                                        read_timeout=120))
        return self._client

    def describe(self) -> dict[str, Any]:
        how = "env keys" if self._env[0] else (f"profile {self.profile}" if self.profile else "default chain")
        return {"adapter": self.name, "model": self.model, "region": self.region, "credentials": how}

    def retryable(self, e: Exception) -> bool:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "") if hasattr(e, "response") else ""
        return code not in ("AccessDeniedException", "ValidationException", "ResourceNotFoundException",
                            "UnrecognizedClientException")

    def complete(self, prompt: str, images: list[tuple[str, bytes]]) -> str:
        content: list[dict[str, Any]] = [{"image": {"format": f, "source": {"bytes": b}}} for f, b in images]
        content.append({"text": prompt})
        r = self.client.converse(modelId=self.model, messages=[{"role": "user", "content": content}],
                                 inferenceConfig={"maxTokens": self.max_tokens, "temperature": self.temperature})
        return "".join(c.get("text", "") for c in r["output"]["message"]["content"])


# ---- Strands Agents ---------------------------------------------------------------------------

class StrandsAdapter(LLMAdapter):
    """A Strands Agents model as the answerer: bedrock, openai, anthropic, ollama or litellm.

        {strands: {provider: bedrock, model_id: us.amazon.nova-pro-v1:0, region: us-east-1}}
        {strands: {provider: openai, model_id: gpt-4.1-mini, api_key_env: OPENAI_API_KEY}}
        {strands: {provider: ollama, model_id: qwen3:4b, host: "http://localhost:11434"}}

    Each row gets a fresh agent with no tools, so no conversation carries over between rows."""

    name = "strands"

    def __init__(self, provider: str = "bedrock", model_id: str = "us.amazon.nova-pro-v1:0", region: str = "us-east-1",
                 profile: str | None = None, api_key_env: str | None = None, base_url: str | None = None,
                 host: str | None = None, access_key_id_env: str | None = None,
                 secret_access_key_env: str | None = None, session_token_env: str | None = None,
                 model_factory: Any = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.provider, self.model_id, self.region, self.profile = provider, model_id, region, profile
        self.api_key_env, self.base_url, self.host = api_key_env, base_url, host
        self._env = (access_key_id_env, secret_access_key_env, session_token_env)
        self._factory = model_factory
        self._model: Any = None
        self._model_lock = threading.Lock()

    def describe(self) -> dict[str, Any]:
        return {"adapter": self.name, "provider": self.provider, "model": self.model_id,
                **({"region": self.region} if self.provider == "bedrock" else {})}

    def make_model(self) -> Any:
        if self._factory:
            return self._factory()
        try:
            import strands  # noqa: F401
        except ImportError as e:
            raise ImportError("the strands adapter needs: pip install 'decider-lab[strands-agents]'") from e
        key = os.environ.get(self.api_key_env) if self.api_key_env else None
        if self.provider == "bedrock":
            from strands.models import BedrockModel

            return BedrockModel(boto_session=aws_session(self.region, self.profile, *self._env), model_id=self.model_id,
                                temperature=self.temperature, max_tokens=self.max_tokens)
        if self.provider in ("openai", "litellm"):
            from strands.models.litellm import LiteLLMModel
            from strands.models.openai import OpenAIModel

            cls = OpenAIModel if self.provider == "openai" else LiteLLMModel
            args = {k: v for k, v in (("api_key", key), ("base_url", self.base_url)) if v}
            return cls(client_args=args, model_id=self.model_id,
                       params={"temperature": self.temperature, "max_tokens": self.max_tokens})
        if self.provider == "anthropic":
            from strands.models.anthropic import AnthropicModel

            return AnthropicModel(client_args={"api_key": key} if key else {}, model_id=self.model_id,
                                  max_tokens=self.max_tokens, params={"temperature": self.temperature})
        if self.provider == "ollama":
            from strands.models.ollama import OllamaModel

            return OllamaModel(self.host or "http://localhost:11434", model_id=self.model_id,
                               temperature=self.temperature)
        raise ValueError(f"unknown strands provider {self.provider!r}: bedrock, openai, anthropic, ollama, litellm")

    def model(self) -> Any:
        """One model for the whole run: building a provider client per row is slow (a new boto3 client
        each time), and a model holds no conversation; only an agent does."""
        with self._model_lock:
            if self._model is None:
                self._model = self.make_model()
            return self._model

    def complete(self, prompt: str, images: list[tuple[str, bytes]]) -> str:
        from strands import Agent

        agent = Agent(model=self.model(), callback_handler=None, tools=[])  # fresh history per row
        return str(agent(prompt))


# ---- OpenAI-compatible endpoints --------------------------------------------------------------

class ChatAdapter(LLMAdapter):
    """Any OpenAI-compatible /chat/completions endpoint: OpenAI, vLLM, Ollama, TGI, LM Studio, a
    gateway. `api_key_env` names the variable holding the key ("" for none)."""

    name = "chat"

    def __init__(self, model: str, base_url: str = "https://api.openai.com/v1", api_key_env: str = "OPENAI_API_KEY",
                 timeout: float = 120.0, **kw: Any) -> None:
        super().__init__(**kw)
        self.model, self.base_url, self.api_key_env, self.timeout = model, base_url.rstrip("/"), api_key_env, timeout

    def describe(self) -> dict[str, Any]:
        return {"adapter": self.name, "model": self.model, "base_url": self.base_url}

    def retryable(self, e: Exception) -> bool:
        return not (isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429)

    def complete(self, prompt: str, images: list[tuple[str, bytes]]) -> str:
        key = os.environ.get(self.api_key_env, "") if self.api_key_env else ""
        body = {"model": self.model, "temperature": self.temperature, "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": prompt}]}
        req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {key}"} if key else {})})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
