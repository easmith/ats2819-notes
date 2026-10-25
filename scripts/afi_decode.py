#!/usr/bin/env python3
"""Decode the ADFU loaders out of the SDK's AFI package, and check what bootstrap fetched.

The SDK's psp_rel/bin/AFI.bin is an Actions .fw package holding a FAT32 volume, and every file
inside it is stored XOR 0x38. The loaders this repository's tools upload to the device
(NorHwsc.bin, NorFwsc.bin, ADFUS.BIN, nor_id.bin) come out of there — they are not redistributed
here, which is why scripts/bootstrap.sh runs this.

Usage:
  python3 scripts/afi_decode.py vendor/2819p/psp_rel/bin/AFI.bin vendor/afi
  python3 scripts/afi_decode.py --verify vendor/CHECKSUMS vendor
"""
import hashlib
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from fwfat import extract_fat  # noqa: E402

XOR = 0x38

# Every name inside the SDK's package carries a "~0" (or "~1", …) suffix — that is how the vendor's
# own build wrote the volume, not an artefact of reading it. The tools here want the plain names.
TRAILING_REVISION = re.compile(r"~\d+$")


def plain_name(name: str) -> str:
    """'NorFwsc.bin~0' -> 'NorFwsc.bin'."""
    return TRAILING_REVISION.sub("", name)


def decode(data: bytes) -> bytes:
    """Undo the XOR 0x38 every file inside an AFI package is stored with."""
    return bytes(b ^ XOR for b in data)


def extract(afi_path: Path, out_dir: Path) -> list:
    """Decode every file in the package into out_dir; returns the paths written."""
    afi_path, out_dir = Path(afi_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    with tempfile.TemporaryDirectory() as raw_dir:
        for raw in extract_fat(afi_path.read_bytes(), Path(raw_dir)):
            relative = raw.relative_to(raw_dir)
            target = out_dir / relative.with_name(plain_name(relative.name))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(decode(raw.read_bytes()))
            written.append(target)
    return written


def verify_checksums(manifest: Path, root: Path) -> list:
    """Names from the manifest whose file is missing or has the wrong sha256."""
    problems = []
    for line in Path(manifest).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        digest, name = line.split(maxsplit=1)
        path = Path(root) / name.strip()
        if not path.is_file():
            problems.append(f"{name.strip()}: missing")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            problems.append(f"{name.strip()}: sha256 differs from the expected {digest[:16]}…")
    return problems


def main(argv):
    if argv[:1] == ["--verify"]:
        problems = verify_checksums(Path(argv[1]), Path(argv[2]))
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            print(f"{len(problems)} bootstrapped file(s) are not what these tools expect",
                  file=sys.stderr)
            return 1
        print("bootstrapped loaders verified")
        return 0
    if len(argv) != 2:
        sys.exit(__doc__)
    written = extract(Path(argv[0]), Path(argv[1]))
    print(f"decoded {len(written)} file(s) into {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
