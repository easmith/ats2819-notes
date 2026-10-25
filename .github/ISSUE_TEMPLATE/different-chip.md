---
name: My device is not exactly this one
about: Before trusting any address here on different hardware, establish what you have
title: "[device] "
labels: different-hardware
---

Everything in this repository was verified on **one** unit: an AGPTEK S22 bone-conduction headset,
Actions ATS2819 / US281B, GD25Q16 flash. On any other chip the loader addresses, the patch sites and
the flash geometry are very likely different, and writing with them can brick a device.

Please fill in what you can before asking whether something applies to yours.

**The device**
- Product name and where it was sold:
- Chip markings (photo of the board if you have it open):
- Bluetooth advertised name:
- USB `idVendor:idProduct` in normal mode:
- USB disk label, if it presents one:

**What the tools report** (all read-only)
- `sudo .venv/bin/python tools/adfu_core.py --probe` output:
- Flash id from a dry run (`tools/adfu_flash.py stock`, which writes nothing):
- Config ids 17–21 from `tools/cfgdump.py`:
- First 16 bytes of `tts_rc.lib` (`MP3 `, rate, frame size, then `v2  ` on this platform):

**What you are trying to do**

**Have you taken a full backup?** If not, do that first — `docs/en/05-adfu.md` describes the
read-only paths, and nothing in this repository can restore a device whose own image you do not have.
