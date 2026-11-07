from __future__ import annotations

import csv
import importlib.metadata
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arcagi_pipeline.game_provenance import inspect_runtime_packages, inspect_tycho_environment


class GameProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_tycho_auto_resolution_requires_one_matching_version(self):
        tycho = self.root / "tycho"
        for version in ("v1", "v2"):
            source = tycho / "environment_files" / "gridgame" / version
            source.mkdir(parents=True)
            (source / "metadata.json").write_text(
                json.dumps({"game_id": f"gridgame-{version}", "version": version, "class_name": "GridGame"}),
                encoding="utf-8",
            )
            (source / "gridgame.py").write_text("def step(state): return state\n", encoding="utf-8")
        ambiguous = inspect_tycho_environment(tycho, "gridgame")
        self.assertEqual(ambiguous["status"], "unverified")
        self.assertEqual(ambiguous["reason"], "multiple_matching_tycho_game_environments")
        bound = inspect_tycho_environment(
            tycho, "gridgame", source_dir=tycho / "environment_files" / "gridgame" / "v1"
        )
        self.assertEqual(bound["status"], "verified")
        self.assertEqual(bound["version"], "v1")
        self.assertEqual(bound["game_source_path"], "gridgame.py")

    def _add_distribution(self, site: Path, name: str, version: str, module: str, source: Path | None = None):
        package = site / module
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
        normalized_name = name.replace("-", "_")
        dist_info = site / f"{normalized_name}-{version}.dist-info"
        dist_info.mkdir(parents=True)
        (dist_info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n",
            encoding="utf-8",
        )
        paths = [f"{module}/__init__.py", f"{normalized_name}-{version}.dist-info/METADATA"]
        with (dist_info / "RECORD").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            for row in paths:
                writer.writerow([row, "", ""])
        if source is not None:
            (dist_info / "direct_url.json").write_text(
                json.dumps({"url": source.as_uri(), "dir_info": {"editable": True}}),
                encoding="utf-8",
            )
            with (dist_info / "RECORD").open("a", encoding="utf-8", newline="") as stream:
                csv.writer(stream).writerow([f"{normalized_name}-{version}.dist-info/direct_url.json", "", ""])

    def _add_duck_dependencies(self, site: Path):
        for name in (
            "jinja2", "numpy", "pillow", "pyyaml", "matplotlib", "python-dotenv",
            "requests", "imageio", "imageio-ffmpeg", "scipy", "kaggle",
        ):
            try:
                version = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                version = "1.0"
            self._add_distribution(site, name, version, name.replace("-", "_"))

    def test_runtime_fingerprints_editable_taaf_source_and_detects_change(self):
        site = self.root / "site"
        source = self.root / "taaf-source"
        source.mkdir()
        (source / "pyproject.toml").write_text("[project]\nname='tufa-arc-agi-framework'\n", encoding="utf-8")
        (source / "framework.py").write_text("VALUE = 1\n", encoding="utf-8")
        self._add_distribution(site, "arcengine", "0.9.3", "arcengine")
        self._add_distribution(site, "arc-agi", "0.9.9", "arc_agi")
        self._add_distribution(site, "tufa-arc-agi-framework", "0.1.0", "tufa_framework", source)
        self._add_duck_dependencies(site)
        self._add_distribution(site, "helper-library", "1.0", "helper_library")
        with patch.dict(os.environ, {"PYTHONPATH": str(site)}):
            first = inspect_runtime_packages(sys.executable, "duck")
            taaf = first["packages"]["tufa-arc-agi-framework"]
            self.assertEqual(first["status"], "verified", first)
            self.assertIn("arc-agi", first["required_packages"])
            self.assertEqual(first["python_executable"], str(Path(sys.executable).resolve()))
            self.assertTrue(first["dependency_inventory_sha256"])
            self.assertEqual(taaf["source_kind"], "editable_local")
            self.assertEqual(taaf["editable_source"], str(source.resolve()))
            first_digest = taaf["source_tree_sha256"]
            self.assertEqual(len(first_digest), 64)

            (source / "framework.py").write_text("VALUE = 2\n", encoding="utf-8")
            second = inspect_runtime_packages(sys.executable, "duck")
        self.assertNotEqual(
            first_digest,
            second["packages"]["tufa-arc-agi-framework"]["source_tree_sha256"],
        )

    def test_runtime_binds_transitive_dependency_versions(self):
        site = self.root / "site"
        self._add_distribution(site, "arcengine", "0.9.3", "arcengine")
        self._add_distribution(site, "arc-agi", "0.9.9", "arc_agi")
        self._add_distribution(site, "tufa-arc-agi-framework", "0.1.0", "taaf")
        self._add_duck_dependencies(site)
        self._add_distribution(site, "helper-library", "1.0", "helper_library")
        with patch.dict(os.environ, {"PYTHONPATH": str(site)}):
            first = inspect_runtime_packages(sys.executable, "duck")
            self._add_distribution(site, "helper-library", "1.1", "helper_library")
            second = inspect_runtime_packages(sys.executable, "duck")
        self.assertEqual(first["status"], "verified", first)
        self.assertEqual(second["status"], "verified", second)
        self.assertNotEqual(first["dependency_inventory_sha256"], second["dependency_inventory_sha256"])

    def test_runtime_without_required_distributions_is_unverified(self):
        missing_site = self.root / "empty-site"
        missing_site.mkdir()
        with patch.dict(os.environ, {"PYTHONPATH": str(missing_site)}):
            result = inspect_runtime_packages(sys.executable, "duck")
        self.assertEqual(result["status"], "unverified")
        self.assertIn("arcengine", result["required_packages"])
        self.assertIn("arc-agi", result["required_packages"])
        self.assertIn("tufa-arc-agi-framework", result["required_packages"])

    def test_raw_profile_verifies_actual_module_and_rejects_duplicate_versions(self):
        site=self.root/"raw-site"
        self._add_distribution(site,"arcengine","0.9.3","arcengine")
        for name in ("numpy","pydantic"):
            self._add_distribution(site,name,importlib.metadata.version(name),name)
        with patch.dict(os.environ,{"PYTHONPATH":str(site)}):
            valid=inspect_runtime_packages(sys.executable,"raw-arcengine")
            self.assertEqual(valid["status"],"verified",valid)
            self.assertEqual(valid["required_packages"],["arcengine","numpy","pydantic"])
            self._add_distribution(site,"pydantic","999.0","pydantic")
            invalid=inspect_runtime_packages(sys.executable,"raw-arcengine")
            self.assertEqual(invalid["status"],"unverified")

    def test_raw_profile_rejects_metadata_whose_module_resolves_elsewhere(self):
        site=self.root/"shadow-site"
        self._add_distribution(site,"arcengine","0.9.3","arcengine")
        self._add_distribution(site,"numpy",importlib.metadata.version("numpy"),"numpy")
        self._add_distribution(site,"pydantic",importlib.metadata.version("pydantic"),"other_pydantic")
        with patch.dict(os.environ,{"PYTHONPATH":str(site)}):
            result=inspect_runtime_packages(sys.executable,"raw-arcengine")
        self.assertEqual(result["status"],"unverified")


if __name__ == "__main__":
    unittest.main()
