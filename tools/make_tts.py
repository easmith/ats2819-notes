"""Build a tts_rc.lib (Actions US281B "v2" format) with Russian and English prompts.

Layout (see 2819p psp_rel/mmm_drv/music_player/music_player_tts_play.c):
  "MP3 " u16 sample_rate u16 frame_size "v2  " u32 0          -> 16-byte header
  8-byte sections from 0x10: {u32 offset, u16 length, u16 tail_zero}, two per prompt:
  section 2*id = language 0 (stock: Chinese, here Russian), 2*id+1 = language 1 (English)
  audio from 0x400. tail_zero = 0: no zero-frame compression.

Usage: python3 tools/make_tts.py OUT_DIR
"""
import os
import struct
import subprocess
import sys

# id: (russian, english). Ids follow 2819p tts_res/tts_flist.txt; only ids the stock device has.
PROMPTS = {
    0: ("Блютус", "Bluetooth"),
    1: ("Жду связи", "Waiting"),
    2: ("Подключено", "Connected"),
    3: ("Отключено", "Disconnected"),
    8: ("Плеер", "Player"),
    9: ("Флешка", "USB drive"),
    11: ("Радио", "Radio"),
    12: ("Аудиовход", "Line in"),
    14: ("USB звук", "USB audio"),
    15: ("Накопитель", "USB disk"),
    17: ("Низкий заряд", "Battery low"),
    18: ("Батарея разряжена", "Battery empty"),
    19: ("Включено", "Power on"),
    20: ("Выключение", "Power off"),
    22: ("ноль", "zero"),
    23: ("один", "one"),
    24: ("два", "two"),
    25: ("три", "three"),
    26: ("четыре", "four"),
    27: ("пять", "five"),
    28: ("шесть", "six"),
    29: ("семь", "seven"),
    30: ("восемь", "eight"),
    31: ("девять", "nine"),
    32: ("десять", "ten"),
    33: ("сто", "hundred"),
    35: ("точка", "point"),
    37: ("Эф Эм", "FM"),
    38: ("Ищу пару", "Pairing"),
    39: ("Пара есть", "Paired"),
    40: ("Пары нет", "Unpaired"),
}
VOICES = ("Milena", "Samantha")
SAMPLE_RATE, FRAME_SIZE = 16000, 72      # MPEG-2 layer III, 16 kHz, 16 kbps mono
NUM_PROMPTS = 41
AUDIO_START = 0x400


def run(*cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def strip_tags(data):
    if data[:3] == b"ID3":
        size = (data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F)
        data = data[10 + size:]
    if len(data) >= 128 and data[-128:-125] == b"TAG":
        data = data[:-128]
    return data


def check_frames(data, name):
    """Every frame must be a 72-byte MPEG-2 L3 16 kHz mono frame (constant size, <= 512 bytes)."""
    if len(data) % FRAME_SIZE:
        raise ValueError(f"{name}: {len(data)} bytes is not a whole number of {FRAME_SIZE}-byte frames")
    for off in range(0, len(data), FRAME_SIZE):
        h = data[off:off + 4]
        if h[0] != 0xFF or (h[1] & 0xFE) != 0xF2 or (h[3] >> 6) != 3:
            raise ValueError(f"{name}: bad frame header {h.hex()} at {off}")


def synth(text, voice, out_dir, tag):
    aiff, wav, mp3 = (os.path.join(out_dir, f"{tag}.{ext}") for ext in ("aiff", "wav", "mp3"))
    run("say", "-v", voice, "-o", aiff, text)
    # trim silence at both ends, mono 16 kHz, a little gain for bone conduction
    run("ffmpeg", "-y", "-i", aiff, "-af",
        "silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
        "silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
        "apad=pad_dur=0.05,volume=1.5,alimiter=limit=0.95",
        "-ac", "1", "-ar", str(SAMPLE_RATE), wav)
    run("lame", "--silent", "-m", "m", "--cbr", "-b", "16", "--resample", "16", "-t", "--noreplaygain", wav, mp3)
    data = strip_tags(open(mp3, "rb").read())
    check_frames(data, tag)
    open(mp3, "wb").write(data)
    os.remove(aiff)
    os.remove(wav)
    return data


def main():
    out_dir = sys.argv[1]
    clips_dir = os.path.join(out_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    sections = [(0, 0)] * (2 * NUM_PROMPTS)
    audio = b""
    for pid in range(NUM_PROMPTS):
        if pid not in PROMPTS:
            continue
        for lang, (text, voice) in enumerate(zip(PROMPTS[pid], VOICES)):
            data = synth(text, voice, clips_dir, f"{pid:02d}_{'ru' if lang == 0 else 'en'}")
            if len(data) > 0xFFFF:
                raise ValueError(f"prompt {pid} too long")
            sections[2 * pid + lang] = (AUDIO_START + len(audio), len(data))
            audio += data
    header = b"MP3 " + struct.pack("<HH", SAMPLE_RATE, FRAME_SIZE) + b"v2  " + bytes(4)
    index = b"".join(struct.pack("<IHH", off, ln, 0) for off, ln in sections)
    lib = (header + index).ljust(AUDIO_START, b"\0") + audio
    path = os.path.join(out_dir, "tts_rc.lib")
    open(path, "wb").write(lib)
    print(f"{path}: {len(lib)} bytes, {sum(1 for s in sections if s[1])} clips")


if __name__ == "__main__":
    main()
