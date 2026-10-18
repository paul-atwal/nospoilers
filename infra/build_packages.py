#!/usr/bin/env python3
"""Build reproducible Linux/x86_64 Lambda packages from locked requirements."""

from __future__ import annotations

import argparse
import compileall
import hashlib
import py_compile
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PACKAGE_PREFIXES = ("nflreadpy", "pandas", "pyarrow")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
LAMBDA_PYTHON = (3, 11)
LAMBDA_TASK_ROOT = "/var/task"
# Both Lambdas only call DynamoDB. The other botocore/boto3 service models are
# most of the archive and only slow the cold-start download and extraction.
AWS_SERVICE_MODELS = frozenset({"dynamodb"})


def _install_dependencies(kind: str, target: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-compile",
            "--target",
            str(target),
            "--platform",
            "manylinux2014_x86_64",
            "--implementation",
            "cp",
            "--python-version",
            "311",
            "--only-binary=:all:",
            "-r",
            f"requirements-{kind}.txt",
        ],
        check=True,
        cwd=ROOT / "backend",
    )


def _copy_application(target: Path) -> None:
    package_root = target / "backend" / "nospoil_nfl"
    package_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        ROOT / "backend" / "nospoil_nfl",
        package_root,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store"),
    )


def _reject_excluded_dependencies(target: Path) -> None:
    names = {path.name.lower().replace("-", "_") for path in target.iterdir()}
    included = sorted(
        name for name in names if name.startswith(EXCLUDED_PACKAGE_PREFIXES)
    )
    if included:
        raise RuntimeError(
            "Lambda dependency set contains reconciliation-only packages: "
            + ", ".join(included)
        )


def _prune_aws_service_models(target: Path) -> None:
    for data_root in (target / "botocore" / "data", target / "boto3" / "data"):
        if not data_root.is_dir():
            continue
        for service in data_root.iterdir():
            if service.is_dir() and service.name not in AWS_SERVICE_MODELS:
                shutil.rmtree(service)


def _compile_bytecode(target: Path) -> None:
    """Precompile so Lambda does not recompile every module on each cold start.

    /var/task is read-only, so without shipped bytecode every cold start pays
    to compile FastAPI, pydantic, and botocore from source. Unchecked-hash
    bytecode stays valid regardless of the archive's fixed timestamps.
    """
    if sys.version_info[:2] != LAMBDA_PYTHON:
        raise RuntimeError(
            "Lambda bytecode must be compiled with Python "
            + ".".join(map(str, LAMBDA_PYTHON))
        )
    compiled = compileall.compile_dir(
        target,
        quiet=1,
        workers=0,
        stripdir=str(target),
        prependdir=LAMBDA_TASK_ROOT,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    if not compiled:
        raise RuntimeError("Lambda bytecode compilation failed")


def _write_reproducible_zip(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w") as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(relative, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def build(kind: str, output_dir: Path) -> Path:
    """Build one package and return its content-addressed archive path."""
    with tempfile.TemporaryDirectory(prefix=f"nospoil-{kind}-") as temp:
        target = Path(temp) / "package"
        target.mkdir()
        _install_dependencies(kind, target)
        _copy_application(target)
        _reject_excluded_dependencies(target)
        _prune_aws_service_models(target)
        _compile_bytecode(target)

        temporary_archive = output_dir / f"{kind}.tmp.zip"
        _write_reproducible_zip(target, temporary_archive)

    digest = hashlib.sha256(temporary_archive.read_bytes()).hexdigest()
    final = output_dir / f"{kind}-{digest[:16]}.zip"
    temporary_archive.replace(final)
    return final


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    for stale in args.output_dir.glob("*.zip"):
        stale.unlink()
    for kind in ("read", "sync"):
        print(build(kind, args.output_dir))


if __name__ == "__main__":
    main()
