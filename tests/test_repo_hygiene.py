"""What ships must be small, free of personal data, and free of anybody else's archives."""
import subprocess

from conftest import ROOT
from privacy_scan import scan


def git_ls_files():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                         capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def test_no_vendor_or_reference_files_are_tracked():
    tracked = git_ls_files()
    assert not [p for p in tracked if p.startswith(("vendor/2819p", "vendor/actions-adfu",
                                                    "vendor/afi", "reference/", ".venv/"))]


def test_repo_is_small_enough_for_plain_git():
    total = sum((ROOT / p).stat().st_size for p in git_ls_files() if (ROOT / p).is_file())
    assert total < 8 * 1024 * 1024, f"{total} bytes tracked"


def test_whole_tree_is_free_of_personal_strings():
    assert scan([ROOT / p for p in git_ls_files()]) == []
