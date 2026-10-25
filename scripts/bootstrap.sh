#!/usr/bin/env bash
# Fetch everything this repository deliberately does not redistribute, and verify it.
#
#   * the Actions US281B / ATS2819 SDK (github.com/Pachouli/2819p) — the ADFU loaders live inside
#     its psp_rel/bin/AFI.bin, and build_fw.py needs its upgrade.bin
#   * actions-adfu (github.com/sonigeez/actions-adfu, MIT) plus the endpoint patch in patches/
#   * pyusb and the rest of requirements.txt, into .venv
#
# Safe to run again: existing clones are updated, not replaced, and nothing touches a device.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
VENDOR="$ROOT/vendor"
mkdir -p "$VENDOR"

clone_or_update() {   # url dir
  local url="$1" dir="$2"
  if [ -d "$dir/.git" ]; then
    echo "==> $(basename "$dir") already here, updating"
    git -C "$dir" pull --ff-only --quiet || echo "    (no fast-forward available, keeping what is here)"
  else
    echo "==> cloning $(basename "$dir")"
    git clone --depth 1 --quiet "$url" "$dir"
  fi
}

clone_or_update https://github.com/Pachouli/2819p "$VENDOR/2819p"
clone_or_update https://github.com/sonigeez/actions-adfu "$VENDOR/actions-adfu"

echo "==> applying the endpoint patch to actions-adfu"
PATCH="$ROOT/patches/actions-adfu-endpoints.patch"
if git -C "$VENDOR/actions-adfu" apply --check "$PATCH" 2>/dev/null; then
  git -C "$VENDOR/actions-adfu" apply "$PATCH"
  echo "    applied"
elif git -C "$VENDOR/actions-adfu" apply --reverse --check "$PATCH" 2>/dev/null; then
  echo "    already applied"
else
  echo "    ERROR: the patch neither applies nor is already applied — upstream has moved." >&2
  echo "    Without it the bulk endpoints are hardcoded and every tool here fails on this device." >&2
  echo "    Fix patches/actions-adfu-endpoints.patch against the current upstream, then rerun." >&2
  exit 1
fi

echo "==> decoding the ADFU loaders out of the SDK package"
python3 "$ROOT/scripts/afi_decode.py" "$VENDOR/2819p/psp_rel/bin/AFI.bin" "$VENDOR/afi"

echo "==> installing Python dependencies into .venv"
[ -d "$ROOT/.venv" ] || python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --quiet --upgrade pip
"$ROOT/.venv/bin/pip" install --quiet -r "$ROOT/requirements-dev.txt"
"$ROOT/.venv/bin/pip" install --quiet -e "$VENDOR/actions-adfu"

echo "==> verifying what we fetched"
python3 "$ROOT/scripts/afi_decode.py" --verify "$VENDOR/CHECKSUMS" "$VENDOR"

cat <<'DONE'

Ready. Use .venv/bin/python for anything that talks to the device, and sudo for USB access:

  sudo .venv/bin/python tools/adfu_flash.py stock            # dry run, writes nothing
  .venv/bin/python -m pytest tests/ -q                       # the host-side checks

Read SAFETY.md before the first write.
DONE
