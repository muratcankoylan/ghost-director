import hashlib
import json
import os
import shutil
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import pytest
from ghost_director.studio import context as context_module
from ghost_director.config import Settings
from ghost_director.jobs.store import ProductionStore
from ghost_director.service import ProductionService

from ghost_director.studio.context import REPO, ROLES, load_context


INTEGRATION_ROOT = Path(os.environ.get("GHOST_TEST_STUDIO_ROOT", REPO))
HAS_STUDIO = (INTEGRATION_ROOT / "tools/resolve_bundle.py").is_file()


class ContextTests(unittest.TestCase):
    @unittest.skipUnless(HAS_STUDIO, "Optional Studio OS checkout unavailable; public contracts are tested separately")
    def test_real_resolver_binds_canonical_roles_and_pins_whole_files(self):
        for alias, (domain, role_id) in ROLES.items():
            with self.subTest(role=alias):
                context = load_context(alias, studio_root=INTEGRATION_ROOT)
                self.assertEqual(context["role_id"], role_id)
                self.assertIsNone(context["persona_id"])
                self.assertEqual(context["resolver"]["bundle"]["domain"], domain)
                self.assertEqual(context["resolver"]["bundle"]["seats"][0]["persona"], "none")
                self.assertEqual(len(context["files"]), len({entry["path"] for entry in context["files"]}))
                for entry in context["files"]:
                    original = (INTEGRATION_ROOT / entry["path"]).read_bytes()
                    self.assertEqual(entry["content"].encode(), original)
                    self.assertEqual(entry["sha256"], hashlib.sha256(original).hexdigest())
                self.assertEqual(context["loaded_bytes"], sum(entry["bytes"] for entry in context["files"]))

    @unittest.skipUnless(HAS_STUDIO, "Optional Studio OS checkout unavailable; public contracts are tested separately")
    def test_imported_hash_mismatch_blocks_model_context(self):
        read_bytes = Path.read_bytes
        def tampered(path):
            data = read_bytes(path)
            if str(path).endswith("skills/founder/ai-film-method/SKILL.md"):
                return data + b"\nchanged"
            return data
        with patch.object(Path, "read_bytes", tampered), self.assertRaisesRegex(ValueError, "hash changed"):
            load_context("planner", studio_root=INTEGRATION_ROOT)

    def test_unlisted_role_fails_before_resolving(self):
        with patch("ghost_director.studio.context.subprocess.run") as resolve:
            with self.assertRaises(ValueError):
                load_context("invented-role")
            resolve.assert_not_called()

    def test_context_budget_rejects_instead_of_truncating(self):
        with patch("ghost_director.studio.context.MAX_CONTEXT_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "never truncate"):
                load_context("producer")


@pytest.fixture
def standalone(tmp_path, monkeypatch):
    root = tmp_path / "public-checkout"
    public = root / "ghost_director" / "studio"
    # Copy only original application contracts and the loader; no resolver,
    # registry, prompt/persona tree or inherited production is available.
    shutil.copytree(context_module.PUBLIC_ROOT, public, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(context_module, "REPO", root)
    monkeypatch.setattr(context_module, "PUBLIC_ROOT", public)
    monkeypatch.delenv("GHOST_STUDIO_ROOT", raising=False)
    return root


@pytest.mark.parametrize("role", list(ROLES))
def test_public_role_contracts_are_complete_and_pinned_without_studio_os(standalone, role):
    with patch("ghost_director.studio.context.subprocess.run", side_effect=AssertionError("Public contexts execute no resolver")):
        result = load_context(role)
    assert result["role_id"] == ROLES[role][1]
    assert result["persona_id"] is None
    assert result["domain"] == ROLES[role][0]
    assert result["context_source"] == result["resolver"]["source"] == "bundled-public"
    assert result["resolver"]["registry_pins"] == []
    assert result["resolver"]["bundle"]["seats"][0]["persona"] == "none"
    assert len(result["files"]) == 2
    for entry in result["files"]:
        raw = (standalone / entry["path"]).read_bytes()
        assert entry["content"].encode() == raw
        assert entry["sha256"] == hashlib.sha256(raw).hexdigest()
        assert entry["bytes"] == len(raw)
        assert entry["path"].startswith("ghost_director/studio/contracts/")
    assert result["loaded_bytes"] == sum(entry["bytes"] for entry in result["files"])
    assert context_module.context_status()["source"] == "bundled-public"
    assert result["resolver"]["bundle_sha256"] == hashlib.sha256(json.dumps(result["resolver"]["bundle"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_public_contracts_obey_context_budget(standalone, monkeypatch):
    monkeypatch.setattr(context_module, "MAX_CONTEXT_BYTES", 1)
    with pytest.raises(ValueError, match="never truncate"):
        load_context("producer")


@pytest.mark.parametrize("via_environment", [True, False])
def test_explicit_invalid_studio_root_never_silently_falls_back(standalone, monkeypatch, via_environment):
    missing = standalone / "missing-studio-os"
    if via_environment:
        monkeypatch.setenv("GHOST_STUDIO_ROOT", str(missing))
    with patch("ghost_director.studio.context.subprocess.run") as subprocess_run:
        with pytest.raises(ValueError, match="no bundled fallback"):
            load_context("producer", studio_root=None if via_environment else missing)
        subprocess_run.assert_not_called()


def test_partial_colocated_studio_root_does_not_hide_missing_registry(standalone):
    tools = standalone / "tools"
    tools.mkdir()
    (tools / "resolve_bundle.py").write_text("# Not a complete Studio OS checkout\n")
    with pytest.raises(ValueError, match="no bundled fallback"):
        load_context("producer")


def test_external_resolver_symlink_cannot_escape_trusted_checkout(standalone, tmp_path):
    external = tmp_path / "external"
    (external / "tools").mkdir(parents=True)
    target = tmp_path / "outside.py"
    target.write_text("raise RuntimeError('never execute')\n")
    (external / "tools/resolve_bundle.py").symlink_to(target)
    with patch("ghost_director.studio.context.subprocess.run") as subprocess_run:
        with pytest.raises(ValueError, match="unsafe"):
            load_context("producer", studio_root=external)
        subprocess_run.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_external_context_fails_before_provider_call(standalone, monkeypatch, tmp_path):
    monkeypatch.setenv("GHOST_STUDIO_ROOT", str(tmp_path / "absent"))
    store = ProductionStore(tmp_path / "production", opening_entries=())
    service = ProductionService(store, Settings("", "", "", root=store.root))
    service.runner.text = AsyncMock(side_effect=AssertionError("No paid provider call is permitted"))
    run = store.create_run()
    try:
        with pytest.raises(ValueError, match="no bundled fallback"):
            await service.role("producer", "Return a bounded assignment", {}, run["id"])
        service.runner.text.assert_not_called()
        assert store.snapshot()["jobs"] == []
    finally:
        await service.runner.http.close()
