"""Production configuration layering, with explicit development overrides."""
from pathlib import Path
import tomllib

from ..config import load


def load_runtime_config(config_path, *, defaults_path=None, override_path=None, development=False):
    if override_path is not None and not development:
        raise ValueError("--override-config requires explicit --development mode")
    if defaults_path is None:
        bundled = Path(__file__).resolve().parents[2] / "defaults.toml"
        defaults_path = bundled if bundled.is_file() else None
    if defaults_path is not None:
        with Path(defaults_path).open("rb") as source:
            profile = tomllib.load(source).get("profile")
        for candidate in (config_path, override_path):
            if candidate is None:
                continue
            with Path(candidate).open("rb") as source:
                configured = tomllib.load(source).get("profile", profile)
            if profile and configured != profile:
                raise ValueError("System configuration cannot change the packaged deployment profile")
    return load(override_path or config_path, defaults_path=defaults_path,
                system_path=config_path if override_path else None)
