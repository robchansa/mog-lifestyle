#!/usr/bin/env python3
"""Deploy a staged release to the Namecheap Python app over explicit FTPS.

The FTP account is scoped to the application folder, so all remote paths are
relative to that folder.  Private settings and the SQLite database live in
``~/mog-data`` and are therefore unreachable to this account.
"""
from __future__ import annotations

import argparse
import ftplib
import io
import os
import posixpath
import ssl
import sys
import time
from pathlib import Path

CODE_DIRS = ("app", "tools", "docs")
ROOT_FILES = (
    "passenger_wsgi.py", "run.py", "requirements.txt", "README.md", "BUILD.json",
)


class DeployError(RuntimeError):
    pass


FTP_ERRORS = (DeployError, OSError, ValueError) + ftplib.all_errors


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise DeployError(f"missing required environment variable: {name}")
    return value


def entries(ftp: ftplib.FTP_TLS) -> list[tuple[str, dict[str, str]]]:
    return [(name, facts) for name, facts in ftp.mlsd()
            if name not in (".", "..")]


def remove_tree(ftp: ftplib.FTP_TLS, name: str) -> None:
    """Remove one known code directory without following links elsewhere."""
    ftp.cwd(name)
    try:
        for child, facts in entries(ftp):
            if facts.get("type") == "dir":
                remove_tree(ftp, child)
            else:
                ftp.delete(child)
    finally:
        ftp.cwd("..")
    ftp.rmd(name)


def ensure_dir(ftp: ftplib.FTP_TLS, path: str) -> None:
    current = ftp.pwd()
    try:
        for part in (p for p in path.split("/") if p):
            try:
                ftp.cwd(part)
            except ftplib.error_perm:
                ftp.mkd(part)
                ftp.cwd(part)
    finally:
        ftp.cwd(current)


def upload_file(ftp: ftplib.FTP_TLS, local: Path, remote: str) -> None:
    parent = posixpath.dirname(remote)
    if parent:
        ensure_dir(ftp, parent)
    with local.open("rb") as source:
        ftp.storbinary(f"STOR {remote}", source, blocksize=256 * 1024)


def deploy(stage: Path, ftp: ftplib.FTP_TLS, remote_path: str = "/") -> int:
    if not stage.is_dir():
        raise DeployError(f"staged site not found: {stage}")
    missing = [name for name in (*CODE_DIRS, *ROOT_FILES)
               if not (stage / name).exists()]
    if missing:
        raise DeployError("staged site is incomplete: " + ", ".join(missing))

    ftp.cwd(remote_path)
    root_names = {name for name, _ in entries(ftp)}
    if not {"passenger_wsgi.py", "BUILD.json"}.issubset(root_names):
        raise DeployError(
            "refusing to deploy: the FTP root is not the MOG Python app folder"
        )

    for directory in CODE_DIRS:
        if directory in root_names:
            remove_tree(ftp, directory)

    uploaded = 0
    for local in sorted(path for path in stage.rglob("*") if path.is_file()):
        remote = local.relative_to(stage).as_posix()
        upload_file(ftp, local, remote)
        uploaded += 1

    ensure_dir(ftp, "tmp")
    restart = f"deployed {time.time_ns()}\n".encode()
    ftp.storbinary("STOR tmp/restart.txt", io.BytesIO(restart))
    return uploaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=Path, help="unpacked release folder")
    args = parser.parse_args(argv)

    try:
        host = required("NAMECHEAP_FTP_HOST")
        user = required("NAMECHEAP_FTP_USER")
        password = required("NAMECHEAP_FTP_PASSWORD")
        # GitHub exposes an unset optional secret as an empty string, so the
        # fallback must handle both a missing variable and an empty one.
        port = int(os.environ.get("NAMECHEAP_FTP_PORT", "").strip() or "21")
        remote_path = os.environ.get("NAMECHEAP_FTP_PATH", "/") or "/"

        context = ssl.create_default_context()
        with ftplib.FTP_TLS(context=context, timeout=60) as ftp:
            ftp.connect(host, port)
            ftp.login(user, password)
            ftp.prot_p()
            ftp.set_pasv(True)
            count = deploy(args.stage, ftp, remote_path)
            ftp.quit()
    except FTP_ERRORS as exc:
        print(f"deploy failed: {exc}", file=sys.stderr)
        return 1

    print(f"deployed {count} files over encrypted FTPS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
