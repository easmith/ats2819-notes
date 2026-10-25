#!/usr/bin/env python3
"""Find personal strings in files before they reach a public commit.

The firmware dumps in this repository came off a real device, and a real device stores the
names of whatever it was paired with. Those names are kept out of the published files; this
script is what proves it, and it runs before every commit:

    python scripts/privacy_scan.py --staged     # what git is about to record
    python scripts/privacy_scan.py firmware     # any path, recursively

Exit code 1 means something was found. Each finding prints as path:offset:encoding:needle.
Needles are searched in every encoding the device or a desktop may have written them in, so a
name stored as UTF-16 inside a flash dump is found as readily as an ASCII one.
"""
import subprocess
import sys
from pathlib import Path
from typing import Iterable, NamedTuple, Sequence

DEFAULT_NEEDLES: tuple[str, ...] = (
    "Pixel 8", "Pixel 2", "MacBook", "Eugene", "Евгений", "easmithrus", "/Users/eugene",
)
ENCODINGS: tuple[str, ...] = ("utf-8", "utf-16-le", "cp1251")

# Files that legitimately contain these strings: the MIT copyright line is not a leak, and
# the needle list below — plus the tests that exercise it — have to spell the needles out.
ALLOWLIST: frozenset[str] = frozenset({"LICENSE", "privacy_scan.py", "test_privacy_scan.py"})

SKIP_DIRS: frozenset[str] = frozenset({".git", ".superpowers", "__pycache__", ".venv", "vendor"})


class Hit(NamedTuple):
    path: Path
    offset: int
    encoding: str
    needle: str


def _patterns(needle: str) -> list[tuple[bytes, str]]:
    """The needle's byte forms, one per encoding, without duplicates.

    "Eugene" encodes identically in utf-8 and cp1251; reporting it twice would make a single
    leak look like two, so the first encoding that produces a given byte string wins.
    """
    seen: set[bytes] = set()
    out: list[tuple[bytes, str]] = []
    for encoding in ENCODINGS:
        try:
            pattern = needle.encode(encoding)
        except UnicodeEncodeError:
            continue
        if pattern and pattern not in seen:
            seen.add(pattern)
            out.append((pattern, encoding))
    return out


def _files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        path = Path(path)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and not SKIP_DIRS.intersection(child.parts):
                    yield child
        elif path.is_file():
            yield path


def scan(paths: Iterable[Path],
         needles: Sequence[str] = DEFAULT_NEEDLES,
         allowlist: frozenset[str] = ALLOWLIST) -> list[Hit]:
    """Every occurrence of every needle in the given files and directories."""
    compiled = [(pattern, encoding, needle)
                for needle in needles for pattern, encoding in _patterns(needle)]
    hits: list[Hit] = []
    for file in _files(paths):
        if file.name in allowlist:
            continue
        try:
            blob = file.read_bytes()
        except OSError:
            continue
        for pattern, encoding, needle in compiled:
            start = blob.find(pattern)
            while start != -1:
                hits.append(Hit(file, start, encoding, needle))
                start = blob.find(pattern, start + 1)
    return sorted(hits, key=lambda h: (str(h.path), h.offset, h.needle))


def staged_paths() -> list[Path]:
    """Files git would record in the next commit."""
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                         capture_output=True, text=True, check=True).stdout
    return [Path(line) for line in out.splitlines() if line and Path(line).is_file()]


def main(argv: Sequence[str]) -> int:
    args = list(argv)
    staged = "--staged" in args
    if staged:
        args.remove("--staged")
    paths = [Path(a) for a in args]
    if staged:
        paths += staged_paths()
    if not paths:
        print("nothing to scan: pass paths or --staged", file=sys.stderr)
        return 0
    hits = scan(paths)
    for hit in hits:
        print(f"{hit.path}:{hit.offset}:{hit.encoding}:{hit.needle}")
    if hits:
        print(f"{len(hits)} personal string(s) found — do not commit", file=sys.stderr)
        return 1
    print(f"privacy scan clean ({len(list(_files(paths)))} file(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
