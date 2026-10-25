# Voice prompts in your own language

[Русская версия](../ru/08-custom-prompts.md).

The firmware ships 31 Chinese prompts and no English ones, and nothing selects a second language,
so the only route is to replace the prompt library. That library is one file inside the firmware
image, which makes this the least invasive change you can make to these headphones — and it is what
this whole repository was built to do.

## The 31 prompts

Ids follow the SDK's `common_tts_id.h` enum. These are the ids the stock firmware actually carries;
the gaps (4–7, 10, 13, 16, 21, 34, 36) are prompts this product does not use.

| id | SDK name | What the Chinese says | Russian used here | English used here |
|---|---|---|---|---|
| 0 | `TTS_ENTER_BT` | Bluetooth mode | Блютус | Bluetooth |
| 1 | `TTS_WAIT_BT_CON` | waiting for a connection | Жду связи | Waiting |
| 2 | `TTS_BT_CON_OK` | connected | Подключено | Connected |
| 3 | `TTS_BT_DISCON` | disconnected | Отключено | Disconnected |
| 8 | `TTS_PLAY_SDCARD` | card playback | Плеер | Player |
| 9 | `TTS_PLAY_UHOST` | USB-host playback | Флешка | USB drive |
| 11 | `TTS_ENTER_RADIO` | radio mode | Радио | Radio |
| 12 | `TTS_PLAY_LINEIN` | line input | Аудиовход | Line in |
| 14 | `TTS_ENTER_USBBOX` | USB audio mode | USB звук | USB audio |
| 15 | `TTS_ENTER_UDISK` | USB disk mode | Накопитель | USB disk |
| 17 | `TTS_BAT_LOW_CHARGE` | battery low, charge it | Низкий заряд | Battery low |
| 18 | `TTS_BATTERY_LOW` | battery empty | Батарея разряжена | Battery empty |
| 19 | `TTS_POWERON` | power on | Включено | Power on |
| 20 | `TTS_POWEROFF` | powering off | Выключение | Power off |
| 22–31 | `TTS_SECTION_NUM_0…9` | digits 0–9 | ноль … девять | zero … nine |
| 32 | `TTS_SECTION_TEN` | ten | десять | ten |
| 33 | `TTS_SECTION_HUNDRED` | hundred | сто | hundred |
| 35 | `TTS_SECTION_DOT` | decimal point | точка | point |
| 37 | `TTS_SECTION_CHAR_FM` | "FM" | Эф Эм | FM |
| 38 | `TTS_WAIT_BT_PAIR` | pairing | Ищу пару | Pairing |
| 39 | `TTS_PAIR_SUCCEED` | paired | Пара есть | Paired |
| 40 | `TTS_PAIR_DISCON` | pairing lost | Пары нет | Unpaired |

The digits, "ten", "hundred", "point" and "FM" are how the radio reads a frequency out: 101.2 comes
out as one–zero–one–point–two.

## Building a library

`tools/make_tts.py` holds the texts in a `PROMPTS` dict — id → (language 0, language 1) — and builds
the library in the factory format:

```bash
python3 tools/make_tts.py build/tts
#   -> build/tts/tts_rc.lib and build/tts/clips/*.mp3
```

It synthesizes each phrase with the macOS `say` command, converts with `ffmpeg`, and encodes with
`lame` to MP3 16 kHz, 16 kbit/s mono CBR — the format the firmware's decoder expects. Rebuilding the
published library this way reproduces it **byte for byte**, so the published file is exactly what
the script makes.

To change a language or the wording, edit `PROMPTS` (and `VOICES` for the voices) and rebuild. On a
machine without `say`, substitute any TTS that can produce mono 16 kHz WAV and keep the `lame`
invocation.

## The size limit, which is the real constraint

Between the end of the firmware image and the VRAM region at logical `0xF0000` there are **56 768
bytes** free. The stock library is 39 872 bytes, so a replacement may be at most about 96 KB in
total.

The Russian + English library here is **87 280 bytes** — 47 408 more than stock — leaving roughly
9 KB of slack. A bilingual set at natural phrasing came to about 112 KB and did not fit, which is
why the phrases are as short as they are ("Жду связи", not "Ожидание подключения"). If you need
longer phrasing, drop prompts you do not care about: the digits alone are ten of the thirty-one.

## Putting it on the device

```bash
python3 tools/build_fw.py                     # build/fw/afi_plain.bin and build/fw/UPGRADE.HEX
sudo .venv/bin/python tools/adfu_flash.py ours --write --verify 0x5000
```

`build_fw.py` takes the stock image, swaps `TTS_RC.LIB` for the new library, shifts the files after
it, fixes every checksum, and refuses to produce an image whose LFI would reach VRAM. Then
[06-flashing.md](06-flashing.md) applies in full — including the acceptance read, which is the part
that matters.

To flash the prompts published here without building anything, skip straight to the write: `ours`
falls back to `firmware/prompts-ru-en/afi_plain.bin`.

## The second language is built but unreachable

`tts_rc.lib` has two slots per prompt, and the SDK's own `case/fwpkg/tts_flist.txt` is a
two-column list — Chinese and English — with every English slot NULL. That is the vendor's
second-language mechanism, and this product shipped with the second column empty.

The library built here fills **both** columns: Russian in slot 0, English in slot 1. Slot 0 is what
the device plays. Nothing was found in the firmware that selects slot 1 — no button combination, no
config item — and since the stock firmware had no English entries at all, the selector may simply
not be reachable without patching firmware code. The English audio is there, ready, if someone
works out how to switch languages. If you do, it is worth an issue.
