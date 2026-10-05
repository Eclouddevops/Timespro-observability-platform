#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Install Node Exporter on a remote EC2 instance
#
# Usage:
#   ./scripts/install-node-exporter.sh <host>
#   ./scripts/install-node-exporter.sh <host> <ssh-key-path>
#   ./scripts/install-node-exporter.sh <host> ssm          ← no SSH key needed
#   ./scripts/install-node-exporter.sh <host> <key> ec2-user
#
# Examples:
#   ./scripts/install-node-exporter.sh 54.92.186.206
#   ./scripts/install-node-exporter.sh 54.92.186.206 ~/.ssh/monitoring-key.pem
#   ./scripts/install-node-exporter.sh 54.92.186.206 ssm
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

TARGET="${1:-}"
SSH_KEY="${2:-}"
SSH_USER="${3:-ubuntu}"
NE_VERSION="1.7.0"

[[ -z "$TARGET" ]] && {
    echo "Usage: $0 <host> [ssh-key-path|ssm] [ssh-user]"
    echo ""
    echo "Examples:"
    echo "  $0 54.92.186.206 ssm                        # via AWS SSM (no key needed)"
    echo "  $0 54.92.186.206 ~/.ssh/my-key.pem          # via SSH with key"
    echo "  $0 54.92.186.206 ~/.ssh/my-key.pem ec2-user # Amazon Linux"
    exit 1
}

# ── Method: AWS SSM (no SSH key needed) ──────────────────────────────
if [[ "$SSH_KEY" == "ssm" ]]; then
    echo "=== Installing Node Exporter via AWS SSM on $TARGET ==="
    echo "    (No SSH key required — uses IAM Instance Profile)"
    echo ""

    # Find instance ID from IP
    REGION="${AWS_DEFAULT_REGION:-us-east-1}"
    INSTANCE_ID=$(aws ec2 describe-instances \
        --filters "Name=ip-address,Values=$TARGET" \
                  "Name=instance-state-name,Values=running" \
        --query "Reservations[0].Instances[0].InstanceId" \
        --output text --region "$REGION" 2>/dev/null || echo "None")

    if [[ -z "$INSTANCE_ID" || "$INSTANCE_ID" == "None" ]]; then
        INSTANCE_ID=$(aws ec2 describe-instances \
            --filters "Name=private-ip-address,Values=$TARGET" \
                      "Name=instance-state-name,Values=running" \
            --query "Reservations[0].Instances[0].InstanceId" \
            --output text --region "$REGION" 2>/dev/null || echo "None")
    fi

    if [[ -z "$INSTANCE_ID" || "$INSTANCE_ID" == "None" ]]; then
        echo "❌ Cannot find running instance for IP: $TARGET"
        echo "   Make sure the SSM Agent is running on the instance"
        exit 1
    fi

    echo "   Instance ID : $INSTANCE_ID"
    echo "   Region      : $REGION"
    echo ""

    COMMAND_ID=$(aws ssm send-command \
        --instance-ids "$INSTANCE_ID" \
        --document-name "AWS-RunShellScript" \
        --region "$REGION" \
        --comment "Install Node Exporter for Observability Platform" \
        --parameters 'commands=["NE_VERSION=1.7.0","if command -v node_exporter >/dev/null 2>&1; then echo already_installed; sudo systemctl enable --now node_exporter 2>/dev/null || true; exit 0; fi","wget -q https://github.com/prometheus/node_exporter/releases/download/v${NE_VERSION}/node_exporter-${NE_VERSION}.linux-amd64.tar.gz -O /tmp/ne.tar.gz","tar -xzf /tmp/ne.tar.gz -C /tmp","sudo mv /tmp/node_exporter-${NE_VERSION}.linux-amd64/node_exporter /usr/local/bin/","rm -rf /tmp/node_exporter-1.7.0.linux-amd64 /tmp/ne.tar.gz","printf \"[Unit]\nDescription=Prometheus Node Exporter\nAfter=network.target\n[Service]\nUser=nobody\nExecStart=/usr/local/bin/node_exporter --web.listen-address=:9100\nRestart=on-failure\n[Install]\nWantedBy=multi-user.target\n\" | sudo tee /etc/systemd/system/node_exporter.service","sudo systemctl daemon-reload","sudo systemctl enable --now node_exporter","sleep 3","curl -sf http://localhost:9100/metrics | head -2 && echo node_exporter_installed_ok"]' \
        --query "Command.CommandId" \
        --output text 2>&1) || {
            echo "❌ SSM command failed. Make sure:"
            echo "   1. SSM Agent is installed on the instance"
            echo "   2. Instance has AmazonSSMManagedInstanceCore policy"
            exit 1
        }

    echo "   SSM Command ID: $COMMAND_ID"
    echo "   Waiting 20 seconds for installation to complete..."
    sleep 20

    echo ""
    STATUS=$(aws ssm get-command-invocation \
        --command-id "$COMMAND_ID" \
        --instance-id "$INSTANCE_ID" \
        --region "$REGION" \
        --query "Status" \
        --output text 2>/dev/null || echo "Unknown")

    OUTPUT=$(aws ssm get-command-invocation \
        --command-id "$COMMAND_ID" \
        --instance-id "$INSTANCE_ID" \
        --region "$REGION" \
        --query "StandardOutputContent" \
        --output text 2>/dev/null || echo "")

    echo "   Status : $STATUS"
    echo "   Output : $OUTPUT"
    echo ""

    if [[ "$STATUS" == "Success" ]]; then
        echo "✅ Node Exporter installed successfully on $TARGET!"
    else
        echo "⚠️  Check AWS SSM console for details"
        echo "   AWS Console → Systems Manager → Run Command → $COMMAND_ID"
    fi

    echo ""
    echo "Next steps:"
    echo "  1. Open port 9100 in Security Group for $TARGET"
    echo "     (Allow TCP 9100 from 54.88.150.33/32)"
    echo "  2. Prometheus will detect it automatically in next scan"
    exit 0
fi

# ── Method: SSH ───────────────────────────────────────────────────────
echo "=== Installing Node Exporter via SSH on $TARGET ==="

# Build SSH options
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=15 -o BatchMode=yes"

if [[ -n "$SSH_KEY" && -f "$SSH_KEY" ]]; then
    echo "   Using key : $SSH_KEY"
    SSH_OPTS="$SSH_OPTS -i $SSH_KEY"
else
    # Auto-detect SSH key
    for KEY_PATH in \
        /observability-platform/secrets/*.pem \
        ~/.ssh/*.pem \
        ~/.ssh/id_rsa \
        ~/.ssh/id_ed25519; do
        if [[ -f "$KEY_PATH" ]]; then
            echo "   Auto-detected key: $KEY_PATH"
            SSH_OPTS="$SSH_OPTS -i $KEY_PATH"
            break
        fi
    done
fi

echo "   User      : $SSH_USER"
echo "   Host      : $TARGET"
echo ""

ssh $SSH_OPTS "${SSH_USER}@${TARGET}" 'bash -s' << 'REMOTE'
set -euo pipefail
NE_VERSION="1.7.0"
if command -v node_exporter &>/dev/null; then
    echo "Node Exporter already installed"
    sudo systemctl enable --now node_exporter 2>/dev/null || true
    exit 0
fi
echo "Downloading Node Exporter ${NE_VERSION}..."
wget -q "https://github.com/prometheus/node_exporter/releases/download/v${NE_VERSION}/node_exporter-${NE_VERSION}.linux-amd64.tar.gz" -O /tmp/ne.tar.gz
tar -xzf /tmp/ne.tar.gz -C /tmp
sudo mv "/tmp/node_exporter-${NE_VERSION}.linux-amd64/node_exporter" /usr/local/bin/
rm -rf "/tmp/node_exporter-${NE_VERSION}.linux-amd64" /tmp/ne.tar.gz
sudo tee /etc/systemd/system/node_exporter.service > /dev/null << 'SVCEOF'
[Unit]
Description=Prometheus Node Exporter
After=network.target
[Service]
User=nobody
ExecStart=/usr/local/bin/node_exporter --web.listen-address=:9100
Restart=on-failure
[Install]
WantedBy=multi-user.target
SVCEOF
sudo systemctl daemon-reload
sudo systemctl enable --now node_exporter
sleep 2
curl -sf http://localhost:9100/metrics | head -2
echo "✅ Node Exporter installed on port 9100"
REMOTE

echo ""
echo "✅ Done! Instance $TARGET will appear in Grafana within 2 minutes"
echo ""
echo "Remaining step: Open port 9100 in AWS Security Group"
echo "  EC2 → Security Groups → Add Inbound: TCP 9100 from 54.88.150.33/32"
