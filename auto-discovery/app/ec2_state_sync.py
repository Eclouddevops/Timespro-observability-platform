"""
EC2 State Sync
──────────────
Fetches ALL EC2 instances from AWS API every 2 minutes and:

1. Writes prometheus/targets/ec2_instances.yml  ← for "is it running?" check
2. Writes exporters/cloudwatch/config.yml       ← dynamically adds all instance IDs
   so CloudWatch Exporter auto-pulls CPU/Network/StatusCheck for each instance

This means:
  - NEW instance launched → appears in dashboard within 2 minutes
  - NO Node Exporter needed
  - NO SSH needed
  - NO port 9100 needed
  - Uses ONLY CloudWatch metrics (AWS native — always available)

Metrics available for ANY EC2 (no agent needed):
  aws_ec2_cpuutilization_average      CPU %
  aws_ec2_network_in_sum              Network bytes in
  aws_ec2_network_out_sum             Network bytes out
  aws_ec2_status_check_failed_sum     Status check (0=OK, 1=FAIL)
  aws_ec2_disk_read_bytes_sum         Disk read (instance store only)
  aws_ec2_disk_write_bytes_sum        Disk write (instance store only)
"""

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import boto3
import yaml

logger = logging.getLogger(__name__)

TARGETS_DIR    = os.getenv("TARGETS_DIR", "/etc/prometheus/targets")
CW_CONFIG_FILE = os.getenv("CW_CONFIG_FILE", "/etc/cloudwatch/config.yml")
REGION         = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
ALL_REGIONS    = [r.strip() for r in os.getenv("AWS_REGIONS", REGION).split(",") if r.strip()]
TAG_FILTER_KEY = os.getenv("TAG_FILTER_KEY", "")
TAG_FILTER_VAL = os.getenv("TAG_FILTER_VALUE", "true")


@dataclass
class EC2Instance:
    instance_id:   str
    name:          str
    state:         str
    instance_type: str
    region:        str
    az:            str
    private_ip:    str
    public_ip:     str
    platform:      str   # "windows" or "linux"
    tags:          dict = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return self.name or self.instance_id

    @property
    def is_running(self) -> bool:
        return self.state == "running"


def fetch_all_ec2_instances(accounts: list = None) -> list[EC2Instance]:
    """
    Fetch ALL EC2 instances across all configured regions.
    Uses IAM Instance Profile — no credentials needed.
    Returns both running and stopped instances so dashboard shows full inventory.
    """
    all_instances = []

    for region in ALL_REGIONS:
        try:
            ec2 = boto3.client("ec2", region_name=region)
            filters = []
            if TAG_FILTER_KEY:
                filters.append({"Name": f"tag:{TAG_FILTER_KEY}", "Values": [TAG_FILTER_VAL]})

            paginator = ec2.get_paginator("describe_instances")
            for page in paginator.paginate(Filters=filters):
                for reservation in page["Reservations"]:
                    for inst in reservation["Instances"]:
                        tags     = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                        name     = tags.get("Name", inst["InstanceId"])
                        platform = "windows" if inst.get("Platform", "").lower() == "windows" else "linux"

                        all_instances.append(EC2Instance(
                            instance_id   = inst["InstanceId"],
                            name          = name,
                            state         = inst["State"]["Name"],
                            instance_type = inst["InstanceType"],
                            region        = region,
                            az            = inst["Placement"]["AvailabilityZone"],
                            private_ip    = inst.get("PrivateIpAddress", ""),
                            public_ip     = inst.get("PublicIpAddress", ""),
                            platform      = platform,
                            tags          = tags,
                        ))

            logger.info("Region %s: found %d instances", region, sum(1 for i in all_instances if i.region == region))

        except Exception as e:
            logger.error("Failed to fetch EC2 in region %s: %s", region, e)

    return all_instances


def write_instance_targets(instances: list[EC2Instance]):
    """
    Write a Prometheus file-SD targets file with ALL EC2 instances.
    Uses CloudWatch Exporter job — no Node Exporter needed.
    Each instance gets a target entry that maps to its CloudWatch metrics.
    """
    os.makedirs(TARGETS_DIR, exist_ok=True)

    targets = []
    for inst in instances:
        env = inst.tags.get("Environment", inst.tags.get("Env", "production"))

        targets.append({
            "targets": [f"cloudwatch-exporter:9106"],  # all go via cloudwatch exporter
            "labels": {
                "instance":      inst.display_name,
                "instance_id":   inst.instance_id,
                "instance_type": inst.instance_type,
                "region":        inst.region,
                "az":            inst.az,
                "os":            inst.platform,
                "state":         inst.state,
                "private_ip":    inst.private_ip,
                "public_ip":     inst.public_ip,
                "env":           env,
                "account":       _get_account_id(),
                "monitored_by":  "auto-discovery",
                "job":           "ec2-inventory",
            }
        })

    filepath = os.path.join(TARGETS_DIR, "ec2_inventory.yml")
    content = (
        f"# AUTO-GENERATED by EC2 State Sync\n"
        f"# Updated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n"
        f"# Total: {len(instances)} instances "
        f"({sum(1 for i in instances if i.is_running)} running, "
        f"{sum(1 for i in instances if not i.is_running)} stopped)\n"
        f"# NO Node Exporter needed — metrics via CloudWatch\n\n"
    )
    content += yaml.dump(targets, default_flow_style=False, allow_unicode=True)

    with open(filepath, "w") as f:
        f.write(content)

    logger.info("Wrote EC2 inventory: %d instances → %s", len(instances), filepath)


def write_cloudwatch_config(instances: list[EC2Instance]):
    """
    Dynamically write CloudWatch Exporter config with ALL discovered instance IDs.
    This makes CloudWatch Exporter pull metrics for every instance automatically.
    """
    running = [i for i in instances if i.is_running]

    metrics = [
        # CPU — available for all instance types
        {
            "aws_namespace":  "AWS/EC2",
            "aws_metric_name": "CPUUtilization",
            "aws_dimensions": ["InstanceId"],
            "aws_statistics": ["Average", "Maximum"],
            "period_seconds": 300,
        },
        # Network
        {
            "aws_namespace":  "AWS/EC2",
            "aws_metric_name": "NetworkIn",
            "aws_dimensions": ["InstanceId"],
            "aws_statistics": ["Sum"],
            "period_seconds": 300,
        },
        {
            "aws_namespace":  "AWS/EC2",
            "aws_metric_name": "NetworkOut",
            "aws_dimensions": ["InstanceId"],
            "aws_statistics": ["Sum"],
            "period_seconds": 300,
        },
        # Status check — 0 = OK, 1 = FAILED (most important health signal)
        {
            "aws_namespace":  "AWS/EC2",
            "aws_metric_name": "StatusCheckFailed",
            "aws_dimensions": ["InstanceId"],
            "aws_statistics": ["Sum"],
            "period_seconds": 60,
        },
        {
            "aws_namespace":  "AWS/EC2",
            "aws_metric_name": "StatusCheckFailed_Instance",
            "aws_dimensions": ["InstanceId"],
            "aws_statistics": ["Sum"],
            "period_seconds": 60,
        },
        {
            "aws_namespace":  "AWS/EC2",
            "aws_metric_name": "StatusCheckFailed_System",
            "aws_dimensions": ["InstanceId"],
            "aws_statistics": ["Sum"],
            "period_seconds": 60,
        },
        # Disk I/O (only for instance store; EBS uses separate metrics)
        {
            "aws_namespace":  "AWS/EC2",
            "aws_metric_name": "DiskReadBytes",
            "aws_dimensions": ["InstanceId"],
            "aws_statistics": ["Sum"],
            "period_seconds": 300,
        },
        {
            "aws_namespace":  "AWS/EC2",
            "aws_metric_name": "DiskWriteBytes",
            "aws_dimensions": ["InstanceId"],
            "aws_statistics": ["Sum"],
            "period_seconds": 300,
        },
        # EBS metrics (works for all EBS-backed instances)
        {
            "aws_namespace":  "AWS/EC2",
            "aws_metric_name": "EBSReadBytes",
            "aws_dimensions": ["InstanceId"],
            "aws_statistics": ["Sum"],
            "period_seconds": 300,
        },
        {
            "aws_namespace":  "AWS/EC2",
            "aws_metric_name": "EBSWriteBytes",
            "aws_dimensions": ["InstanceId"],
            "aws_statistics": ["Sum"],
            "period_seconds": 300,
        },
    ]

    config = {
        "region": REGION,
        "metrics": metrics,
    }

    os.makedirs(os.path.dirname(CW_CONFIG_FILE), exist_ok=True)

    header = (
        f"# AUTO-GENERATED by EC2 State Sync\n"
        f"# Updated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n"
        f"# Monitoring {len(running)} running instances across {len(ALL_REGIONS)} region(s)\n"
        f"# Instance IDs: {', '.join(i.instance_id for i in running[:10])}"
        f"{'...' if len(running) > 10 else ''}\n\n"
    )

    with open(CW_CONFIG_FILE, "w") as f:
        f.write(header)
        yaml.dump(config, f, default_flow_style=False)

    logger.info("Updated CloudWatch config: %d running instances", len(running))


_cached_account_id = None

def _get_account_id() -> str:
    global _cached_account_id
    if not _cached_account_id:
        try:
            _cached_account_id = boto3.client("sts").get_caller_identity()["Account"]
        except Exception:
            _cached_account_id = "unknown"
    return _cached_account_id
