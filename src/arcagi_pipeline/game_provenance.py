from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


_SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
_IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache", "node_modules"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _environment_files(root: Path) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlinks are not allowed in a version-bound game environment: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in _IGNORED_PARTS for part in relative.parts):
            continue
        if path.name.lower() == ".env" or path.suffix.lower() in _SECRET_SUFFIXES:
            continue
        files.append({"path": relative.as_posix(), "sha256": sha256_file(path)})
    return files


def _tree_digest(files: list[dict[str, str]]) -> str:
    wire = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


def _read_metadata(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _matches_game(metadata_game_id: Any, requested: str) -> bool:
    if not isinstance(metadata_game_id, str):
        return False
    return metadata_game_id == requested or metadata_game_id.startswith(requested + "-")


def inspect_tycho_environment(
    tycho_root: Path,
    game_id: str,
    *,
    source_dir: Path | None = None,
) -> dict[str, Any]:
    """Fingerprint one unambiguous Tycho game environment without changing it."""
    tycho_root = tycho_root.expanduser().resolve()
    if source_dir is not None:
        selected_root = source_dir.expanduser().resolve()
        if not selected_root.is_dir():
            raise FileNotFoundError(f"Tycho game source directory not found: {selected_root}")
        metadata_candidates = [selected_root / "metadata.json"]
        if not metadata_candidates[0].is_file():
            metadata_candidates = sorted(selected_root.rglob("metadata.json"))
        found = [
            (path, _read_metadata(path))
            for path in metadata_candidates
            if path.is_file()
        ]
        found = [(path, value) for path, value in found if value is not None]
        if len(found) != 1:
            raise ValueError(
                f"expected exactly one metadata.json under Tycho game source directory; found {len(found)}"
            )
        metadata_path, metadata = found[0]
        selected_root = metadata_path.parent.resolve()
    else:
        environment_root = tycho_root / "environment_files"
        if not environment_root.is_dir():
            return {
                "status": "unverified",
                "reason": "tycho_environment_files_not_found",
                "game_id": game_id,
            }
        matches: list[tuple[Path, dict[str, Any]]] = []
        for metadata_path in environment_root.rglob("metadata.json"):
            metadata = _read_metadata(metadata_path)
            if metadata is not None and _matches_game(metadata.get("game_id"), game_id):
                matches.append((metadata_path, metadata))
        if not matches:
            return {
                "status": "unverified",
                "reason": "matching_tycho_game_environment_not_found",
                "game_id": game_id,
            }
        if len(matches) != 1:
            return {
                "status": "unverified",
                "reason": "multiple_matching_tycho_game_environments",
                "game_id": game_id,
                "candidate_count": len(matches),
            }
        metadata_path, metadata = matches[0]
        selected_root = metadata_path.parent.resolve()

    metadata_game_id = metadata.get("game_id")
    if not _matches_game(metadata_game_id, game_id):
        raise ValueError(
            f"Tycho game metadata ID {metadata_game_id!r} does not match requested game {game_id!r}"
        )

    class_name = str(metadata.get("class_name") or "").strip()
    base_id = str(metadata_game_id).split("-", maxsplit=1)[0]
    candidate_names = {
        f"{stem.lower()}.py"
        for stem in (class_name, base_id)
        if stem
    }
    files = _environment_files(selected_root)
    if not files:
        raise ValueError(f"Tycho game source directory has no auditable files: {selected_root}")
    if not any(item["path"] == "metadata.json" for item in files):
        raise ValueError("Tycho metadata.json is excluded or missing from the source fingerprint")
    matching_sources = [
        item["path"] for item in files
        if Path(item["path"]).name.lower() in candidate_names
    ]
    if len(matching_sources) == 1:
        game_source_path = matching_sources[0]
    elif len(matching_sources) > 1:
        raise ValueError(f"multiple Tycho game source files match {metadata_game_id!r}")
    else:
        python_files = [item["path"] for item in files if item["path"].lower().endswith(".py")]
        if len(python_files) == 1:
            game_source_path = python_files[0]
        else:
            raise ValueError(
                f"cannot identify a unique Tycho game source file for {metadata_game_id!r}"
            )

    version = metadata.get("version")
    if not isinstance(version, (str, int, float)) or not str(version).strip():
        version = str(metadata_game_id)
    return {
        "status": "verified",
        "game_id": game_id,
        "environment_game_id": str(metadata_game_id),
        "version": str(version),
        "source_root": str(selected_root),
        "metadata_path": "metadata.json",
        "metadata_sha256": sha256_file(selected_root / "metadata.json"),
        "game_source_path": Path(game_source_path).as_posix(),
        "source_tree_sha256": _tree_digest(files),
        "source_files": files,
    }


def inspect_runtime_packages(python: str, backend: str) -> dict[str, Any]:
    """Fingerprint installed engine distributions and editable local sources."""
    required_by_backend = {
        "tycho": ["arc-agi", "arcengine", "jinja2", "numpy", "pillow", "pyyaml"],
        "raw-arcengine": ["arcengine", "numpy", "pydantic"],
        "duck": [
            "arcengine", "matplotlib", "python-dotenv", "requests",
            "tufa-arc-agi-framework", "arc-agi", "imageio", "imageio-ffmpeg",
            "numpy", "scipy", "kaggle",
        ],
    }
    if backend not in required_by_backend:
        raise ValueError(f"unsupported runtime backend: {backend}")
    script = r"""
import hashlib, importlib.metadata as metadata, importlib.util as module_util, json, platform, re, sys, sysconfig
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

ignored = {".git", "__pycache__", ".pytest_cache", "node_modules"}
secret_suffixes = {".pem", ".key", ".p12", ".pfx"}

def directory_digest(root):
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            return None
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in ignored for part in rel.parts):
            continue
        if path.name.lower() == ".env" or path.suffix.lower() in secret_suffixes or path.suffix.lower() == ".pyc":
            continue
        rows.append((rel.as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    if not rows:
        return None
    digest = hashlib.sha256()
    for rel, sha in rows:
        wire = (rel + "\0" + sha + "\n").encode("utf-8")
        digest.update(len(wire).to_bytes(8, "big"))
        digest.update(wire)
    return digest.hexdigest()

def package_record(name, fingerprint_files=False):
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None
    record = {
        "version": dist.version,
        "installed_tree_sha256": None,
        "installed_file_count": None,
        "source_kind": "installed_distribution",
    }
    if fingerprint_files:
        rows = []
        for file in dist.files or ():
            path = Path(dist.locate_file(file))
            if not path.is_file() or path.suffix.lower() == ".pyc" or "__pycache__" in path.parts:
                continue
            try:
                rel = str(file).replace("\\", "/")
                sha = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            rows.append((rel, sha))
        installed = hashlib.sha256()
        for rel, sha in sorted(rows):
            wire = (rel + "\0" + sha + "\n").encode("utf-8")
            installed.update(len(wire).to_bytes(8, "big"))
            installed.update(wire)
        record["installed_tree_sha256"] = installed.hexdigest() if rows else None
        record["installed_file_count"] = len(rows)
        module_name={"arcengine":"arcengine","numpy":"numpy","pydantic":"pydantic"}.get(name)
        if module_name:
            try:
                spec=module_util.find_spec(module_name)
                origin=Path(spec.origin).resolve() if spec and spec.origin else None
                record["module_origin"]=str(origin) if origin else None
                record["module_matches_distribution"]=bool(origin and any(Path(dist.locate_file(file)).resolve()==origin for file in (dist.files or ())))
            except (ImportError,ValueError,AttributeError):record["module_matches_distribution"]=False
    direct = dist.read_text("direct_url.json")
    if direct:
        try:
            info = json.loads(direct)
        except json.JSONDecodeError:
            info = {}
        editable = bool(info.get("dir_info", {}).get("editable"))
        if editable:
            parsed = urlparse(info.get("url", ""))
            if parsed.scheme == "file":
                source = Path(url2pathname(unquote(parsed.path))).resolve()
                digest = directory_digest(source) if source.is_dir() else None
                record["source_kind"] = "editable_local" if digest else "editable_local_unverified"
                record["editable_source"] = str(source)
                record["source_tree_sha256"] = digest
            else:
                record["source_kind"] = "editable_remote_unverified"
    return record

def canonical_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()

names = [
    "arc-agi", "arcengine", "jinja2", "numpy", "pillow", "pyyaml",
    "matplotlib", "python-dotenv", "requests", "tufa-arc-agi-framework",
    "imageio", "imageio-ffmpeg", "scipy", "kaggle", "pydantic",
]
code_packages = {"arc-agi", "arcengine", "tufa-arc-agi-framework", "numpy", "pydantic"}
packages = {name: package_record(name, fingerprint_files=(name in code_packages)) for name in names}
inventory = []
for dist in metadata.distributions():
    name = dist.metadata.get("Name")
    if name:
        inventory.append({"name": canonical_name(name), "version": str(dist.version)})
inventory.sort(key=lambda row: (row["name"], row["version"]))
inventory_wire = json.dumps(inventory, sort_keys=True, separators=(",", ":"))
runtime = {
    "python_executable": str(Path(sys.executable).resolve()),
    "python_version": platform.python_version(),
    "python_implementation": platform.python_implementation(),
    "python_cache_tag": getattr(sys.implementation, "cache_tag", None),
    "python_soabi": sysconfig.get_config_var("SOABI"),
    "dependency_inventory": inventory,
    "dependency_inventory_sha256": hashlib.sha256(inventory_wire.encode("utf-8")).hexdigest(),
    "packages": packages,
}
print(json.dumps(runtime))
"""
    try:
        result = subprocess.run(
            [python, "-c", script],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        payload = {}
    packages = payload.get("packages", {}) if isinstance(payload, dict) else {}
    inventory = payload.get("dependency_inventory") if isinstance(payload, dict) else None
    required = required_by_backend[backend]

    inventory_by_name: dict[str, list[str]] = {}
    for row in inventory or []:
        if isinstance(row, dict) and isinstance(row.get("name"), str) and isinstance(row.get("version"), str):
            inventory_by_name.setdefault(row["name"], []).append(row["version"])

    def is_bound(name: str) -> bool:
        package = packages.get(name)
        if not isinstance(package, dict) or not package.get("version"):
            return False
        installed_versions = set(inventory_by_name.get(name, []))
        if installed_versions != {package["version"]}:
            return False
        if name in {"arc-agi", "arcengine"} and not package.get("installed_tree_sha256"):
            return False
        if backend=="raw-arcengine" and (not package.get("installed_tree_sha256") or not package.get("module_matches_distribution")):
            return False
        if package.get("source_kind") == "editable_local":
            return bool(package.get("editable_source") and package.get("source_tree_sha256"))
        return not str(package.get("source_kind", "")).startswith("editable_")

    verified = bool(
        payload.get("python_executable")
        and payload.get("python_version")
        and payload.get("python_implementation")
        and isinstance(inventory, list)
        and payload.get("dependency_inventory_sha256")
        and all(is_bound(name) for name in required)
    )
    return {
        "status": "verified" if verified else "unverified",
        "reason": None if verified else "required_runtime_distribution_source_unavailable",
        "python_executable": payload.get("python_executable"),
        "python_version": payload.get("python_version"),
        "python_implementation": payload.get("python_implementation"),
        "python_cache_tag": payload.get("python_cache_tag"),
        "python_soabi": payload.get("python_soabi"),
        "required_packages": required,
        "dependency_inventory": inventory,
        "dependency_inventory_sha256": payload.get("dependency_inventory_sha256"),
        "packages": packages,
    }
