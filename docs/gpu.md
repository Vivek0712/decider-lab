# Running on a rented GPU

```bash
pip install "decider-lab[gpu]"
vastai set api-key <key>                 # once
decider-lab gpu offers --gpu A100_SXM4 --max-price 1.0
decider-lab gpu run lab.yaml --gpu A100_SXM4 --max-price 0.8 --max-hours 2 --ssh-key ~/.ssh/id_ed25519
```

What `gpu run` does, in order:

1. **Budget check.** Refuses unless your vast.ai credit covers `max-price x max-hours` plus $0.50.
2. **Rent.** The cheapest verified offer matching `--gpu`, `--num-gpus`, `--disk`, reliability above 0.98 and a driver for CUDA 12.6 or newer, labelled `decider-lab:<lab>`.
3. **Copy.** decider-lab's source and the lab directory (without `runs/`) over ssh. Nothing else from your machine: no cloud credentials. `--env HF_TOKEN` passes a variable to the remote command for that run only.
4. **Bootstrap** (`src/decider_lab/gpu/bootstrap.sh`): reads the driver's CUDA version from `nvidia-smi`, installs torch from the matching PyTorch index (cu126, cu128 or cu130), pins it, then installs strands-decider (`--strands-decider` pip spec) and decider-lab without letting either replace torch. `--fast-kernels` also builds causal-conv1d.
5. **Run** `decider-lab run` on the host in the background, streaming its log.
6. **Fetch** `runs/` plus the remote run and bootstrap logs into your lab directory.
7. **Destroy** the instance with `-y`, then check it is gone. This runs on success, on failure, at `--max-hours`, and on Ctrl-C. `--keep` skips it for debugging.

Leftovers: `decider-lab gpu ls` lists instances labelled `decider-lab:*`, and `decider-lab gpu down --all` destroys them.

## Traps this handles (each one cost a real run once)

| trap | handled by |
|---|---|
| `pip install torch` brings a wheel for a newer CUDA than the host's driver ("driver too old") | torch from the index that matches the driver, pinned with a constraints file |
| `vastai destroy instance ID` without `-y` asks, aborts, and the instance keeps billing | always `-y`, then verified |
| a stale server on the port answers instead of the new one | `serve` refuses a port that already answers |
| the server dies while loading and the run waits forever | the process is watched; its exit ends the wait with the log path |
| `pkill -f pattern` over ssh matches its own shell | decider-lab never kills by pattern; the server runs in its own process group |
| a run that is never collected keeps billing | a hard `--max-hours` deadline, then destroy |

Other providers fit the same flow (rent, copy, bootstrap, run, fetch, destroy); vast.ai is the one implemented.
