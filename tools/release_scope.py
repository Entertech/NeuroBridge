"""Resolve a complete installer selection for one immutable release candidate."""

from __future__ import annotations

from fnmatch import fnmatchcase
import subprocess
import tomllib

from .release_pipeline import CONFIG, is_business_source, matrix, run, select_targets, system_version


def scope_for(head: str, baselines: dict[str, str], requested: str = "") -> dict:
    targets = matrix()
    available = list(dict.fromkeys(system_version(target) for target in targets))
    if set(baselines) != set(available):
        raise ValueError("release baselines do not cover the configured systems")
    selection = CONFIG["selection"]
    configured = ",".join(selection["default_systems"])
    mode = "explicit" if requested else "configured" if configured else "affected"
    if requested or configured:
        chosen = select_targets(targets, requested or configured)
        return {"mode": mode, "systems": list(dict.fromkeys(map(system_version, chosen))),
                "baseCommits": baselines}

    changed_by_base = {}
    changed_by_system = {}
    for system in available:
        base = baselines[system]
        if base not in changed_by_base:
            changed_by_base[base] = changed_business_paths(base, head)
        platform = system.split("-", 1)[0]
        changed_by_system[system] = sorted(
            path for path in changed_by_base[base] if platform in affected_platforms(path, selection)
        )
    return {"mode": mode, "systems": [system for system in available if changed_by_system[system]],
            "baseCommits": baselines, "changedFiles": changed_by_system}


def changed_business_paths(base: str, head: str) -> list[str]:
    paths = run("git", "diff", "--no-renames", "--name-only", "-z", base, head, "--").split("\0")
    version_sources = {"pyproject.toml", "config/kylin-runtime-manifest.toml", "neurobridge/version_registry.toml"}
    changed = sorted(version_sources.intersection(paths))
    for path in changed:
        try:
            before, after = (tomllib.loads(run("git", "show", f"{ref}:{path}")) for ref in (base, head))
        except subprocess.CalledProcessError:
            continue  # A new or deleted runtime input is itself a change.
        if path == "pyproject.toml":
            for source in (before, after):
                source.get("project", {}).pop("version", None)
        elif path == "config/kylin-runtime-manifest.toml":
            for source in (before, after):
                source.get("runtime", {}).pop("application_version", None)
                source.get("runtime", {}).pop("file_name", None)
        else:
            before, after = before["northbound_wire_protocol"], after["northbound_wire_protocol"]
        if before == after:
            paths.remove(path)
    return paths


def affected_platforms(path: str, selection: dict) -> set[str]:
    if not path or path.lower().endswith((".md", ".rst")):
        return set()
    platforms = {"windows", "kylin"}
    specific = {platform for platform in platforms
                if any(fnmatchcase(path, pattern) for pattern in selection[f"{platform}_paths"])}
    if specific:
        return specific
    if is_business_source(path) or any(fnmatchcase(path, pattern) for pattern in selection["shared_paths"]):
        return platforms
    return set()
