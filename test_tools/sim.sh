#!/usr/bin/env bash
#
# One-command Betaflight SITL + Gazebo bring-up.
#
#   sim.sh setup     one-time: build firmware, fetch and build the Gazebo plugin,
#                    provision the EEPROM. Idempotent — safe to re-run as a repair.
#   sim.sh up        start Gazebo, SITL and the RC bridge in a tmux session.
#   sim.sh down      stop the session and any stragglers holding the sim ports.
#   sim.sh status    report what is running and which ports are bound.
#
# Gazebo runs inside the gz-env distrobox, which uses host networking, so the
# UDP links to the host-side SITL process need no plumbing.
#
# Exit codes: 0 ok, 1 failure, 2 precondition you need to fix.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$SCRIPT_DIR/.." && pwd)

AEROLOOP_DIR=${AEROLOOP_DIR:-$HOME/dev/aeroloop_gazebo}
RUN_DIR=${RUN_DIR:-$HOME/.betaflight-sim}
SESSION=${SESSION:-bfsim}
WORLD=${WORLD:-test_betaflight.sdf}
BOX=${BOX:-gz-env}

BINARY=$REPO/obj/main/betaflight_SITL_SITL_GAZEBO.elf
PLUGIN_SO=$AEROLOOP_DIR/plugins/build/libBetaflightPlugin.so
CONTAINERFILE=$REPO/.devcontainer/containerfile.gazebo
ARM_CFG=$SCRIPT_DIR/arm.txt
RC_BRIDGE=$SCRIPT_DIR/rc_joystick_bridge.py
AEROLOOP_URL=https://github.com/betaflight/aeroloop_gazebo.git

# ports: 9002 SITL->Gazebo, 9003 Gazebo->SITL, 9004 RC->SITL, 5761 MSP
SIM_UDP_PORTS=(9002 9003 9004)

say()  { printf '  \033[32m%-9s\033[0m %s\n' "$1" "$2"; }
skip() { printf '  \033[90m%-9s %s\033[0m\n' "skip" "$1"; }
info() { printf '%s\n' "$1"; }
die()  { printf '\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }
need() { printf '\033[33mnot ready:\033[0m %s\n' "$1" >&2; exit 2; }

in_box() { distrobox enter "$BOX" -- bash -lc "$1"; }

# Who holds a UDP port? Prints "pid/name" or nothing.
udp_owner() {
    ss -lnupH "sport = :$1" 2>/dev/null \
        | grep -oP 'users:\(\("\K[^"]+(?=",pid=\d+)|pid=\K\d+' \
        | paste -sd/ - || true
}

# ---------------------------------------------------------------- setup

cmd_setup() {
    info "setup"

    # 1. firmware
    if [[ ! -x $BINARY ]]; then
        say "build" "firmware (missing)"
        make -C "$REPO" CONFIG=SITL_GAZEBO
    elif [[ -n $(find "$REPO/src" -newer "$BINARY" -print -quit 2>/dev/null) ]]; then
        say "build" "firmware (src/ is newer)"
        make -C "$REPO" CONFIG=SITL_GAZEBO
    else
        skip "firmware up to date"
    fi

    # 2. plugin source, pinned to whatever the devcontainer pins
    local ref
    ref=$(sed -n 's/^ARG AEROLOOP_REF=\(.*\)$/\1/p' "$CONTAINERFILE" | head -1)
    [[ -n $ref ]] || die "no AEROLOOP_REF in $CONTAINERFILE"

    if [[ ! -d $AEROLOOP_DIR/.git ]]; then
        say "clone" "$AEROLOOP_URL -> $AEROLOOP_DIR"
        git clone -b gz "$AEROLOOP_URL" "$AEROLOOP_DIR"
    fi
    if [[ $(git -C "$AEROLOOP_DIR" rev-parse HEAD) != "$ref" ]]; then
        say "checkout" "aeroloop_gazebo @ ${ref:0:12}"
        git -C "$AEROLOOP_DIR" fetch --quiet origin
        git -C "$AEROLOOP_DIR" checkout --quiet "$ref"
    else
        skip "aeroloop_gazebo already at ${ref:0:12}"
    fi

    # 3. plugin binary, built inside the container that has the gz headers
    if [[ ! -f $PLUGIN_SO ]]; then
        say "build" "BetaflightPlugin inside $BOX"
        in_box "set -e
                cd '$AEROLOOP_DIR/plugins'
                mkdir -p build && cd build
                cmake .. && make -j\$(nproc)"
        [[ -f $PLUGIN_SO ]] || die "plugin build produced no $PLUGIN_SO"
    else
        skip "BetaflightPlugin already built"
    fi

    # 4. EEPROM, provisioned headlessly so Configurator is not needed to arm
    mkdir -p "$RUN_DIR"
    if [[ ! -f $RUN_DIR/eeprom.bin ]]; then
        say "provision" "eeprom.bin from $(basename "$ARM_CFG")"
        if ! ( cd "$RUN_DIR" && "$BINARY" --config "$ARM_CFG" ) >"$RUN_DIR/provision.log" 2>&1; then
            cat "$RUN_DIR/provision.log" >&2
            die "provisioning failed, see $RUN_DIR/provision.log"
        fi
        [[ -f $RUN_DIR/eeprom.bin ]] || { cat "$RUN_DIR/provision.log" >&2
            die "provisioning produced no eeprom.bin"; }
    else
        skip "eeprom.bin exists (delete it to re-provision)"
    fi

    info "ready — run '$(basename "$0") up'"
}

# ---------------------------------------------------------------- up

cmd_up() {
    local headless="" with_rc=1 rc_args=()
    while [[ $# -gt 0 ]]; do
        case $1 in
            --headless) headless="-s --headless-rendering"; shift ;;
            --no-rc)    with_rc=0; shift ;;
            --world)    WORLD=$2; shift 2 ;;
            --)         shift; rc_args=("$@"); break ;;
            *)          die "unknown option $1" ;;
        esac
    done

    if tmux has-session -t "$SESSION" 2>/dev/null; then
        info "session '$SESSION' already up — attaching"
        exec tmux attach -t "$SESSION"
    fi

    [[ -x $BINARY   ]] || need "no SITL binary. Run '$(basename "$0") setup'."
    [[ -f $PLUGIN_SO ]] || need "no Gazebo plugin. Run '$(basename "$0") setup'."
    [[ -f $RUN_DIR/eeprom.bin ]] || need "no eeprom.bin. Run '$(basename "$0") setup'."

    local owner
    owner=$(udp_owner 9002)
    [[ -z $owner ]] || need "UDP 9002 is held by $owner — the Gazebo plugin cannot bind. Run '$(basename "$0") down'."

    if (( with_rc )) && ! compgen -G "/dev/input/js*" >/dev/null; then
        with_rc=0
        info "note: no /dev/input/js* — RC pane skipped, Gazebo and SITL will still start"
    fi

    mkdir -p "$RUN_DIR"

    # Pane commands live in their own scripts: no nested quoting, and each one
    # can be re-run by hand when a pane misbehaves.
    cat > "$RUN_DIR/gazebo.sh" <<EOF
#!/usr/bin/env bash
cd "$AEROLOOP_DIR"
export SDF_PATH="\$PWD/models:\${SDF_PATH:-}"
export GZ_SIM_RESOURCE_PATH="\$PWD/worlds:\${GZ_SIM_RESOURCE_PATH:-}"
export GZ_SIM_SYSTEM_PLUGIN_PATH="\$PWD/plugins/build:\${GZ_SIM_SYSTEM_PLUGIN_PATH:-}"
# absolute path: ~/.local/bin/gz is a distrobox wrapper and would recurse here
exec /usr/bin/gz sim -r -v 4 $headless "worlds/$WORLD"
EOF

    cat > "$RUN_DIR/sitl.sh" <<EOF
#!/usr/bin/env bash
cd "$RUN_DIR"
echo "waiting for the Gazebo plugin to bind UDP 9002..."
for _ in \$(seq 1 60); do
    ss -lnuH "sport = :9002" | grep -q . && break
    sleep 0.5
done
exec "$BINARY"
EOF

    printf '#!/usr/bin/env bash\nsleep 2\nexec %q' "$RC_BRIDGE" > "$RUN_DIR/rc.sh"
    for a in ${rc_args[@]+"${rc_args[@]}"}; do printf ' %q' "$a" >> "$RUN_DIR/rc.sh"; done
    printf '\n' >> "$RUN_DIR/rc.sh"

    chmod +x "$RUN_DIR"/{gazebo.sh,sitl.sh,rc.sh}

    # Gazebo lives in the container; everything else runs on the host.
    local gz_pane="distrobox enter '$BOX' -- bash -lc '$RUN_DIR/gazebo.sh'"
    tmux new-session -d -s "$SESSION" -n sim -c "$RUN_DIR" "$gz_pane"
    tmux set-option -t "$SESSION" remain-on-exit on          # keep crashes readable
    tmux set-option -t "$SESSION" pane-border-status top
    tmux select-pane  -t "$SESSION:sim.0" -T gazebo
    tmux split-window -t "$SESSION:sim" -v -c "$RUN_DIR" "$RUN_DIR/sitl.sh"
    tmux select-pane  -t "$SESSION:sim.1" -T sitl
    if (( with_rc )); then
        tmux split-window -t "$SESSION:sim" -v -c "$RUN_DIR" "$RUN_DIR/rc.sh"
        tmux select-pane -t "$SESSION:sim.2" -T rc
    fi
    tmux select-layout -t "$SESSION:sim" even-vertical

    info "session '$SESSION' started (world: $WORLD)"
    exec tmux attach -t "$SESSION"
}

# ---------------------------------------------------------------- down

cmd_down() {
    info "down"
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        say "kill" "tmux session '$SESSION'"
        tmux kill-session -t "$SESSION"
    else
        skip "no tmux session '$SESSION'"
    fi

    # Only ever kill processes we recognise — never whatever happens to hold a
    # port, and never a shell that merely mentions these names in its arguments.
    local killed=0 pid name
    for pid in $(pgrep -f '(^|/)(gz|ruby) sim |betaflight_SITL_SITL_GAZEBO|rc_joystick_bridge\.py' 2>/dev/null || true); do
        [[ $pid == "$$" || $pid == "$PPID" ]] && continue
        name=$(ps -p "$pid" -o comm= 2>/dev/null || echo "?")
        case $name in
            gz|ruby|gz-sim*|betaflight_SITL|python3|rc_joystick*) ;;
            *) continue ;;        # anything else is a false positive, leave it
        esac
        say "kill" "$name ($pid)"
        kill "$pid" 2>/dev/null || true
        killed=1
    done
    (( killed )) || skip "no stray sim processes"
}

# ---------------------------------------------------------------- status

cmd_status() {
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        info "session '$SESSION': up"
        tmux list-panes -t "$SESSION:sim" -F '  pane #{pane_index} #{pane_title}  #{?pane_dead,dead,running}'
    else
        info "session '$SESSION': down"
    fi
    info "ports"
    local p owner
    for p in "${SIM_UDP_PORTS[@]}"; do
        owner=$(udp_owner "$p")
        printf '  udp %-5s %s\n' "$p" "${owner:-—}"
    done
    owner=$(ss -lntpH "sport = :5761" 2>/dev/null | grep -oP 'pid=\K\d+' | paste -sd/ - || true)
    printf '  tcp %-5s %s\n' 5761 "${owner:-—}"
}

# ----------------------------------------------------------------

case ${1-} in
    setup)  shift; cmd_setup "$@" ;;
    up)     shift; cmd_up "$@" ;;
    down)   shift; cmd_down "$@" ;;
    status) shift; cmd_status "$@" ;;
    *)      sed -n '3,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 2 ;;
esac
