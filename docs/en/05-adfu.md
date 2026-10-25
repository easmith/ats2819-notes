# ADFU: the protocol, the loaders, and reading flash without writing it

[Русская версия](../ru/05-adfu.md). Full working notes:
[../../notes/RE_NOTES.md](../../notes/RE_NOTES.md).

## The boot ROM's own commands

ADFU is a USB mass-storage-shaped protocol: commands ride in a CBW, replies in a CSW, payload over
the bulk endpoints. The endpoints must be **autodetected** — the ones this device reports are not
the 0x02/0x81 that a hardcoded tool assumes, which is what
`patches/actions-adfu-endpoints.patch` fixes.

Entering ADFU from disk mode: inquiry `0xCC` → reply `ACTIONSUSBD`, then command `0xCB`. The ROM
then appears as `10d6:10d6` and answers an inquiry with `\x00CADFUD\x18QA\x00`.

| CBW | What it does |
|---|---|
| `0xCD 0x93` | read memory |
| `0xCD 0x13` | write RAM |
| `0xCD 0x20` | call code at an address |

**The ROM's memory read is byte-wise.** Ordinary memory comes back correctly; a 32-bit peripheral
register returns only its low byte. This produced two memorable pieces of confusion here: a "live
`CTL = 0x38`" that was really the low byte of `0x00361838`, and "`SPI0+0x20 = 0x80`" that was the
low byte of `0x001E0F80`. Registers have to be read by code of your own, with `lw` —
`tools/adfu_flash.py --regs`.

## A stub of our own

`tools/adfu_stub.py` uploads a small MIPS32 program into device RAM with `0x13`, runs it with
`0x20`, and the program **returns into the ROM's ADFU command loop at `0xbfc040ed`** instead of
ending. The device therefore stays in service mode and the USB session survives. The stub does real
32-bit loads and stores, so it can read registers, and it can claim a flash mapping entry to read
arbitrary flash through the XIP window.

Two things learned the hard way:

* **Mind which mapping entry you claim.** The tools here use **entry 9** (`MAP_ENTRY` in
  `tools/adfu_stub.py`), pointed at virtual `0x02000000`. The ROM maps *its* entry 0 to the same
  virtual base when it boots, so two entries can end up claiming one address — a read that comes
  back plausible but wrong is the failure mode. Every read taken this way here was cross-checked
  against an independent dump (the overlap from logical `0x5980` matched byte for byte), and that
  check is what makes the result trustworthy, not the choice of entry.
* **Restore what the loaders changed.** HWSC and FWSC reconfigure SPI0; the stub restores SPI0 and
  the mapping registers from values read earlier and then returns to the ROM, so the XIP window
  keeps working.

## The vendor loaders, and what they are allowed to do

| Loader | Load address | Entry points |
|---|---|---|
| `ADFUS.BIN` | `0xbfc28000` | its own command dispatcher |
| `NorHwsc.bin` | `0xbfc2c000` | offset 0: identify the flash chip |
| `NorFwsc.bin` | `0xbfc2c000` | `+0x200` init, `+0x400` the write driver |

HWSC and FWSC share one slot and run in separate passes. ADFUS pre-sets its driver pointer
(`0xbfc28bbc`) to `0xbfc2c400`.

### ADFUS dispatch

`cb[0] = 0xCD` selects vendor commands (`0xB0` is reset); `cb[1] & 0x7F` is the sub-command and
`cb[1] & 0x80` marks the IN direction:

| Sub-command | Handler | Purpose |
|---|---|---|
| `0x10` | `0xbfc28661` | RW_PHY — the flash path, see below |
| `0x13` | `0xbfc284a5` | RAM write (`0x93` reads) |
| `0x20` | `0xbfc28511` | SWITCH |
| `0x21` | `0xbfc28561` | CALL_ENTRY — calls an address with no arguments, keeps the return value |
| `0x22` | | POLL |
| `0x23` | | GET_STATUS — sends bytes from the stored pointer |
| `0x60` | `0xbfc28775` | DOWNLOAD_IMAGE |

In the `0x10` handler, `mode = addr >> 24` and `addr &= 0xFFFFFF`; data moves in chunks of up to
8 KB through `0x9fc20000`. Mode `0xFF` calls `drv(0xFF,0,0,0)`; a mode with bit `0x40` receives the
OUT data and then calls the driver (the write path); any other mode calls the driver and then sends
the buffer IN — the only read-shaped path.

### The FWSC driver's modes — and why `0x10` is never a read

| Mode | What the driver actually does |
|---|---|
| `0xFF` | nothing, returns 0 |
| `0x80` (bit 0x80 without 0x40) | nothing; ADFUS sends back whatever is in the RAM buffer |
| `0xC0` | LFI write; the first call also prepares the region |
| low nibble 0 (`0x00`, `0x40`) | **MBREC write**, sector 0, signature `0x1234` |
| low nibble 6 (`0x06`, `0x46`) | **BREC write** at +1024 |

**There is no read mode.** Modes `0x00` and `0x06` run the *write* routines even when issued in the
IN direction. Issuing one as a "read" overwrites the boot records with whatever is in the buffer.

Worse, the `NorFwsc.bin` that comes out of the SDK's `AFI.bin` erases NOR `0x0000`–`0xFFFF` on its
**first** driver call in **any** mode, `0x80` and `0xFF` included, and erases the whole chip at init
if `header[2] == 2`. The `0x80`/`0xFF`-are-harmless rule holds only for the plainer `Fwsc.bin` from
the SDK's production directory. Treat any driver call as a write.

Safe steps, then, are: upload ADFUS and SWITCH; call HWSC and GET_STATUS (identify only); call FWSC
init and GET_STATUS. The vendor pair offers **no** read-only NOR dump command at all.

## Reading flash anyway, three ways

1. **Through the ROM, via the XIP window.** `tools/adfu_core.py` reads `0x10000000` with `0x93`.
   Simple and safe, but the window starts at whatever logical offset the mapping holds (`0x5980` on
   the working firmware), so the boot records are not in it.
2. **Through a stub and mapping entry 9.** `tools/adfu_stub.py mapat <phys> <len>` points that
   entry at any offset and reads it. This is how the first megabyte, boot records included, was
   recovered.
3. **Raw SPI, through the loader's own read op.** `NorFwsc`'s ops table entry 0 at `0xbfc2d065` is
   a plain `read(phys, buf, len)` using SPI command `0x0B`, returning *physical* bytes — lines,
   CRCs and scrambling included. `tools/adfu_flash.py --read PHY LEN` uses it. This is the only way
   to see what is actually on the chip, and it is how `firmware/raw/raw_first_1MB.bin` was taken.

For reading the way the **processor** reads — through the cache, with CRC checking and the
randomizer — see `--xip2` in [02-flash-format.md](02-flash-format.md). That is the only read that
answers the question "will the boot ROM be able to use this?".

## Two operational lessons

* **Do not split one ADFU visit across two tool invocations.** After a long read the USB session is
  unusable and a fresh process cannot re-establish it: `CSW status 2`, then `Overflow`, then
  `Pipe error`, then a timeout. Anything that must happen in one visit has to happen in one process
  — which is why `--write` has `--verify`, reading back inside the same session.
* **A healthy device leaves software-entered ADFU by itself** within seconds. While the firmware
  would not boot, the device sat in ADFU indefinitely, which made the mode look stable. To catch a
  working device, poll for `10d6:10d6` and start the instant it appears.
* After a run that ends with the loader's `OVER` command, the device stops answering the ROM
  inquiry until it is replugged. `start_adfus()` handles this by reusing a resident ADFUS when the
  inquiry is refused, rather than uploading over running code.
