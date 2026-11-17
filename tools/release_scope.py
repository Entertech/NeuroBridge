"""Resolve a complete installer selection for one immutable release candidate."""

from __future__ import annotations

from fnmatch import fnmatchcase
import subprocess
import tomllib

from .release_pipeline import is_business_source, matrix, run, select_targets, software_versions, system_version, version_tuple


def scope_for(head: str, baselines: dict[str, str], requested: str = "", *, config: dict) -> dict:
    targets = matrix(config)
    available = list(dict.fromkeys(system_version(target) for target in targets))
    if set(baselines) != set(available):
        raise ValueError("release baselines do not cover the configured systems")
    selection = config.get("selection")
    configured = ",".join(selection["default_systems"]) if selection is not None else ""
    mode = "explicit" if requested else "configured" if configured else "affected"
    current = registry_at(head)
    if "software_versions" in current:
        requested_systems = {system_version(target) for target in select_targets(targets, requested or configured)}
        paths = changed_systems(head, baselines, selection or {})
        eligible = advanced_systems(current, baselines, paths)
        return {"mode": mode, "systems": [system for system in available if system in eligible & requested_systems],
                "baseCommits": baselines, "changedFiles": paths, "versionControlled": True}
    if selection is None and not requested:
        # Source snapshots from before system selection used the entire matrix.
        return {"mode": "legacy", "systems": available, "baseCommits": baselines}
    if requested or configured:
        chosen = select_targets(targets, requested or configured)
        return {"mode": mode, "systems": list(dict.fromkeys(map(system_version, chosen))),
                "baseCommits": baselines}

    changed_by_system = changed_systems(head, baselines, selection)
    return {"mode": mode, "systems": [system for system in available if changed_by_system[system]],
            "baseCommits": baselines, "changedFiles": changed_by_system}


def registry_at(ref: str) -> dict:
    return tomllib.loads(run("git", "show", f"{ref}:neurobridge/version_registry.toml"))


def advanced_systems(current: dict, baselines: dict[str, str], paths: dict[str, list]) -> set[str]:
    versions = software_versions(current, baselines)
    previous = {ref: software_versions(registry_at(ref), baselines) for ref in set(baselines.values())}
    eligible = set()
    for system, baseline in baselines.items():
        old, new = previous[baseline][system], versions[system]
        if version_tuple(new) < version_tuple(old):
            raise ValueError(f"{system} version regressed: {old} -> {new}")
        if new != old:
            if not paths[system]:
                raise ValueError(f"{system} version advanced without corresponding software changes: {old} -> {new}")
            eligible.add(system)
    return eligible


def changed_systems(head: str, baselines: dict[str, str], selection: dict) -> dict[str, list]:
    changed_by_base = {}
    changed_by_system = {}
    for system, base in baselines.items():
        if base not in changed_by_base:
            changed_by_base[base] = changed_business_paths(base, head)
        platform = system.split("-", 1)[0]
        changed_by_system[system] = sorted(
            path for path in changed_by_base[base] if platform in affected_platforms(path, selection)
        )
    return changed_by_system


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
                if any(fnmatchcase(path, pattern) for pattern in selection.get(f"{platform}_paths", ()))}
    if specific:
        return specific
    if is_business_source(path) or any(fnmatchcase(path, pattern) for pattern in selection.get("shared_paths", ())):
        return platforms
    return set()
