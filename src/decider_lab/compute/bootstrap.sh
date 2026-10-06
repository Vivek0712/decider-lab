#!/usr/bin/env bash
# Prepare a machine for decider-lab: a venv, torch built for this machine, strands-decider, and
# decider-lab. Works as root (rented containers) or a sudo user (cloud VMs), on GPU or CPU.
# Idempotent; writes $WORK/BOOTSTRAP_OK when done.
#
#   WORK=~/decider-lab-work bash bootstrap.sh "<strands-decider pip spec>" [extras]
#
# Traps handled, each of which cost a real run once:
#  - a default `pip install torch` brings a wheel for a newer CUDA than the host's driver
#    ("The NVIDIA driver on your system is too old"): torch comes from the PyTorch index that
#    matches the driver, and is pinned so nothing installed later replaces it;
#  - no C compiler: Triton compiles the linear-attention kernels on the first request, so the
#    server loads, /health says ok, and every answer is an HTTP 500. gcc is installed and checked.
set -euo pipefail
STRANDS_SPEC=${1:-"strands-decider[vision,cuda] @ git+https://github.com/strands-labs/strands-decider@3e94e9d84c620ed5a95f1a3310c3decb971e261c"}
LAB_EXTRAS=${2:-"heldout"}
WORK=${WORK:-$HOME/decider-lab-work}
LAB_SRC=${LAB_SRC:-$WORK/decider-lab}
VENV=${VENV:-$WORK/venv}
log() { echo "[bootstrap $(date -u +%H:%M:%SZ)] $*"; }
SUDO=""; [ "$(id -u)" = 0 ] || SUDO="sudo -n"

rm -f "$WORK/BOOTSTRAP_OK"
need=""
for c in git rsync gcc; do command -v "$c" >/dev/null || need="$need $c"; done
python3 -c "import venv, ensurepip" 2>/dev/null || need="$need venv"
if [ -n "$need" ]; then
  log "apt:$need"
  # a fresh cloud VM may still be running its own apt on first boot
  for _ in $(seq 30); do $SUDO fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break; sleep 10; done
  ($SUDO apt-get -qq update && DEBIAN_FRONTEND=noninteractive $SUDO apt-get -qq install -y git rsync build-essential \
    python3-venv python3-dev) >/dev/null 2>&1 || log "WARN: apt failed"
fi
command -v gcc >/dev/null || { log "FAIL: no C compiler (gcc); Triton kernels cannot compile"; exit 1; }

# ---- 1. which torch: the driver's CUDA version, or CPU --------------------------------------
DRIVER_CUDA=$(nvidia-smi 2>/dev/null | grep -oE "CUDA Version: [0-9]+\.[0-9]+" | awk '{print $3}' || true)
if [ -n "$DRIVER_CUDA" ]; then
  D=$(echo "$DRIVER_CUDA" | awk -F. '{printf "%d%02d", $1, $2}')
  if   [ "$D" -ge 1300 ]; then CU=cu130
  elif [ "$D" -ge 1208 ]; then CU=cu128
  elif [ "$D" -ge 1206 ]; then CU=cu126
  else log "FAIL: driver supports CUDA $DRIVER_CUDA; torch needs a driver with CUDA 12.6 or newer"; exit 1
  fi
  log "GPU, driver CUDA $DRIVER_CUDA -> torch from the $CU index"
else
  CU=cpu
  log "no NVIDIA GPU -> CPU torch (models answer, slowly; training needs a GPU)"
fi

# ---- 2. venv + torch, pinned ------------------------------------------------------------------
PY=$(command -v python3.12 || command -v python3.11 || command -v python3)
"$PY" -c "import sys; assert sys.version_info >= (3, 10), sys.version" || { log "FAIL: Python 3.10+ needed"; exit 1; }
mkdir -p "$WORK"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
. "$VENV/bin/activate"
python -m pip install -q -U pip wheel
if ! python -c "import torch" 2>/dev/null; then
  python -m pip install -q torch --index-url "https://download.pytorch.org/whl/$CU"
fi
if [ "$CU" != cpu ]; then
  python -c "import torch; assert torch.cuda.is_available(), 'torch cannot see the GPU'; print('torch', torch.__version__, 'cuda', torch.version.cuda, torch.cuda.get_device_name(0))"
else
  python -c "import torch; print('torch', torch.__version__, 'cpu')"
fi
echo "torch==$(python -c "import torch; print(torch.__version__.split('+')[0])")" > "$WORK/constraints.txt"

# ---- 3. strands-decider and decider-lab, never replacing torch -----------------------------
log "installing $STRANDS_SPEC"
python -m pip install -q -c "$WORK/constraints.txt" "$STRANDS_SPEC"
log "installing decider-lab[$LAB_EXTRAS] from $LAB_SRC"
python -m pip install -q -c "$WORK/constraints.txt" -e "$LAB_SRC[$LAB_EXTRAS]"
if [ "${FAST_KERNELS:-0}" = 1 ] && [ "$CU" != cpu ]; then
  # causal-conv1d speeds up Qwen3.5's recurrent layers; it compiles, so it is optional.
  timeout 1800 python -m pip install -q -c "$WORK/constraints.txt" --no-build-isolation causal-conv1d \
    || log "WARN: causal-conv1d did not install; the slower path will be used"
fi
python -c "import torch, strands_decider, decider_lab; print('ok', strands_decider.__file__)"
decider-lab doctor || true
touch "$WORK/BOOTSTRAP_OK"
log "done"
