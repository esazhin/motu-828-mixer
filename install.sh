#!/usr/bin/env bash
# One-step install of the MOTU 828 mixer on Linux.
#
#   ./install.sh               install: Python 3.14 venv, dependencies, config.local; check amidi and the 828
#   ./install.sh --run         ... then start the service in this terminal
#   ./install.sh --systemd     ... and install + enable the systemd user unit (starts now and with your login)
#   ./install.sh --launcher    ... and install the desktop launcher (app menu / dock entry)
#   ./install.sh --all         --systemd --launcher
#   ./install.sh --dev         also install the test dependencies (pytest, httpx)
#
# Safe to run again: every step is skipped when already done. Nothing is written outside this repository except
# the systemd unit (~/.config/systemd/user), the launcher (~/.local/bin, ~/.local/share/applications) and, when
# uv is missing and you agree, uv itself (~/.local/bin). No root needed.
set -euo pipefail

REPO="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PYTHON_VERSION="3.14"
RUN=0 SYSTEMD=0 LAUNCHER=0 DEV=0
for arg in "$@"; do
    case "$arg" in
        --run) RUN=1 ;;
        --systemd) SYSTEMD=1 ;;
        --launcher) LAUNCHER=1 ;;
        --all) SYSTEMD=1; LAUNCHER=1 ;;
        --dev) DEV=1 ;;
        -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "install.sh: unknown option $arg (try --help)" >&2; exit 2 ;;
    esac
done

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Linux" ] || fail "this installer is for Linux (the 828 is driven through ALSA rawmidi)"
cd "$REPO"

# ---- uv: creates the venv and fetches Python 3.14 itself when the system has none -------------------------------
bold "Python"
if ! command -v uv >/dev/null 2>&1; then
    warn "uv (https://docs.astral.sh/uv/) is not installed; it manages the Python version and the dependencies."
    if [ -t 0 ]; then
        read -r -p "  Install uv for this user with the official installer (curl https://astral.sh/uv/install.sh | sh)? [y/N] " answer
    else
        answer=n
    fi
    case "$answer" in
        [yY]*) curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH" ;;
        *) fail "install uv (or run: pip install --user uv), then run ./install.sh again" ;;
    esac
fi
command -v uv >/dev/null 2>&1 || fail "uv is still not on PATH; open a new shell or add ~/.local/bin to PATH"
ok "uv $(uv --version | awk '{print $2}')"

if [ -x .venv/bin/python ] && .venv/bin/python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 14) else 1)" 2>/dev/null; then
    ok ".venv already has $(.venv/bin/python --version)"
else
    [ -e .venv ] && { warn ".venv is not Python $PYTHON_VERSION: recreating it"; rm -rf .venv; }
    uv venv --quiet --python "$PYTHON_VERSION" .venv
    ok "created .venv with $(.venv/bin/python --version)"
fi
if [ "$DEV" = 1 ]; then
    uv pip install --quiet --python .venv/bin/python -e '.[dev]'
    ok "installed the service and the test dependencies"
else
    uv pip install --quiet --python .venv/bin/python -e .
    ok "installed the service (fastapi, uvicorn, websockets)"
fi

# ---- configuration -----------------------------------------------------------------------------------------------
bold "Configuration"
if [ -f config.local ]; then
    ok "config.local exists (left as it is)"
else
    cp config.local.example config.local
    ok "created config.local from config.local.example (port 8280, listening on all interfaces)"
fi
PORT="$(sed -n 's/^[[:space:]]*MOTU_HTTP_PORT[[:space:]]*=[[:space:]]*//p' config.local | tail -1)"
PORT="${MOTU_HTTP_PORT:-${PORT:-8280}}"

# ---- ALSA and the unit -------------------------------------------------------------------------------------------
bold "The 828"
if command -v amidi >/dev/null 2>&1; then
    ok "amidi found"
    if amidi -l 2>/dev/null | grep -q '828 COM'; then
        ok "the 828's control port (828 COM) is present"
        dev="$(amidi -l 2>/dev/null | awk '/828 COM/ {print $2}' | head -1)"      # e.g. hw:3,0,1
        card="${dev#hw:}"; card="${card%%,*}"
        node="$(ls /dev/snd/midiC"${card}"D* 2>/dev/null | head -1)"
        if [ -n "$node" ] && [ -r "$node" ] && [ -w "$node" ]; then
            ok "$node is readable and writable by $(id -un)"
        elif [ -n "$node" ]; then
            warn "$node is not accessible by $(id -un): add yourself to the 'audio' group (sudo usermod -aG audio $(id -un), then log in again), or run from a desktop session"
        fi
    else
        warn "no port named '828 COM' right now: connect and power the 828, or set MOTU_PORT_NAME in config.local to the name amidi -l shows"
    fi
else
    warn "amidi is missing: install alsa-utils (Debian/Ubuntu: sudo apt install alsa-utils · Fedora: sudo dnf install alsa-utils · Arch: sudo pacman -S alsa-utils)"
fi

# ---- optional: desktop launcher and systemd user unit ------------------------------------------------------------
if [ "$LAUNCHER" = 1 ]; then
    bold "Desktop launcher"
    mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
    ln -sfn "$REPO/scripts/motu-mixer-start" "$HOME/.local/bin/motu-mixer-start"
    cp deploy/motu-mixer.desktop "$HOME/.local/share/applications/motu-mixer.desktop"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    ok "'MOTU 828 Mixer' is in the application menu; it starts the service if needed and opens the page"
    case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "$HOME/.local/bin is not on your PATH; the menu entry still works, the command motu-mixer-start needs it" ;; esac
fi

if [ "$SYSTEMD" = 1 ]; then
    bold "systemd user unit"
    command -v systemctl >/dev/null 2>&1 || fail "systemctl not found; start the service with ./install.sh --run or scripts/motu-mixer-start instead"
    mkdir -p "$HOME/.config/systemd/user"
    sed "s|<repo>|$REPO|g" deploy/motu-mixer.service > "$HOME/.config/systemd/user/motu-mixer.service"
    systemctl --user daemon-reload
    systemctl --user enable --now motu-mixer.service
    sleep 1
    if systemctl --user is-active --quiet motu-mixer.service; then
        ok "motu-mixer.service is running and enabled for your login (journalctl --user -u motu-mixer -f)"
    else
        fail "motu-mixer.service did not start: systemctl --user status motu-mixer"
    fi
fi

# ---- done --------------------------------------------------------------------------------------------------------
bold "Done"
echo "  Open http://localhost:$PORT/ (or http://$(hostname):$PORT/ from another device on your network)."
if [ "$SYSTEMD" = 1 ]; then
    :
elif [ "$RUN" = 1 ]; then
    echo "  Starting the service in this terminal (Ctrl+C stops it)."
    echo
    exec .venv/bin/python -m _motu_mixer
else
    echo "  Start it with:  .venv/bin/python -m _motu_mixer      (or ./install.sh --run, or scripts/motu-mixer-start)"
    echo "  Start it with your login instead:  ./install.sh --systemd"
fi
