"""Eval run-record core — the regression-tracking spine for eval v2.

A *run record* is a small JSON manifest written once per eval run. It pins the
exact prompt versions evaluated (via ``prompt_fingerprint``), the git commit, the
sources consulted, and the findings produced. Because the prompt compiler is
byte-deterministic (proven by ``test_determinism_byte_identical``), an identical
config + engine always yields an identical fingerprint, so two records with the
same fingerprints evaluated byte-identical prompts.

No network, no new dependencies — stdlib only. The runners inject an explicit
``out_dir`` so tests stay hermetic; the default is ``var/evals/runs`` (already
covered by the ``var/`` gitignore rule).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNS_DIR = REPO_ROOT / "var" / "evals" / "runs"

# Bump when the RunRecord shape changes in a way old readers can't handle.
SCHEMA_VERSION = 1


def prompt_fingerprint(prompt: str) -> str:
    """Return the sha256 hexdigest of the UTF-8 prompt text — the prompt version ID."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def fingerprint_key(source: Path | str, language_code: str) -> str:
    """Build a prompt_fingerprints key: "{source_relpath_posix}::{language_code}".

    The source path is made relative to the repo root and rendered POSIX-style so
    records are byte-identical across Windows and CI. Qualifying by path (not slug)
    prevents same-slug collisions, e.g. client.example.yaml and a local
    clients/acme-wellness.yaml that both carry slug "acme-wellness".
    """
    p = Path(source)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(REPO_ROOT)
        except ValueError:
            p = Path(source)  # outside the repo — keep as given
    return f"{p.as_posix()}::{language_code}"


def git_short_commit(cwd: str | Path | None = None) -> str:
    """Return `git rev-parse --short HEAD`, or "unknown" on any failure.

    Must never raise: a missing git, a non-repo cwd, or a nonzero exit all fall
    back to "unknown" so a run is never blocked by version metadata.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(cwd) if cwd is not None else str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = out.stdout.strip()
    return commit or "unknown"


@dataclass(frozen=True)
class RunRecord:
    schema_version: int
    run_id: str
    created_at: str
    layer: str
    git_commit: str
    prompt_fingerprints: dict[str, str]
    sources: list[str]
    findings: list[dict]
    finding_count: int

    @classmethod
    def create(
        cls,
        *,
        layer: str,
        prompt_fingerprints: dict[str, str],
        sources: list[str],
        findings: list,
        git_cwd: str | Path | None = None,
        now: datetime | None = None,
    ) -> RunRecord:
        """Build a record, deriving run_id/created_at/git_commit/finding_count.

        ``findings`` are the live Finding / TranscriptFinding dataclasses; they are
        serialized to plain dicts via ``dataclasses.asdict``.
        """
        moment = (now or datetime.now(UTC)).astimezone(UTC)
        stamp = moment.strftime("%Y%m%dT%H%M%SZ")
        finding_dicts = [asdict(f) for f in findings]
        return cls(
            schema_version=SCHEMA_VERSION,
            run_id=f"{stamp}-{layer}",
            created_at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
            layer=layer,
            git_commit=git_short_commit(git_cwd),
            prompt_fingerprints=dict(prompt_fingerprints),
            sources=list(sources),
            findings=finding_dicts,
            finding_count=len(finding_dicts),
        )


def write_run_record(record: RunRecord, out_dir: str | Path) -> Path:
    """Write ``record`` as pretty JSON to ``out_dir/<run_id>.json``; return the path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{record.run_id}.json"
    path.write_text(json.dumps(asdict(record), indent=2) + "\n", encoding="utf-8")
    return path


def read_run_record(path: str | Path) -> RunRecord:
    """Inverse of ``write_run_record``; raise on an unknown schema_version."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported run-record schema_version {version!r} "
            f"(this build reads version {SCHEMA_VERSION})"
        )
    return RunRecord(**data)
