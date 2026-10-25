# Where these images come from

The device: a bone-conduction headset sold as **AGPTEK S22**. It advertises Bluetooth as `B18`,
enumerates as USB `10d6:1101`, and exposes an 8 GB FAT32 disk named `AGP-S22`. The SoC is an
Actions **ATS2819 / US281B**; the flash is a GD25Q16 (2 MB, JEDEC id `c84015`).

No firmware for this product is published anywhere by its vendor. These files were read off one
physical unit over ADFU with the tools in `tools/`, in October 2026.

## The files

| File | What it is |
|---|---|
| `nor_logical_1MB.bin` | The **logical** (de-randomized, CRC-stripped) NOR image: MBREC, BREC, the LFI head and directory, and all 47 firmware files. Every checksum in it verifies. This is what the toolchain writes back, and it is the recovery image for a device that no longer boots. |
| `tts_rc_device.lib` | The stock `TTS_RC.LIB` extracted from that image: 31 Chinese prompts, zero English ones. |
| `config_device.bin` | The stock `config.bin`, useful for comparing a different unit against this one. |
| `../raw/raw_first_1MB.bin` | A **physical** read of a working device — the same data as stored on the chip, in 66-byte lines with their CRCs, scrambled with the chip's own eFuse seed. It is the reference for anything about the physical format. |
| `../prompts-ru-en/tts_rc.lib` | A replacement prompt library with Russian in the language-0 slot and English in language-1. The speech was synthesized locally with the macOS `say` command. |
| `../prompts-ru-en/afi_plain.bin` | The flashable image built from `nor_logical_1MB.bin` with that prompt library in place of the stock one. This is what was actually written to the device. |
| `../prompts-ru-en/clips/` | The individual prompt clips as MP3, so you can hear what each id says without flashing anything. |

## Why the raw reference stops at 1 MB

The chip is 2 MB. Past roughly physical `0x1F0000` it holds the pairing database, which on a used
device contains the names of everything it was ever paired with — real people's phones and laptops.
That region is not firmware, is not needed to rebuild or restore anything, and is not published.
The raw reference is therefore truncated to the first megabyte, which covers the whole boot and
firmware area. The logical image contains no pairing data at all.

## Copyright

The firmware code in these images belongs to **Actions Semiconductor** and **AGPTEK**. No licence is
granted for it here, and the MIT licence of this repository does not cover it. It is published for
one practical reason: without it, a device bricked by the vendor's own loaders cannot be restored,
and nothing equivalent exists in public. If you hold rights to this code and want it taken down,
open an issue and it will be removed.
