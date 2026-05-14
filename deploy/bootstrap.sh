#!/usr/bin/env bash
# Bootstrap a fresh Oracle Cloud Ubuntu 22.04/24.04 ARM VM for the biotech bot.
#
# Run this script ONCE on the VM after SSH'ing in as the `ubuntu` user.
# Assumes:
#   - You already have ~/code/BIOTECH-TRADING-BOT cloned with --recursive
#   - You have already scp'd .env and dexter/.env into the repo

set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/code/BIOTECH-TRADING-BOT}"
SERVICE_USER="${USER}"
LOG_FILE="/var/log/biotech-bot.log"

echo "==> [1/9] Verifying repo present at $REPO_DIR"
if [ ! -d "$REPO_DIR" ]; then
  echo "ERROR: $REPO_DIR not found. Clone with:"
  echo "  git clone --recursive https://github.com/<YOU>/BIOTECH-TRADING-BOT.git $REPO_DIR"
  exit 1
fi
if [ ! -f "$REPO_DIR/.env" ] || [ ! -f "$REPO_DIR/dexter/.env" ]; then
  echo "ERROR: .env files missing. scp them from your Mac first:"
  echo "  scp .env ubuntu@<ip>:$REPO_DIR/.env"
  echo "  scp dexter/.env ubuntu@<ip>:$REPO_DIR/dexter/.env"
  exit 1
fi

echo "==> [2/9] Updating apt and installing system packages"
sudo apt update
sudo DEBIAN_FRONTEND=noninteractive apt install -y \
  git python3 python3-venv python3-pip python3-dev \
  build-essential libpq-dev curl unzip ca-certificates \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
  libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
  libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64 || \
sudo DEBIAN_FRONTEND=noninteractive apt install -y \
  git python3 python3-venv python3-pip python3-dev \
  build-essential libpq-dev curl unzip ca-certificates \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
  libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
  libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2

echo "==> [3/9] Installing Bun (idempotent)"
if ! command -v bun >/dev/null 2>&1; then
  curl -fsSL https://bun.com/install | bash
fi
export PATH="$HOME/.bun/bin:$PATH"
if ! grep -q '.bun/bin' "$HOME/.bashrc"; then
  echo 'export PATH="$HOME/.bun/bin:$PATH"' >> "$HOME/.bashrc"
fi
bun --version

echo "==> [4/9] Creating Python venv + installing requirements"
cd "$REPO_DIR"
if [ ! -d "venv" ]; then
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo "==> [5/9] Installing Dexter (Bun) dependencies"
cd "$REPO_DIR/dexter"
"$HOME/.bun/bin/bun" install
cd "$REPO_DIR"

echo "==> [6/9] Preparing log file"
sudo touch "$LOG_FILE"
sudo chown "$SERVICE_USER:$SERVICE_USER" "$LOG_FILE"

echo "==> [7/9] Installing systemd unit"
sudo tee /etc/systemd/system/biotech-bot.service >/dev/null <<EOF
[Unit]
Description=Biotech Trading Bot (APScheduler + Dexter weekly brief)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$REPO_DIR
Environment="PATH=$REPO_DIR/venv/bin:$HOME/.bun/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=$REPO_DIR/venv/bin/python -m src.main
Restart=always
RestartSec=15
StandardOutput=append:$LOG_FILE
StandardError=append:$LOG_FILE

[Install]
WantedBy=multi-user.target
EOF

echo "==> [8/9] Installing logrotate config"
sudo tee /etc/logrotate.d/biotech-bot >/dev/null <<'EOF'
/var/log/biotech-bot.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
    copytruncate
}
EOF

echo "==> [9/9] Enabling and starting service"
sudo systemctl daemon-reload
sudo systemctl enable biotech-bot
sudo systemctl restart biotech-bot
sleep 3
sudo systemctl --no-pager status biotech-bot || true

echo ""
echo "==> Done. Tail logs with:"
echo "   tail -f $LOG_FILE"
echo ""
echo "==> If you see 'Bot Started' in Discord, the deployment is live."
