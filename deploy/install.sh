#!/usr/bin/env bash
# Radarban VPS installer — Ubuntu 22.04 / 24.04, run as root:
#   curl -fsSL https://raw.githubusercontent.com/mhmdshahi/news-center/main/deploy/install.sh -o install.sh
#   sudo bash install.sh mcp.radarban.com
# Safe to re-run (updates code, keeps data and secrets).
set -euo pipefail

MCP_HOST="${1:-mcp.radarban.com}"
REPO="https://github.com/mhmdshahi/news-center.git"
BASE=/opt/radarban
APP=$BASE/app
DATA=$BASE/data
ENVF=/etc/radarban.env
USR=radar

[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }

echo "==> packages"
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip caddy ufw >/dev/null

echo "==> user + code"
id $USR >/dev/null 2>&1 || useradd --system --home $BASE --shell /usr/sbin/nologin $USR
mkdir -p $BASE
if [ -d $APP/.git ]; then git -C $APP pull -q --ff-only; else git clone -q $REPO $APP; fi
python3 -m venv $BASE/venv
$BASE/venv/bin/pip install -q --upgrade pip
$BASE/venv/bin/pip install -q -r $APP/requirements.txt -r $APP/server/requirements.txt

echo "==> data (first run: start from the repo's current library so codes / dedup continue)"
if [ ! -d $DATA ]; then
  mkdir -p $DATA
  for f in cursor.json events.json seen.json sources_status.json inbox.jsonl runs.jsonl dropped.jsonl outbox.json sent.json; do
    [ -f $APP/data/$f ] && cp $APP/data/$f $DATA/$f
  done
  # cards already sent to Telegram are not re-pushed in bulk: mark everything older than 24 h as delivered
  $BASE/venv/bin/python - <<'PY'
import json, time
from datetime import datetime, timezone
D = "/opt/radarban/data"
try:
    out = json.load(open(f"{D}/outbox.json"))["messages"]
except Exception:
    out = []
cut = time.time() - 24 * 3600
sent = {}
for m in out:
    try:
        t = datetime.fromisoformat(m["created"].replace("Z", "+00:00")).timestamp()
    except Exception:
        continue
    if t < cut:
        sent[m["id"]] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
json.dump({"sent": sent, "failed": {}}, open(f"{D}/panel_sent.json", "w"))
PY
fi
chown -R $USR:$USR $BASE

echo "==> secrets file $ENVF"
if [ ! -f $ENVF ]; then
  SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')
  cat > $ENVF <<EOF
RADAR_DATA=$DATA
RADAR_MCP_SECRET=$SECRET
RADAR_MCP_HOSTS=$MCP_HOST
RADAR_MCP_PORT=8787
# fill these two (PANEL_SECRET = ingest_secret in the panel's inc/config.php):
PANEL_URL=https://radarban.com/panel
PANEL_SECRET=
# optional
SEC_USER_AGENT=Radarban research bot admin@radarban.com
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
EOF
fi
chown root:$USR $ENVF; chmod 640 $ENVF

echo "==> systemd"
unit() { cat > /etc/systemd/system/$1; }
unit radar-mcp.service <<EOF
[Unit]
Description=Radarban MCP server
After=network-online.target
[Service]
User=$USR
EnvironmentFile=$ENVF
WorkingDirectory=$APP
ExecStart=$BASE/venv/bin/python server/mcp_server.py
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$DATA
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF
unit radar-relay.service <<EOF
[Unit]
Description=Radarban relay (panel pull route)
After=network-online.target
[Service]
User=$USR
EnvironmentFile=$ENVF
WorkingDirectory=$APP
ExecStart=$BASE/venv/bin/python server/relay.py
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$DATA
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF
oneshot() {  # name, command, OnCalendar
unit radar-$1.service <<EOF
[Unit]
Description=Radarban $1
[Service]
Type=oneshot
User=$USR
EnvironmentFile=$ENVF
WorkingDirectory=$APP
ExecStart=$BASE/venv/bin/python $2
TimeoutStartSec=600
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$DATA
PrivateTmp=true
EOF
unit radar-$1.timer <<EOF
[Unit]
Description=Radarban $1 timer
[Timer]
OnCalendar=$3
AccuracySec=5s
Persistent=true
[Install]
WantedBy=timers.target
EOF
}
oneshot collect "collector/collect.py" "*:0/5"
oneshot push    "server/push_panel.py" "*:*:30"
oneshot review  "scripts/review.py --hours 6" "*-*-* 00,06,12,18:03:00"
systemctl daemon-reload
systemctl restart radar-mcp.service radar-relay.service 2>/dev/null || true
systemctl enable --now radar-mcp.service radar-relay.service radar-collect.timer radar-push.timer radar-review.timer >/dev/null

echo "==> Caddy (HTTPS for $MCP_HOST)"
cat > /etc/caddy/Caddyfile <<EOF
$MCP_HOST {
	request_body {
		max_size 5MB
	}
	handle /relay/* {
		reverse_proxy 127.0.0.1:8788
	}
	handle {
		reverse_proxy 127.0.0.1:8787
	}
	log {
		output discard
	}
}
EOF
systemctl reload caddy || systemctl restart caddy

echo "==> firewall"
ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

source $ENVF
echo
echo "============================================================"
echo " Done. Connector URL (paste ONLY into claude.ai → Settings → Connectors → Add custom connector;"
echo " never into a chat):"
echo
echo "   https://$MCP_HOST/$RADAR_MCP_SECRET/mcp"
echo
echo " Next: put PANEL_SECRET into $ENVF  (nano $ENVF), then: systemctl restart radar-relay; systemctl start radar-push.service"
echo " Status: systemctl list-timers 'radar-*' ; journalctl -u radar-mcp -n 50"
echo "============================================================"
