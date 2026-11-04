#!/usr/bin/env python3
"""Report local prerequisites without exposing credentials or contacting providers."""
import argparse
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(root))
    from ghost_director.config import load_settings
    settings = load_settings(args.env_file)
    from ghost_director.studio.context import context_status
    try:
        context = context_status(settings.studio_root)
    except ValueError as error:
        context = {"available": False, "error": str(error)}
    packages = {}
    for package in ("fastapi", "pydantic", "uvicorn", "httpx", "pillow", "meta-sam-parser"):
        try:
            packages[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            packages[package] = None
    commands = {}
    for command in ("node", "npm", "ffmpeg", "ffprobe", "blender", "zstd"):
        local_node = root / "web/node_modules/node/bin/node"
        executable = str(local_node) if command == "node" and local_node.is_file() else settings.blender if command == "blender" and settings.blender else shutil.which(command)
        if executable is None:
            commands[command] = {"available": False}
            continue
        try:
            result = subprocess.run([executable, "-version" if command in ("ffmpeg", "ffprobe") else "--version"], capture_output=True, text=True, timeout=10)
            commands[command] = {"available": result.returncode == 0, "version": result.stdout.splitlines()[0] if result.stdout else "unknown", "source": "web package executable" if command == "node" and local_node.is_file() else "configured executable" if command == "blender" and settings.blender else "PATH"}
        except (OSError, subprocess.TimeoutExpired):
            commands[command] = {"available": False}
    report = {
        "python": sys.version.split()[0], "python_supported": sys.version_info >= (3, 11),
        "packages": packages, "commands": commands, "role_context": context,
        "web_built": (root / "web/dist/index.html").is_file(),
        "default_demo_assets_present": (root / "productions/ghost-director-hackathon/assets/scene.template.json").is_file(),
        "notes": ["Node >=26 is required by web/package.json; the installed web package executable takes precedence over the shell version.", "Blender 5.2.3 LTS was used for the current set; GHOST_BLENDER can name an absolute executable outside PATH.", "Viewing an imported demo needs no model keys, Blender or FFmpeg.", "Generating references needs Blender; video decode/review needs ffmpeg and ffprobe.", "Exporting a Zstandard-compressed Blender source requires zstd; importing/viewing its already exported bundle does not.", "Private configuration was read for tool paths. Credential values are never printed; provider access was not tested."],
    }
    print(json.dumps(report, indent=2))
    return 0 if report["python_supported"] and all(packages.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
