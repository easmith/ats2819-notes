# Read this before writing anything to a device

Writing to the NOR flash of one of these headsets can leave it unable to boot. That happened here
once and the device came back, but only because a full backup existed. Everything below is a lesson
that cost something.

**[Русская версия](SAFETY.ru.md)**

## Before the first write

1. **Take a full backup and keep it off the device.** `tools/adfu_core.py` and
   `tools/adfu_stub.py` read flash without writing a byte. An image you did not take yourself may
   not match your unit.
2. **Charge the headset.** ADFU does not charge. Hours in service mode flattened the battery here
   until the protection cut in and the device stopped responding entirely.
3. **Read [06-flashing.md](docs/en/06-flashing.md) and [07-recovery.md](docs/en/07-recovery.md).**
   The recovery procedure is known and it works; knowing it beforehand is what turns a brick into
   an inconvenience.

## The rules

* **Never call the FWSC write driver except to write deliberately.** The loader decoded out of the
  SDK package erases NOR `0x0000`–`0xFFFF` — the boot records — on its **first** driver call, in
  **any** mode, including the modes that look like reads. If its header byte 2 is 2, it erases the
  whole chip at init.
* **Never send ADFU command `0x10` as a read.** The FWSC driver has no read mode. Modes `0x00`,
  `0x40` and `0xC0` all run write routines.
* **Never write with `--sw-seed`.** It reproduces the exact failure that bricked this device: the
  image is written with software seed 0 while the boot ROM reads with the chip's eFuse seed.
* **Write and verify in one process, in one USB session.** After a long read the session is unusable
  and a fresh process cannot re-establish it. Use `--write --verify`, which reads back before you
  unplug.
* **Judge a verification by the byte comparison, never by the CRC flag.** The cache raises a CRC
  error on erased regions, which is normal; `20480/20480 IDENTICAL` is the answer that matters.
* **Do not trust an offline decoder as proof that a flash will boot.** One here verified an image
  the device could not read. Only a read-back through the cache with the hardware seed proves it.
* **The disk-upgrade path is not safer than ADFU.** `UPGRADE.HEX` carries its own FWSC loader and
  runs it, so an unpatched package bricks the device the same way, with no pre-write check.

## If the device no longer boots

It enumerates as `10d6:10d6` with a red LED: that is the boot ROM in ADFU, and it means the ROM
could not start the firmware — not that the device is dead. It is writable over ADFU, and
[07-recovery.md](docs/en/07-recovery.md) is the way back.
