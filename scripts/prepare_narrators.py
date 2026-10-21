"""Build redistributable Mandarin narrator presets for the existing GPT-SoVITS backend.

Development only: pip install kokoro==0.9.4 'misaki[zh]==0.9.4'
Serving the resulting packs does not need Kokoro or Misaki.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KOKORO_REPO = "hexgrad/Kokoro-82M-v1.1-zh"
KOKORO_REVISION = "01e7505bd6a7a2ac4975463114c3a7650a9f7218"
GSV_REPO = "lj1995/GPT-SoVITS"
GSV_REVISION = "8f8857563c040d542de108a2c18b0425abd6c9c5"
REFERENCE_TEXT = "科技改变生活，创新连接未来。接下来，为您介绍本期的主要内容。"
PRESETS = [("播音女声", "narrator_female", "zf_001", "女"),
           ("播音男声", "narrator_male", "zm_010", "男")]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def download(repo: str, revision: str, filename: str, cache: Path, metadata: dict):
    target = cache / repo.split("/")[-1] / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    file_info = next(x for x in metadata["siblings"] if x["rfilename"] == filename)
    expected = file_info.get("lfs", {}).get("sha256")
    if target.exists() and (not expected or sha256(target) == expected):
        return target
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{filename}?download=true"
    print(f"Downloading {repo}/{filename}", flush=True)
    partial = target.with_name(target.name + ".partial")
    with urllib.request.urlopen(url, timeout=60) as src, partial.open("wb") as dst:
        count = 0
        next_mark = 50 * 1024 * 1024
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
            count += len(chunk)
            if count >= next_mark:
                print(f"  {filename}: {count // 1024 // 1024} MiB", flush=True)
                next_mark += 50 * 1024 * 1024
    if expected and sha256(partial) != expected:
        raise ValueError(f"Upstream SHA256 mismatch: {filename}")
    partial.replace(target)
    return target


def prepare(data: Path, cache: Path, previews: Path):
    import numpy as np
    import soundfile as sf
    import torch
    from kokoro import KModel, KPipeline

    torch.set_num_threads(8)
    torch.set_num_interop_threads(2)
    torch.manual_seed(20261021)
    data.mkdir(parents=True, exist_ok=True)
    previews.mkdir(parents=True, exist_ok=True)
    kokoro = get_json(f"https://huggingface.co/api/models/{KOKORO_REPO}/revision/{KOKORO_REVISION}?blobs=true")
    gsv = get_json(f"https://huggingface.co/api/models/{GSV_REPO}/revision/{GSV_REVISION}?blobs=true")
    requests = [(KOKORO_REPO, KOKORO_REVISION, f, kokoro) for f in
                ["config.json", "kokoro-v1_1-zh.pth", "README.md", "voices/zf_001.pt", "voices/zm_010.pt"]]
    requests += [(GSV_REPO, GSV_REVISION, f, gsv) for f in
                 ["README.md", "s1v3.ckpt", "v2Pro/s2Gv2ProPlus.pth"]]
    paths = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        tasks = [(repo, filename, executor.submit(download, repo, revision, filename, cache, meta))
                 for repo, revision, filename, meta in requests]
        for repo, filename, future in tasks:
            paths[repo, filename] = future.result()
    licenses = cache / "licenses"
    licenses.mkdir(exist_ok=True)
    for filename, url in {
        "Apache-2.0.txt": "https://www.apache.org/licenses/LICENSE-2.0.txt",
        "GPT-SoVITS-MIT.txt": "https://raw.githubusercontent.com/RVC-Boss/GPT-SoVITS/1b47ca07ef43a284e0b8bd7f29d3e1e3ff76082a/LICENSE",
    }.items():
        target = licenses / filename
        if not target.exists():
            target.write_bytes(urllib.request.urlopen(url, timeout=60).read())
    model = KModel(repo_id=KOKORO_REPO, config=str(paths[KOKORO_REPO, "config.json"]),
                   model=str(paths[KOKORO_REPO, "kokoro-v1_1-zh.pth"])).to("cpu").eval()
    pipeline = KPipeline(lang_code="z", repo_id=KOKORO_REPO, model=model)
    report = []
    for name, slug, speaker, gender in PRESETS:
        vdir = data / "voices" / name
        if vdir.exists():
            raise FileExistsError(f"Refusing to overwrite an existing voice: {vdir}")
        vdir.mkdir(parents=True)
        embedding = torch.load(paths[KOKORO_REPO, f"voices/{speaker}.pt"], map_location="cpu", weights_only=True)
        results = list(pipeline(REFERENCE_TEXT, voice=embedding, speed=1.02))
        waveform = np.concatenate([r.audio.cpu().numpy() for r in results])
        seconds = len(waveform) / 24000
        if not 3 <= seconds <= 10:
            raise ValueError(f"Reference audio must be 3–10 seconds, got {seconds:.2f}")
        # Keep the reference dry; no effects, backing track or copied human recording.
        peak = float(np.max(np.abs(waveform)))
        if peak < 0.01:
            raise ValueError("Reference contains no usable signal")
        rms = float(np.sqrt(np.mean(waveform ** 2)))
        reference_gain = min(10 ** (-18 / 20) / rms, 0.95 / peak)
        waveform *= reference_gain
        sf.write(vdir / "ref_audio.wav", waveform, 24000, subtype="PCM_16")
        shutil.copy2(vdir / "ref_audio.wav", previews / f"{slug}_reference.wav")
        shutil.copy2(paths[GSV_REPO, "s1v3.ckpt"], vdir / "gpt_weights.ckpt")
        shutil.copy2(paths[GSV_REPO, "v2Pro/s2Gv2ProPlus.pth"], vdir / "sovits_weights.pth")
        (vdir / "LICENSES").mkdir()
        for p in licenses.iterdir():
            shutil.copy2(p, vdir / "LICENSES" / p.name)
        shutil.copy2(paths[KOKORO_REPO, "README.md"], vdir / "LICENSES/Kokoro-model-card.md")
        shutil.copy2(paths[GSV_REPO, "README.md"], vdir / "LICENSES/GPT-SoVITS-model-card.md")
        description = f"正式中文{gender}声旁白。GPT-SoVITS v2ProPlus 官方基座＋Kokoro 1.1 中文 {speaker} 合成参考音色；非独立微调模型。"
        info = {
            "schema_version": 1, "name": name, "asset_name": slug,
            "description": description, "model_version": "v2ProPlus",
            "voice_type": "zero_shot_preset_not_finetuned",
            "gpt_source": {"repo": GSV_REPO, "revision": GSV_REVISION, "path": "s1v3.ckpt", "license": "MIT"},
            "sovits_source": {"repo": GSV_REPO, "revision": GSV_REVISION, "path": "v2Pro/s2Gv2ProPlus.pth", "license": "MIT"},
            "reference_source": {"kind": "synthetic", "repo": KOKORO_REPO, "revision": KOKORO_REVISION,
                                 "speaker": speaker, "model_license": "Apache-2.0", "text": REFERENCE_TEXT,
                                 "duration_seconds": round(seconds, 4), "sample_rate": 24000, "speed": 1.02,
                                 "seed": 20261021, "device": "cpu", "generator": "kokoro 0.9.4 / misaki 0.9.4",
                                 "normalization": "target RMS -18 dBFS, peak capped at 0.95",
                                 "dataset_statement": "Upstream says its 100 Chinese speakers use professional data permissively granted by LongMaoData."},
            "sha256": {f: sha256(vdir / f) for f in ["gpt_weights.ckpt", "sovits_weights.pth", "ref_audio.wav"]},
        }
        (vdir / "PROVENANCE.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        (vdir / "README.md").write_text(
            f"# {name}\n\n{description}\n\n"
            "这是完整的 GPT-SoVITS 兼容音色预设，不是用真人播音员录音训练的专属检查点。"
            "男女预设共享同一对官方基座权重，音色和语气通过参考音频区分。"
            "参考音频由获授权专业语音数据训练的 Kokoro 中文模型合成，没有截取新闻节目或名人录音。\n\n"
            f"参考文本：{REFERENCE_TEXT}\n\n"
            "推理使用现有 GPT-SoVITS 后端，无需额外安装 Kokoro。来源版本和哈希见 PROVENANCE.json，许可见 LICENSES/。\n",
            encoding="utf-8")
        sys_path = str(ROOT)
        import sys
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from app.registry import VoiceRegistry
        registry = VoiceRegistry(data)
        voice = registry.create(name, description=description, ref_text=REFERENCE_TEXT, ref_lang="zh")
        for field, filename in [("gpt", "gpt_weights.ckpt"), ("sovits", "sovits_weights.pth"), ("ref_audio", "ref_audio.wav")]:
            registry.set_file(name, field, f"voices/{name}/{filename}")
        report.append(info)
        print(f"Prepared {slug}: {seconds:.2f}s reference", flush=True)
    (previews / "preparation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "downloads/formal-narrators")
    parser.add_argument("--preview-dir", type=Path, default=ROOT / "tmp/narrator-auditions")
    args = parser.parse_args()
    prepare(args.data_dir.resolve(), args.cache_dir.resolve(), args.preview_dir.resolve())


if __name__ == "__main__":
    main()
