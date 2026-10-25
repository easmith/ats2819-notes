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


def test_bootstrap_manifest_is_published():
    """The pinned loader checksums are the whole point of bootstrap's last step."""
    assert "vendor/CHECKSUMS" in git_ls_files()


def test_no_tool_references_the_private_working_tree():
    """The port repointed string paths; paths built from components were missed once already.

    Private inputs (the old build/eats2819 tree, reference/) must be gone entirely. "dumps" may
    only appear as generated output, i.e. on a line that routes it through build/.
    """
    offenders = []
    for tool in sorted((ROOT / "tools").glob("*.py")):
        for number, line in enumerate(tool.read_text().splitlines(), 1):
            for needle in ("eats2819", "afi_base", "reference/"):
                if needle in line:
                    offenders.append(f"{tool.name}:{number} {needle}")
            if '"dumps"' in line and "out_dir(" not in line and "BUILD_ROOT" not in line:
                offenders.append(f'{tool.name}:{number} "dumps" outside build/')
    assert offenders == []


def test_no_editor_or_tooling_artifacts_are_tracked():
    """.idea/ was committed before .gitignore covered it and reached the published repository."""
    junk = (".idea/", ".vscode/", ".pytest_cache/", ".DS_Store", "__pycache__/", ".venv/")
    assert [p for p in git_ls_files() if p.startswith(junk) or p.endswith(".DS_Store")] == []
