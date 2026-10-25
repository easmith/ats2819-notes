# ATS2819 notes: reflashing an Actions bone-conduction headset

Reverse-engineering notes, working tools and firmware images for the Actions **ATS2819 / US281B**
platform, worked out on a bone-conduction headset sold as **AGPTEK S22** (Bluetooth name `B18`,
USB disk `AGP-S22`). It started as "these headphones only speak Chinese" and ended with Russian
voice prompts on the device — by way of bricking it once and getting it back.

[Русская версия](README.ru.md) · [Documentation index](docs/en/00-index.md) · **[Read SAFETY.md before writing anything to a device](SAFETY.md)**

## The finding worth your time

If you are here because an Actions chip stopped booting after you flashed it with the vendor's own
loaders: **the data is probably fine and the seed is wrong.**

The SPI controller scrambles everything it writes to NOR with a hardware randomizer. Its seed comes
from one of two places, selected by bit 14 (`RAND_SEL`) of `SPI0+0x00`:

* bit set — the seed is read from the software register `SPI0+0x14`;
* bit clear — the chip's own eFuse seed is used. **This is what the boot ROM reads with**, always.

The SDK's `NorFwsc` loader sets that bit and latches a seed out of a table in its own header, and
in the shipped loader that table is all zeros. So it writes an image scrambled with seed 0, every
checksum verifies, the content is byte-perfect — and the boot ROM, decoding with the eFuse seed,
sees noise, finds no boot record and drops into ADFU. One word fixes it:

```
0xBFC2C308 (file offset 0x308 in NorFwsc.bin):  0x34794000  ->  0x34790000
            ori t9,v1,0x4000                    ori t9,v1,0
```

That is `use_hw_seed()` in `tools/adfu_flash.py`, applied by default. The whole story, including the
offline decoder that "verified" the unbootable image and why it was wrong, is in
[02-flash-format.md](docs/en/02-flash-format.md) and `09-journey.md`.

## What is here

| Path | What it is |
|---|---|
| `docs/en/`, `docs/ru/` | the documentation, in English and Russian — same files in both |
| `tools/` | the working scripts: ADFU access, a MIPS stub, flashing with read-back verification, image building, prompt building |
| `firmware/stock/` | the factory firmware image of one S22, decoded and checksum-verified — the recovery image |
| `firmware/prompts-ru-en/` | Russian + English voice prompts, and an image ready to flash |
| `firmware/raw/` | a raw physical read of a working device: the reference for the on-chip format |
| `notes/` | the working reverse-engineering notes, with addresses and disassembly |
| `scripts/bootstrap.sh` | fetches the SDK and decodes the loaders — none of that is redistributed here |

## Getting started

```bash
scripts/bootstrap.sh                                    # SDK, loaders, .venv
.venv/bin/python -m pytest tests/ -q                    # host-side checks, no device needed
sudo .venv/bin/python tools/adfu_flash.py stock         # dry run: talks to the device, writes nothing
```

Flashing, and the acceptance read that tells you whether the chip can be read back the way the boot
ROM reads it, are in [06-flashing.md](docs/en/06-flashing.md). Making your own prompts is in
`08-custom-prompts.md`.

## Scope, and how much to trust this

* Verified on **one** unit of one product. Every address, offset and patch site here belongs to that
  SDK revision; `scripts/bootstrap.sh` checks the loaders' checksums and the tools warn loudly if
  they do not match, but a different chip in the same family will need its own work.
* Developed on **macOS** (Apple silicon). USB access needs `sudo`. Linux should work and was not
  tried. External tools used: `ffmpeg`, `lame`, and `binutils` for `gobjdump` (MIPS16 disassembly —
  Capstone cannot do MIPS16).
* The voice prompts were synthesized locally with the macOS `say` command, so the published audio is
  as good as that voice and no better. Rebuilding it is one command and byte-reproducible.

## What is not solved

* **English prompts are built but unreachable.** They sit in the language-1 slot of `tts_rc.lib`,
  which is the vendor's own second-language mechanism, but the stock firmware shipped zero English
  entries and nothing was found that selects the second language. Switching may need firmware code.
* **Pairings are lost on reflash.** They live in the last 64 KB of the chip, outside the firmware
  area, and are not preserved by anything here.
* **No S22 firmware exists in public** other than the image in this repository. If you brick a unit
  and this image does not suit it, there is nothing else to fall back on.
* The disk-upgrade path (`UPGRADE.HEX` on the device's own disk) is built and host-verified but
  **never flashed**, and it is not the safe option — see [04-fwu-format.md](docs/en/04-fwu-format.md).

## Licence

Code and scripts: MIT, see [LICENSE](LICENSE). Documentation: CC BY 4.0. The images under
`firmware/` are **not** covered by either — they are vendor firmware, published so that a bricked
device can be restored; see [firmware/stock/PROVENANCE.md](firmware/stock/PROVENANCE.md).

`patches/actions-adfu-endpoints.patch` applies to [actions-adfu](https://github.com/sonigeez/actions-adfu) (MIT).
