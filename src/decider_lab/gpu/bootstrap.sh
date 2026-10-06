#!/usr/bin/env bash
# Prepare a fresh GPU host for decider-lab: a venv, torch built for the host's driver,
# strands-decider, and decider-lab itself. Idempotent; writes /root/BOOTSTRAP_OK when done.
#
#   bash bootstrap.sh "<strands-decider pip spec>" [extras]
#
# The one trap this exists for: a default `pip install torch` brings a wheel built for a newer
# CUDA than many rented hosts' drivers support ("The NVIDIA driver on your system is too old").
# So torch is installed first from the PyTorch index that matches the driver, then pinned
# with a constraints file so nothing installed afterwards can replace it.
set -euo pipefail
STRANDS_SPEC=${1:-"strands-decider[vision,cuda] @ git+https://github.com/strands-labs/strands-decider@3e94e9d84c620ed5a95f1a3310c3decb971e261c"}
LAB_EXTRAS=${2:-"heldout"}
LAB_SRC=${LAB_SRC:-/root/decider-lab}
VENV=${VENV:-/root/venv}
log() { echo "[bootstrap $(date -u +%H:%M:%SZ)] $*"; }

rm -f /root/BOOTSTRAP_OK
if ! command -v git >/dev/null || ! command -v rsync >/dev/null; then
  log "apt: git rsync"
  (apt-get -qq update && apt-get -qq install -y git rsync) >/dev/null 2>&1 || log "WARN: apt failed; continuing"
fi

# ---- 1. the driver's CUDA version decides the torch wheel ---------------------------------
DRIVER_CUDA=$(nvidia-smi 2>/dev/null | grep -oE "CUDA Version: [0-9]+\.[0-9]+" | awk '{print $3}' || true)
[ -n "$DRIVER_CUDA" ] || { log "FAIL: nvidia-smi reports no CUDA driver"; exit 1; }
ver() { echo "$1" | awk -F. '{printf "%d%02d", $1, $2}'; }
D=$(ver "$DRIVER_CUDA")
if   [ "$D" -ge 1300 ]; then CU=cu130
elif [ "$D" -ge 1208 ]; then CU=cu128
elif [ "$D" -ge 1206 ]; then CU=cu126
else log "FAIL: driver supports CUDA $DRIVER_CUDA; torch needs a driver with CUDA 12.6 or newer"; exit 1
fi
log "driver CUDA $DRIVER_CUDA -> torch from the $CU index"

# ---- 2. venv + torch, pinned ---------------------------------------------------------------
PY=$(command -v python3.12 || command -v python3.11 || command -v python3)
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
. "$VENV/bin/activate"
python -m pip install -q -U pip wheel
if ! python -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
  python -m pip install -q torch --index-url "https://download.pytorch.org/whl/$CU"
fi
python -c "import torch; assert torch.cuda.is_available(), 'torch cannot see the GPU'; print('torch', torch.__version__, 'cuda', torch.version.cuda, torch.cuda.get_device_name(0))"
TORCH_VER=$(python -c "import torch; print(torch.__version__.split('+')[0])")
echo "torch==$TORCH_VER" > /root/constraints.txt

# ---- 3. strands-decider and decider-lab, never replacing torch --------------------------
log "installing $STRANDS_SPEC"
python -m pip install -q -c /root/constraints.txt "$STRANDS_SPEC"
log "installing decider-lab[$LAB_EXTRAS] from $LAB_SRC"
python -m pip install -q -c /root/constraints.txt -e "$LAB_SRC[$LAB_EXTRAS]"
if [ "${FAST_KERNELS:-0}" = 1 ]; then
  # causal-conv1d speeds up Qwen3.5's recurrent layers; it compiles, so it is optional.
  timeout 1800 python -m pip install -q -c /root/constraints.txt --no-build-isolation causal-conv1d \
    || log "WARN: causal-conv1d did not install; the slower path will be used"
fi
python -c "import torch, strands_decider, decider_lab; assert torch.cuda.is_available(); print('ok', strands_decider.__file__)"
decider-lab doctor || true
touch /root/BOOTSTRAP_OK
log "done"
