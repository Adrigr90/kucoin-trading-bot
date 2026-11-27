"""A stand-in for the batch mode of OpenSSH's sftp, for the tests of tools/pc/sincronizar.ps1
without a server: the four folders of the chroot of kbot_sync are local folders, named by
the environment (FAKE_SFTP_COPIAS, FAKE_SFTP_ARCHIVO, FAKE_SFTP_DESAFIOS, FAKE_SFTP_RECIBOS).

Like sftp -b: each line is echoed as ``sftp> …``; a command that fails stops the batch with
exit code 1, unless it starts with ``-``; a path outside the four folders does not exist,
as in the chroot. Only what the script uses: get [-p], ls -1 (without the names that start
with a dot, like sftp), rm (with ``*``), put and rename (which replaces its target, like the
posix-rename of OpenSSH).

Like OpenSSH, put empties its target before writing any data. Two cuts of the connection
(exit code 255), as a dropped network would:
- FAKE_SFTP_CUT_AT_RM=n, just before the n-th deletion of a batch (the clean-up of the
  temporary names of the PC is not counted): n=1 before any deletion, n=2 after the first;
- FAKE_SFTP_CUT_AT_PUT=n, in the n-th put of a batch, after emptying its target and before
  the first byte of data.

FAKE_SFTP_FAIL_GET=text makes every get of a path that holds that text fail on its own, as a
single failed download that ``-get`` lets the batch go past.
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


class Cut(Exception):
    pass


def remote(path: str) -> Path:
    parts = path.strip("/").split("/") if not path.startswith("/") else []
    if not parts or parts[0] not in VIEWS:
        raise Failed(f'File "{path}" not found.')
    folder = os.environ[f"FAKE_SFTP_{parts[0].upper()}"]
    return Path(folder, *parts[1:])


def matching(pattern: Path) -> list[Path]:
    if "*" not in pattern.name:
        return [pattern] if pattern.is_file() else []
    if not pattern.parent.is_dir():
        return []
    return sorted(p for p in pattern.parent.iterdir() if fnmatch.fnmatch(p.name, pattern.name))


def get(source: str, target: str) -> None:
    matches = matching(remote(source))
    if not matches:
        raise Failed(f'File "{source}" not found.')
    for match in matches:
        local = Path(target)
        if target.endswith("/") or local.is_dir():
            local = local / match.name
        shutil.copyfile(match, local)


def run(line: str, cut_this_put: bool) -> None:
    words = shlex.split(line)
    command, arguments = words[0], [w for w in words[1:] if w not in ("-p", "-1")]
    if command == "get":
        failing = os.environ.get("FAKE_SFTP_FAIL_GET", "")
        if failing and failing in arguments[0]:
            raise Failed(f"Couldn't read {arguments[0]}: Failure")
        get(arguments[0], arguments[1])
    elif command == "put":
        target = remote(arguments[1])
        target.write_bytes(b"")  # emptied first: OpenSSH opens its target with O_TRUNC
        if cut_this_put:
            raise Cut()
        shutil.copyfile(arguments[0], target)
    elif command == "rename":
        source, target = remote(arguments[0]), remote(arguments[1])
        if not source.is_file():
            raise Failed(f'Couldn\'t rename file "{arguments[0]}": not found')
        os.replace(source, target)
    elif command == "rm":
        matches = matching(remote(arguments[0]))
        if not matches:
            raise Failed(f'Couldn\'t delete file: "{arguments[0]}" not found')
        for match in matches:
            match.unlink()
    elif command == "ls":
        folder = remote(arguments[0])
        if not folder.is_dir():
            raise Failed(f'Can\'t ls: "{arguments[0]}" not found')
        for entry in sorted(folder.iterdir()):
            if not entry.name.startswith("."):
                print(f"{arguments[0].rstrip('/')}/{entry.name}")
    else:
        raise Failed(f"Invalid command: {command}")


def main(argv: list[str]) -> int:
    batch = Path(argv[argv.index("-b") + 1])
    cut_rm = int(os.environ.get("FAKE_SFTP_CUT_AT_RM", "0"))
    cut_put = int(os.environ.get("FAKE_SFTP_CUT_AT_PUT", "0"))
    deletions = puts = 0
    for raw in batch.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        if raw.lstrip("-").startswith("rm ") and "/.subiendo-" not in raw:
            deletions += 1
            if deletions == cut_rm:
                print("Connection closed", file=sys.stderr)
                return 255
        if raw.startswith("put "):
            puts += 1
        print(f"sftp> {raw}")
        tolerant = raw.startswith("-")
        try:
            run(raw[1:] if tolerant else raw, raw.startswith("put ") and puts == cut_put)
        except Cut:
            print("Connection closed", file=sys.stderr)
            return 255
        except (Failed, OSError, IndexError) as error:
            print(str(error), file=sys.stderr)
            if not tolerant:
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
