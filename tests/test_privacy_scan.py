"""The privacy scanner must find personal strings in any encoding the device uses."""
from conftest import ROOT  # noqa: F401  (also installs sys.path entries)
from privacy_scan import scan


def test_finds_ascii_needle(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"\x00\x01Pixel 8\xff")
    hits = scan([f])
    assert [(h.needle, h.encoding, h.offset) for h in hits] == [("Pixel 8", "utf-8", 2)]


def test_finds_utf16le_needle(tmp_path):
    f = tmp_path / "b.bin"
    f.write_bytes(b"\x00" + "MacBook".encode("utf-16-le"))
    assert [h.encoding for h in scan([f])] == ["utf-16-le"]


def test_finds_cp1251_needle(tmp_path):
    f = tmp_path / "c.bin"
    f.write_bytes("Евгений".encode("cp1251"))
    assert len(scan([f])) == 1


def test_clean_file_has_no_hits(tmp_path):
    f = tmp_path / "d.bin"
    f.write_bytes(b"iphone charging!\x00AT+IPHONEACCEV=1,1,4")
    assert scan([f]) == []


def test_directory_is_scanned_recursively(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "e.bin").write_bytes(b"Eugene")
    assert len(scan([tmp_path])) == 1


def test_allowlisted_file_keeps_its_intentional_byline(tmp_path):
    f = tmp_path / "LICENSE"
    f.write_bytes(b"Copyright (c) 2026 Eugene")
    assert scan([f]) == []


def test_the_scanner_and_its_own_tests_are_allowlisted():
    """The needle list and the tests that exercise it must not flag themselves."""
    assert scan([ROOT / "scripts" / "privacy_scan.py",
                 ROOT / "tests" / "test_privacy_scan.py"]) == []
