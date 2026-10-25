"""bootstrap.sh must fail loudly when what it fetched is not what these tools expect."""
import hashlib

from afi_decode import verify_checksums


def write_manifest(tmp_path, entries):
    manifest = tmp_path / "CHECKSUMS"
    manifest.write_text("".join(f"{digest}  {name}\n" for digest, name in entries))
    return manifest


def test_verify_checksums_empty_on_match(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"hello")
    manifest = write_manifest(tmp_path, [(hashlib.sha256(b"hello").hexdigest(), "a.bin")])
    assert verify_checksums(manifest, tmp_path) == []


def test_verify_checksums_reports_mismatch(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"hello")
    manifest = write_manifest(tmp_path, [("00" * 32, "a.bin")])
    problems = verify_checksums(manifest, tmp_path)
    assert len(problems) == 1 and "a.bin" in problems[0]


def test_verify_checksums_reports_a_missing_file(tmp_path):
    manifest = write_manifest(tmp_path, [("00" * 32, "gone.bin")])
    problems = verify_checksums(manifest, tmp_path)
    assert len(problems) == 1 and "gone.bin" in problems[0]
