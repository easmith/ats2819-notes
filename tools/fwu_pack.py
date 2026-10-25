"""Pack a plaintext payload (an AFI image) into an Actions FWU v3 container.

FWU v3 (rockbox utils/atj2137/atjboottool/fwu.c) keeps its own ECC private key inside the
file, so the 32-byte session key is fixed by sector 0 + block A + block B alone. We reuse
those three pieces from a valid reference FWU and only re-encrypt the payload:

  sector0 (ref, fw_size patched) | P[:offA] | blockA (1024, ref) | P[offA:offA+offB] | blockB (512, ref) | P[offA+offB:]
  offA = 512 * (1 + (sector0[0x1ee] & 15)), offB = 512 * (1 + (sector0[0x1fe] & 15))

Payload cipher: the "ATJ2127" sector cipher (rockbox atj2127_decrypt_sector), inverted here.
The key comes from a patched atjboottool (KEYBUF_OUT), see notes/FWU_FORMAT.md.

Options:
  --tail     append a 512-byte FwuTail (what fwsp.py BuildAFI writes after the encrypted AFI)
  --prefix   prepend a file such as upgrade.bin (2819 UPGRADE.FWU = upgrade.bin + FWU)

Usage: python3 tools/fwu_pack.py REF.FWU KEYBUF.bin PAYLOAD OUT [--tail] [--prefix upgrade.bin]
"""
import argparse
import struct

ATJ2127_KEY = [0x42146ea2, 0x892c8e85, 0x9f9f6d27, 0x545fedc3,
               0x09e5c0ca, 0x2dfa7e61, 0x4e5322e6, 0xb19185b9]
HDR_FW_SIZE = 16          # u32 after the 16-byte signature
SECTOR = 512


def encrypt_sector(plain, session, rounds):
    key = [k ^ s for k, s in zip(ATJ2127_KEY, session)]
    words = list(struct.unpack(f"<{len(plain) // 4}I", plain))
    for r in range(rounds):
        p = words[8 * r:8 * r + 8]
        c = [p[j] ^ key[j + 1] for j in range(7)]
        c.append(p[7] ^ key[1] ^ key[4])
        words[8 * r:8 * r + 8] = c
        key[1:7] = key[2:8]
        key[7] = c[7] ^ session[7]
    return struct.pack(f"<{len(words)}I", *words)


def checksum32(buf):
    return sum(struct.unpack(f"<{len(buf) // 4}I", buf)) & 0xFFFFFFFF


def fwu_tail(body):
    tail = bytearray(SECTOR)
    tail[0], tail[1] = 1, 7
    struct.pack_into("<II", tail, 16, checksum32(body), 0x55AA55AA)
    tail[24:32] = b"FwuTail\0"
    struct.pack_into("<I", tail, 508, checksum32(bytes(tail[:508])))
    return bytes(tail)


def pack(ref, keybuf, payload, tail=False):
    sector0 = bytearray(ref[:SECTOR])
    off_a = SECTOR * (1 + (sector0[0x1EE] & 15))
    off_b = SECTOR * (1 + (sector0[0x1FE] & 15))
    block_a = ref[SECTOR + off_a:SECTOR + off_a + 1024]
    block_b = ref[SECTOR + off_a + 1024 + off_b:SECTOR + off_a + 1024 + off_b + SECTOR]

    payload = payload + b"\0" * (-len(payload) % SECTOR)
    if len(payload) < off_a + off_b:
        raise SystemExit("payload must be at least 0x%x bytes" % (off_a + off_b))
    fw_size = len(payload) + SECTOR + 1024 + SECTOR
    total = fw_size + (SECTOR if tail else 0)
    # rockbox derives rounds from the file length; both stay well under 32 MB here
    rounds = max(1, 16 - (total >> 25))
    session = struct.unpack("<8I", keybuf)
    enc = b"".join(encrypt_sector(payload[i:i + SECTOR], session, rounds)
                   for i in range(0, len(payload), SECTOR))

    struct.pack_into("<I", sector0, HDR_FW_SIZE, total if tail else fw_size)
    out = (bytes(sector0) + enc[:off_a] + block_a + enc[off_a:off_a + off_b]
           + block_b + enc[off_a + off_b:])
    assert len(out) == fw_size
    return out + fwu_tail(out) if tail else out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("keybuf")
    ap.add_argument("payload")
    ap.add_argument("out")
    ap.add_argument("--tail", action="store_true")
    ap.add_argument("--prefix")
    a = ap.parse_args()

    keybuf = open(a.keybuf, "rb").read()
    if len(keybuf) != 32:
        raise SystemExit("keybuf must be 32 bytes")
    data = pack(open(a.ref, "rb").read(), keybuf, open(a.payload, "rb").read(), a.tail)
    if a.prefix:
        data = open(a.prefix, "rb").read() + data
    open(a.out, "wb").write(data)
    print("wrote %d bytes to %s" % (len(data), a.out))


if __name__ == "__main__":
    main()
