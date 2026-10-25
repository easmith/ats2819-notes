"""The shipped raw reference must agree with the shipped logical image, line for line."""
from conftest import ROOT

import verify_raw


def test_device_and_sdk_layouts_put_brec_at_different_lines():
    parts = (b"\x00" * 0x400, b"\x11" * 0x400, b"\x22" * 0x400)
    device = verify_raw.layout(parts, "device", 100)
    sdk = verify_raw.layout(parts, "sdk", 100)
    assert device[0xFC0 // 64] == b"\x11" * 64
    assert sdk[0x400 // 64] == b"\x11" * 64


def test_default_reference_is_the_published_raw_read():
    assert verify_raw.DEFAULT_REF == ROOT / "firmware/raw/raw_first_1MB.bin"


def test_shipped_raw_reference_is_a_function_of_logical_line_content():
    """Every repeated logical line maps to one and only one physical line."""
    ref = verify_raw.lines(verify_raw.DEFAULT_REF.read_bytes())
    want = verify_raw.layout(verify_raw.image_parts("stock"), "device", len(ref))
    table = {}
    for plain, physical in zip(want, ref):
        if plain is not None:
            assert table.setdefault(plain, physical) == physical
    assert len(table) > 1000
