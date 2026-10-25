# Recovery: what a brick looks like, and the way back

Written after the headset was bricked by a write and brought back.
[Русская версия](../ru/07-recovery.md).

## You cannot kill it outright

The boot ROM — about 32 KB at `0xbfc00000` — is in silicon. If flash holds nothing the ROM accepts,
it comes up on USB as **`10d6:10d6`**, which is ADFU, and anything can be written through it. No
write to flash damages that state.

| What you see on USB | What it means |
|---|---|
| `10d6:10d6`, red LED | the ROM could not start the firmware — nothing bootable in flash |
| `10d6:1101` plus the `AGP-S22` disk | the firmware is running |
| nothing at all, red LED | off and charging, **or** deeply discharged |

The last row matters: a healthy headset plugged in while switched off presents **no USB device**.
That is not a fault.

## The two root causes found here

**1. The randomizer seed.** The SDK's loader writes with software seed 0 while the boot ROM reads
with the chip's eFuse seed. Every checksum verifies, the content is byte-perfect, and the ROM still
sees noise. Fixed by one word at `0xBFC2C308` (`0x34794000` → `0x34790000`), which clears
`RAND_SEL` so the write engine uses the same seed source as the ROM. This is `use_hw_seed()` in
`tools/adfu_flash.py`, on by default. Full account in
[02-flash-format.md](02-flash-format.md).

**2. The BREC offset.** The SDK loader writes BREC at logical `0x400` and the LFI after it; this
device's MBREC looks for BREC at `0xFC0`. Fixed by `fix_brec_offset()`, re-encoding three EXTEND'ed
immediates at `0xBFC2F534/556/5D6`. Confirmed against the factory image, where `0x400`–`0xFBF` is
empty and BREC starts at `0xFC0`.

Both fixes are checked before anything is uploaded: `tools/loader_check.py` compares the loader
against the revision these addresses belong to and **warns loudly** if it differs, without
refusing — a repository cannot know every SDK build, and the decision is yours.

## The procedure

With the device in `10d6:10d6`:

```bash
# 1. dry run: ADFUS + HWSC + FWSC init, no write
sudo .venv/bin/python tools/adfu_flash.py stock
#    expect: flash id c84015, FWSC init status 0

# 2. write the factory image back
sudo .venv/bin/python tools/adfu_flash.py stock --write --verify 0x5000
#    expect: BREC ok, LFI ok, MBREC ok, ~8 s, 20480/20480 IDENTICAL

# 3. replug the cable; the device should boot
```

That is the run that recovered this device on 25 October 2026: 8.1 s of writing, replug, and it
powered on, enumerated `10d6:1101` and mounted its disk.

## Rakes already stepped on

* **Byte-wise register reads lie.** The ROM's `0x93` read copies memory a byte at a time, so a
  32-bit register returns only its low byte. Read registers with a stub, via `lw`
  (`--regs`).
* **Two mapping entries can claim one virtual address.** The ROM maps its entry 0 to virtual
  `0x02000000`; the tools point entry 9 there too. Reads taken this way were right, but only the
  overlap cross-check proved it — never trust such a read on its own.
* **The `SPI_REQ` handshake is mandatory.** While the CPU holds the FIFO (`CTL` bit 8), the cache
  cannot read flash and the window returns `FF`. Hand the FIFO back and wait for `STATUS` bit 8 to
  clear.
* **Bit 2 of `0xC0140000`.** FWSC's init sets it and the cache then bypasses decoding. Clear it to
  read decoded data.
* **A CRC error on an erased region is normal.** Empty lines have `FF FF` check bytes, which match
  no CRC. The flag alone proves nothing; compare bytes.
* **After the loader's `OVER` command the device needs a replug** before it answers a ROM inquiry
  again. `start_adfus()` reuses a resident ADFUS when the inquiry is refused rather than uploading
  over running code.
* **Software-entered ADFU ends by itself** on a healthy device, in seconds. Catch it with a waiter
  that polls for `10d6:10d6`.
* **ADFU does not charge.** Hours in service mode flattened the battery until the protection
  tripped; nothing but a long charge helped. Charge fully before flashing.
* **The first FWSC driver call erases the first 64 KB** in any mode. Until you mean to write, call
  init (`+0x200`) only, never the driver (`+0x400`).

## What to trust in this repository

| File | How much to trust it |
|---|---|
| `firmware/stock/nor_logical_1MB.bin` | the logical image, every checksum verified, **and a device was recovered from it** — proven by use |
| `firmware/raw/raw_first_1MB.bin` | a raw physical read of a *working* device, taken after recovery. Scrambled with the chip's own eFuse seed, so it starts `17 61 ee cc`, not the seed-0 `86 53 df 0e`. Alignment validated: the empty logical range `0x400`–`0xFC0` lands exactly on an empty physical range at the 66-byte stride |
| `firmware/prompts-ru-en/afi_plain.bin` | the image that was flashed and verified on the device |
| `tools/verify_raw.py`, `tools/norline.py` | useful for format work, **not** evidence that a flash will boot |

The raw reference is the independent yardstick this project lacked for most of its life: with it, a
future write can be compared byte for byte without relying on our own model of the format.
