#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Install Node Exporter on a remote EC2 instance
#
# Usage (run from your local machine or the observability server):
#   ./scripts/install-node-exporter.sh <ec2-private-ip-or-hostname>
#   ./scripts/install-node-exporter.sh 10.0.1.100
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

TARGET=${1:-}
[[ -z "$TARGET" ]] && { echo "Usage: $0 <host>"; exit 1; }

NE_VERSION="1.7.0"
REMOTE_SCRIPT=$(cat <<'SCRIPT'
set -euo pipefail
NE_VERSION="1.7.0"
if ! command -v node_exporter &>/dev/null; then
    echo "Installing Node Exporter ${NE_VERSION}..."
    wget -q "https://github.com/prometheus/node_exporter/releases/download/v${NE_VERSION}/node_exporter-${NE_VERSION}.linux-amd64.tar.gz" \
        -O /tmp/node_exporter.tar.gz
    tar -xzf /tmp/node_exporter.tar.gz -C /tmp
    sudo mv "/tmp/node_exporter-${NE_VERSION}.linux-amd64/node_exporter" /usr/local/bin/
    rm -rf "/tmp/node_exporter-${NE_VERSION}.linux-amd64" /tmp/node_exporter.tar.gz

    sudo tee /etc/systemd/system/node_exporter.service > /dev/null <<'EOF'
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

    sudo systemctl daemon-reload
    sudo systemctl enable --now node_exporter
    echo "Node Exporter installed and running on :9100"
else
    echo "Node Exporter already installed: $(node_exporter --version 2>&1 | head -1)"
fi
SCRIPT
)

echo "Installing Node Exporter on $TARGET..."
ssh -o StrictHostKeyChecking=no "ubuntu@${TARGET}" "bash -s" <<< "$REMOTE_SCRIPT"
echo "Done. Add ${TARGET}:9100 to prometheus/targets/ec2_nodes.yml"
