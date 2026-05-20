# Oracle VM Deployment Runbook

Deploys the biotech bot to an Oracle Cloud Always-Free ARM VM running Ubuntu.

## Files

- `bootstrap.sh` — one-shot installer; run once on a fresh VM after cloning the repo and scp-ing the .env files
- `biotech-bot.service` — systemd unit template (also installed by bootstrap.sh)
- `logrotate-biotech-bot` — logrotate config for `/var/log/biotech-bot.log`

## Prerequisites on the VM side

| Step | What |
|---|---|
| 1 | Oracle Cloud account with Always Free tier active |
| 2 | Compute instance: `VM.Standard.A1.Flex`, Canonical Ubuntu 22.04 or 24.04 (aarch64), 1-2 OCPUs, 6-12 GB RAM |
| 3 | Public IP assigned |
| 4 | **VCN Security List ingress rule for TCP/22 from your source IP** (or `0.0.0.0/0` for any) — Oracle does NOT open this by default in all images |
| 5 | SSH public key uploaded during VM creation |

### How to open VCN port 22 ingress

In the Oracle Cloud Console:

1. Open your instance → **Primary VNIC** → click the **Subnet** link
2. Click **Security Lists** → open the default security list for the VCN
3. **Add Ingress Rule**:
   - Stateless: No
   - Source CIDR: `0.0.0.0/0` (or just your home IP)
   - IP Protocol: TCP
   - Source Port Range: blank
   - Destination Port Range: `22`
4. Save

Within ~30 seconds, SSH should reach the VM.

## Deploy procedure (from your Mac)

```bash
# 1. SSH check (run from your Mac)
ssh -o ConnectTimeout=10 ubuntu@<PUBLIC_IP> 'echo OK'

# 2. Clone the repo on the VM
ssh ubuntu@<PUBLIC_IP> '
  mkdir -p ~/code &&
  cd ~/code &&
  git clone --recursive https://github.com/<YOUR_USER>/BIOTECH-TRADING-BOT.git
'

# 3. Copy .env files (must be done before running bootstrap)
scp .env ubuntu@<PUBLIC_IP>:~/code/BIOTECH-TRADING-BOT/.env
scp dexter/.env ubuntu@<PUBLIC_IP>:~/code/BIOTECH-TRADING-BOT/dexter/.env

# 4. Run bootstrap
ssh ubuntu@<PUBLIC_IP> 'bash ~/code/BIOTECH-TRADING-BOT/deploy/bootstrap.sh'

# 5. Tail logs to confirm "Bot Started" Discord ping
ssh ubuntu@<PUBLIC_IP> 'tail -f /var/log/biotech-bot.log'
```

## Smoke tests on the VM

After bootstrap, run these to verify everything works end-to-end:

```bash
ssh ubuntu@<PUBLIC_IP>
cd ~/code/BIOTECH-TRADING-BOT
source venv/bin/activate

# Clinical tracker (cheap, hits ClinicalTrials.gov + Discord)
python -m src.main --clinical

# Free Yahoo fallback tool
cd dexter && bun run scripts/test-yahoo-tool.ts AAPL

# Watchlist manager (reads/writes Neon)
bun run scripts/test-manage-watchlist.ts

# Daily pulse driver (free delta report + Discord + Neon)
bun run scripts/daily-pulse.ts
```

## Updating the deployment

```bash
ssh ubuntu@<PUBLIC_IP>
cd ~/code/BIOTECH-TRADING-BOT
git pull --recurse-submodules
source venv/bin/activate && pip install -r requirements.txt
cd dexter && bun install && cd ..
sudo systemctl restart biotech-bot
```

## Common gotchas

- **`bun: command not found` in systemd logs**: PATH wasn't picked up. Make sure the systemd unit `Environment="PATH=..."` includes `/home/ubuntu/.bun/bin`.
- **`Could not connect to display`**: Playwright needs the headless mode (which Dexter uses). If you see this, the browser tool was misconfigured.
- **`Insufficient credits` mid-week from FDS**: Top up at financialdatasets.ai. Dexter falls back to Alpaca + Yahoo automatically thanks to the SKILL.md fallback policy.
- **`bot keeps restarting every 15s`**: Check `tail -n 200 /var/log/biotech-bot.log`. Most common cause is a missing `.env` key.
- **First-run capacity error on Oracle**: Try a different region; the ARM Always Free shape is heavily oversubscribed in popular regions.

## Schedule

| Job | Cadence |
|---|---|
| Clinical Tracker | Every 6 hours (interval) |
| Dexter Weekly Brief | Monday 6:00 AM America/New_York |
| Dexter Daily Pulse | Tue-Fri 6:30 AM America/New_York |

All managed by APScheduler in [`src/main.py`](../src/main.py).
