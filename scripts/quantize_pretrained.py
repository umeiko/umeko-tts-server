"""预训练模型 CPU int8 预量化（在本地主力机上运行，产出上传到服务器）。

为什么需要它：小内存云服务器上"加载 fp32 + 现场量化"会导致 swap 颠簸。
预量化后服务器直接加载 int8 模型，启动快、内存省。

用法:
    pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu
    pip install "transformers==4.57.6" "numpy==1.26.4"
    python scripts/quantize_pretrained.py path/to/chinese-roberta-wwm-ext-large path/to/chinese-hubert-base

产物: 与模型目录同级的 <目录名>.int8.pt
上传到服务器: /opt/GPT-SoVITS/GPT_SoVITS/pretrained_models/ 下即可（保持文件名）。

注意: torch / transformers 版本必须与服务器严格一致，否则服务器端反序列化可能失败。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import torch


def load_model(model_dir: Path):
    from transformers import AutoModelForMaskedLM, HubertModel

    cfg = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    model_type = cfg.get("model_type", "")
    if model_type == "bert":
        return AutoModelForMaskedLM.from_pretrained(model_dir)
    if model_type == "hubert":
        return HubertModel.from_pretrained(model_dir)
    raise ValueError(f"不支持的 model_type: {model_type!r}（{model_dir}）")


def strip_parametrizations(model: torch.nn.Module) -> None:
    """把 weight_norm 等参数化折叠回普通权重——parametrized 模块无法整体 pickle。"""
    from torch.nn.utils import parametrize

    for module in model.modules():
        if parametrize.is_parametrized(module):
            for name in list(module.parametrizations.keys()):
                parametrize.remove_parametrizations(module, name, leave_parametrized=True)
            module._load_state_dict_pre_hooks.clear()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for arg in sys.argv[1:]:
        model_dir = Path(arg)
        if not (model_dir / "config.json").is_file():
            print(f"跳过 {model_dir}：不是有效的模型目录（缺 config.json）")
            continue
        t0 = time.time()
        print(f"[{model_dir.name}] 加载 fp32 ...", flush=True)
        model = load_model(model_dir)
        strip_parametrizations(model)
        print(f"[{model_dir.name}] 动态量化 int8 ...", flush=True)
        qmodel = torch.ao.quantization.quantize_dynamic(
            model, {torch.nn.Linear}, dtype=torch.qint8
        )
        out = model_dir.with_name(model_dir.name + ".int8.pt")
        torch.save(qmodel, out)
        print(
            f"[{model_dir.name}] -> {out} "
            f"({out.stat().st_size / 1e6:.0f} MB, 耗时 {time.time() - t0:.0f}s)",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
