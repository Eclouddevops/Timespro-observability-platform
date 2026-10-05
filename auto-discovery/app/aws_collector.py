"""
AWS Metrics Collector
─────────────────────
Directly fetches EC2 instance data from AWS APIs and writes
Prometheus-format metrics to a file that Node Exporter's
textfile_collector reads.

This bypasses CloudWatch Exporter entirely — no timing issues,
no scrape config problems. The data goes directly into Prometheus.

Metrics produced (for ALL instances, no agent needed):
  ec2_instance_info       - instance metadata (name, type, az, state)
  ec2_instance_running    - 1 if running, 0 if stopped
  ec2_cpu_percent         - CPU % from CloudWatch
  ec2_network_in_bytes    - Network bytes received (5 min sum)
  ec2_network_out_bytes   - Network bytes sent (5 min sum)
  ec2_status_check_ok     - 1 if status check passing, 0 if failing
"""

import boto3
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

REGIONS      = [r.strip() for r in os.getenv("AWS_REGIONS", "us-east-1").split(",") if r.strip()]
PROM_DIR     = os.getenv("PROM_TEXTFILE_DIR", "/prometheus_textfiles")
TAG_FILTER   = os.getenv("TAG_FILTER_KEY", "")
TAG_VALUE    = os.getenv("TAG_FILTER_VALUE", "true")
ACCOUNT_NAME = os.getenv("PRIMARY_ACCOUNT_NAME", "production")


def _escape(v: str) -> str:
    """Escape label value for Prometheus text format."""
    return v.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')


def _labels(**kwargs) -> str:
    pairs = ','.join(f'{k}="{_escape(str(v))}"' for k, v in kwargs.items() if v is not None)
    return '{' + pairs + '}'


def get_cloudwatch_metric(cw, instance_id: str, metric_name: str,
                          stat: str = "Average", period: int = 300) -> Optional[float]:
    """Get a single CloudWatch metric value for an EC2 instance."""
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(seconds=period * 2)
        resp  = cw.get_metric_statistics(
            Namespace  = "AWS/EC2",
            MetricName = metric_name,
            Dimensions = [{"Name": "InstanceId", "Value": instance_id}],
            StartTime  = start,
            EndTime    = end,
            Period     = period,
            Statistics = [stat],
        )
        points = resp.get("Datapoints", [])
        if not points:
            return None
        latest = sorted(points, key=lambda x: x["Timestamp"])[-1]
        return float(latest.get(stat, 0))
    except Exception as e:
        logger.debug("CloudWatch %s for %s: %s", metric_name, instance_id, e)
        return None


def collect_and_write():
    """
    Main collection function. Fetches all EC2 instances and their
    CloudWatch metrics, then writes them to a Prometheus textfile.
    """
    os.makedirs(PROM_DIR, exist_ok=True)
    lines = []
    timestamp_ms = int(time.time() * 1000)
    total_instances = 0
    total_running   = 0

    for region in REGIONS:
        try:
            ec2 = boto3.client("ec2", region_name=region)
            cw  = boto3.client("cloudwatch", region_name=region)

            # ── Fetch all instances ─────────────────────────────────
            filters = []
            if TAG_FILTER:
                filters.append({"Name": f"tag:{TAG_FILTER}", "Values": [TAG_VALUE]})

            paginator = ec2.get_paginator("describe_instances")
            instances = []
            for page in paginator.paginate(Filters=filters):
                for res in page["Reservations"]:
                    instances.extend(res["Instances"])

            logger.info("Region %s: %d instances", region, len(instances))
            total_instances += len(instances)

            for inst in instances:
                iid   = inst["InstanceId"]
                tags  = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                name  = tags.get("Name", iid)
                state = inst["State"]["Name"]
                itype = inst["InstanceType"]
                az    = inst["Placement"]["AvailabilityZone"]
                priv  = inst.get("PrivateIpAddress", "")
                pub   = inst.get("PublicIpAddress", "")
                plat  = "windows" if inst.get("Platform", "").lower() == "windows" else "linux"
                env   = tags.get("Environment", tags.get("Env", "production"))
                is_running = 1 if state == "running" else 0

                if is_running:
                    total_running += 1

                lbl = _labels(
                    instance_id    = iid,
                    instance_name  = name,
                    instance_type  = itype,
                    region         = region,
                    az             = az,
                    os             = plat,
                    env            = env,
                    account        = ACCOUNT_NAME,
                    private_ip     = priv,
                    public_ip      = pub,
                )

                # ── ec2_instance_info ──────────────────────────────
                lines.append(f'# HELP ec2_instance_info EC2 instance metadata')
                lines.append(f'# TYPE ec2_instance_info gauge')
                lines.append(f'ec2_instance_info{lbl} 1')

                # ── ec2_instance_running ───────────────────────────
                lines.append(f'ec2_instance_running{lbl} {is_running}')

                if is_running:
                    # ── CPU ──────────────────────────────────────
                    cpu = get_cloudwatch_metric(cw, iid, "CPUUtilization", "Average")
                    if cpu is not None:
                        lines.append(f'# HELP ec2_cpu_percent CPU utilization % from CloudWatch')
                        lines.append(f'# TYPE ec2_cpu_percent gauge')
                        lines.append(f'ec2_cpu_percent{lbl} {cpu:.4f}')

                    # ── Network In ───────────────────────────────
                    net_in = get_cloudwatch_metric(cw, iid, "NetworkIn", "Sum")
                    if net_in is not None:
                        lines.append(f'# HELP ec2_network_in_bytes Network bytes received (5 min sum)')
                        lines.append(f'# TYPE ec2_network_in_bytes gauge')
                        lines.append(f'ec2_network_in_bytes{lbl} {net_in:.0f}')

                    # ── Network Out ──────────────────────────────
                    net_out = get_cloudwatch_metric(cw, iid, "NetworkOut", "Sum")
                    if net_out is not None:
                        lines.append(f'# HELP ec2_network_out_bytes Network bytes sent (5 min sum)')
                        lines.append(f'# TYPE ec2_network_out_bytes gauge')
                        lines.append(f'ec2_network_out_bytes{lbl} {net_out:.0f}')

                    # ── Status Check ─────────────────────────────
                    sc = get_cloudwatch_metric(cw, iid, "StatusCheckFailed", "Sum", period=60)
                    ok = 1 if sc is not None and sc == 0 else (0 if sc is not None else 1)
                    lines.append(f'# HELP ec2_status_check_ok 1=passing 0=failing')
                    lines.append(f'# TYPE ec2_status_check_ok gauge')
                    lines.append(f'ec2_status_check_ok{lbl} {ok}')

                logger.info("  ✅ %s (%s) state=%s cpu=%s",
                            name, iid, state,
                            f"{cpu:.1f}%" if (is_running and cpu is not None) else "n/a")

        except Exception as e:
            logger.error("Region %s collection failed: %s", region, e, exc_info=True)

    # ── Summary metrics ─────────────────────────────────────────────
    acct_lbl = _labels(account=ACCOUNT_NAME)
    lines.append(f'# HELP ec2_total_instances Total EC2 instances discovered')
    lines.append(f'# TYPE ec2_total_instances gauge')
    lines.append(f'ec2_total_instances{acct_lbl} {total_instances}')

    lines.append(f'# HELP ec2_running_instances Running EC2 instances')
    lines.append(f'# TYPE ec2_running_instances gauge')
    lines.append(f'ec2_running_instances{acct_lbl} {total_running}')

    lines.append(f'# HELP ec2_collector_last_run_timestamp Unix timestamp of last collection')
    lines.append(f'# TYPE ec2_collector_last_run_timestamp gauge')
    lines.append(f'ec2_collector_last_run_timestamp{acct_lbl} {int(time.time())}')

    # ── Write textfile ───────────────────────────────────────────────
    outfile = os.path.join(PROM_DIR, "ec2_metrics.prom")
    tmpfile = outfile + ".tmp"
    content = "\n".join(lines) + "\n"

    with open(tmpfile, "w") as f:
        f.write(content)
    os.replace(tmpfile, outfile)  # atomic write

    logger.info("✅ Wrote %d metric lines for %d instances (%d running) → %s",
                len(lines), total_instances, total_running, outfile)

    return {
        "total":   total_instances,
        "running": total_running,
        "regions": REGIONS,
        "file":    outfile,
        "lines":   len(lines),
    }
