"""GPT-SoVITS 真实推理后端：包装官方 TTS_infer_pack 推理管线。

- 权重热切换：缓存当前已加载的 (GPT, SoVITS) 权重，音色变化时才调用
  init_t2s_weights / init_vits_weights，避免每个请求重复加载。
- 线程安全由引擎层的全局串行锁保证，本类不做额外加锁。
- 重依赖（torch / numpy / GPT_SoVITS）全部延迟导入，未安装时给出中文指引。
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from .base import BackendError, BaseBackend, SynthesisJob

logger = logging.getLogger("umeko-tts")


def _patch_torchaudio_load() -> None:
    """torchaudio 2.11+ 的 load 默认走 torchcodec（Windows 上需要 FFmpeg 完整
    DLL，难以安装）。这里把 torchaudio.load 替换为 soundfile 实现，返回与原版
    相同的 (Tensor[channels, samples], sample_rate)。"""
    import torch
    import torchaudio

    def load(path, *args, **kwargs):
        import soundfile as sf

        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        return torch.from_numpy(data.T.copy()), sr

    torchaudio.load = load


class GSVBackend(BaseBackend):
    name = "gpt-sovits"
    requires_files = True

    def __init__(self, settings):
        self._settings = settings
        self._pipeline = None
        self._loaded: tuple[str, str] | None = None
        self._device: str | None = None
        self._is_half: bool = False
        self._start_lock = threading.Lock()

    # ---------- 生命周期 ----------

    def startup(self) -> None:
        with self._start_lock:
            if self._pipeline is not None:
                return
            root: Path = self._settings.gsv_root
            if not root.is_dir():
                raise BackendError(
                    f"未找到 GPT-SoVITS 目录: {root}。"
                    "请执行 git clone https://github.com/RVC-Boss/GPT-SoVITS 并安装其依赖，"
                    "然后用环境变量 GSV_ROOT 指向仓库目录（详见 README）。"
                )
            cfg: Path = self._settings.gsv_config
            if not cfg.is_file():
                raise BackendError(f"未找到推理配置文件: {cfg}")
            for p in (str(root), str(root / "GPT_SoVITS")):
                if p not in sys.path:
                    sys.path.insert(0, p)
            # 官方实现依赖 cwd 解析 tts_infer.yaml 中预训练模型的相对路径
            os.chdir(root)
            try:
                import torch
                _patch_torchaudio_load()
                from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
            except ImportError as e:
                raise BackendError(
                    f"GPT-SoVITS 依赖未安装完整: {e}。请按官方文档安装 torch 与 requirements.txt。"
                ) from e

            device = self._settings.gsv_device
            if device == "auto":
                device = "cuda" if torch.cuda.is_available() else "cpu"
            is_half = self._settings.gsv_is_half
            if is_half == "auto":
                is_half = device == "cuda"
            else:
                is_half = is_half == "true"

            tts_config = TTS_Config(str(cfg))
            tts_config.device = device
            tts_config.is_half = is_half
            if device == "cpu":
                self._patch_pretrained_loaders()
            try:
                self._pipeline = TTS(tts_config)
            except Exception as e:
                raise BackendError(f"GPT-SoVITS 推理管线初始化失败: {e}") from e
            self._device = device
            self._is_half = is_half
            # 启动时已按配置加载默认音色权重，记录路径避免首个请求重复加载
            self._loaded = self._config_weights_key(tts_config)

    @staticmethod
    def _config_weights_key(tts_config) -> tuple[str, str] | None:
        try:
            custom = getattr(tts_config, "configs", {}).get("custom", {})
            gpt = custom.get("t2s_weights_path") or getattr(tts_config, "t2s_weights_path", "")
            sovits = custom.get("vits_weights_path") or getattr(tts_config, "vits_weights_path", "")
            if gpt and sovits:
                return (os.path.abspath(gpt), os.path.abspath(sovits))
        except Exception:
            pass
        return None

    _loaders_patched = False

    @classmethod
    def _patch_pretrained_loaders(cls) -> None:
        """劫持 transformers 的模型加载：模型目录旁存在 <目录名>.int8.pt 时直接加载
        预量化模型；否则正常加载 fp32 后现场量化。避免小内存机器"先读 1.3G fp32
        再量化"造成的内存峰值与 swap 颠簸。"""
        if cls._loaders_patched:
            return
        cls._loaders_patched = True
        import torch
        from transformers import AutoModelForMaskedLM, HubertModel

        def wrap(model_cls):
            original = model_cls.from_pretrained.__func__

            def from_pretrained(inner_cls, base_path, *args, **kwargs):
                base = Path(str(base_path))
                qfile = base.with_name(base.name + ".int8.pt")
                if qfile.is_file():
                    logger.info("加载预量化 int8 模型: %s", qfile)
                    return torch.load(qfile, map_location="cpu", weights_only=False)
                model = original(inner_cls, str(base_path), *args, **kwargs)
                if os.environ.get("GSV_CPU_INT8", "1") == "1":
                    try:
                        model = torch.ao.quantization.quantize_dynamic(
                            model, {torch.nn.Linear}, dtype=torch.qint8
                        )
                        logger.info("CPU int8 动态量化完成: %s", base_path)
                    except Exception as e:
                        logger.warning("CPU int8 动态量化跳过 %s: %s", base_path, e)
                return model

            model_cls.from_pretrained = classmethod(from_pretrained)

        wrap(AutoModelForMaskedLM)
        wrap(HubertModel)

    # ---------- 推理 ----------

    def _ensure_weights(self, job: SynthesisJob):
        key = (os.path.abspath(job.gpt_weights), os.path.abspath(job.sovits_weights))
        if key == self._loaded:
            return
        try:
            self._pipeline.init_t2s_weights(job.gpt_weights)
            self._pipeline.init_vits_weights(job.sovits_weights)
        except Exception as e:
            raise BackendError(f"加载音色「{job.voice_name}」权重失败: {e}") from e
        self._loaded = key

    def synthesize(self, job: SynthesisJob) -> tuple[bytes, int]:
        if self._pipeline is None:
            self.startup()
        self._ensure_weights(job)
        req = {
            "text": job.text,
            "text_lang": job.text_lang,
            "ref_audio_path": job.ref_audio_path,
            "prompt_text": job.ref_text,
            "prompt_lang": job.ref_lang,
            "top_k": job.top_k,
            "top_p": job.top_p,
            "temperature": job.temperature,
            "text_split_method": "cut5",
            "batch_size": 1,
            "speed_factor": job.speed_factor,
            "split_bucket": True,
            "return_fragment": False,
            "seed": -1,
            "streaming_mode": False,
            "parallel_infer": True,
            "repetition_penalty": 1.35,
            "media_type": "wav",
        }
        try:
            generator = self._pipeline.run(req)
            sample_rate, audio = next(generator)
        except Exception as e:
            raise BackendError(f"推理失败: {e}") from e
        return self._to_pcm16(audio), int(sample_rate)

    @staticmethod
    def _to_pcm16(audio) -> bytes:
        import numpy as np

        arr = np.asarray(audio)
        if arr.dtype == np.int16:
            return arr.tobytes()
        if np.issubdtype(arr.dtype, np.floating):
            return (np.clip(arr, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        return arr.astype(np.int16).tobytes()

    def status(self) -> dict:
        loaded = None
        if self._loaded:
            loaded = {"gpt": self._loaded[0], "sovits": self._loaded[1]}
        return {
            "name": self.name,
            "demo": False,
            "initialized": self._pipeline is not None,
            "device": self._device or "未初始化",
            "half_precision": self._is_half,
            "loaded_weights": loaded,
        }
