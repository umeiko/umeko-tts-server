# -*- coding: utf-8 -*-
"""从 GitHub Release 下载预置音色权重包并装入本地注册表。

用法:
    python scripts/download_voices.py                 # 下载全部预置音色
    python scripts/download_voices.py mambo           # 只下载指定音色（用资源名）
    python scripts/download_voices.py nina            # 仁菜（井芹仁菜），保留已有默认音色
    python scripts/download_voices.py --tag TAG       # 指定 release tag
    python scripts/download_voices.py --proxy-prefix https://ghfast.top/   # 走加速镜像

每个 zip 内含 gpt_weights.ckpt / sovits_weights.pth / ref_audio.wav / voice.json，
脚本解压到 data/voices/<音色名>/ 并把元数据合并进 data/voices.json。
纯标准库实现，无需安装额外依赖。
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

REPO = "umeiko/umeko-tts-server"
DEFAULT_TAG = "voices-v1.0.0"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def http_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def download(url: str, dest: Path, total: int) -> None:
    req = urllib.request.Request(url)
    done = 0
    next_mark = 0.1
    with urllib.request.urlopen(req, timeout=3600) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total and done / total >= next_mark:
                print(f"  {done / 1e6:.0f}/{total / 1e6:.0f} MB ({done * 100 // total}%)", flush=True)
                next_mark += 0.1


def merge_registry(meta: dict, data_dir: Path = DATA) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    reg_path = data_dir / "voices.json"
    if reg_path.is_file():
        registry = json.loads(reg_path.read_text(encoding="utf-8"))
    else:
        registry = {"default": None, "voices": {}}
    registry.setdefault("voices", {})

    name = meta["name"]
    now = datetime.now().isoformat(timespec="seconds")
    old = registry["voices"].get(name, {})
    registry["voices"][name] = {
        "description": meta.get("description", old.get("description", "")),
        "ref_text": meta.get("ref_text", old.get("ref_text", "")),
        "ref_lang": meta.get("ref_lang", old.get("ref_lang", "zh")),
        "gpt_weights": f"voices/{name}/gpt_weights.ckpt",
        "sovits_weights": f"voices/{name}/sovits_weights.pth",
        "ref_audio": f"voices/{name}/ref_audio.wav",
        "created_at": old.get("created_at", now),
        "updated_at": now,
    }
    if not registry.get("default") or (meta.get("is_default") and not registry["default"]):
        registry["default"] = name
    reg_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def install_pack(zpath: Path, data_dir: Path = DATA) -> dict:
    """Install the original format and provenance-bearing packs, preserving attribution."""
    with zipfile.ZipFile(zpath) as z:
        meta = json.loads(z.read("voice.json").decode("utf-8"))
        name = meta["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[\w\-一-鿿]{1,64}", name):
            raise ValueError("Invalid voice name in archive")
        required = ("gpt_weights.ckpt", "sovits_weights.pth", "ref_audio.wav")
        for member in required:
            if z.getinfo(member).file_size == 0:
                raise ValueError(f"Empty voice file: {member}")
        expected = {}
        if "PROVENANCE.json" in z.namelist():
            expected = json.loads(z.read("PROVENANCE.json").decode("utf-8")).get("sha256", {})
        optional = [n for n in z.namelist() if n in ("README.md", "PROVENANCE.json", "preview.wav")
                    or (n.startswith("LICENSES/") and len(n.split("/")) == 2
                        and n.split("/")[1] not in ("", ".", "..") and "\\" not in n)]
        data_dir.mkdir(parents=True, exist_ok=True)
        # Verify every file before replacing any installed voice files.
        with tempfile.TemporaryDirectory(prefix=".voice-install-", dir=data_dir) as td:
            stage = Path(td)
            for member in [*required, *optional]:
                target = stage / member
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with z.open(member) as src, target.open("wb") as dst:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        digest.update(chunk)
                        dst.write(chunk)
                if member in expected and digest.hexdigest() != expected[member]:
                    raise ValueError(f"SHA256 mismatch: {member}")
            voice_dir = data_dir / "voices" / name
            voice_dir.mkdir(parents=True, exist_ok=True)
            for member in [*required, *optional]:
                dest = voice_dir / member
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(stage / member, dest)
        merge_registry(meta, data_dir)
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description="下载预置音色权重包")
    ap.add_argument("assets", nargs="*", help="只下载指定资源名（如 mambo、nina、narrator_male），默认全部")
    ap.add_argument("--tag", default=DEFAULT_TAG, help=f"release tag，默认 {DEFAULT_TAG}")
    ap.add_argument("--repo", default=REPO, help=f"GitHub 仓库，默认 {REPO}")
    ap.add_argument("--file", type=Path, action="append", default=[], help="安装本地音色 zip，可重复传入")
    ap.add_argument("--data-dir", type=Path, default=DATA, help="音色与注册表保存目录")
    ap.add_argument("--proxy-prefix", default="",
                    help="下载加速镜像前缀（如 https://ghfast.top/），拼在下载地址前")
    args = ap.parse_args()

    if args.file:
        if args.assets:
            ap.error("--file cannot be combined with remote asset names")
        for zpath in args.file:
            meta = install_pack(zpath, args.data_dir)
            print(f"[完成] {meta['name']} -> {args.data_dir / 'voices' / meta['name']}")
        return

    rel = http_json(f"https://api.github.com/repos/{args.repo}/releases/tags/{args.tag}")
    assets = [a for a in rel["assets"] if a["name"].endswith(".zip")]
    if args.assets:
        wanted = {a if a.endswith(".zip") else f"{a}.zip" for a in args.assets}
        assets = [a for a in assets if a["name"] in wanted]
    if not assets:
        sys.exit("release 中没有匹配的 zip 资源")

    for a in assets:
        size_mb = a["size"] / 1e6
        print(f"[下载] {a['name']} ({size_mb:.1f} MB) ...", flush=True)
        with tempfile.TemporaryDirectory() as td:
            zpath = Path(td) / a["name"]
            download(args.proxy_prefix + a["browser_download_url"], zpath, a["size"])
            meta = install_pack(zpath, args.data_dir)
            voice_dir = args.data_dir / "voices" / meta["name"]
        print(f"[完成] {meta['name']} -> {voice_dir}")

    print("\n全部完成，启动服务即可使用。")


if __name__ == "__main__":
    main()
