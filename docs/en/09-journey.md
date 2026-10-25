# How it went, mistakes included

[Русская версия](../ru/09-journey.md). From "these headphones only speak Chinese" to Russian voice
prompts, over two days, with one brick in the middle. The mistakes are the valuable part, so they
are kept.

## Identifying the thing

The headset is a bone-conduction unit, Bluetooth `B18`, USB disk `AGP-S22`, and nothing on it says
who made the silicon. USB vendor `10d6` pointed at Actions Semiconductor. The platform was first
taken for US282A; the configuration identifiers (`FACTORY_FW_INFO_*`) and the file formats later
pinned it as **ATS2819 / US281B**, and a public SDK for exactly that platform exists
(github.com/Pachouli/2819p). That SDK is the source of almost everything that followed.

`tts_rc.lib` turned out to have slots for a second language, all empty: 31 Chinese prompts, zero
English. No button or setting switches language. The prompts have to be replaced.

## Prompts, and the size wall

Russian and English prompts were synthesized in the factory format (MP3 16 kHz, 16 kbit/s mono,
table format "v2"). A bilingual set at natural phrasing came to ~112 KB and did not fit the free
space; shortened phrasing brought it to 87 KB, which fits with ~9 KB to spare.

## Getting the firmware out, read-only

This was the hard part, and the vendor toolchain is no help: ADFUS → HWSC → FWSC can only write.
Worse, the modes that *look* like reads run write routines — one early script assumed mode `0x00`
was a read, and it was scrapped before it ever ran. The SDK's `NorFwsc` erases the first 64 KB on
its first driver call in any mode.

What worked was reading what the processor sees: the flash is mapped at `0x10000000` and the boot
ROM can read memory. Two obstacles followed. The window does not start at the beginning of flash
(it sat at logical `0x5980`), so the boot records and the directory were missing from the dump. And
the ROM's read is byte-wise, so the mapping registers returned rubbish.

Both were solved by uploading a small MIPS program into RAM (`tools/adfu_stub.py`) that does real
32-bit accesses, repoints a mapping entry at offset 0, and **returns into the ROM's command loop**
so the device stays reachable. Result: a complete logical image whose header, directory and all 47
file checksums verify.

## The first write, and the brick

The plan was conservative: write the **factory** image first, to test the write path on something
that cannot be wrong. Every step reported ok in 8 seconds — and the headset never booted again. It
came up as `10d6:10d6`: the ROM could not start the firmware.

Four mistakes made that possible:

1. We had never seen **how the firmware physically sits in flash**, only how the processor sees it,
   so there was nothing to compare the loader's output against.
2. Unresolved contradictions sat in the notes — "the loader always enables the transform when
   writing" next to "the flash is not encrypted" — and nobody forced them to agree.
3. The loaders came from the SDK while the factory firmware was written by a different build of the
   tools. We verified that the loaders *run*; we never verified that they write in the same format.
4. The recovery path depended on the same unverified write mechanism, and reading from a *cold* ROM
   had not been tested before it was needed.

The right order would have been: learn to read flash directly, compare the format, test a write on
an empty region, and only then touch the start of the chip.

## Recovery, and two false bottoms

Reading from a cold ROM turned out to hang on the XIP window, and running FWSC's init straight from
the ROM rebooted the device via the watchdog. The way through was the vendor ADFUS plus a raw SPI
read function found inside the loader itself (`--read`) — the first time anyone here saw the actual
bytes on the chip.

**Cause #1, addresses.** The SDK loader writes BREC right after MBREC at `0x400`; this device's
MBREC looks for it at `0xFC0`. Confirmed afterwards against the factory image, where `0x400`–`0xFBF`
is empty and BREC starts at `0xFC0`. Fixed by re-encoding three constants.

**A false sense of verification.** A check reporting "14 476 lines, 0 mismatches" was worth nothing:
the line dictionary was built from our own earlier dump, so we were comparing our write against our
own write. Separately, a file named `nor_0x000000_before_flash.bin` turned out not to be a pre-write
snapshot at all, but a copy of the first 64 KB of the logical image. Names lie.

## The geometry, the first solid ground

A raw read of the pairing region at the end of the chip showed **plain text**, and the difference
between physical and logical addresses there was a constant `0xF07C`. Arithmetic then gave the whole
model: lines cover physical `0`…`0x1F0000`, the last 64 KB is stored as-is, logical capacity is
`(0x1F0000 / 66) × 64 = 0x1E0F80`.

Minutes later the registers confirmed it: after FWSC init, `SPI0+0x20` and `0xC0140008` both hold
exactly `0x001E0F80`. Two independent roads to one number — the model was right.

## A real judge at last

The register map came from leaked Actions SDKs, the ATS2819 datasheet and the SDK's own
`Design_Specification.h`; `hal_spi_phy_init` in the SDK's object files gave the exact initialization
sequence. From that came `--xip2`: reading the flash **the way the processor reads it**. That is the
acceptance test the project had lacked from day one.

Two of our own bugs surfaced immediately, both of which had made reads return nothing but `FF`: a
missing `SPI_REQ` handshake (while the CPU holds the FIFO, the cache cannot read flash), and a
failure to clear bit 2 of `0xC0140000`, which FWSC's init sets to bypass decoding.

## Cause #2: the seed

With CRC checking on, the read failed. With software seed 0 it returned:

```
matches factory image: 20480/20480  -- IDENTICAL, no CRC errors
```

So **the data in flash was intact** — scrambled with seed 0 while the ROM reads with the chip's
built-in seed. The loader's init showed where: `CTL |= 0x4000` to take the seed from a register,
then `header[240 + idx*4]` stored into `SPI0+0x14`. The vendor's own `fwsp.py` showed what that
table is: `chipid_data`, with the real CHIPID hidden in it at a pseudo-random index. In the shipped
loader it is all zeros.

This is exactly the "NOR encryption by eFuse" (`INF_SPINOR_ENCRYPT=1`) the investigation had started
from — and had spent a whole day believing was switched off.

## The fix, and the device coming back

One word: `0xBFC2C308`, `ori t9,v1,0x4000` → `ori t9,v1,0`. Dry run status 0; writing the factory
image took 8.1 s; replug, and the headset powered on, enumerated `10d6:1101` and mounted its disk.

Independent confirmation followed from the ROM itself: it reads MBREC with `CTL = 0x0018120A` —
`CRC_EN` set, `RAND_RXEN` set, bit 14 clear — and `SPI0+0x14` is never written anywhere in the whole
ROM. That is precisely the configuration we now write under.

## Two detours and the finish

The device then refused to be caught in service mode twice in a row: a healthy unit leaves
software-entered ADFU after a few seconds, while the bricked one had waited there forever. Then it
stopped powering on at all, red LED only — deep discharge, because ADFU does not charge and it had
spent hours there. A long charge fixed it.

At that point the plan was to flash the prompts with the vendor's own disk upgrade, on the theory
that the firmware's own driver would have the right parameters by construction. Reading
`upgrade.bin` killed that idea: it loads `NORFWSC BIN` **from the package** and runs it, so the disk
path writes with the same broken loader and offers no check before it does. The firmware's own NOR
driver takes no part in an upgrade.

So the prompts went in over ADFU, with a physical reference read taken first — the whole 2 MB of a
working device, the independent yardstick the project had never had. Then:

```
echo WRITE | python tools/adfu_flash.py ours --write --verify 0x5000
  write 8.4 s, 20480/20480 IDENTICAL, VERDICT PASS
```

Replug, power on, and the headset speaks Russian.

## What kept this recoverable

The boot ROM cannot be erased: as long as flash holds nothing bootable, the device comes up in ADFU
and can be written. And there was a full copy of the firmware — now proven not by argument but by
having restored a device from it.

## Lessons

1. **Verification must come from an independent source, not from your own model.** The central
   mistake: comparing a write against our own understanding of the format. The honest judges are a
   hardware read (`--xip2`) and the device booting.
2. **Resolve contradictions in your notes before acting.** "Encryption is off, no key needed" lived
   happily beside "the loader enables the randomizer and writes a seed". That contradiction *was*
   the bug.
3. **Do not trust a number read by an unreliable method.** Byte-wise register reads gave `0x38`
   instead of `0x00361838` and `0x80` instead of `0x001E0F80`; both cost hours.
4. **State conclusions with the confidence they have earned.** "Root cause found" was announced
   three times too early — once from a read in the wrong mode, once from a CRC flag that also fires
   on erased regions.
5. **An agent's report is data, not truth.** One subagent argued convincingly that the line format
   was irrelevant and that its own decoder proved the chip content correct. The decoder modelled the
   seed-0 encoding, which is why it "verified" an unbootable image. Another searched for the literal
   `0xC0110014`, found nothing, and concluded the seed register was never written — the address is
   computed (`ori s2,t8,0x14`). Both reports were confident and wrong.
6. **"Prefer the vendor's own mechanism" is not always sound.** It was the working assumption for a
   while, and here it was false: the vendor's disk upgrade runs the same loader with the same zero
   seed table, and unlike ADFU it cannot be checked before it writes.
7. **Watch the power.** Flashing on a flat battery is its own way to lose a device, and service mode
   does not charge it.
