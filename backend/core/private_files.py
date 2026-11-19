"""Permissions for local state and backups; Windows uses its own access controls."""

import os
import sys
from pathlib import Path


def chmod(path, mode):
    if sys.platform != "win32":
        try:
            path.chmod(mode)
        except FileNotFoundError:
            pass  # SQLite may remove its sidecars when the last connection closes.


def directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    chmod(path, 0o700)


def sqlite_files(path):
    # Precreate without truncating, so SQLite inherits 0600 for new WAL and SHM files.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(descriptor)
    for item in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        chmod(item, 0o600)


def backups(root):
    if not root.exists() or root.is_symlink():
        return
    for parent, dirs, files in os.walk(root, followlinks=False):
        base = Path(parent)
        chmod(base, 0o700)
        dirs[:] = [name for name in dirs if not (base / name).is_symlink()]
        for name in files:
            path = base / name
            if not path.is_symlink():
                chmod(path, 0o600)
