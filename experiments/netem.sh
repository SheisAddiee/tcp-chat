#!/usr/bin/env bash
# Controlled network impairment with Linux tc netem.
# Applies ONLY to the loopback interface by default, so it affects tests on
# this machine and nothing else. Use only on machines you own.
#
#   sudo bash experiments/netem.sh loss 10          # drop 10% of packets
#   sudo bash experiments/netem.sh delay 50         # add 50 ms each way (RTT +100 ms on lo)
#   sudo bash experiments/netem.sh both 50 5        # 50 ms delay + 5% loss
#   sudo bash experiments/netem.sh status
#   sudo bash experiments/netem.sh off              # ALWAYS run this when finished
set -euo pipefail

IFACE="${IFACE:-lo}"
if [ "$IFACE" != "lo" ]; then
  echo "WARNING: applying netem to $IFACE affects ALL traffic on it (including your SSH session)."
  read -r -p "Continue? [y/N] " ok; [ "$ok" = "y" ] || exit 1
fi

ensure_module() {
  if ! modprobe sch_netem 2>/dev/null; then
    echo "netem kernel module missing. On Ubuntu cloud images install it with:"
    echo "  sudo apt-get install -y linux-modules-extra-\$(uname -r)"
    exit 1
  fi
}

apply() {
  ensure_module
  tc qdisc replace dev "$IFACE" root netem "$@"
  echo "netem on $IFACE: $*"
  tc qdisc show dev "$IFACE"
}

case "${1:-status}" in
  loss)   apply loss "${2:?percent}%";;
  delay)  apply delay "${2:?ms}ms";;
  both)   apply delay "${2:?ms}ms" loss "${3:?percent}%";;
  off)    tc qdisc del dev "$IFACE" root 2>/dev/null || true; echo "netem removed from $IFACE";;
  status) tc qdisc show dev "$IFACE";;
  *)      echo "usage: $0 loss <pct> | delay <ms> | both <ms> <pct> | off | status"; exit 1;;
esac
