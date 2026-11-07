"""Package tracked release files and the published LS20 samples."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LS20_SAMPLES = {
    "outputs/ls20/seed22/" + name for name in (
        "MANIFEST.json", "README.md", "report.json",
        *(f"packages/unit{i}_{kind}.json" for i in (1, 2, 3) for kind in ("public", "supervisor")),
    )
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    destination = parser.parse_args().output
    raw = subprocess.check_output(["git", "ls-files", "-z", "--cached"], cwd=ROOT)
    paths = sorted({name for name in raw.decode("utf-8").split("\0") if name})
    entries = []
    credential = re.compile(rb"(?:sk-[a-zA-Z0-9]{20,}|Bearer\s+[a-zA-Z0-9._-]{20,}|[\"']api_key[\"']\s*:\s*[\"'][^\"']{8,})")
    for name in paths:
        path = ROOT/name
        if not path.is_file():
            continue
        data = path.read_bytes()
        # Tests deliberately contain fake credentials; inspect those separately.
        if not name.startswith("tests/") and credential.search(data):
            raise ValueError("credential-like content in " + name)
        if (name not in LS20_SAMPLES and
                any(part in {".git", "__pycache__", "outputs", ".venv"} for part in Path(name).parts)):
            raise ValueError("unexpected private/runtime path " + name)
        entries.append({"path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    manifest = {"scope": "tracked files in the checkout", "files": entries}
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination/"arcagi-pipeline-delivery.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            archive.write(ROOT/entry["path"], entry["path"])
        archive.writestr("RELEASE_MANIFEST.json", json.dumps(manifest, indent=2)+"\n")
    (destination/"RELEASE_MANIFEST.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"files": len(entries), "archive": str(destination / "arcagi-pipeline-delivery.zip")}))


if __name__ == "__main__":
    main()
