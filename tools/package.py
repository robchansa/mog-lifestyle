#!/usr/bin/env python3
"""Build the deployable bundle.

    python3 tools/package.py                    # dist/mog-lifestyle-<version>-<commit>.zip
    python3 tools/package.py --stage dist/site  # also leave the unpacked tree

The zip is what you upload to Namecheap: extract it inside the Python app's
folder and `passenger_wsgi.py` sits at the top, where cPanel expects it.  The
GitHub workflow deploys the staged tree from this same script, so a manual
upload and an automatic deploy contain exactly the same files.

Only what the server runs goes in.  Never the database, the development secret
key, tests, the client's intake form, design sources, git metadata or caches.
The build is reproducible: same commit, byte-identical zip.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Everything shipped, relative to the repository root.
INCLUDE = (
    "app",
    "passenger_wsgi.py",
    "run.py",
    "requirements.txt",
    "README.md",
    "docs/NAMECHEAP.md",
    "docs/DEPLOYMENT.md",
    "docs/mog.env.example",
    "tools/audit.py",
)
# Never shipped, wherever it appears.
EXCLUDED_NAMES = {"__pycache__", ".DS_Store", ".git", "data", ".secret_key", "dist"}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm",
                     ".env", ".log")         # mog.env.example is a template, not .env
FIXED_TIME = (2020, 1, 1, 0, 0, 0)          # zip timestamps, for reproducibility


def version() -> str:
    namespace: dict = {}
    exec((ROOT / "app" / "__init__.py").read_text(), namespace)
    return namespace["__version__"]


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def shipped(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if any(part in EXCLUDED_NAMES for part in relative.parts):
        return False
    return not path.name.endswith(EXCLUDED_SUFFIXES)


def collect() -> list[Path]:
    files: list[Path] = []
    for entry in INCLUDE:
        target = ROOT / entry
        if target.is_dir():
            files += [p for p in sorted(target.rglob("*")) if p.is_file() and shipped(p)]
        elif target.is_file():
            files.append(target)
        else:
            raise SystemExit(f"package: {entry} is missing")
    return sorted(set(files))


def build_info() -> dict:
    commit = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    return {
        "version": version(),
        "commit": commit + ("-dirty" if commit and dirty else ""),
        "committed_at": git("log", "-1", "--format=%cI"),
    }


def build(out_dir: Path, stage: Path | None = None) -> Path:
    files = collect()
    info = build_info()
    out_dir.mkdir(parents=True, exist_ok=True)
    short = (info["commit"] or "local")[:7]
    if info["commit"].endswith("-dirty"):
        short += "-uncommitted"          # built from changes not yet in git
    archive = out_dir / f"mog-lifestyle-{info['version']}-{short}.zip"
    stamp = json.dumps(info, indent=2, sort_keys=True) + "\n"

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            name = path.relative_to(ROOT).as_posix()
            entry = zipfile.ZipInfo(name, FIXED_TIME)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (0o755 if name in ("run.py",) else 0o644) << 16
            zf.writestr(entry, path.read_bytes())
        entry = zipfile.ZipInfo("BUILD.json", FIXED_TIME)
        entry.compress_type = zipfile.ZIP_DEFLATED
        entry.external_attr = 0o644 << 16
        zf.writestr(entry, stamp)

    if stage is not None:
        if stage.exists():
            shutil.rmtree(stage)
        for path in files:
            target = stage / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        (stage / "BUILD.json").write_text(stamp)
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / "dist"), help="output folder")
    parser.add_argument("--stage", help="also write the unpacked tree here")
    args = parser.parse_args(argv)
    archive = build(Path(args.out), Path(args.stage) if args.stage else None)
    with zipfile.ZipFile(archive) as zf:
        count = len(zf.namelist())
    shown = archive.relative_to(ROOT) if archive.is_relative_to(ROOT) else archive
    print(f"  {shown}  ({count} files, {archive.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
