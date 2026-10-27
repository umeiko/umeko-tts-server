"""Prepare the Nina character voice with pinned weights, reference and provenance.

Maintainer utility: stop the service first, run in the GPT-SoVITS environment,
audition the voice, then use pack_voices.py 仁菜. End users use download_voices.py nina.
"""

import argparse
import hashlib
import json
import shutil
import urllib.request
from pathlib import Path
from urllib.parse import quote

from download_voices import merge_registry

ROOT = Path(__file__).resolve().parents[1]
REVISION = "8ca20dd1f84feda57396531ef5413130a5d9cc60"
MODEL_REPO = "WLWolf56/gpt-sovits-model"
SOURCES = {
    "gpt_weights.ckpt": (
        "动漫/gbc/gpt/井芹仁菜V3-e15.ckpt",
        "5c3ed030b9e1cc9e1eddf3059a350ae45227eaaefb8ea5a756450d1e6561e8dd",
    ),
    "sovits_weights.pth": (
        "动漫/gbc/sovits/井芹仁菜V3_e100_s1800.pth",
        "8bf55c079112b365c6453afb6644e17c6bcd0196f96da0f62d144473d47cca38",
    ),
}
SAMPLE_MODEL = "63968837b9ea45c2a3dd0d44af51c09b"
SAMPLE_TASK = "4b51d82bd6184d2fbdbe6dabb7f401de"
SAMPLE_SHA = "cd0163b94cfa26ebf60f4a9816e4854398305534eacad593a74f7749ecaf2eb4"
REF_TEXT = "今日は皆さんと一緒に過ごせて本当に嬉しかったです。これからも頑張って、もっと成長していきたいと思います。"


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url, dest, expected):
    if not dest.is_file() or sha256(dest) != expected:
        part = dest.with_suffix(dest.suffix + ".part")
        req = urllib.request.Request(
            url, headers={"User-Agent": "umeko-tts-voice-preparation"}
        )
        with (
            urllib.request.urlopen(req, timeout=120) as source,
            part.open("wb") as target,
        ):
            shutil.copyfileobj(source, target)
        if sha256(part) != expected:
            raise ValueError(f"Unexpected source digest: {dest.name}")
        part.replace(dest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "downloads/nina")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for filename, (path, digest) in SOURCES.items():
        source = f"https://huggingface.co/{MODEL_REPO}/resolve/{REVISION}/{quote(path)}?download=true"
        cached = args.cache_dir / filename
        fetch(source, cached, digest)
        paths[filename] = cached
    sample = args.cache_dir / "public_reference_sample.mp3"
    if not sample.is_file() or sha256(sample) != SAMPLE_SHA:
        with urllib.request.urlopen(
            f"https://api.fish.audio/model/{SAMPLE_MODEL}", timeout=30
        ) as response:
            profile = json.load(response)
        item = next(
            (s for s in profile.get("samples", []) if s.get("task_id") == SAMPLE_TASK),
            None,
        )
        if not item:
            raise RuntimeError(
                "The pinned public reference is no longer available; do not substitute a different voice."
            )
        fetch(item["audio"], sample, SAMPLE_SHA)
    import soundfile as sf

    audio, sr = sf.read(sample, dtype="float32", always_2d=True)
    target = args.data_dir / "voices/仁菜"
    target.mkdir(parents=True, exist_ok=True)
    for filename, source in paths.items():
        shutil.copy2(source, target / filename)
    # Word/segment ASR verified: the first two complete Japanese sentences end at 6.84s.
    sf.write(
        target / "ref_audio.wav",
        audio[: int(6.88 * sr)].mean(axis=1),
        sr,
        subtype="PCM_16",
    )
    meta = {
        "name": "仁菜",
        "description": "井芹仁菜角色音色，GPT-SoVITS v2 微调（音色修订 V3）。同角色公开样本参考，非商业交流使用；来源说明见音色包。",
        "ref_text": REF_TEXT,
        "ref_lang": "ja",
        "is_default": False,
    }
    merge_registry(meta, args.data_dir)
    provenance = {
        "name": "仁菜",
        "model_version": "GPT-SoVITS v2; character checkpoint revision V3",
        "voice_type": "fine_tuned_character_model",
        "weights": {
            "repository": "https://huggingface.co/" + MODEL_REPO,
            "revision": REVISION,
            "files": {filename: path for filename, (path, _) in SOURCES.items()},
            "original_training_author": "Not verified by the mirror; do not attribute authorship to Umeko.",
        },
        "reference": {
            "source": f"https://fish.audio/m/{SAMPLE_MODEL}/",
            "sample_task": SAMPLE_TASK,
            "source_sha256": SAMPLE_SHA,
            "range_seconds": [0, 6.88],
            "kind": "publicly shared synthetic sample of the same character; not the original checkpoint's bundled training reference",
            "text": REF_TEXT,
            "language": "ja",
        },
        "usage": "Character voice for non-commercial learning/exchange. No commercial or character/performer rights are granted. The mirror does not supply a standalone open-weight license. Server code licensing does not apply to these assets.",
        "sha256": {
            filename: sha256(target / filename)
            for filename in [*SOURCES, "ref_audio.wav"]
        },
    }
    (target / "PROVENANCE.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (target / "README.md").write_text(
        "# 仁菜（井芹仁菜）\n\nGPT-SoVITS v2 架构，音色文件修订号 V3；不是 GPT-SoVITS v3 架构。\n\n"
        "权重镜像：https://huggingface.co/WLWolf56/gpt-sovits-model ，固定修订与文件哈希见 PROVENANCE.json。镜像未标注独立开放权重许可证，原训练者未经镜像核实；本项目不是训练作者。\n\n"
        f"参考音频来自公开同角色合成样本 https://fish.audio/m/{SAMPLE_MODEL}/ 的前 6.88 秒，经日语 ASR 核对前两句；不是原权重附带的参考音频。\n\n"
        "相关原作者系列说明：https://www.bilibili.com/video/BV1tTFReoEmH/ ，仅供非商业交流，保留角色与原作者权利，不授予商用许可，不得把本包权重当作服务器代码的 MIT 授权内容。\n\n"
        '安装：python scripts/download_voices.py nina\n\n请求示例：{"voice":"仁菜","text":"你好，欢迎使用语音合成。","text_lang":"zh"}\n',
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"name": "仁菜", "directory": str(target), "default_voice_preserved": True},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
