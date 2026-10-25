"""Dump an Actions US282A config.bin ("cfgUTF8"): items by ID, with names from an SDK config_id.h."""
import re
import struct
import sys


def items(blob):
    first = struct.unpack_from("<I", blob, 0x10)[0]
    out = {}
    for i in range((first - 0x10) // 8):
        off, ln = struct.unpack_from("<II", blob, 0x10 + 8 * i)
        if ln and off + ln <= len(blob):
            out[i] = blob[off:off + ln]
    return out


def show(raw):
    if len(raw) >= 2 and all(32 <= c < 127 or c == 0 for c in raw) and raw.rstrip(b"\0") and raw[0] >= 32:
        return "str " + repr(raw.rstrip(b"\0").decode())
    vals = struct.unpack_from(f"<{len(raw) // 2}H", raw)
    return "num " + " ".join(str(v) for v in vals)


def names(path):
    m = {}
    for line in open(path, encoding="latin-1"):
        r = re.match(r"\s*#define\s+(SETTING_\w+)\s+(\d+)", line)
        if r:
            m[int(r.group(2))] = r.group(1)
    return m


def main(argv):
    """Usage: cfgdump.py DEVICE_CONFIG.bin [SDK_CONFIG.bin|-] [config_id.h]"""
    if not argv:
        sys.exit(main.__doc__)
    dev = items(open(argv[0], "rb").read())
    sdk = items(open(argv[1], "rb").read()) if len(argv) > 1 and argv[1] != "-" else {}
    nm = names(argv[2]) if len(argv) > 2 else {}
    for i in sorted(set(dev) | set(sdk)):
        d = show(dev[i]) if i in dev else "-"
        s = show(sdk[i]) if i in sdk else "-"
        mark = "" if d == s else "  *"
        print(f"{i:3d} {nm.get(i, '?'):45s} dev: {d:40s} sdk: {s}{mark}")


if __name__ == "__main__":
    main(sys.argv[1:])
