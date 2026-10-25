#!/usr/bin/env python3
"""ATS2819 SPI-NOR "CRC line" physical format: codec + verifier.

THIS IS NOT A BOOT ORACLE. It models the encoding with randomizer seed 0, so it happily
verified an image the device could not boot — the seed was the problem, and this decoder
cannot see the seed. Use it to reason about the line format, never as proof that a flash
will boot; the only acceptance test is reading the chip back through the cache with the
hardware seed (tools/adfu_flash.py --xip2). The full story is in docs/en/09-journey.md.

The SPI0 controller stores each 64-byte LOGICAL line as a 66-byte PHYSICAL line:

    phys_line = scramble( plain[0:64] || crc16(plain) )

  * crc16 = poly 0x1021, init 0xFFFF, data bits fed LSB-first, result byte-swapped,
    stored little-endian (this is NorFwsc's 0xbfc2f6a1 / the stock BREC driver's CRC).
  * scramble is a GF(2)-linear, *content-only* keystream (no address, no key, no chip id):
        g[m]  = parity(out_byte_m_plain & 0x3C)      (one feedback bit per byte)
        ks[k] bit j = XOR of g[k-t] for t in LAGS[j], t <= k
        phys[k] = src[k] ^ ks[k]
    The 2 CRC bytes are scrambled as well and also feed g.
  * Logical address a is at physical (a//64)*66 + a%64, for a below the CRC/raw
    boundary in SPI0+0x20; above it the NOR is plain 1:1.

Derived by GF(2) solve over 3929 distinct lines of dumps/raw_ref_sdk_layout_0x40008.bin
(rank 513, zero inconsistent equations) and validated on the 14476 independent lines of
dumps/raw_0x000000_e9418_m0_164007.bin.

  python3 tools/norline.py decode RAW.bin OUT.bin      # physical -> logical, checks CRC
  python3 tools/norline.py verify RAW.bin LOGICAL.bin  # re-encode LOGICAL, compare to RAW
"""
import sys

LAGS = {
 0: [2,3,4,6,10,13,15,16,17,19,20,21,22,23,25,26,30,33,35,36,39,41,44,48,52,53,56,60,61,63,64,65],
 1: [1,2,6,8,9,10,11,12,14,19,20,24,25,30,36,37,38,44,46,47,48,49,50,51,55,59,61,62,63,64,65],
 2: [2,4,5,6,8,13,14,15,18,20,22,23,25,27,29,30,35,36,37,38,39,40,41,42,44,46,47,49,52,55,56,57,
     58,59,60,61,62,63,65],
 3: [3,5,6,7,9,14,15,16,19,21,23,24,26,28,30,31,36,37,38,39,40,41,42,43,45,47,48,50,53,56,57,58,
     59,60,61,62,63,64],
 4: [4,6,7,8,10,15,16,17,20,22,24,25,27,29,30,37,38,39,40,41,48,49,50,51,53,54,55,58,63,65],
 5: [5,7,8,9,11,17,19,20,22,24,27,28,29,30,31,33,36,37,39,40,44,45,46,49,52,54,57,60,61,63,64,65],
 6: [5,7,8,9,10,11,13,17,19,20,24,25,28,34,35,40,41,42,47,50,56,57,62,63,65],
 7: [2,3,4,6,7,10,11,12,15,16,19,22,23,26,30,31,33,35,36,37,38,39,42,44,47,49,53,57,58,59,60],
}
LINE, PLINE = 64, 66
ERASED = b"\xff" * PLINE


def crc16(buf):
    crc = 0xFFFF
    for b in buf:
        for i in range(8):
            crc = ((crc << 1) & 0xFFFF) ^ 0x1021 if crc & 0x8000 else (crc << 1) & 0xFFFF
            if (b >> i) & 1:
                crc ^= 0x1021
    return ((crc >> 8) | ((crc << 8) & 0xFF00)) & 0xFFFF


def _keystream_byte(g, k):
    ks = 0
    for j in range(8):
        v = 0
        for t in LAGS[j]:
            if t <= k:
                v ^= g[k - t]
        ks |= v << j
    return ks


def encode(plain):
    """64 logical bytes -> 66 physical bytes."""
    assert len(plain) == LINE
    c = crc16(plain)
    src = bytes(plain) + bytes((c & 0xFF, c >> 8))
    g, out = [], bytearray()
    for k in range(PLINE):
        out.append(src[k] ^ _keystream_byte(g, k))
        g.append(bin(src[k] & 0x3C).count("1") & 1)
    return bytes(out)


def decode(phys):
    """66 physical bytes -> (64 logical bytes, crc_ok). The keystream is causal, so the
    plain byte is recovered before it is needed as feedback."""
    assert len(phys) == PLINE
    g, src = [], bytearray()
    for k in range(PLINE):
        b = phys[k] ^ _keystream_byte(g, k)
        src.append(b)
        g.append(bin(b & 0x3C).count("1") & 1)
    plain = bytes(src[:LINE])
    return plain, crc16(plain) == (src[64] | (src[65] << 8))


def decode_image(raw):
    """Physical bytes -> (logical bytes, list of lines with a bad CRC, erased line count)."""
    out, bad, erased = bytearray(), [], 0
    for n in range(len(raw) // PLINE):
        line = raw[n * PLINE:(n + 1) * PLINE]
        if line == ERASED:
            out += b"\xff" * LINE
            erased += 1
            continue
        p, ok = decode(line)
        out += p
        if not ok:
            bad.append(n)
    return bytes(out), bad, erased


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    cmd, a, b = sys.argv[1:4]
    if cmd == "decode":
        logical, bad, erased = decode_image(open(a, "rb").read())
        open(b, "wb").write(logical)
        print(f"{len(logical)//LINE} lines, {erased} erased, {len(bad)} bad CRC -> {b}")
        if bad:
            print("  bad lines:", bad[:20])
        return 1 if bad else 0
    if cmd == "verify":
        raw, logical = open(a, "rb").read(), open(b, "rb").read()
        ok = mism = skip = 0
        for n in range(min(len(raw) // PLINE, len(logical) // LINE)):
            want = logical[n * LINE:(n + 1) * LINE]
            got = raw[n * PLINE:(n + 1) * PLINE]
            if got == ERASED:
                skip += 1
            elif encode(want) == got:
                ok += 1
            else:
                mism += 1
                if mism <= 5:
                    print(f"  line {n} (phys 0x{n*PLINE:x}, logical 0x{n*LINE:x}) differs")
        print(f"match {ok}, MISMATCH {mism}, erased-in-raw {skip}")
        return 1 if mism else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
