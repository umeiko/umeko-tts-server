"""CPU-only launcher for the local web/API/MCP service; configuration comes from env."""

import os
from pathlib import Path
import runpy
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.chdir(root)
os.environ.update(TTS_BACKEND="gsv", GSV_DEVICE="cpu", GSV_IS_HALF="false")
threads = max(1, int(os.environ.get("TTS_CPU_THREADS", "8")))
os.environ.setdefault("OMP_NUM_THREADS", str(threads))
os.environ.setdefault("MKL_NUM_THREADS", str(threads))
import torch

torch.set_num_threads(threads)
torch.set_num_interop_threads(min(2, threads))
runpy.run_module("app.main", run_name="__main__")
