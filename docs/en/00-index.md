# Documentation

Reverse-engineering notes for the Actions ATS2819 / US281B platform, worked out on an AGPTEK S22
bone-conduction headset. [Русская версия](../ru/00-index.md).

| Document | What it covers |
|---|---|
| [01-device.md](01-device.md) | The device: identifiers, controls, ADFU service mode, and how to tell whether your unit is the same |
| [02-flash-format.md](02-flash-format.md) | The chip: how data physically sits in NOR, the SPI0 registers, the randomizer and its seed, execute-in-place |
| [03-firmware-layout.md](03-firmware-layout.md) | The firmware: boot records, the LFI image, the 47 files, `tts_rc.lib`, `config.bin` |

Start at [SAFETY.md](../../SAFETY.md) if you intend to write to a device.

The raw working notes, with addresses and disassembly, are in `notes/` — this
documentation is the readable account of what is in them.
