# Shared registry values for shell entry points. Source this file with Bash.
# Resolve code beside this helper, independently of the configured corpus root.
_ledger_layout_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_ledger_layout_python="${LEDGER_PYTHON:-$_ledger_layout_root/.venv/bin/python}"
if [ ! -x "$_ledger_layout_python" ]; then
    _ledger_layout_python=python3
fi
read -r LEDGER_INDICES_SUBDIR LEDGER_IDENTITY_SUBDIR LEDGER_IDENTITY_PREFIX <<< "$(
    PYTHONPATH="$_ledger_layout_root${PYTHONPATH:+:$PYTHONPATH}" "$_ledger_layout_python" -c '
from ledger.layout import INDICES_SUBDIR, NOTE_LAYOUTS
identity = NOTE_LAYOUTS["identity"]
print(INDICES_SUBDIR, identity.subdir, identity.prefix)
'
)"
# ponytail: hooks must not write into the corpus root if Python is broken.
: "${LEDGER_INDICES_SUBDIR:=08_indices}" "${LEDGER_IDENTITY_SUBDIR:=01_identity}" "${LEDGER_IDENTITY_PREFIX:=id__}"
unset _ledger_layout_root _ledger_layout_python
