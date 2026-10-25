#!/usr/bin/env python3
"""Read the FAT32 image embedded in an Actions .fw package (100TMFWFSTCA / ACTS-FWT001).

The SDK ships its loaders inside such a package (psp_rel/bin/AFI.bin): a plain FAT32 volume with
the files at its root. Layout observed on ATS282x and ATS2819 alike: image base 0x10,
FAT1 at base+0x4000, data (cluster 2 = root directory) at base+0x203000.

Usage: python3 tools/fwfat.py PACKAGE.fw OUT_DIR [SECTORS_PER_CLUSTER]
"""
import os
import struct
import sys
from pathlib import Path

BASE = 0x10
FAT = BASE + 0x4000
DATA = BASE + 0x203000


class Volume:
    def __init__(self, blob: bytes, sectors_per_cluster: int = 8):
        self.d = blob
        self.cs = sectors_per_cluster * 512

    def chain(self, c):
        while 2 <= c < 0x0FFFFFF8:
            yield c
            c = struct.unpack_from("<I", self.d, FAT + 4 * c)[0] & 0x0FFFFFFF

    def read(self, c, size):
        buf = b"".join(self.d[DATA + (x - 2) * self.cs: DATA + (x - 1) * self.cs]
                       for x in self.chain(c))
        return buf[:size] if size is not None else buf

    @staticmethod
    def entries(raw):
        lfn = []
        for i in range(0, len(raw), 32):
            e = raw[i:i + 32]
            if e[0] == 0:
                break
            if e[0] == 0xE5:
                lfn = []
                continue
            if e[11] == 0x0F:
                part = e[1:11] + e[14:26] + e[28:32]
                lfn.insert(0, part.decode("utf-16le", "ignore").split("\0")[0].replace("￿", ""))
                continue
            name = "".join(lfn) or (
                e[0:8].decode("latin1").strip()
                + ("." + e[8:11].decode("latin1").strip() if e[8:11].strip() else ""))
            lfn = []
            if e[11] & 0x08:          # volume label
                continue
            clus = struct.unpack_from("<H", e, 20)[0] << 16 | struct.unpack_from("<H", e, 26)[0]
            size = struct.unpack_from("<I", e, 28)[0]
            yield name, e[11], clus, size

    def walk(self, cluster, out: Path, written):
        for name, attr, clus, size in self.entries(self.read(cluster, None)):
            if name in (".", ".."):
                continue
            path = out / name
            if attr & 0x10:
                path.mkdir(parents=True, exist_ok=True)
                self.walk(clus, path, written)
            else:
                path.write_bytes(self.read(clus, size))
                written.append(path)


def extract_fat(blob: bytes, out_dir: Path, sectors_per_cluster: int = 8) -> list:
    """Write every file in the embedded volume under out_dir; returns the paths written."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list = []
    Volume(blob, sectors_per_cluster).walk(2, out_dir, written)
    return written


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    spc = int(argv[2]) if len(argv) > 2 else 8
    for path in extract_fat(Path(argv[0]).read_bytes(), Path(argv[1]), spc):
        print(f"{path.stat().st_size:9d}  {os.path.relpath(path, argv[1])}")


if __name__ == "__main__":
    main(sys.argv[1:])
