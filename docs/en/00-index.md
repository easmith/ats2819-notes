# Documentation

Reverse-engineering notes for the Actions ATS2819 / US281B platform, worked out on an AGPTEK S22
bone-conduction headset. [Русская версия](../ru/00-index.md).

| Document | What it covers |
|---|---|
| [01-device.md](01-device.md) | The device: identifiers, controls, ADFU service mode, and how to tell whether your unit is the same |
| [02-flash-format.md](02-flash-format.md) | The chip: how data physically sits in NOR, the SPI0 registers, the randomizer and its seed, execute-in-place |
| [03-firmware-layout.md](03-firmware-layout.md) | The firmware: boot records, the LFI image, the 47 files, `tts_rc.lib`, `config.bin` |
| [04-fwu-format.md](04-fwu-format.md) | The update file: `UPGRADE.HEX`, the FWU v3 container, the AFI package, and why the disk path is not the safe one |
| [05-adfu.md](05-adfu.md) | ADFU: the protocol, the vendor loaders, which commands destroy boot records, and how to read flash without writing it |
| [06-flashing.md](06-flashing.md) | Flashing: dry run, write, and the acceptance read that tells you whether the boot ROM can use the result |
| [07-recovery.md](07-recovery.md) | Recovery: what a brick looks like, the two causes found here, the way back, and every rake already stepped on |

Start at [SAFETY.md](../../SAFETY.md) if you intend to write to a device.

The raw working notes, with addresses and disassembly, are in [`notes/`](../../notes) — this
documentation is the readable account of what is in them.
