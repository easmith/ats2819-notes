"""Where things live, and what to tell the reader when something is missing.

Three kinds of file:
  * repository assets — the firmware images that ship here:        asset("firmware", ...)
  * bootstrapped material — the SDK and its loaders, which this
    repository does not redistribute:                              vendor("afi", "NorFwsc.bin")
  * generated output — anything the tools build, kept out of git:  out_dir("fw")
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_ROOT = REPO_ROOT / "vendor"
BUILD_ROOT = REPO_ROOT / "build"

BOOTSTRAP_HINT = (
    "run scripts/bootstrap.sh — it fetches the Actions SDK, decodes the ADFU loaders out of it "
    "and installs the Python dependencies that are deliberately not redistributed here"
)


class BootstrapMissing(FileNotFoundError):
    """Something bootstrap.sh was supposed to provide is not there."""


def asset(*parts: str) -> Path:
    """A file that ships with this repository."""
    path = REPO_ROOT.joinpath(*parts)
    if not path.exists():
        raise FileNotFoundError(f"{path.relative_to(REPO_ROOT)} is missing from this repository")
    return path


def vendor(*parts: str) -> Path:
    """A file that scripts/bootstrap.sh brings in."""
    path = VENDOR_ROOT.joinpath(*parts)
    if not path.exists():
        raise BootstrapMissing(f"vendor/{'/'.join(parts)} is not here — {BOOTSTRAP_HINT}")
    return path


def out_dir(*parts: str) -> Path:
    """A directory for generated files, created on demand and ignored by git."""
    path = BUILD_ROOT.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def image_or_shipped(name: str) -> Path:
    """A freshly built image if tools/build_fw.py made one, otherwise the published copy.

    This is what lets a reader flash the published prompts without building anything.
    """
    built = BUILD_ROOT / "fw" / name
    return built if built.is_file() else asset("firmware", "prompts-ru-en", name)
