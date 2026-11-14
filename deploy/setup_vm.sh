#!/usr/bin/env bash
# Run on a fresh Ubuntu VM (AWS EC2 or GCP Compute Engine) from the project folder:
#   sudo bash deploy/setup_vm.sh
set -euo pipefail

APP_DIR=/opt/tcp-chat
PORT="${CHAT_PORT:-5000}"

echo "==> Installing Python and network tools for the experiments"
apt-get update -y
apt-get install -y python3 tcpdump iproute2 netcat-openbsd
# tc netem (Experiments 2 and 4) needs this module package on Ubuntu cloud kernels
apt-get install -y "linux-modules-extra-$(uname -r)" || echo "   (netem module package not found; Experiments 2/4 may need it)"

echo "==> Creating service user 'chat' (no login shell)"
id chat >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin chat

echo "==> Copying app to $APP_DIR"
mkdir -p "$APP_DIR"
cp protocol.py server.py client.py "$APP_DIR"/
# experiments/ and tests/ stay in your home-folder copy; run them from there
[ -f "$APP_DIR/.env" ] || cp .env.example "$APP_DIR/.env"
chown -R chat:chat "$APP_DIR"

echo "==> Installing systemd service"
cp deploy/chat-server.service /etc/systemd/system/chat-server.service
systemctl daemon-reload
systemctl enable --now chat-server
sleep 1
systemctl --no-pager status chat-server | head -n 5

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  echo "==> Opening port $PORT/tcp in ufw"
  ufw allow "$PORT"/tcp
fi

echo
echo "Done. Server listening on TCP port $PORT."
echo "Remember to also allow TCP $PORT in the cloud firewall (AWS security group / GCP firewall rule)."
echo "Live logs: sudo journalctl -u chat-server -f"
