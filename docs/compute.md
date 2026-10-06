# Where a lab runs

The same lab file runs on this machine, on a machine you already have, on an AWS instance launched for the run, or on a rented vast.ai GPU. Pick with `compute:` in `lab.yaml`, or override with `--on`:

```yaml
compute:
  on: local            # local (default) | ssh | aws | vast
  max_hours: 2         # remote runs: hard deadline, the machine is released by then at the latest
  # strands_decider: "strands-decider[vision,cuda] @ git+https://github.com/<fork>/strands-decider@<commit>"
  # env: [HF_TOKEN]    # variables passed to the remote run only
  ssh:  {host: ubuntu@10.0.0.5, key: ~/.ssh/id_ed25519}
  aws:  {instance_type: g6e.xlarge, region: us-east-1, profile: default, disk_gb: 150}
  vast: {gpu: A100_SXM4, max_price: 0.8, ssh_key: ~/.ssh/id_ed25519}
```

```bash
decider-lab run lab.yaml                                    # wherever compute.on says
decider-lab run lab.yaml --on local                         # here, whatever the file says
decider-lab run lab.yaml --on ssh --host ubuntu@10.0.0.5
decider-lab run lab.yaml --on aws --instance-type g6e.xlarge --profile research
decider-lab run lab.yaml --on vast --gpu A100_SXM4 --max-price 0.8
```

| backend | the machine | created and removed by decider-lab | needs |
|---|---|---|---|
| `local` | this one | nothing | a GPU or Apple MPS to serve or train Strands Decider; nothing for HTTP/Python models |
| `ssh` | yours: a workstation, an on-prem server, an instance you manage | a work directory (`~/decider-lab-work`) | ssh with a key; passwordless sudo only if git, gcc or python3-venv are missing |
| `aws` | an EC2 instance for this run | instance, key pair, security group (ssh from your IP only), all tagged `decider-lab` | `pip install "decider-lab[aws]"`, credentials, vCPU quota for the instance family |
| `vast` | a rented GPU | the instance, labelled `decider-lab:<lab>` | `pip install "decider-lab[vast]"`, `vastai set api-key`, an ssh key in your vast.ai account |

## One flow for every remote backend

1. **Check** before spending anything: vast credit covers `max_price x max_hours`; AWS credentials work and the account's vCPU quota for the family (G/VT, P, or standard) covers the instance type, with the quota code to request if it does not; the ssh host answers.
2. **Acquire** the machine.
3. **Copy** decider-lab's source and the lab directory (without `runs/`). Nothing else leaves your machine: no cloud credentials.
4. **Bootstrap** (`src/decider_lab/compute/bootstrap.sh`): gcc and git if missing; torch from the PyTorch index that matches the NVIDIA driver (cu126, cu128 or cu130), or CPU torch on a machine without a GPU; pinned, then strands-decider and decider-lab installed without replacing it.
5. **Run** `decider-lab run --on local` there, detached, streaming its log back.
6. **Fetch** `runs/` and the remote logs into your lab directory.
7. **Release**: vast instances destroyed (`-y`, then checked); AWS instances terminated, then the security group and key pair deleted; ssh machines left as they were. This happens on success, failure, the deadline, Ctrl-C and SIGTERM. `--keep` skips it for debugging.

AWS adds a second safety net: the instance is launched with shutdown-behaviour `terminate` and schedules its own shutdown at `max_hours` + 15 minutes, so it goes away even if your laptop dies mid-run.

Leftovers: `decider-lab compute ls --on vast|aws` lists machines decider-lab started; `decider-lab compute down --on vast|aws --all` removes them. `decider-lab compute offers --on vast --gpu A100_SXM4` lists rentable GPUs and your credit.

## Choosing a machine

- Evaluating HTTP or Python models only: `local`.
- Serving a 2B Strands Decider: any 16 GB+ GPU, Apple silicon, or (slowly) a CPU instance.
- Fine-tuning a 2B decider: a 24 GB+ GPU (RTX 4090, L4, A10G); 40 GB+ (A100, L40S) for larger batches or 4B.
- AWS G-family types (g5, g6, g6e) need a "Running On-Demand G and VT instances" quota above 0, which new accounts often lack; `check` tells you before launching.

## Traps the flow handles (each one cost a real run once)

| trap | handled by |
|---|---|
| default `pip install torch` brings a wheel for a newer CUDA than the driver ("driver too old") | torch from the index matching the driver, pinned |
| no C compiler: the server loads and `/health` says ok, then every answer is HTTP 500 (Triton cannot compile kernels) | gcc installed and checked; `serve` asks one real question before measuring |
| `vastai destroy instance ID` without `-y` asks, aborts, and keeps billing | always `-y`, then verified |
| a background job started over ssh keeps the session open, so nothing streams and no deadline fires | `setsid ... < /dev/null &` |
| a large image takes 20 minutes to pull and the boot wait gives up | a boot timeout (vast: 30 min) and offers with fast networks |
| the first rows all fail and the run burns through the whole suite | the runner stops after 20 failures in a row at the start |
| a stale server on the port answers instead of the new one | `serve` refuses a port that already answers |
