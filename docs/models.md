# Where models come from

Any `serve:` model and any `finetune.<name>.from:` takes a **source**. decider-lab pulls it once, verifies it, caches it, and records exactly what was used in every run's `run.json` (and `runs/<lab>/<model>/source.json`).

```yaml
models:
  v19:  {serve: "hf://StrandsAgents/strands-decider-2B-hobson-v19@bb282d786bc251fd4e3068de3ada9ddbb38127cd"}
  v22:  {serve: "https://my-bucket.s3.us-east-1.amazonaws.com/weights/v22.tar", sha256: 7305d520...}
  mine: {serve: "s3://my-bucket/weights/mine.tar", sha256: 6346192d..., profile: research}
  run7: {serve: "s3://my-bucket/checkpoints/run-7/", profile: research}
  dev:  {serve: checkpoints/run-8}
```

| source | form | verified by | needs |
|---|---|---|---|
| Hugging Face | `hf://org/repo@<commit>` (or a bare `org/repo`) | the resolved commit hash, always recorded | `[hub]` extra; `HF_TOKEN` for private or gated repos |
| S3 object | `s3://bucket/key.tar` (`.tar`, `.tar.gz`, `.tgz`, `.zip`, or a single file) | `sha256:` | `[aws]` extra and your credentials (`profile`, `region`) |
| S3 prefix | `s3://bucket/prefix/` (every object under it) | object ETags, recorded | the same |
| URL | `https://...` (public, or a presigned S3 link) | `sha256:` | nothing |
| directory | a path (relative paths are relative to the lab file) | recorded as is | nothing |

## Policies

- **Pin what you publish.** `hf://org/repo` without a commit (or with a branch) works, prints a warning, and records the commit it resolved to. `require_pinned: true` refuses anything but a full 40-character commit, for labs whose numbers will be shared.
- **Checksums for archives.** With `sha256:`, a mismatch fails the pull and leaves nothing behind. Without it, the pull warns and records the checksum it saw, so you can pin it next time.
- **Safe extraction.** Archive members with absolute paths, `..`, links pointing outside the target, or device files are refused (and Python's own `data` filter is applied where available).
- **The checkpoint is found for you.** After extraction, the directory holding `strands_decider_config.json` (up to two levels down) is served, so `my-run/checkpoint/` inside a tar just works.
- **No credentials on remote machines.** For `--on ssh|aws|vast`, every `s3://` object source is turned into a presigned https URL on your machine (valid 6 hours) and the remote copy of the lab carries that URL instead of your profile. Prefixes cannot be presigned: tar the checkpoint, or run locally. Signatures are never recorded or used as cache keys.
- **Offline.** `HF_HUB_OFFLINE=1` serves Hugging Face models from the cache; other sources are reused from `~/.cache/decider-lab/models/` without a network call.

## Commands

```bash
decider-lab pull hf://StrandsAgents/strands-decider-2B-hobson-v19@bb282d786bc251fd4e3068de3ada9ddbb38127cd
decider-lab pull s3://my-bucket/weights/mine.tar --sha256 6346192d... --profile research
decider-lab pull https://example.com/decider.tar --sha256 7305d520...
decider-lab models                       # what is cached, how big, and from where
```

`pull` is optional: `decider-lab run` pulls whatever it needs. It is useful to warm a cache before going offline, or to check a checksum.
