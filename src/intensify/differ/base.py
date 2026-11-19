"""Drift at a base git revision, so a pull request is only blamed for drift it introduces."""

from __future__ import annotations

import io
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from intensify.spec.models import DriftCategory


class BaseRefError(Exception):
    """The base revision can't be read (not a git repository, or an unknown ref)."""


def repo_prefix(directory: Path) -> str | None:
    """Path of ``directory`` within its git work tree (``""`` at the top), or None outside git."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-prefix"],
            cwd=directory,
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return proc.stdout.strip()


def keys_at(base: str, spec_path: Path, *, fail_on: frozenset[DriftCategory]) -> set[str]:
    """Finding keys for the spec at ``base``.

    Empty if the spec didn't exist at ``base`` or was invalid there.
    """
    from intensify.differ import detect  # noqa: PLC0415 - detect() imports this module
    from intensify.pipeline import SpecInvalidError  # noqa: PLC0415

    directory = spec_path.parent
    prefix = repo_prefix(directory)
    if prefix is None:
        raise BaseRefError(f"{directory} is not inside a git repository")
    try:
        archive = subprocess.run(
            ["git", "archive", "--format=tar", base, "--", "."],
            cwd=directory,
            capture_output=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", errors="replace").strip()
        raise BaseRefError(f"cannot read revision {base!r}: {message}") from None

    with tempfile.TemporaryDirectory(prefix="intensify-base-") as tmp:
        _extract(archive, Path(tmp))
        base_spec = Path(tmp, prefix, spec_path.name)
        if not base_spec.is_file():
            return set()
        try:
            report = detect(base_spec, fail_on=fail_on)
        except SpecInvalidError:
            return set()
        return {f.key for f in report.findings}


def _extract(archive: bytes, target: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(target, filter="data")
            return
        # Python < 3.11.4 has no extraction filters: allow regular files and directories only.
        members = [
            m
            for m in tar.getmembers()
            if (m.isfile() or m.isdir())
            and not PurePosixPath(m.name).is_absolute()
            and ".." not in PurePosixPath(m.name).parts
        ]
        tar.extractall(target, members=members)
