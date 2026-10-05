# -*- coding: utf-8 -*-
"""从 GitHub Release 下载预置音色权重包并装入本地注册表。

用法:
    python scripts/download_voices.py                 # 下载全部预置音色
    python scripts/download_voices.py mambo           # 只下载指定音色（用资源名）
    python scripts/download_voices.py --tag TAG       # 指定 release tag
    python scripts/download_voices.py --proxy-prefix https://ghfast.top/   # 走加速镜像

每个 zip 内含 gpt_weights.ckpt / sovits_weights.pth / ref_audio.wav / voice.json，
脚本解压到 data/voices/<音色名>/ 并把元数据合并进 data/voices.json。
纯标准库实现，无需安装额外依赖。
"""
import argparse
import json
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


def merge_registry(meta: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    reg_path = DATA / "voices.json"
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


def main() -> None:
    ap = argparse.ArgumentParser(description="下载预置音色权重包")
    ap.add_argument("assets", nargs="*", help="只下载指定资源名（如 mambo），默认全部")
    ap.add_argument("--tag", default=DEFAULT_TAG, help=f"release tag，默认 {DEFAULT_TAG}")
    ap.add_argument("--repo", default=REPO, help=f"GitHub 仓库，默认 {REPO}")
    ap.add_argument("--proxy-prefix", default="",
                    help="下载加速镜像前缀（如 https://ghfast.top/），拼在下载地址前")
    args = ap.parse_args()

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
            with zipfile.ZipFile(zpath) as z:
                meta = json.loads(z.read("voice.json").decode("utf-8"))
                voice_dir = DATA / "voices" / meta["name"]
                voice_dir.mkdir(parents=True, exist_ok=True)
                for member in ("gpt_weights.ckpt", "sovits_weights.pth", "ref_audio.wav"):
                    with z.open(member) as src, open(voice_dir / member, "wb") as dst:
                        shutil.copyfileobj(src, dst, length=1024 * 1024)
        merge_registry(meta)
        print(f"[完成] {meta['name']} -> {voice_dir}")

    print("\n全部完成，启动服务即可使用。")


if __name__ == "__main__":
    main()
