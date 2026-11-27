"""A stand-in for the batch mode of OpenSSH's sftp, for the tests of tools/pc/sincronizar.ps1
without a server: the four folders of the chroot of kbot_sync are local folders, named by
the environment (FAKE_SFTP_COPIAS, FAKE_SFTP_ARCHIVO, FAKE_SFTP_DESAFIOS, FAKE_SFTP_RECIBOS).

Like sftp -b: each line is echoed as ``sftp> …``; a command that fails stops the batch with
exit code 1, unless it starts with ``-``; a path outside the four folders does not exist,
as in the chroot. Only what the script uses: get [-p], ls -1, rm and put.

FAKE_SFTP_CUT_AT_RM=n cuts the connection (exit code 255) just before the n-th ``rm`` of a
batch, as a dropped network would: n=1 before any deletion, n=2 after the first one.
"""

from __future__ import annotations

import fnmatch
import os
import shlex
import shutil
import sys
from pathlib import Path

VIEWS = ("copias", "archivo", "desafios", "recibos")


class Failed(Exception):
    pass


def remote(path: str) -> Path:
    parts = path.strip("/").split("/") if not path.startswith("/") else []
    if not parts or parts[0] not in VIEWS:
        raise Failed(f'File "{path}" not found.')
    folder = os.environ[f"FAKE_SFTP_{parts[0].upper()}"]
    return Path(folder, *parts[1:])


def get(source: str, target: str) -> None:
    pattern = remote(source)
    matches = (
        sorted(p for p in pattern.parent.glob("*") if fnmatch.fnmatch(p.name, pattern.name))
        if "*" in pattern.name and pattern.parent.is_dir()
        else ([pattern] if pattern.is_file() else [])
    )
    if not matches:
        raise Failed(f'File "{source}" not found.')
    for match in matches:
        local = Path(target)
        if target.endswith("/") or local.is_dir():
            local = local / match.name
        shutil.copyfile(match, local)


def run(line: str) -> None:
    words = shlex.split(line)
    command, arguments = words[0], [w for w in words[1:] if w not in ("-p", "-1")]
    if command == "get":
        get(arguments[0], arguments[1])
    elif command == "put":
        shutil.copyfile(arguments[0], remote(arguments[1]))
    elif command == "rm":
        target = remote(arguments[0])
        if not target.is_file():
            raise Failed(f'Couldn\'t delete file: "{arguments[0]}" not found')
        target.unlink()
    elif command == "ls":
        folder = remote(arguments[0])
        if not folder.is_dir():
            raise Failed(f'Can\'t ls: "{arguments[0]}" not found')
        for entry in sorted(folder.iterdir()):
            print(f"{arguments[0].rstrip('/')}/{entry.name}")
    else:
        raise Failed(f"Invalid command: {command}")


def main(argv: list[str]) -> int:
    batch = Path(argv[argv.index("-b") + 1])
    cut = int(os.environ.get("FAKE_SFTP_CUT_AT_RM", "0"))
    deletions = 0
    for raw in batch.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        if raw.lstrip("-").startswith("rm "):
            deletions += 1
            if deletions == cut:
                print("Connection closed", file=sys.stderr)
                return 255
        print(f"sftp> {raw}")
        tolerant = raw.startswith("-")
        try:
            run(raw[1:] if tolerant else raw)
        except (Failed, OSError, IndexError) as error:
            print(str(error), file=sys.stderr)
            if not tolerant:
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
