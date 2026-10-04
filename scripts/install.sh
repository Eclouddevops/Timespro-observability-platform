#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Observability Platform — EC2 Install Script
# Ubuntu 20.04 / 22.04
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/<you>/observability-platform/main/scripts/install.sh | bash
#   — or —
#   git clone https://github.com/<you>/observability-platform.git
#   cd observability-platform && ./scripts/install.sh
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="/var/log/obs-install.log"

log()   { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# ── Root check ───────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || error "Run as root: sudo bash scripts/install.sh"

log "=== Observability Platform Installation ==="
log "Repo: $REPO_DIR"

# ── 1. System packages ────────────────────────────────────────────────
log "Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    curl wget git jq unzip apt-transport-https \
    ca-certificates gnupg lsb-release software-properties-common

# ── 2. Docker ─────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    log "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
    usermod -aG docker ubuntu 2>/dev/null || true
    log "Docker installed: $(docker --version)"
else
    log "Docker already installed: $(docker --version)"
fi

# ── 3. Docker Compose v2 ──────────────────────────────────────────────
if ! docker compose version &>/dev/null 2>&1; then
    log "Installing Docker Compose v2..."
    COMPOSE_VERSION="v2.27.0"
    mkdir -p /usr/local/lib/docker/cli-plugins
    curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
        -o /usr/local/lib/docker/cli-plugins/docker-compose
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
    log "Docker Compose: $(docker compose version)"
else
    log "Docker Compose already installed: $(docker compose version)"
fi

# ── 4. Node Exporter on the host ─────────────────────────────────────
if ! command -v node_exporter &>/dev/null; then
    log "Installing Node Exporter on host..."
    NE_VERSION="1.7.0"
    wget -q "https://github.com/prometheus/node_exporter/releases/download/v${NE_VERSION}/node_exporter-${NE_VERSION}.linux-amd64.tar.gz" \
        -O /tmp/node_exporter.tar.gz
    tar -xzf /tmp/node_exporter.tar.gz -C /tmp
    mv "/tmp/node_exporter-${NE_VERSION}.linux-amd64/node_exporter" /usr/local/bin/
    rm -rf "/tmp/node_exporter-${NE_VERSION}.linux-amd64" /tmp/node_exporter.tar.gz

    # Create systemd service
    cat > /etc/systemd/system/node_exporter.service <<'EOF'
[Unit]
Description=Prometheus Node Exporter
After=network.target

[Service]
User=nobody
ExecStart=/usr/local/bin/node_exporter \
    --collector.systemd \
    --collector.processes \
    --web.listen-address=:9100
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now node_exporter
    log "Node Exporter running on :9100"
fi

# ── 5. Create .env if missing ────────────────────────────────────────
if [[ ! -f "$REPO_DIR/.env" ]]; then
    log "Creating .env from .env.example..."
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    log "⚠️  Edit $REPO_DIR/.env with your credentials before continuing!"
    log "   Required: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, GRAFANA_ADMIN_PASSWORD"
fi

# ── 6. Generate self-signed SSL if certs not present ─────────────────
SSL_DIR="$REPO_DIR/nginx/ssl"
mkdir -p "$SSL_DIR"
if [[ ! -f "$SSL_DIR/fullchain.pem" ]]; then
    log "Generating self-signed SSL certificate (replace with real cert for production)..."
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$SSL_DIR/privkey.pem" \
        -out "$SSL_DIR/fullchain.pem" \
        -subj "/C=US/ST=State/L=City/O=Org/CN=localhost" 2>/dev/null
    log "Self-signed cert created. Replace with Let's Encrypt for production."
fi

# ── 7. Start the stack ────────────────────────────────────────────────
log "Starting observability stack..."
cd "$REPO_DIR"
docker compose pull --quiet
docker compose up -d

# ── 8. Wait for Grafana ───────────────────────────────────────────────
log "Waiting for Grafana to be ready..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:3000/api/health &>/dev/null; then
        log "Grafana is ready ✓"
        break
    fi
    sleep 3
done

# ── 9. Open firewall ──────────────────────────────────────────────────
if command -v ufw &>/dev/null; then
    ufw allow 80/tcp  comment "HTTP → HTTPS redirect" 2>/dev/null || true
    ufw allow 443/tcp comment "Grafana HTTPS" 2>/dev/null || true
    ufw allow 9090/tcp comment "Prometheus" 2>/dev/null || true
    ufw allow 9100/tcp comment "Node Exporter" 2>/dev/null || true
fi

# ── Done ──────────────────────────────────────────────────────────────
log ""
log "════════════════════════════════════════════════════"
log "  Installation complete!"
log ""
log "  Grafana:      http://$(curl -sf http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || hostname -I | awk '{print $1}'):3000"
log "  Prometheus:   http://localhost:9090"
log "  Alertmanager: http://localhost:9093"
log "  AI Agent:     http://localhost:8888"
log ""
log "  Default Grafana credentials:"
log "    User: admin"
log "    Pass: (see GRAFANA_ADMIN_PASSWORD in .env)"
log ""
log "  Next steps:"
log "    1. Edit .env with your AWS credentials"
log "    2. Edit prometheus/targets/*.yml to add your resources"
log "    3. Run: docker compose restart prometheus"
log "════════════════════════════════════════════════════"
