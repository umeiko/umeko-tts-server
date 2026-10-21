"""Check release archives, attribution-preserving install, and old-format compatibility."""
import hashlib
import importlib.util
import json
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("voice_download", ROOT / "scripts/download_voices.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
report = []
with tempfile.TemporaryDirectory(prefix="narrator-release-verify-") as td:
    data = Path(td) / "data"
    data.mkdir()
    original = {"default": "已有默认", "voices": {"已有默认": {"description": "keep"}}}
    (data / "voices.json").write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    for slug in ("narrator_male", "narrator_female"):
        path = ROOT / "tmp/release" / (slug + ".zip")
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None
            assert "LICENSES/Apache-2.0.txt" in archive.namelist()
            assert "LICENSES/GPT-SoVITS-MIT.txt" in archive.namelist()
            assert "preview.wav" in archive.namelist()
        meta = module.install_pack(path, data)
        voice_dir = data / "voices" / meta["name"]
        source = json.loads((voice_dir / "PROVENANCE.json").read_text(encoding="utf-8"))
        for filename, expected in source["sha256"].items():
            h = hashlib.sha256()
            with (voice_dir / filename).open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            assert h.hexdigest() == expected
        registry = json.loads((data / "voices.json").read_text(encoding="utf-8"))
        assert registry["default"] == original["default"]
        assert registry["voices"][original["default"]] == original["voices"][original["default"]]
        report.append({"asset": path.name, "bytes": path.stat().st_size,
                       "crc": "passed", "install_and_hashes": "passed",
                       "attribution_preserved": True, "existing_default_preserved": True})
    def fixture(path, name, provenance=None):
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("voice.json", json.dumps({"name": name}))
            for filename in ("gpt_weights.ckpt", "sovits_weights.pth", "ref_audio.wav"):
                z.writestr(filename, b"small installer fixture, not model weights")
            if provenance:
                z.writestr("PROVENANCE.json", json.dumps(provenance))
    legacy = Path(td) / "legacy.zip"
    fixture(legacy, "旧格式音色")
    assert module.install_pack(legacy, data)["name"] == "旧格式音色"
    report.append({"legacy_format": "passed"})
    bad = Path(td) / "bad.zip"
    fixture(bad, "损坏音色", {"sha256": {"ref_audio.wav": "0" * 64}})
    try:
        module.install_pack(bad, data)
        raise AssertionError("Expected checksum failure")
    except ValueError as e:
        assert "SHA256 mismatch" in str(e)
    assert not (data / "voices/损坏音色").exists()
    assert "损坏音色" not in json.loads((data / "voices.json").read_text(encoding="utf-8"))["voices"]
    report.append({"corrupt_pack_rejected_before_install": "passed"})
    fixture(bad, "../outside")
    try:
        module.install_pack(bad, data)
        raise AssertionError("Expected voice name rejection")
    except ValueError as e:
        assert "voice name" in str(e)
    report.append({"invalid_archive_voice_name": "rejected"})
(ROOT / "tmp/release/verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
