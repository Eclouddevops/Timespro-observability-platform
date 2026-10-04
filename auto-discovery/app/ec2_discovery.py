"""
EC2 Auto-Discovery
──────────────────
Scans all AWS regions (or configured ones) for running EC2 instances,
reads their tags, checks if Node Exporter is reachable on port 9100,
and writes/updates the Prometheus file-SD target files automatically.

Flow:
  1. boto3 describes all running EC2 instances (via IAM Instance Profile)
  2. Filter by tags (optional: only instances tagged Monitoring=true)
  3. Check if port 9100 is open (Node Exporter reachable)
  4. If not installed → optionally SSH and install Node Exporter
  5. Write /etc/prometheus/targets/ec2_nodes.yml
  6. POST to Prometheus /-/reload (hot reload — no restart)
  7. Notify MS Teams with a summary of changes
"""

import asyncio
import ipaddress
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Optional

import boto3
import httpx
import yaml

logger = logging.getLogger(__name__)

# ── Config from environment ───────────────────────────────────────────
PROMETHEUS_URL       = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
TARGETS_FILE         = os.getenv("EC2_TARGETS_FILE", "/etc/prometheus/targets/ec2_nodes.yml")
NODE_EXPORTER_PORT   = int(os.getenv("NODE_EXPORTER_PORT", "9100"))
CONNECT_TIMEOUT      = int(os.getenv("CONNECT_TIMEOUT_SEC", "3"))
REGIONS              = [r.strip() for r in os.getenv("AWS_REGIONS", "us-east-1").split(",") if r.strip()]
TAG_FILTER_KEY       = os.getenv("TAG_FILTER_KEY", "")          # e.g. "Monitoring"
TAG_FILTER_VALUE     = os.getenv("TAG_FILTER_VALUE", "true")    # e.g. "true"
AUTO_INSTALL_NE      = os.getenv("AUTO_INSTALL_NODE_EXPORTER", "false").lower() == "true"
SSH_KEY_PATH         = os.getenv("SSH_KEY_PATH", "/secrets/ec2-key.pem")
SSH_USER             = os.getenv("SSH_USER", "ubuntu")
USE_PRIVATE_IP       = os.getenv("USE_PRIVATE_IP", "true").lower() == "true"
MSTEAMS_WEBHOOK_URL  = os.getenv("MSTEAMS_WEBHOOK_URL", "")


@dataclass
class EC2Instance:
    instance_id:   str
    name:          str
    private_ip:    str
    public_ip:     str
    region:        str
    az:            str
    instance_type: str
    state:         str
    tags:          dict = field(default_factory=dict)
    node_exporter_up: bool = False

    @property
    def monitor_ip(self) -> str:
        """IP Prometheus should use to scrape Node Exporter."""
        return self.private_ip if USE_PRIVATE_IP else (self.public_ip or self.private_ip)

    @property
    def target(self) -> str:
        return f"{self.monitor_ip}:{NODE_EXPORTER_PORT}"

    @property
    def display_name(self) -> str:
        return self.name or self.instance_id


class EC2Discovery:
    def __init__(self):
        self.known_instances: dict[str, EC2Instance] = {}
        self.last_targets: set[str] = set()

    # ── AWS Discovery ─────────────────────────────────────────────────

    def discover_instances(self) -> list[EC2Instance]:
        """Scan all configured regions and return running EC2 instances."""
        instances = []
        for region in REGIONS:
            try:
                ec2 = boto3.client("ec2", region_name=region)
                filters = [{"Name": "instance-state-name", "Values": ["running"]}]

                # Optional tag filter — only monitor tagged instances
                if TAG_FILTER_KEY:
                    filters.append({
                        "Name": f"tag:{TAG_FILTER_KEY}",
                        "Values": [TAG_FILTER_VALUE]
                    })

                paginator = ec2.get_paginator("describe_instances")
                for page in paginator.paginate(Filters=filters):
                    for reservation in page["Reservations"]:
                        for inst in reservation["Instances"]:
                            tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                            instances.append(EC2Instance(
                                instance_id   = inst["InstanceId"],
                                name          = tags.get("Name", inst["InstanceId"]),
                                private_ip    = inst.get("PrivateIpAddress", ""),
                                public_ip     = inst.get("PublicIpAddress", ""),
                                region        = region,
                                az            = inst["Placement"]["AvailabilityZone"],
                                instance_type = inst["InstanceType"],
                                state         = inst["State"]["Name"],
                                tags          = tags,
                            ))

                logger.info("Region %s: found %d running instances", region,
                            sum(1 for i in instances if i.region == region))
            except Exception as e:
                logger.error("Failed to discover EC2 in region %s: %s", region, e)

        return instances

    # ── Port Check ────────────────────────────────────────────────────

    def check_node_exporter(self, instance: EC2Instance) -> bool:
        """Return True if Node Exporter is reachable on port 9100."""
        if not instance.monitor_ip:
            return False
        try:
            with socket.create_connection(
                (instance.monitor_ip, NODE_EXPORTER_PORT),
                timeout=CONNECT_TIMEOUT
            ):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    # ── Auto-Install Node Exporter via SSH ────────────────────────────

    def install_node_exporter(self, instance: EC2Instance) -> bool:
        """SSH into instance and install Node Exporter as systemd service."""
        if not AUTO_INSTALL_NE:
            return False
        if not os.path.exists(SSH_KEY_PATH):
            logger.warning("SSH key not found at %s — skipping auto-install", SSH_KEY_PATH)
            return False

        try:
            import paramiko
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            install_ip = instance.public_ip or instance.private_ip
            logger.info("SSH into %s (%s) to install Node Exporter...", instance.name, install_ip)

            client.connect(
                hostname=install_ip,
                username=SSH_USER,
                key_filename=SSH_KEY_PATH,
                timeout=30,
                banner_timeout=30
            )

            install_script = """
set -euo pipefail
NE_VERSION="1.7.0"
if ! command -v node_exporter &>/dev/null; then
    echo "Installing Node Exporter ${NE_VERSION}..."
    wget -q "https://github.com/prometheus/node_exporter/releases/download/v${NE_VERSION}/node_exporter-${NE_VERSION}.linux-amd64.tar.gz" -O /tmp/ne.tar.gz
    tar -xzf /tmp/ne.tar.gz -C /tmp
    sudo mv /tmp/node_exporter-${NE_VERSION}.linux-amd64/node_exporter /usr/local/bin/
    rm -rf /tmp/node_exporter-${NE_VERSION}.linux-amd64 /tmp/ne.tar.gz
    sudo tee /etc/systemd/system/node_exporter.service > /dev/null <<'EOF'
[Unit]
Description=Prometheus Node Exporter
After=network.target
[Service]
User=nobody
ExecStart=/usr/local/bin/node_exporter --web.listen-address=:9100
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now node_exporter
    echo "INSTALLED"
else
    echo "ALREADY_INSTALLED"
fi
"""
            _, stdout, stderr = client.exec_command(install_script)
            result = stdout.read().decode().strip()
            error  = stderr.read().decode().strip()
            client.close()

            if "INSTALLED" in result or "ALREADY_INSTALLED" in result:
                logger.info("Node Exporter installed on %s", instance.name)
                time.sleep(5)   # give it time to start
                return True
            else:
                logger.error("Install failed on %s: %s", instance.name, error)
                return False

        except Exception as e:
            logger.error("SSH install failed for %s: %s", instance.name, e)
            return False

    # ── Prometheus Target File ────────────────────────────────────────

    def build_targets_yaml(self, instances: list[EC2Instance]) -> list[dict]:
        """Build Prometheus file-SD YAML from discovered instances."""
        # Group by region + env tag
        groups: dict[tuple, list] = {}
        for inst in instances:
            if not inst.node_exporter_up:
                continue
            env    = inst.tags.get("Environment", inst.tags.get("Env", "production"))
            key    = (inst.region, env)
            groups.setdefault(key, []).append(inst)

        result = []
        for (region, env), group in sorted(groups.items()):
            targets = [i.target for i in group]
            labels  = {
                "env":       env,
                "region":    region,
                "job":       "ec2-nodes",
                "monitored_by": "auto-discovery",
            }
            # Add per-instance labels via relabeling hint
            result.append({
                "targets": targets,
                "labels":  labels
            })

            # Also write individual entries with instance name label
            for inst in group:
                result.append({
                    "targets": [inst.target],
                    "labels": {
                        "instance":      inst.display_name,
                        "instance_id":   inst.instance_id,
                        "instance_type": inst.instance_type,
                        "env":           env,
                        "region":        region,
                        "az":            inst.az,
                        "job":           "ec2-nodes",
                        "monitored_by":  "auto-discovery",
                    }
                })

        # De-duplicate targets
        seen = set()
        deduped = []
        for entry in result:
            key = tuple(sorted(entry["targets"]))
            if key not in seen:
                seen.add(key)
                deduped.append(entry)

        return deduped

    def write_targets_file(self, yaml_data: list[dict]) -> bool:
        """Write the Prometheus file-SD targets YAML."""
        try:
            content = (
                "# AUTO-GENERATED by EC2 Auto-Discovery Agent\n"
                "# Do not edit manually — changes will be overwritten\n"
                f"# Last updated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n\n"
            )
            content += yaml.dump(yaml_data, default_flow_style=False, allow_unicode=True)

            with open(TARGETS_FILE, "w") as f:
                f.write(content)

            logger.info("Wrote %d target groups to %s", len(yaml_data), TARGETS_FILE)
            return True
        except Exception as e:
            logger.error("Failed to write targets file: %s", e)
            return False

    # ── Prometheus Reload ─────────────────────────────────────────────

    async def reload_prometheus(self) -> bool:
        """Hot-reload Prometheus config via API."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{PROMETHEUS_URL}/-/reload")
                if resp.status_code == 200:
                    logger.info("Prometheus reloaded successfully")
                    return True
                else:
                    logger.warning("Prometheus reload returned %s", resp.status_code)
                    return False
        except Exception as e:
            logger.error("Prometheus reload failed: %s", e)
            return False

    # ── MS Teams Notification ─────────────────────────────────────────

    async def notify_teams(self, added: list[EC2Instance], removed: list[str], total: int):
        """Send a MS Teams card summarising discovery changes."""
        if not MSTEAMS_WEBHOOK_URL:
            return
        if not added and not removed:
            return

        lines = []
        for inst in added:
            lines.append(f"✅ **Added:** {inst.display_name} ({inst.instance_id}) — {inst.instance_type} — {inst.region}")
        for iid in removed:
            lines.append(f"🔴 **Removed:** {iid}")

        body = "\n\n".join(lines)
        payload = {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "Container",
                            "style": "accent",
                            "items": [{
                                "type": "TextBlock",
                                "text": f"🔍 EC2 Auto-Discovery Update — {total} instances monitored",
                                "weight": "Bolder",
                                "size": "Medium",
                                "wrap": True
                            }]
                        },
                        { "type": "TextBlock", "text": body, "wrap": True, "spacing": "Medium" },
                        { "type": "TextBlock", "text": f"🕐 {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}", "isSubtle": True, "size": "Small" }
                    ]
                }
            }]
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(MSTEAMS_WEBHOOK_URL, json=payload)
            logger.info("Teams notification sent")
        except Exception as e:
            logger.warning("Teams notification failed: %s", e)

    # ── Main Discovery Run ────────────────────────────────────────────

    async def run_discovery(self) -> dict:
        """Full discovery cycle — returns summary dict."""
        logger.info("=" * 60)
        logger.info("EC2 Auto-Discovery starting — regions: %s", REGIONS)

        # 1. Discover instances from AWS
        loop = asyncio.get_event_loop()
        instances = await loop.run_in_executor(None, self.discover_instances)
        logger.info("Discovered %d running EC2 instances", len(instances))

        # 2. Check Node Exporter on each
        newly_added   = []
        newly_removed = []

        for inst in instances:
            up = await loop.run_in_executor(None, self.check_node_exporter, inst)
            inst.node_exporter_up = up

            if up:
                logger.info("  ✅ %s (%s) — Node Exporter UP at %s",
                            inst.display_name, inst.instance_id, inst.target)
                if inst.instance_id not in self.known_instances:
                    newly_added.append(inst)
                    logger.info("  🆕 NEW instance added to monitoring: %s", inst.display_name)
            else:
                logger.warning("  ❌ %s (%s) — Node Exporter NOT reachable at %s",
                               inst.display_name, inst.instance_id, inst.target)
                # Try auto-install if enabled
                if AUTO_INSTALL_NE:
                    installed = await loop.run_in_executor(None, self.install_node_exporter, inst)
                    if installed:
                        inst.node_exporter_up = True
                        if inst.instance_id not in self.known_instances:
                            newly_added.append(inst)

        # 3. Detect removed instances
        current_ids = {i.instance_id for i in instances if i.node_exporter_up}
        for iid in list(self.known_instances.keys()):
            if iid not in current_ids:
                newly_removed.append(iid)
                del self.known_instances[iid]
                logger.info("  🗑️  Instance removed from monitoring: %s", iid)

        # Update known instances
        for inst in instances:
            if inst.node_exporter_up:
                self.known_instances[inst.instance_id] = inst

        # 4. Write targets file
        monitored = [i for i in instances if i.node_exporter_up]
        yaml_data = self.build_targets_yaml(monitored)
        self.write_targets_file(yaml_data)

        # 5. Reload Prometheus
        await self.reload_prometheus()

        # 6. Notify Teams if anything changed
        await self.notify_teams(newly_added, newly_removed, len(monitored))

        summary = {
            "total_discovered":  len(instances),
            "total_monitored":   len(monitored),
            "not_reachable":     len(instances) - len(monitored),
            "newly_added":       [i.display_name for i in newly_added],
            "newly_removed":     newly_removed,
            "instances":         [
                {
                    "id":            i.instance_id,
                    "name":          i.display_name,
                    "ip":            i.target,
                    "type":          i.instance_type,
                    "region":        i.region,
                    "az":            i.az,
                    "node_exporter": i.node_exporter_up,
                    "tags":          i.tags,
                }
                for i in instances
            ]
        }

        logger.info("Discovery complete — %d/%d instances monitored", len(monitored), len(instances))
        return summary
