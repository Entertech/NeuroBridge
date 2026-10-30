"""Read-only CI gate for the reviewed build inputs and workflow consumers."""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from tools.check_source_train_workflows import load_workflows

ROOT = Path(__file__).resolve().parents[1]


def requirements(path: Path) -> dict[str, str]:
    """Validate hash mode syntax and return the exact requirement declarations."""
    logical = re.sub(r"\\\s*\n", " ", path.read_text(encoding="utf-8"))
    result = {}
    for line in logical.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        spec, *hashes = re.split(r"\s+--hash=sha256:", line)
        name = spec.split("==", 1)[0]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+==[^\s;]+(?:; .+)?", spec) or not hashes:
            raise ValueError(f"Unpinned or unhashed requirement in {path.name}: {spec}")
        if any(not re.fullmatch(r"[a-f0-9]{64}", value.strip()) for value in hashes):
            raise ValueError(f"Invalid wheel hash in {path.name}")
        if name in result:
            raise ValueError(f"Duplicate requirement in {path.name}: {name}")
        result[name] = spec
    return result


def validate(root: Path = ROOT, workflows: dict | None = None):
    lock = json.loads((root / "config/build-environment.lock.json").read_text(encoding="utf-8"))
    workflows = load_workflows(root) if workflows is None else workflows
    expression = "${{ runner.os == 'Windows' && '" + lock["python"]["windows"] + "' || '" + lock["python"]["linux"] + "' }}"
    allowed_python = set(lock["python"].values()) | {expression}
    labels = {image["label"] for image in lock["runner_images"].values()}
    actions = lock["actions"]
    for version in (*lock["python"].values(), lock["node"]):
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("Build runtimes must use complete versions")
    for name, workflow in workflows.items():
        text = (root / ".github/workflows" / name).read_text(encoding="utf-8")
        if re.search(r"(?:ubuntu|windows)-latest|pip install|apt-get", text):
            raise ValueError(f"Floating runner or dependency installation in {name}; use the locked preparation helper")
        for job in workflow["jobs"].values():
            runner = job.get("runs-on")
            if isinstance(runner, str) and not runner.startswith("${{") and runner not in labels:
                raise ValueError(f"Unknown runner baseline in {name}: {runner}")
            matrix = job.get("strategy", {}).get("matrix", {})
            include = matrix.get("include", []) if isinstance(matrix, dict) else []
            for entry in include if isinstance(include, list) else []:
                if entry.get("runner", next(iter(labels))) not in labels:
                    raise ValueError(f"Floating matrix runner in {name}")
            steps = job.get("steps", [])
            for index, step in enumerate(steps):
                reference = step.get("uses", "")
                if not reference:
                    continue
                repo, separator, commit = reference.partition("@")
                key = repo.removesuffix("/restore").removesuffix("/save")
                if key not in actions or not separator or commit != actions[key]["commit"] or not re.fullmatch(r"[a-f0-9]{40}", commit):
                    raise ValueError(f"Action is not locked to a reviewed commit in {name}: {reference}")
                if key == "actions/setup-python":
                    if step["with"]["python-version"] not in allowed_python:
                        raise ValueError(f"Build Python drift in {name}")
                    if index+1 >= len(steps) or steps[index+1].get("run") != "python tools/build_environment.py check-host":
                        raise ValueError(f"Missing hosted-image gate in {name}")
                if key == "actions/setup-node" and step["with"]["node-version"] != lock["node"]:
                    raise ValueError(f"Node version drift in {name}")
    for key, item in lock["downloads"].items():
        url = urlsplit(item["url"])
        if url.scheme != "https" or url.query or url.fragment or url.username or Path(item["filename"]).name != item["filename"]:
            raise ValueError(f"Unsafe tool source in lock: {key}")
        if item["version"] not in item["url"]:
            raise ValueError(f"Tool URL does not identify its version: {key}")
        algorithm = "sha512" if "sha512" in item else "sha256"
        if not re.fullmatch(r"[a-f0-9]{128}" if algorithm == "sha512" else r"[a-f0-9]{64}", item.get(algorithm, "")):
            raise ValueError(f"Missing download digest: {key}")
    if not re.fullmatch(r"\d{8}T\d{6}Z", lock["apt"]["snapshot"]):
        raise ValueError("APT snapshot must have a fixed UTC timestamp")
    for profile in lock["apt"]["profiles"].values():
        if any(not re.fullmatch(r"[A-Za-z0-9:+.~_-]+", version) for version in profile["packages"].values()):
            raise ValueError("APT package version must be exact")
    tools = requirements(root / "requirements-build.lock")
    if tools != {name: name + "==" + version for name, version in lock["python_tools"].items()}:
        raise ValueError("Python tool lock and manifest disagree")
    if lock["downloads"]["pip"]["sha256"] not in (root / "requirements-build.lock").read_text():
        raise ValueError("Bootstrap pip digest is not allowed by the Python tool lock")
    if lock["downloads"]["pip"]["version"] != lock["python_tools"]["pip"]:
        raise ValueError("Bootstrap and installed pip versions differ")
    runtime = requirements(root / "requirements-runtime-build.lock")
    expected = {line.split("==")[0]: line for line in (root / "requirements.lock").read_text().splitlines()
                if line and not line.startswith("#")}
    expected["bleak-winrt"] = 'bleak-winrt==1.2.0; platform_system == "Windows"'
    if runtime != expected:
        raise ValueError("Build wheel lock changes or omits runtime requirements")
    requirements(root / "requirements-ci.lock")


def main():
    validate()
    print("build environment locks and workflow consumers passed")


if __name__ == "__main__":
    main()
