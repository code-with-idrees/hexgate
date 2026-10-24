#!/usr/bin/env sh
# Install Hexgate from source into an isolated environment.
#
#   curl -fsSL https://raw.githubusercontent.com/code-with-idrees/hexgate/main/scripts/install.sh | sh
#
# Prefers pipx, then uv, then a plain venv. Override the source with
# HEXGATE_REPO, or the venv location with HEXGATE_INSTALL_DIR.

set -eu

REPO="${HEXGATE_REPO:-https://github.com/code-with-idrees/hexgate.git}"
PACKAGE="git+${REPO}"
INSTALL_DIR="${HEXGATE_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/hexgate}"
BIN_DIR="${HEXGATE_BIN_DIR:-$HOME/.local/bin}"

say() {
    printf '%s\n' "$*"
}

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

need_python() {
    command -v "$1" >/dev/null 2>&1 || return 1
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

if command -v pipx >/dev/null 2>&1; then
    say "Installing Hexgate with pipx..."
    pipx install --force "$PACKAGE"
    say ""
    say "Installed. Run: hexgate init && hexgate serve"
    exit 0
fi

if command -v uv >/dev/null 2>&1; then
    say "Installing Hexgate with uv..."
    uv tool install --force "$PACKAGE"
    say ""
    say "Installed. Run: hexgate init && hexgate serve"
    exit 0
fi

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
    if need_python "$candidate"; then
        PYTHON="$candidate"
        break
    fi
done
[ -n "$PYTHON" ] || fail "Python 3.11+ is required (install it, or install pipx/uv)."

say "Using $PYTHON"
mkdir -p "$INSTALL_DIR" "$BIN_DIR"
"$PYTHON" -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python" -m pip install --upgrade pip >/dev/null
say "Installing Hexgate into $INSTALL_DIR/venv..."
"$INSTALL_DIR/venv/bin/python" -m pip install --upgrade "$PACKAGE"

ln -sf "$INSTALL_DIR/venv/bin/hexgate" "$BIN_DIR/hexgate"
ln -sf "$INSTALL_DIR/venv/bin/hg" "$BIN_DIR/hg"

say ""
say "Installed: $BIN_DIR/hexgate"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        say "Add it to your PATH:"
        say "  export PATH=\"$BIN_DIR:\$PATH\""
        ;;
esac
say "Then run: hexgate init && hexgate serve"
