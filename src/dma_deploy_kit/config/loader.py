"""Load and validate a client YAML config into a :class:`ClientConfig`.

``load_client_config`` reads a YAML file, validates it against the schema, and
raises a single :class:`ClientConfigError` whose message lists *every* validation
failure with a dotted, YAML-path-style location — so a config with several
mistakes reports all of them at once instead of one at a time.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import ClientConfig


class ClientConfigError(ValueError):
    """Raised when a client config file is missing, unparseable, or invalid."""


def _format_validation_error(path: Path, error: ValidationError) -> str:
    count = error.error_count()
    plural = "error" if count == 1 else "errors"
    lines = [f"{path}: {count} validation {plural} in client config:"]
    for err in error.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "(root)"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def load_client_config(path: str | Path) -> ClientConfig:
    """Read, parse, and validate a client config YAML file.

    Raises:
        ClientConfigError: if the file cannot be read/parsed, is not a mapping,
            or fails schema validation (message lists all failures).
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ClientConfigError(f"{path}: cannot read file: {exc}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ClientConfigError(f"{path}: invalid YAML: {exc}") from exc

    if data is None:
        raise ClientConfigError(f"{path}: file is empty")
    if not isinstance(data, dict):
        raise ClientConfigError(
            f"{path}: top-level YAML must be a mapping, got {type(data).__name__}"
        )

    try:
        return ClientConfig.model_validate(data)
    except ValidationError as exc:
        raise ClientConfigError(_format_validation_error(path, exc)) from exc
