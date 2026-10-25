<!-- Working notes. Kept as written during the investigation: addresses, disassembly and
     raw measurements, in the order they were found. The readable account is in docs/en/.
     Paths mentioned here are from the private working tree; the published equivalents are
     firmware/stock/nor_logical_1MB.bin, firmware/raw/raw_first_1MB.bin and vendor/afi/. -->

# Формат UPGRADE.FWU / UPGRADE.HEX (US281B / ATS2819)

Источники: `vendor/2819p/case/fwpkg/build_FWU_HEX.bat`, исходники `case/ap/ap_upgrade/*.c` и `case/ap/ap_config/config_*.c`, `psp_rel/bin/upgrade.bin`, скрипт производителя `afi_py/fwsp.py` (из `psp_rel/bin/AFI.bin`). Все файлы внутри AFI.bin закрыты XOR 0x38, расшифрованные копии лежат в `afi_base/` и `afi_py/`.

## Как собирается (build_FWU_HEX.bat)

```
FW2X.exe -fssf US281B_BTBOX.fw MakeBin AFI UPGRADE.IMG
copy upgrade.bin /b + UPGRADE.IMG /b  UPGRADE.FWU
copy UPGRADE.FWU UPGRADE.HEX            (это тот же файл)
```

```
UPGRADE.FWU = [ upgrade.bin, 0x8300 байт, открытый MIPS-код ]
            + [ AFI, зашифрованный FWU v3 (если FWU_ENCRYPT=1) ]
            + [ FwuTail, 512 байт, не шифруется ]
```

## Когда запускается

`config_poweron.c`: при включении, если вставлена карта (`DISK_H`, у наушников это внутренняя память 8 ГБ, судя по всему) и в корне лежит `UPGRADE.HEX` или `UPGRADE.FWU` (имя в Unicode или 8.3). Без кабеля зарядки нужен заряд батареи выше 1 (карта) или выше 3 (uhost).

- `UPGRADE.FWU`: заводской режим, после обновления устройство не перезагружается.
- `UPGRADE.HEX`: пользовательский режим. Файл переименовывается в `UPGRADE~.HEX`, после обновления устройство перезагружается.

## Часть 1: upgrade.bin (проверка на устройстве, `upgrade_app_main.c`)

```c
struct upgrade_header_t {      // смещение 0 файла
    u32 checksum;              // сумма u32-слов [4 .. load_size)
    u8  format[4];             // "UPG\0"
    u32 load_size;             // 0x8300
    u32 load_addr;             // 0x9fc3a000
    u32 main_func;             // 0x9fc41c8d (MIPS16)
};
```

`upgrade.ap` проверяет только `"UPG"` и эту сумму (проверено на SDK upgrade.bin: 0xddb10a6e), читает первые `load_size` байт файла в `load_addr` и вызывает `main_func(params)`. Подписи нет.

В upgrade.bin есть свой драйвер FAT/exFAT, uhost, функции `upgrade_decrypt_read_block`, `upgrade_image_find_file`, `upgrade_load_and_run_hwsc/fwsc`, `upgrade_fw_write`, имена `NORHWSC BIN`, `NORFWSC BIN`, `NORMBRECBIN`, `NORBREC BIN`, `FWIMAGE FW `, `RESLIB  BIN`. Также в нём есть константы FWU v3: сигнатура `11 22 .. ff 75` по смещению 0x82c0, sig2 `3f ad f8 b0 ..` по 0x82d8, таблица decode_A по 0x7e28 и 0x808.

## Часть 2: шифрование FWU v3

Схема та же, что разобрана в rockbox `utils/atj2137/atjboottool/fwu.c`. **В upgrade.bin от 2819 таблицы совпадают полностью**: decode_A (1024 байта, смещение 0x7e28), sect233k1 b (0x8228), G_y (0x8248), G_x (0x8268), сигнатура и sig2 (0x82c0, 0x82d8). Значит, он выполняет ту же процедуру ECIES.

**Закрытый ключ ECC хранится в самом файле** (блок B, закрыт ключом из блока A), так что 32-байтный сеансовый ключ задаётся только сектором 0 и блоками A/B. Секрет устройства не нужен.

Шифр данных: в US282A UPGRADE.HEX стоит вариант «ATJ2127» (atjboottool пишет «looks like an ATJ2127 firmware», 16 раундов на 512-байтный сектор), RC4 не используется. FwuTail у US282A нет, и это **голый** FWU (сигнатура по смещению 0), без upgrade.bin спереди.

Эталон: `reference/fwu/US282A_UPGRADE.HEX`, его ключ: `reference/fwu/US282A_UPGRADE.keybuf`, atjboottool с печатью ключа: `vendor/atjboottool` (make). Упаковщик `tools/fwu_pack.py` берёт сектор 0 и блоки A/B из эталонного FWU и шифрует свой AFI. Ключ извлекается копией atjboottool с печатью keybuf (переменная `KEYBUF_OUT`). Проверено:
- перешифрованный расшифрованный эталон US282A **побайтно совпадает** с оригиналом (все 1 759 232 байта);
- нагрузка другого размера (0x51234 байт, изменённое содержимое) с `--tail` расшифровывается atjboottool без ошибок: проходят все проверки, контрольные суммы FwuTail и сравнение с исходником.

Не проверено:
- примет ли upgrade.bin от 2819 сектор 0 и блоки A/B, взятые из файла US282A;
- шифрует ли FW2X AFI для 2819 вообще (`FWU_ENCRYPT` в fwimage.cfg не задан, значение по умолчанию неизвестно);
- где upgrade.bin ищет начало FWU. Скорее всего, сразу после себя, по смещению 0x8300.

## Часть 3: AFI (fwsp.py BuildAFI)

```
ST_AFI_HEAD (4096 байт):
  "AFI\0", vid u16, pid u16, version[4], date[4] (BCD), length u32,
  sdk_version[4], reserved[8],
  ST_AFI_DIR[126]: name 8.3 [11], type u8, address u32, offset u32, length u32, subtype u32, checksum u32
  reserved[28], head_checksum u32 (= сумма u32 первых 4092 байт)
type: 'H'(0x48)=HWSC, 'F'(0x46)=FWSC, 'M'(0x4D)=MBREC, 'B'(0x42)=BREC, 0x60=NAND_ID, 'I'(0x49)=FWIMAGE.FW (LFI), 0x70=RESLIB.BIN
checksum записи = сумма u32 по данным файла. Длина AFI выравнивается до 512 байт.
MBREC: первые 2 байта = checksum_2(данные[2:]) + 0x1234.
```

```
FwuTail (512 байт, после зашифрованных данных):
  length=1, type=7, reserved[14], fwu_checksum u32 (сумма u32 по зашифрованному AFI),
  flag=0x55aa55aa, "FwuTail\0", crc[32]=0, reserved[444], tail_checksum u32 (сумма первых 508 байт)
```

## Часть 4: FWIMAGE.FW = LFI (fwsp.py BuildLFI)

```
ST_LFI_HEAD (512): lfi_flag u32, sdk_version[4], version[4], vid, pid, dir_checksum u32,
  capinfo[32] (+20), reserved[16], bt_lap[3] (+68), save_bt_flag, reserved[7],
  udisk_setting (+79), usb_setup_info[48] (+80), sdk_description[336], reserved[46],
  head_checksum u16 (= checksum_2 первых 510 байт)
ST_LFI_DIR[n] (32 байта): name[11], attr, subtype, reserved, version u16, offset u32, length u32, reserved[4], checksum u32
  n округляется до границы cache line, последняя запись пустая. dir_checksum = сумма u32 по всем записям.
Порядок файлов: драйверы (по text_addr, entry-таблицы общих модулей объединены), затем AP (по text_addr), затем остальные файлы.
В конце добавляется ещё одна cache line.
```

LFI пишется в открытом виде. При записи (FWSC, команда WRITE_LFI, режим 0xC0) NOR шифрует сам FWSC. При обновлении заводской инструмент сначала **читает** заголовок LFI (WRITE_LFI, режим `0x80000000`, 0x200 байт) и переносит в новый заголовок `capinfo`, BT LAP (+68..70) и usb/sn (+79..127). Разбор NorFwsc.bin (RE_NOTES.md) показал: режима чтения у него нет, и в ответ на 0x80 приходит лишь старое содержимое RAM-буфера. Кроме того, первый же вызов его драйвера стирает NOR 0–0xFFFF. На устройстве его не использовать.

## Что это значит для dumps/flash_full.bin

Заголовки модулей (сигнатура `yqhx` по смещению +4): первый целый заголовок стоит на 0x62c0 (драйвер id 0xff). Заголовка ядра (id 0x00) в дампе нет. Окно `0x10000000` начинается **внутри кода ядра**. Заголовок LFI, каталог LFI, заголовок и начало kernel.drv, MBREC и BREC в дамп не попадают. Все остальные модули видны целиком (43 заголовка: драйверы, AP, *.al), а также tts_rc.lib и config.bin. Ядро на устройстве собрано иначе, чем в SDK: куски kernel.drv из SDK стоят с плавающим сдвигом.

Обновление с карты в прошивку устройства встроено: `config.ap` (заголовок на 0x9ad80) содержит строки `UPGRADE HEX`/`UPGRADE FWU` (0x9bc0c), а `upgrade.ap` (заголовок на 0x9f740) — `upgrade_file_open`, `UPGRADE~HEX`.

Итог: формат известен, шифровать мы умеем. Но чтобы собрать FWIMAGE.FW, недостаёт заголовка LFI, каталога и начала ядра этого устройства. Варианты: свой код через ADFU CALL_ENTRY (чтение NOR, перенастройка окна XIP), или собрать LFI целиком из SDK. Второй вариант рискованнее: прошивка будет не та, что стоит на устройстве. Через NorFwsc прочитать нельзя, см. RE_NOTES.md.

## upgrade.bin, what it actually does (disassembly: build/eats2819/dis/upgrade16.txt)

upgrade_main 0x9fc41c8d(params):

1. **file_open** 0x9fc41351 opens `UPGRADE.FWU` (factory) or `UPGRADE~.HEX` (end user) on DISK_H/DISK_U. It reads the 20-byte UPG header. FWU offset = header.load_size (0x8300). File size ≤ load_size → SIZE_ERR.
2. **load_and_run_hwsc** 0x9fc4179d:
   - AFI file `NORHWSC BIN` → loaded to dir.address, checksum32 must equal dir.checksum (CHECKSUM_ERR);
   - call; it must return a ptr to {buf, len=36} (RET_ERR);
   - flash id = status+20.
3. **find_flash_info** 0x9fc418b5: `NOR_ID  BIN` (type 0x60), 128-byte records. Match the 4 id bytes with 0xFF wildcard → ctx+24, else UNKNOWN.
4. **load_and_run_fwsc** 0x9fc419c5:
   - `NORFWSC BIN` → dir.address (0xbfc2c000), checksum checked;
   - copies the matched NOR_ID record to hdr+112;
   - hdr+388 = 0 (factory) / 3 (end user);
   - calls +0x200, which must return len 36; the driver is +0x400.
5. drv(0,0,0,0): the first NorFwsc driver call erases NOR 0–0xFFFF.
6. fw_write(`NORBREC BIN`, 0x46), fw_write(`FWIMAGE FW `, 0xC0, then drv(0xFF) must be 0, else VERIRY_ERR), fw_write(`NORMBRECBIN`, 0x40), optionally fw_write(`RESLIB  BIN`, 0xC1). Then drv(1), progress 100.

**MBREC is written last**: an interrupted upgrade leaves no valid MBREC, so the ROM should fall back to ADFU.

fw_write: 32 KB chunks → drv(mode, pos, n, buf). checksum32 of the data must equal dir.checksum (checked *after* writing). image_find_file looks only at the **first 15** AFI dir entries (first 512 bytes of the AFI head). The AFI head checksum is not checked.

FWU read (decrypt_read_block 0x9fc4152d):
- block 0 = 18 432 bytes at fwu_off (sector0 + blocks A/B + first 16 KB) → key setup 0x9fc3b9e8;
- block n = 16 KB at fwu_off + n + 2048, decrypted by **ATJ2127** sector cipher 0x9fc3bc28 (constants 0x42146ea2… at 0x9fc3bedc; rounds and key at state+260/+264).

So the payload layout is exactly what tools/fwu_pack.py produces. Constraint: offA + offB ≤ 16 KB (true for any A,B ≤ 15).

## NorFwsc details that matter for the AFI

- Write path 0xbfc2fa95: logical→physical = addr/64·66 (every 64-byte line is stored with a 2-byte CRC16, 0xbfc2f6a1). The "randomizer" bits 0x2000|0x8000 in SPI0 CTL are set while writing whenever flag 0xbfc2fc60 bit 7 is set. 0xbfc2e9e5 always sets that flag. No key is needed from the host.
- Init chip-id check: writes hdr chipid table (hdr+240, index hdr+380) to SPI0+0x14 and reads it back, status 4 on mismatch. The SDK packages define no CHIPID/PACKAGEID, so the table is 0. The device reads SPI0+0x14 = 0.
- MBREC write (0x40): recomputes [0..1] = checksum_2 + 0x1234 and the size.
- BREC write (0x46) copies 64 bytes of flash-chip info into BREC+64.

## Device boot records (dumps/nor_logical_1MB.bin)

- MBREC = NOR[0:0x400] (byte 2 = 0x5A). Built from a different SDK than ours: 624 bytes differ.
- BREC = NOR[0xfc0:0x4280], 0x32c0 bytes. Header:
  - checksums ebec/eedf (both verify), len/all 0x32c0, welcome_off 0x2dc0;
  - **lfi_cap 0xddfc0** (+20), spi_stg_cap 0xF0000 (+24), **decrypt_mode 0** (+28);
  - boot_config (+128): 000000000400000001000000010000000100000001000000000000000000000000000000080000000000000000f0010000100000000000000000000000000000;
  - bonding 03010f00 (+192), mode_para 0.
- The device NOR is **not** encrypted (decrypt_mode 0), so the FWSC header must have +16 = 0 and +20/+24 = 0.

## Plan for our AFI

- NORMBREC = device MBREC; NORBREC = device BREC with lfi_cap and both checksums updated; FWIMAGE = device LFI with the new TTS_RC.LIB (files after it shifted, 64-byte aligned, dir/head checksums recomputed). The new LFI ends at ≈0xEDB70, below VRAM 0xF0000.
- NORHWSC / NORFWSC / NOR_ID from SDK AFI.bin. FWSC header:
  - +2 = 0, +8 = lfi_cap, +12 = spi cap, +16 = 0, +28 = 1;
  - +29 = bonding;
  - +48 = FF×16;
  - +240 = zeros;
  - +384 = boot_config from the device BREC.
- Before writing anything: dry-run HWSC + FWSC init on the device through an ADFU stub (no driver call, so no erase) and check status 0 and flash-id match.

## Built and verified on the host (2026-10-25): build/fw/UPGRADE.HEX

tools/build_fw.py produced build/fw/UPGRADE.HEX (1 054 976 B = upgrade.bin 0x8300 + FWU). Verified entirely on the host, nothing sent to the device:
- atjboottool --fwu decrypts it: signature/size/version/tail all Ok, ECIES Pass, ATJ2127.
- decrypted AFI == build/fw/afi_plain.bin (byte-identical), AFI head checksum ok.
- 6 AFI entries, every checksum32 ok: NORHWSC(0xbfc30000), NORFWSC(0xbfc2c000), NOR_ID, NORBREC, FWIMAGE(len 0xe9900), NORMBREC.
- FWIMAGE vs device LFI: only TTS_RC.LIB changed (39 872 → 87 296 B, == build/tts/tts_rc.lib padded to 64); all other 46 files byte-identical; dir/head checksums recomputed.
- BREC: lfi_cap 0xe9900, decrypt_mode 0, both checksums ok; only bytes 0-3 (checksums) and 20-22 (lfi_cap) differ from device.
- MBREC byte-identical to device.
- FWSC: only header bytes 9-433 changed (operate=0, lfi_cap, spi_cap 0xF0000, decrypt=0, upgrade=1, bonding 03010f00, chipid=0); loader code from +0x200 intact.
- Chip GD25Q16 (device BREC+64 == SDK nor_id record at 0x2280).

Still unverified on the device: that SDK HWSC/FWSC run on this unit and the chip-id check passes with 0. Next step (no flash write): ADFU stub dry-run of HWSC + FWSC init only.
