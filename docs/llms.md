# General LLMs: Amazon Bedrock, Strands Agents, OpenAI-compatible

Compare a decision model with a general LLM on the same rows. All three LLM adapters ask the same prompt (the input, the question, options as letters), let the model reason briefly, and read one probability per option from the JSON object that ends its reply. A decision head reads its probabilities off its output; an LLM states them. NLL, ECE and the yes/no band share in the report show how much that difference matters for your task.

```yaml
models:
  nova:  {bedrock: us.amazon.nova-pro-v1:0, region: us-east-1}                           # Amazon Bedrock
  agent: {strands: {provider: bedrock, model_id: us.amazon.nova-pro-v1:0}}               # Strands Agents
  local: {chat: qwen3:4b, base_url: "http://localhost:11434/v1", api_key_env: ""}        # OpenAI-compatible
  v19:   {serve: "hf://StrandsAgents/strands-decider-2B-hobson-v19@bb282d786bc251fd4e3068de3ada9ddbb38127cd"}
```

## Amazon Bedrock

`bedrock:` takes any model id or inference profile your account can use, through the Converse API: Amazon Nova (`us.amazon.nova-pro-v1:0`, `us.amazon.nova-lite-v1:0`, `us.amazon.nova-micro-v1:0`, `us.amazon.nova-premier-v1:0`, `us.amazon.nova-2-lite-v1:0`), Anthropic Claude, Meta Llama, Mistral and others. Rows with `images` are sent with their images, for models that accept them (Nova, Claude).

| option | default | meaning |
|---|---|---|
| `region` | `us-east-1` | the Bedrock region |
| `max_concurrency` | `5` | concurrent calls per model; Bedrock throttles above a few, and throttled calls are retried with backoff |
| `max_tokens`, `temperature` | `1024`, `0` | generation settings; models such as Nova reason before answering, so leave room |
| `retries` | `4` | per row, for throttling, network errors and replies that are not valid JSON |

Model access must be enabled in the Bedrock console for your account and region. An `AccessDeniedException` or a wrong model id fails the row at once instead of retrying.

## Strands Agents

`strands:` builds a Strands Agents model and asks a fresh agent, with no tools, per row:

```yaml
nova:   {strands: {provider: bedrock,   model_id: us.amazon.nova-pro-v1:0, region: us-east-1, profile: research}}
gpt:    {strands: {provider: openai,    model_id: gpt-4.1-mini, api_key_env: OPENAI_API_KEY}}
claude: {strands: {provider: anthropic, model_id: claude-sonnet-4-5, api_key_env: ANTHROPIC_API_KEY}}
local:  {strands: {provider: ollama,    model_id: qwen3:4b, host: "http://localhost:11434"}}
any:    {strands: {provider: litellm,   model_id: "groq/llama-3.3-70b", api_key_env: GROQ_API_KEY}}
```

Needs `pip install "decider-lab[strands-agents]"` (and the provider's own package for openai, anthropic, ollama or litellm).

## OpenAI-compatible endpoints

`chat:` posts to `<base_url>/chat/completions`: OpenAI, vLLM, Ollama, TGI, LM Studio, or a gateway. `api_key_env` names the variable holding the key, or `""` for none. Any Hugging Face model you host with vLLM or Ollama becomes comparable this way.

## AWS credentials

Bedrock (and `strands` with `provider: bedrock`) take credentials in this order:

1. **Named environment variables**, for keys you supply yourself:
   ```yaml
   nova:
     bedrock: us.amazon.nova-pro-v1:0
     access_key_id_env: LAB_AWS_KEY          # the NAMES of the variables, not the values
     secret_access_key_env: LAB_AWS_SECRET
     session_token_env: LAB_AWS_TOKEN        # for temporary credentials
   ```
   ```bash
   export LAB_AWS_KEY=AKIA...  LAB_AWS_SECRET=...  LAB_AWS_TOKEN=...
   ```
2. **A profile**: `profile: research` (from `~/.aws/config`, including SSO).
3. **The default chain**: `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN`, then the default profile, SSO, or an instance role.

Keys are read when the model is first called and never written: `run.json` records the model, region and which of the three methods was used, not the keys. Never put key values in `lab.yaml`.

**On a remote backend** (`--on ssh|aws|vast`), LLM models are API calls and need no GPU, so the simplest is to run them here and the GPU models there:

```bash
decider-lab run lab.yaml --only nova gpt                 # here
decider-lab run lab.yaml --only v19 mine --on vast       # on the GPU
decider-lab report runs/<lab> --baseline v19             # one report over both
```

If they must run remotely, pass the variables explicitly for that run only: `--env LAB_AWS_KEY LAB_AWS_SECRET LAB_AWS_TOKEN`. Prefer short-lived session credentials for that: they reach the remote machine's process environment for the length of the run. On `--on aws`, an instance role is the better path when your organisation provides one.
