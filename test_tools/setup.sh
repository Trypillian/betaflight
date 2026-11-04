#!/usr/bin/env bash
#
# Host and container prerequisites for sim.sh.
#
#   setup.sh          check the host, build the gz-env container, then hand off
#                     to 'sim.sh setup'. Idempotent — safe to re-run as a repair.
#   setup.sh --check  report what is missing and change nothing.
#   setup.sh --no-sim stop after the container; skip 'sim.sh setup'.
#
# Division of labour: this script owns everything *outside* the betaflight repo
# (host packages, the distrobox, Gazebo itself); sim.sh setup owns everything
# inside it (firmware, plugin, EEPROM).
#
# Host packages are reported, never installed: sudo on the host is interactive
# here, and guessing at someone's package manager is worse than telling them
# what to run. Inside the box sudo is passwordless, so apt runs unattended.
#
# Exit codes: 0 ok, 1 failure, 2 precondition you need to fix.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$SCRIPT_DIR/.." && pwd)

BOX=${BOX:-gz-env}
BOX_IMAGE=${BOX_IMAGE:-ubuntu:24.04}
CONTAINERFILE=$REPO/.devcontainer/containerfile.gazebo
SIM=$SCRIPT_DIR/sim.sh

say()  { printf '  \033[32m%-9s\033[0m %s\n' "$1" "$2"; }
skip() { printf '  \033[90m%-9s %s\033[0m\n' "skip" "$1"; }
info() { printf '%s\n' "$1"; }
die()  { printf '\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }
need() { printf '\033[33mnot ready:\033[0m %s\n' "$1" >&2; exit 2; }

in_box() { distrobox enter "$BOX" -- bash -lc "$1"; }

# Host commands sim.sh shells out to, and the Fedora package that carries each.
# "cmd:package" — the package name is only ever printed, never acted on.
HOST_NEEDS=(
    podman:podman          # distrobox backend
    distrobox:distrobox    # runs Gazebo in a container
    tmux:tmux              # sim.sh up drives panes with it
    git:git                # clones aeroloop_gazebo
    make:make              # builds the SITL firmware
    gcc:gcc                # ditto
    ss:iproute             # sim.sh checks who holds the UDP ports
    python3:python3        # the RC bridge (stdlib only, no pip)
)

# Gazebo Harmonic and the headers the plugin links against, mirroring
# .devcontainer/containerfile.gazebo. Kept as a list here rather than parsed
# out of the containerfile: that file is a multi-stage build, not a manifest.
BOX_PKGS=(
    build-essential cmake pkg-config git curl ca-certificates python3
    cppzmq-dev
    gz-harmonic libgz-sim8-dev libgz-plugin2-dev libgz-math7-dev
    libgz-transport13-dev libgz-msgs10-dev
)
# One package that, if present, means the box is already provisioned.
BOX_SENTINEL=libgz-sim8-dev

CHECK_ONLY=0
RUN_SIM=1
while [[ $# -gt 0 ]]; do
    case $1 in
        --check)  CHECK_ONLY=1; shift ;;
        --no-sim) RUN_SIM=0; shift ;;
        -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown option $1 (try --help)" ;;
    esac
done

# ---------------------------------------------------------------- host

info "host"

missing=()
for entry in "${HOST_NEEDS[@]}"; do
    cmd=${entry%%:*}; pkg=${entry#*:}
    if command -v "$cmd" >/dev/null 2>&1; then
        skip "$cmd"
    else
        say "missing" "$cmd (package: $pkg)"
        missing+=("$pkg")
    fi
done

if (( ${#missing[@]} )); then
    info ""
    need "install these first, then re-run:
       sudo dnf install ${missing[*]}
   (on Debian/Ubuntu the names differ: iproute2 for ss, g++ alongside gcc)"
fi

[[ -f $CONTAINERFILE ]] || die "missing $CONTAINERFILE — sim.sh reads the pinned plugin revision from it"

# ---------------------------------------------------------------- container

info "container"

box_exists() { podman container exists "$BOX" 2>/dev/null; }

if box_exists; then
    skip "distrobox '$BOX' exists"
elif (( CHECK_ONLY )); then
    say "would create" "distrobox '$BOX' from $BOX_IMAGE"
else
    say "create" "distrobox '$BOX' from $BOX_IMAGE"
    distrobox create --name "$BOX" --image "$BOX_IMAGE" --yes
fi

box_provisioned() {
    box_exists && in_box "dpkg -s $BOX_SENTINEL" >/dev/null 2>&1
}

if box_provisioned; then
    skip "Gazebo Harmonic installed in '$BOX'"
elif (( CHECK_ONLY )); then
    say "would install" "Gazebo Harmonic + libgz-*-dev in '$BOX'"
else
    say "provision" "Gazebo Harmonic in '$BOX' (several minutes, downloads ~1 GB)"
    # The OSRF archive is the only source of gz-harmonic on 24.04.
    in_box "set -e
        sudo apt-get update
        sudo apt-get install -y --no-install-recommends curl ca-certificates
        curl -fsSL https://packages.osrfoundation.org/gazebo.gpg |
            sudo dd of=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg
        echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable noble main' |
            sudo tee /etc/apt/sources.list.d/gazebo-stable.list >/dev/null
        sudo apt-get update
        sudo apt-get install -y --no-install-recommends ${BOX_PKGS[*]}" \
        || die "provisioning '$BOX' failed; re-run, or enter it with 'distrobox enter $BOX' to look"
    box_provisioned || die "'$BOX' still has no $BOX_SENTINEL after provisioning"
fi

if (( CHECK_ONLY )); then
    info ""
    info "check only — nothing was changed"
    exit 0
fi

# Spell gz absolutely: inside the box ~/.local/bin/gz is a distrobox wrapper
# that would re-enter the container and recurse.
gz_version=$(in_box "/usr/bin/gz sim --versions 2>/dev/null | head -1" | tr -d '\r')
[[ -n $gz_version ]] || die "'/usr/bin/gz sim' does not run inside '$BOX'"
say "gazebo" "sim $gz_version"

# ---------------------------------------------------------------- handoff

if (( RUN_SIM )); then
    info ""
    [[ -x $SIM ]] || die "missing $SIM"
    exec "$SIM" setup
fi

info ""
info "environment ready — run '$(basename "$SIM") setup'"
