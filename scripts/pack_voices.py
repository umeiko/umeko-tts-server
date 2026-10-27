# -*- coding: utf-8 -*-
"""把 data/voices 下的音色打包成带元数据的 zip，供 GitHub Release 分发。

每个 zip 内容（扁平）:
    gpt_weights.ckpt / sovits_weights.pth / ref_audio.wav / voice.json
voice.json 含 name/description/ref_lang，下载脚本据此重建注册表。
"""
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "tmp" / "release"

# 音色名 -> release 资源文件名
ASSET_NAMES = {
    "曼波": "mambo",
    "高松灯": "takamatsu_tomori",
    "爱音": "chihaya_anon",
    "播音男声": "narrator_male",
    "播音女声": "narrator_female",
    "仁菜": "nina",
}


def main():
    registry = json.loads((DATA / "voices.json").read_text(encoding="utf-8"))
    voices = registry["voices"]
    only = set(sys.argv[1:]) or set(voices)
    OUT.mkdir(parents=True, exist_ok=True)

    for name, info in voices.items():
        if name not in only:
            continue
        slug = ASSET_NAMES.get(name, name)
        voice_dir = DATA / "voices" / name
        files = ["gpt_weights.ckpt", "sovits_weights.pth", "ref_audio.wav"]
        missing = [f for f in files if not (voice_dir / f).is_file()]
        if missing:
            print(f"[skip] {name}: 缺文件 {missing}")
            continue
        meta = {
            "name": name,
            "description": info.get("description", ""),
            "ref_text": info.get("ref_text", ""),
            "ref_lang": info.get("ref_lang", "zh"),
            "is_default": registry.get("default") == name,
        }
        provenance = voice_dir / "PROVENANCE.json"
        if provenance.is_file():
            source = json.loads(provenance.read_text(encoding="utf-8"))
            meta["model_version"] = source.get("model_version", "")
            meta["voice_type"] = source.get("voice_type", "")
        out = OUT / f"{slug}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for f in files:
                z.write(voice_dir / f, f)
            for optional in ("README.md", "PROVENANCE.json", "preview.wav"):
                path = voice_dir / optional
                if path.is_file():
                    z.write(path, optional)
            for path in sorted((voice_dir / "LICENSES").glob("*")):
                if path.is_file():
                    z.write(path, "LICENSES/" + path.name)
            z.writestr("voice.json", json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"[ok] {name} -> {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
