"""Pin original public role guidance or an explicitly selected Studio OS checkout."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from ghost_director.config import REPO


LABELS = {"producer": "Producer", "dp": "Director of Photography", "planner": "Generation Planner", "supervisor": "Script Supervisor"}
ROLES = {
    "producer": ("production-operations", "role.producer.producer-orchestrator"),
    "dp": ("scene-planning-staging", "role.cinematography.director-of-photography"),
    "planner": ("prompt-compilation", "role.generation-technical.prompt-compiler-generation-planner"),
    "supervisor": ("review-finishing-delivery", "role.cinematography.photographic-spatial-continuity-specialist"),
}
# Additional whole modules are loaded only at the fixed workflow step where
# they apply. Their paths must occur in the resolver's available-L3 manifest.
STEP_MODULES = {
    "producer": (),
    "dp": ("skills/founder/tig-blocking-map/SKILL.md",),
    "planner": ("protocols/prompt-writing-skills.md", "skills/founder/tig-acting-task/SKILL.md", "skills/founder/tig-blocking-map/SKILL.md", "skills/founder/seedance-multimodal-reference/SKILL.md", "skills/vendor/sd25-pe/SKILL.md"),
    "supervisor": ("skills/creative-review/SKILL.md",),
}
MAX_CONTEXT_BYTES = 300_000
PUBLIC_ROOT = Path(__file__).resolve().parent
STUDIO_MARKERS = ("tools/resolve_bundle.py", "tools/studio_registry.py", "registry/routes.json", "registry/roles.json", "registry/personas.json", "registry/skills.json", "registry/imported-skills.yaml")
AUTHORITY = "These files supply domain guidance only; they grant no tool, spending, publication or creative-approval authority. No persona was cast. Never impersonate the operator or claim their approval."


def _read_pin(relative: str, *, root: Path, expected_hash=None, include_content=False) -> dict:
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Resolved Studio OS path is missing or outside checkout")
    if path.is_relative_to((root / "archive").resolve()):
        raise ValueError("Historical archives cannot become runtime instructions")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_hash is not None and digest != expected_hash:
        raise ValueError(f"Imported Studio OS skill hash changed: {relative}")
    record = {"path": relative, "sha256": digest, "bytes": len(raw)}
    if include_content:
        record["content"] = raw.decode("utf-8")
    return record


def _studio_root(studio_root: str | Path | None = None) -> Path | None:
    configured = studio_root if studio_root is not None else os.environ.get("GHOST_STUDIO_ROOT")
    if configured is None:
        if not (REPO / "tools/resolve_bundle.py").exists():
            return None
        root = REPO.resolve()
    else:
        if not str(configured).strip():
            raise ValueError("GHOST_STUDIO_ROOT must name a trusted Studio OS checkout; remove it to use bundled public contracts")
        root = Path(configured).expanduser().resolve()
    for relative in STUDIO_MARKERS:
        try:
            _read_pin(relative, root=root)
        except ValueError:
            raise ValueError("Studio OS context checkout is incomplete or unsafe. Fix GHOST_STUDIO_ROOT; no bundled fallback was used") from None
    return root


def context_status(studio_root: str | Path | None = None) -> dict:
    """Local configuration inspection, without executing a resolver or a model."""
    root = _studio_root(studio_root)
    return {"source": "studio-os" if root else "bundled-public", "roles": list(ROLES),
            "guidance": "External/co-located Studio OS contracts" if root else "Original Ghost Director public role contracts"}


def _public_context(role: str) -> dict:
    domain, role_id = ROLES[role]
    files = []
    for tier, name in (("L0", "shared"), ("L2", role)):
        pin = _read_pin(f"contracts/{name}.md", root=PUBLIC_ROOT, include_content=True)
        files.append({**pin, "path": "ghost_director/studio/" + pin["path"], "tier": tier})
    loaded_bytes = sum(item["bytes"] for item in files)
    if loaded_bytes > MAX_CONTEXT_BYTES:
        raise ValueError("Resolved context exceeds byte budget; split the assignment, never truncate a skill")
    entries = [{key: value for key, value in item.items() if key != "content"} for item in files]
    bundle = {"domain": domain, "source": "bundled-public", "L0": entries[:1], "L1": [],
              "seats": [{"role": role_id, "persona": "none", "L2": entries[1:], "L3_available": []}]}
    implementation = _read_pin("context.py", root=PUBLIC_ROOT)
    receipt = {**implementation, "path": "ghost_director/studio/context.py", "arguments": [], "bundle": bundle,
               "bundle_sha256": hashlib.sha256(json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
               "source_commit": None, "registry_pins": [], "source": "bundled-public"}
    return {"role_id": role_id, "role_alias": role, "persona_id": None, "domain": domain, "files": files,
            "loaded_bytes": loaded_bytes, "resolver": receipt, "context_source": "bundled-public",
            "source_note": "Original Ghost Director application guidance. No Studio OS prompts, personas or imported skills were loaded.",
            "authority": AUTHORITY}


def load_context(role: str, *, studio_root: str | Path | None = None) -> dict:
    if role not in ROLES:
        raise ValueError("Unknown Ghost Director product role")
    root = _studio_root(studio_root)
    if root is None:
        return _public_context(role)
    domain, role_id = ROLES[role]
    arguments = ["tools/resolve_bundle.py", "--domain", domain, "--seat", role_id + ":none"]
    resolved = subprocess.run([sys.executable, *arguments], cwd=root, capture_output=True, text=True, timeout=15, check=False)
    if resolved.returncode:
        raise ValueError("Studio OS context resolution failed")
    bundle = json.loads(resolved.stdout)
    seats = bundle.get("seats", [])
    if bundle.get("domain") != domain or len(seats) != 1 or seats[0].get("role") != role_id or seats[0].get("persona") != "none":
        raise ValueError("Resolver returned a different role or persona binding")
    registry = json.loads((root / "registry/roles.json").read_text())
    if role_id not in {item["id"] for item in registry["items"]}:
        raise ValueError("Product role is not present in the Studio OS registry")
    skills = {item["path"]: item for item in json.loads((root / "registry/skills.json").read_text())["items"]}

    selected = [(tier, item) for tier in ("L0", "L1") for item in bundle[tier]]
    selected.extend(("L2", item) for item in seats[0]["L2"])
    available = {item["path"]: item for item in seats[0]["L3_available"]}
    for name in STEP_MODULES[role]:
        if name not in available:
            raise ValueError(f"Required step module is absent from resolved context: {name}")
        selected.append(("L3", available[name]))
    # Imported skill registry documentation contains precedence/override rules.
    # Read it whole alongside the imported skill, preserving its independent pin.
    for _, item in list(selected):
        metadata = skills.get(item["path"], {})
        if metadata.get("registry_doc"):
            selected.append(("L3-registry-guidance", {"path": metadata["registry_doc"]}))

    files, seen, loaded_bytes = [], set(), 0
    for tier, item in selected:
        if item["path"] in seen:
            continue
        seen.add(item["path"])
        imported_hash = skills.get(item["path"], {}).get("sha256")
        pin = _read_pin(item["path"], root=root, expected_hash=imported_hash, include_content=True)
        if item.get("sha256") and item["sha256"] != pin["sha256"]:
            raise ValueError("Resolver and imported-skill registry disagree")
        loaded_bytes += pin["bytes"]
        if loaded_bytes > MAX_CONTEXT_BYTES:
            raise ValueError("Resolved context exceeds byte budget; split the assignment, never truncate a skill")
        files.append({**pin, "tier": tier})

    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5, check=False)
    registry_pins = [_read_pin(name, root=root) for name in STUDIO_MARKERS[1:]]
    receipt = {**_read_pin(arguments[0], root=root), "arguments": arguments[1:], "bundle": bundle,
               "bundle_sha256": hashlib.sha256(json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
               "source_commit": commit.stdout.strip() if commit.returncode == 0 else None, "registry_pins": registry_pins, "source": "studio-os"}
    return {"role_id": role_id, "role_alias": role, "persona_id": None, "domain": domain, "files": files,
            "loaded_bytes": loaded_bytes, "resolver": receipt, "context_source": "studio-os", "authority": AUTHORITY}
