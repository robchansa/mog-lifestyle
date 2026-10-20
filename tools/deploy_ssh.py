#!/usr/bin/env python3
"""Deploy a staged release to Namecheap over SSH and rsync."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath


class DeployError(RuntimeError):
    pass


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise DeployError(f"missing required environment variable: {name}")
    return value


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if len(args) != 1 or not Path(args[0]).is_dir():
        print("usage: deploy_ssh.py STAGE", file=sys.stderr)
        return 2
    stage = Path(args[0])
    try:
        host = required("NAMECHEAP_SSH_HOST")
        user = required("NAMECHEAP_SSH_USER")
        key = required("NAMECHEAP_SSH_KEY_FILE")
        remote = required("NAMECHEAP_APP_PATH")
        python = required("NAMECHEAP_PYTHON")
        if not remote.startswith("/home/") or len(PurePosixPath(remote).parts) < 4 or ".." in PurePosixPath(remote).parts:
            raise DeployError("NAMECHEAP_APP_PATH must be an absolute application folder under /home/USER")
        if any(not (stage / name).exists() for name in ("app", "tools", "docs", "passenger_wsgi.py", "run.py", "BUILD.json")):
            raise DeployError("staged release is incomplete")
        port = os.environ.get("NAMECHEAP_SSH_PORT", "22").strip() or "22"
        ssh = ["ssh", "-i", key, "-p", port, "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=yes"]
        target = f"{user}@{host}"
        subprocess.run([*ssh, target, f"cd {shlex.quote(remote)} && test -f passenger_wsgi.py && test -f BUILD.json && command -v rsync >/dev/null && test -x {shlex.quote(python)}"], check=True)
        rsync = ["rsync", "--archive", "--compress", "--delete-delay",
                 "--exclude", "tmp/", "--exclude", "*.log", "-e",
                 " ".join(shlex.quote(part) for part in ssh)]
        # Mirror only code directories; preserve cPanel files and runtime data.
        for directory in ("app", "tools", "docs"):
            subprocess.run([*rsync, f"{stage / directory}/",
                            f"{target}:{shlex.quote(remote.rstrip('/') + '/' + directory + '/') }"], check=True)
        files = [str(stage / name) for name in ("passenger_wsgi.py", "run.py", "requirements.txt", "README.md", "BUILD.json")]
        subprocess.run([*rsync, *files, f"{target}:{shlex.quote(remote.rstrip('/') + '/') }"], check=True)
        command = f"{shlex.quote(python)} run.py --migrate && mkdir -p tmp && touch tmp/restart.txt"
        subprocess.run([*ssh, target, f"cd {shlex.quote(remote)} && {command}"], check=True)
    except (DeployError, OSError, subprocess.CalledProcessError) as exc:
        print(f"deploy failed: {exc}", file=sys.stderr)
        return 1
    print(f"deployed staged site over SSH to {user}@{host}:{remote}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
