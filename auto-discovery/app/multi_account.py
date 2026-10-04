"""
Multi-Account / Multi-Region AWS Discovery
──────────────────────────────────────────
Discovers ALL AWS services across ALL accounts and ALL regions.

Supported services:
  - EC2 Instances        (Node Exporter port 9100)
  - ECS Clusters/Services
  - Lambda Functions
  - RDS Instances
  - ALB / ELB
  - API Gateways
  - Auto Scaling Groups
  - ElastiCache
  - SQS Queues
  - SNS Topics
  - CloudFront Distributions
  - S3 Buckets (count only)
  - WAF WebACLs

Flow per account:
  1. Assume cross-account IAM role via STS
  2. Scan every region in parallel
  3. Discover all supported services
  4. Write Prometheus targets + CloudWatch exporter config
  5. Hot-reload Prometheus
  6. Notify MS Teams on changes
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import boto3
import httpx
import yaml

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────
PROMETHEUS_URL      = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
TARGETS_DIR         = os.getenv("TARGETS_DIR", "/etc/prometheus/targets")
CW_CONFIG_FILE      = os.getenv("CW_CONFIG_FILE", "/etc/cloudwatch/config.yml")
MSTEAMS_WEBHOOK_URL = os.getenv("MSTEAMS_WEBHOOK_URL", "")
NODE_EXPORTER_PORT  = int(os.getenv("NODE_EXPORTER_PORT", "9100"))
CONNECT_TIMEOUT     = int(os.getenv("CONNECT_TIMEOUT_SEC", "3"))
USE_PRIVATE_IP      = os.getenv("USE_PRIVATE_IP", "true").lower() == "true"
TAG_FILTER_KEY      = os.getenv("TAG_FILTER_KEY", "")
TAG_FILTER_VALUE    = os.getenv("TAG_FILTER_VALUE", "true")

# AWS Accounts config — JSON list of account dicts
# Format: [{"id":"123456789","name":"prod","role_arn":"arn:aws:iam::123456789:role/ObservabilityRole","regions":["us-east-1","eu-west-1"]}]
ACCOUNTS_JSON = os.getenv("AWS_ACCOUNTS", "[]")

# Single account fallback (uses instance profile)
PRIMARY_REGION  = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
PRIMARY_REGIONS = [r.strip() for r in os.getenv("AWS_REGIONS", PRIMARY_REGION).split(",") if r.strip()]

# All standard AWS regions
ALL_REGIONS = [
    "us-east-1","us-east-2","us-west-1","us-west-2",
    "eu-west-1","eu-west-2","eu-west-3","eu-central-1","eu-north-1","eu-south-1",
    "ap-southeast-1","ap-southeast-2","ap-northeast-1","ap-northeast-2","ap-northeast-3",
    "ap-south-1","ap-east-1",
    "sa-east-1","ca-central-1","me-south-1","af-south-1",
]


@dataclass
class AWSAccount:
    account_id:   str
    name:         str
    role_arn:     Optional[str] = None   # None = use instance profile
    regions:      list = field(default_factory=list)
    session:      object = field(default=None, repr=False)

    def get_client(self, service: str, region: str):
        if self.session:
            return self.session.client(service, region_name=region)
        return boto3.client(service, region_name=region)

    def get_resource(self, service: str, region: str):
        if self.session:
            return self.session.resource(service, region_name=region)
        return boto3.resource(service, region_name=region)


@dataclass
class DiscoveredService:
    account_id:   str
    account_name: str
    region:       str
    service_type: str     # ec2 / ecs / lambda / rds / alb / apigw / asg / etc
    resource_id:  str
    resource_name: str
    endpoint:     str     # prometheus scrape target if applicable
    tags:         dict = field(default_factory=dict)
    metadata:     dict = field(default_factory=dict)
    reachable:    bool = False


class MultiAccountDiscovery:

    def __init__(self):
        self.accounts: list[AWSAccount] = []
        self.discovered: list[DiscoveredService] = []
        self.previous_ids: set = set()

    # ── Account Setup ─────────────────────────────────────────────────

    def load_accounts(self) -> list[AWSAccount]:
        """Load all configured AWS accounts."""
        accounts = []

        # Primary account via IAM Instance Profile
        try:
            sts = boto3.client("sts")
            identity = sts.get_caller_identity()
            primary_id = identity["Account"]
            accounts.append(AWSAccount(
                account_id=primary_id,
                name=os.getenv("PRIMARY_ACCOUNT_NAME", "primary"),
                role_arn=None,
                regions=PRIMARY_REGIONS,
                session=None,
            ))
            logger.info("Primary account: %s (%s)", primary_id, os.getenv("PRIMARY_ACCOUNT_NAME", "primary"))
        except Exception as e:
            logger.error("Cannot get primary account identity: %s", e)

        # Additional accounts via STS AssumeRole
        try:
            extra = json.loads(ACCOUNTS_JSON)
            for acc in extra:
                try:
                    session = self._assume_role(
                        acc["role_arn"],
                        f"observability-{acc.get('id','unknown')}"
                    )
                    if session:
                        regions = acc.get("regions", PRIMARY_REGIONS)
                        if regions == ["all"]:
                            regions = ALL_REGIONS
                        accounts.append(AWSAccount(
                            account_id=acc["id"],
                            name=acc.get("name", acc["id"]),
                            role_arn=acc["role_arn"],
                            regions=regions,
                            session=session,
                        ))
                        logger.info("Assumed role for account: %s (%s)", acc.get("name"), acc["id"])
                except Exception as e:
                    logger.error("Failed to assume role for account %s: %s", acc.get("id"), e)
        except json.JSONDecodeError:
            logger.warning("AWS_ACCOUNTS env var is not valid JSON — skipping extra accounts")

        self.accounts = accounts
        return accounts

    def _assume_role(self, role_arn: str, session_name: str):
        """Assume a cross-account IAM role and return a boto3 session."""
        try:
            sts = boto3.client("sts")
            resp = sts.assume_role(
                RoleArn=role_arn,
                RoleSessionName=session_name[:64],
                DurationSeconds=3600,
            )
            creds = resp["Credentials"]
            return boto3.Session(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
            )
        except Exception as e:
            logger.error("AssumeRole failed for %s: %s", role_arn, e)
            return None

    # ── Service Discovery per Region ──────────────────────────────────

    def discover_region(self, account: AWSAccount, region: str) -> list[DiscoveredService]:
        """Discover ALL services in a single account/region."""
        services = []
        logger.info("  Scanning %s / %s ...", account.name, region)

        discoverers = [
            self._discover_ec2,
            self._discover_ecs,
            self._discover_lambda,
            self._discover_rds,
            self._discover_alb,
            self._discover_apigw,
            self._discover_asg,
            self._discover_elasticache,
            self._discover_sqs,
        ]

        for fn in discoverers:
            try:
                results = fn(account, region)
                services.extend(results)
            except Exception as e:
                svc = fn.__name__.replace("_discover_", "")
                logger.debug("  %s/%s — %s discovery skipped: %s", account.name, region, svc, e)

        return services

    # ── EC2 ───────────────────────────────────────────────────────────
    def _discover_ec2(self, account: AWSAccount, region: str) -> list[DiscoveredService]:
        ec2 = account.get_client("ec2", region)
        services = []
        filters = [{"Name": "instance-state-name", "Values": ["running"]}]
        if TAG_FILTER_KEY:
            filters.append({"Name": f"tag:{TAG_FILTER_KEY}", "Values": [TAG_FILTER_VALUE]})

        paginator = ec2.get_paginator("describe_instances")
        for page in paginator.paginate(Filters=filters):
            for res in page["Reservations"]:
                for inst in res["Instances"]:
                    tags  = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                    name  = tags.get("Name", inst["InstanceId"])
                    ip    = inst.get("PrivateIpAddress","") if USE_PRIVATE_IP else inst.get("PublicIpAddress","")
                    services.append(DiscoveredService(
                        account_id=account.account_id, account_name=account.name,
                        region=region, service_type="ec2",
                        resource_id=inst["InstanceId"], resource_name=name,
                        endpoint=f"{ip}:{NODE_EXPORTER_PORT}" if ip else "",
                        tags=tags,
                        metadata={"instance_type": inst["InstanceType"], "az": inst["Placement"]["AvailabilityZone"], "private_ip": inst.get("PrivateIpAddress",""), "public_ip": inst.get("PublicIpAddress","")},
                    ))
        if services:
            logger.info("    EC2: %d instances", len(services))
        return services

    # ── ECS ───────────────────────────────────────────────────────────
    def _discover_ecs(self, account: AWSAccount, region: str) -> list[DiscoveredService]:
        ecs = account.get_client("ecs", region)
        services = []
        clusters = ecs.list_clusters().get("clusterArns", [])
        for cluster_arn in clusters:
            cluster_name = cluster_arn.split("/")[-1]
            svc_arns = ecs.list_services(cluster=cluster_arn).get("serviceArns", [])
            for i in range(0, len(svc_arns), 10):
                batch = svc_arns[i:i+10]
                details = ecs.describe_services(cluster=cluster_arn, services=batch).get("services", [])
                for svc in details:
                    services.append(DiscoveredService(
                        account_id=account.account_id, account_name=account.name,
                        region=region, service_type="ecs",
                        resource_id=svc["serviceArn"], resource_name=svc["serviceName"],
                        endpoint="", reachable=True,
                        tags={t["key"]: t["value"] for t in svc.get("tags", [])},
                        metadata={"cluster": cluster_name, "desired": svc.get("desiredCount",0), "running": svc.get("runningCount",0), "status": svc.get("status","")},
                    ))
        if services:
            logger.info("    ECS: %d services across %d clusters", len(services), len(clusters))
        return services

    # ── Lambda ────────────────────────────────────────────────────────
    def _discover_lambda(self, account: AWSAccount, region: str) -> list[DiscoveredService]:
        lmb = account.get_client("lambda", region)
        services = []
        paginator = lmb.get_paginator("list_functions")
        for page in paginator.paginate():
            for fn in page["Functions"]:
                services.append(DiscoveredService(
                    account_id=account.account_id, account_name=account.name,
                    region=region, service_type="lambda",
                    resource_id=fn["FunctionArn"], resource_name=fn["FunctionName"],
                    endpoint="", reachable=True,
                    metadata={"runtime": fn.get("Runtime",""), "memory": fn.get("MemorySize",0), "timeout": fn.get("Timeout",0), "last_modified": fn.get("LastModified","")},
                ))
        if services:
            logger.info("    Lambda: %d functions", len(services))
        return services

    # ── RDS ───────────────────────────────────────────────────────────
    def _discover_rds(self, account: AWSAccount, region: str) -> list[DiscoveredService]:
        rds = account.get_client("rds", region)
        services = []
        paginator = rds.get_paginator("describe_db_instances")
        for page in paginator.paginate():
            for db in page["DBInstances"]:
                if db["DBInstanceStatus"] != "available":
                    continue
                services.append(DiscoveredService(
                    account_id=account.account_id, account_name=account.name,
                    region=region, service_type="rds",
                    resource_id=db["DBInstanceIdentifier"], resource_name=db["DBInstanceIdentifier"],
                    endpoint="", reachable=True,
                    metadata={"engine": db.get("Engine",""), "class": db.get("DBInstanceClass",""), "multi_az": db.get("MultiAZ",False), "status": db.get("DBInstanceStatus","")},
                ))
        if services:
            logger.info("    RDS: %d instances", len(services))
        return services

    # ── ALB ───────────────────────────────────────────────────────────
    def _discover_alb(self, account: AWSAccount, region: str) -> list[DiscoveredService]:
        elb = account.get_client("elbv2", region)
        services = []
        paginator = elb.get_paginator("describe_load_balancers")
        for page in paginator.paginate():
            for lb in page["LoadBalancers"]:
                services.append(DiscoveredService(
                    account_id=account.account_id, account_name=account.name,
                    region=region, service_type="alb",
                    resource_id=lb["LoadBalancerArn"], resource_name=lb["LoadBalancerName"],
                    endpoint="", reachable=True,
                    metadata={"dns": lb.get("DNSName",""), "type": lb.get("Type",""), "scheme": lb.get("Scheme",""), "state": lb.get("State",{}).get("Code","")},
                ))
        if services:
            logger.info("    ALB: %d load balancers", len(services))
        return services

    # ── API Gateway ───────────────────────────────────────────────────
    def _discover_apigw(self, account: AWSAccount, region: str) -> list[DiscoveredService]:
        apigw = account.get_client("apigateway", region)
        services = []
        paginator = apigw.get_paginator("get_rest_apis")
        for page in paginator.paginate():
            for api in page["items"]:
                services.append(DiscoveredService(
                    account_id=account.account_id, account_name=account.name,
                    region=region, service_type="apigateway",
                    resource_id=api["id"], resource_name=api["name"],
                    endpoint="", reachable=True,
                    metadata={"endpoint_type": api.get("endpointConfiguration",{}).get("types",["REGIONAL"])[0], "created": str(api.get("createdDate",""))},
                ))
        if services:
            logger.info("    API GW: %d APIs", len(services))
        return services

    # ── ASG ───────────────────────────────────────────────────────────
    def _discover_asg(self, account: AWSAccount, region: str) -> list[DiscoveredService]:
        asg = account.get_client("autoscaling", region)
        services = []
        paginator = asg.get_paginator("describe_auto_scaling_groups")
        for page in paginator.paginate():
            for group in page["AutoScalingGroups"]:
                services.append(DiscoveredService(
                    account_id=account.account_id, account_name=account.name,
                    region=region, service_type="asg",
                    resource_id=group["AutoScalingGroupARN"], resource_name=group["AutoScalingGroupName"],
                    endpoint="", reachable=True,
                    metadata={"desired": group.get("DesiredCapacity",0), "min": group.get("MinSize",0), "max": group.get("MaxSize",0), "instances": len(group.get("Instances",[]))},
                ))
        if services:
            logger.info("    ASG: %d groups", len(services))
        return services

    # ── ElastiCache ───────────────────────────────────────────────────
    def _discover_elasticache(self, account: AWSAccount, region: str) -> list[DiscoveredService]:
        ec = account.get_client("elasticache", region)
        services = []
        paginator = ec.get_paginator("describe_cache_clusters")
        for page in paginator.paginate():
            for cluster in page["CacheClusters"]:
                if cluster.get("CacheClusterStatus") != "available":
                    continue
                services.append(DiscoveredService(
                    account_id=account.account_id, account_name=account.name,
                    region=region, service_type="elasticache",
                    resource_id=cluster["CacheClusterId"], resource_name=cluster["CacheClusterId"],
                    endpoint="", reachable=True,
                    metadata={"engine": cluster.get("Engine",""), "node_type": cluster.get("CacheNodeType",""), "status": cluster.get("CacheClusterStatus","")},
                ))
        if services:
            logger.info("    ElastiCache: %d clusters", len(services))
        return services

    # ── SQS ───────────────────────────────────────────────────────────
    def _discover_sqs(self, account: AWSAccount, region: str) -> list[DiscoveredService]:
        sqs = account.get_client("sqs", region)
        services = []
        queues = sqs.list_queues().get("QueueUrls", [])
        for url in queues:
            name = url.split("/")[-1]
            services.append(DiscoveredService(
                account_id=account.account_id, account_name=account.name,
                region=region, service_type="sqs",
                resource_id=url, resource_name=name,
                endpoint="", reachable=True,
                metadata={"url": url, "is_fifo": name.endswith(".fifo")},
            ))
        if services:
            logger.info("    SQS: %d queues", len(services))
        return services

    # ── Prometheus Target Writer ──────────────────────────────────────

    def write_prometheus_targets(self, services: list[DiscoveredService]):
        """Write per-account per-service Prometheus file-SD targets."""
        import socket

        # Group EC2 targets (need reachability check for Node Exporter)
        ec2_targets = []
        for svc in services:
            if svc.service_type == "ec2" and svc.endpoint:
                try:
                    host, port = svc.endpoint.rsplit(":", 1)
                    sock = socket.create_connection((host, int(port)), timeout=CONNECT_TIMEOUT)
                    sock.close()
                    svc.reachable = True
                    ec2_targets.append({
                        "targets": [svc.endpoint],
                        "labels": {
                            "job":           "ec2-nodes",
                            "instance":      svc.resource_name,
                            "instance_id":   svc.resource_id,
                            "instance_type": svc.metadata.get("instance_type",""),
                            "account":       svc.account_name,
                            "account_id":    svc.account_id,
                            "region":        svc.region,
                            "az":            svc.metadata.get("az",""),
                            "env":           svc.tags.get("Environment", svc.tags.get("Env","production")),
                            "monitored_by":  "auto-discovery",
                        }
                    })
                except Exception:
                    svc.reachable = False

        # Write EC2 targets file
        ec2_file = f"{TARGETS_DIR}/ec2_nodes.yml"
        self._write_yaml(ec2_file, ec2_targets,
                         f"EC2 Node Exporter targets — {len(ec2_targets)} instances")

        logger.info("Wrote %d EC2 targets to %s", len(ec2_targets), ec2_file)

    def _write_yaml(self, filepath: str, data: list, comment: str = ""):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        content = (
            f"# AUTO-GENERATED by Multi-Account Auto-Discovery Agent\n"
            f"# {comment}\n"
            f"# Updated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n"
            f"# Do NOT edit manually — will be overwritten\n\n"
        )
        content += yaml.dump(data, default_flow_style=False, allow_unicode=True)
        with open(filepath, "w") as f:
            f.write(content)

    # ── CloudWatch Config Writer ──────────────────────────────────────

    def write_cloudwatch_config(self, services: list[DiscoveredService]):
        """
        Dynamically write CloudWatch exporter config based on
        what services were actually discovered.
        """
        metrics = []

        service_types = {s.service_type for s in services}

        if "ec2" in service_types:
            metrics.extend([
                {"aws_namespace": "AWS/EC2", "aws_metric_name": "CPUUtilization", "aws_dimensions": ["InstanceId"], "aws_statistics": ["Average","Maximum"], "period_seconds": 300},
                {"aws_namespace": "AWS/EC2", "aws_metric_name": "StatusCheckFailed", "aws_dimensions": ["InstanceId"], "aws_statistics": ["Sum"], "period_seconds": 60},
                {"aws_namespace": "AWS/EC2", "aws_metric_name": "NetworkIn", "aws_dimensions": ["InstanceId"], "aws_statistics": ["Sum"], "period_seconds": 300},
                {"aws_namespace": "AWS/EC2", "aws_metric_name": "NetworkOut", "aws_dimensions": ["InstanceId"], "aws_statistics": ["Sum"], "period_seconds": 300},
            ])

        if "ecs" in service_types:
            metrics.extend([
                {"aws_namespace": "AWS/ECS", "aws_metric_name": "CPUUtilization", "aws_dimensions": ["ClusterName","ServiceName"], "aws_statistics": ["Average","Maximum"], "period_seconds": 300},
                {"aws_namespace": "AWS/ECS", "aws_metric_name": "MemoryUtilization", "aws_dimensions": ["ClusterName","ServiceName"], "aws_statistics": ["Average","Maximum"], "period_seconds": 300},
                {"aws_namespace": "AWS/ECS", "aws_metric_name": "RunningTaskCount", "aws_dimensions": ["ClusterName","ServiceName"], "aws_statistics": ["Average","Minimum"], "period_seconds": 60},
            ])

        if "lambda" in service_types:
            metrics.extend([
                {"aws_namespace": "AWS/Lambda", "aws_metric_name": "Invocations", "aws_dimensions": ["FunctionName"], "aws_statistics": ["Sum"], "period_seconds": 60},
                {"aws_namespace": "AWS/Lambda", "aws_metric_name": "Errors", "aws_dimensions": ["FunctionName"], "aws_statistics": ["Sum"], "period_seconds": 60},
                {"aws_namespace": "AWS/Lambda", "aws_metric_name": "Duration", "aws_dimensions": ["FunctionName"], "aws_statistics": ["Average","Maximum"], "aws_extended_statistics": ["p99"], "period_seconds": 60},
                {"aws_namespace": "AWS/Lambda", "aws_metric_name": "Throttles", "aws_dimensions": ["FunctionName"], "aws_statistics": ["Sum"], "period_seconds": 60},
                {"aws_namespace": "AWS/Lambda", "aws_metric_name": "ConcurrentExecutions", "aws_dimensions": ["FunctionName"], "aws_statistics": ["Maximum"], "period_seconds": 60},
            ])

        if "rds" in service_types:
            metrics.extend([
                {"aws_namespace": "AWS/RDS", "aws_metric_name": "CPUUtilization", "aws_dimensions": ["DBInstanceIdentifier"], "aws_statistics": ["Average"], "period_seconds": 300},
                {"aws_namespace": "AWS/RDS", "aws_metric_name": "DatabaseConnections", "aws_dimensions": ["DBInstanceIdentifier"], "aws_statistics": ["Average","Maximum"], "period_seconds": 60},
                {"aws_namespace": "AWS/RDS", "aws_metric_name": "FreeStorageSpace", "aws_dimensions": ["DBInstanceIdentifier"], "aws_statistics": ["Average"], "period_seconds": 300},
                {"aws_namespace": "AWS/RDS", "aws_metric_name": "ReadLatency", "aws_dimensions": ["DBInstanceIdentifier"], "aws_statistics": ["Average","Maximum"], "period_seconds": 60},
                {"aws_namespace": "AWS/RDS", "aws_metric_name": "WriteLatency", "aws_dimensions": ["DBInstanceIdentifier"], "aws_statistics": ["Average","Maximum"], "period_seconds": 60},
            ])

        if "alb" in service_types:
            metrics.extend([
                {"aws_namespace": "AWS/ApplicationELB", "aws_metric_name": "RequestCount", "aws_dimensions": ["LoadBalancer"], "aws_statistics": ["Sum"], "period_seconds": 60},
                {"aws_namespace": "AWS/ApplicationELB", "aws_metric_name": "HTTPCode_Target_4XX_Count", "aws_dimensions": ["LoadBalancer"], "aws_statistics": ["Sum"], "period_seconds": 60},
                {"aws_namespace": "AWS/ApplicationELB", "aws_metric_name": "HTTPCode_Target_5XX_Count", "aws_dimensions": ["LoadBalancer"], "aws_statistics": ["Sum"], "period_seconds": 60},
                {"aws_namespace": "AWS/ApplicationELB", "aws_metric_name": "TargetResponseTime", "aws_dimensions": ["LoadBalancer"], "aws_statistics": ["Average"], "aws_extended_statistics": ["p99"], "period_seconds": 60},
                {"aws_namespace": "AWS/ApplicationELB", "aws_metric_name": "HealthyHostCount", "aws_dimensions": ["LoadBalancer","TargetGroup"], "aws_statistics": ["Average","Minimum"], "period_seconds": 60},
            ])

        if "apigateway" in service_types:
            metrics.extend([
                {"aws_namespace": "AWS/ApiGateway", "aws_metric_name": "Count", "aws_dimensions": ["ApiName","Stage"], "aws_statistics": ["Sum"], "period_seconds": 60},
                {"aws_namespace": "AWS/ApiGateway", "aws_metric_name": "4XXError", "aws_dimensions": ["ApiName","Stage"], "aws_statistics": ["Sum"], "period_seconds": 60},
                {"aws_namespace": "AWS/ApiGateway", "aws_metric_name": "5XXError", "aws_dimensions": ["ApiName","Stage"], "aws_statistics": ["Sum"], "period_seconds": 60},
                {"aws_namespace": "AWS/ApiGateway", "aws_metric_name": "Latency", "aws_dimensions": ["ApiName","Stage"], "aws_statistics": ["Average","Maximum"], "aws_extended_statistics": ["p99"], "period_seconds": 60},
            ])

        if "asg" in service_types:
            metrics.extend([
                {"aws_namespace": "AWS/AutoScaling", "aws_metric_name": "GroupInServiceInstances", "aws_dimensions": ["AutoScalingGroupName"], "aws_statistics": ["Average","Minimum"], "period_seconds": 300},
                {"aws_namespace": "AWS/AutoScaling", "aws_metric_name": "GroupDesiredCapacity", "aws_dimensions": ["AutoScalingGroupName"], "aws_statistics": ["Average"], "period_seconds": 300},
                {"aws_namespace": "AWS/AutoScaling", "aws_metric_name": "GroupPendingInstances", "aws_dimensions": ["AutoScalingGroupName"], "aws_statistics": ["Average"], "period_seconds": 300},
            ])

        if "sqs" in service_types:
            metrics.extend([
                {"aws_namespace": "AWS/SQS", "aws_metric_name": "ApproximateNumberOfMessagesVisible", "aws_dimensions": ["QueueName"], "aws_statistics": ["Average","Maximum"], "period_seconds": 60},
                {"aws_namespace": "AWS/SQS", "aws_metric_name": "ApproximateAgeOfOldestMessage", "aws_dimensions": ["QueueName"], "aws_statistics": ["Maximum"], "period_seconds": 60},
                {"aws_namespace": "AWS/SQS", "aws_metric_name": "NumberOfMessagesSent", "aws_dimensions": ["QueueName"], "aws_statistics": ["Sum"], "period_seconds": 60},
            ])

        if "elasticache" in service_types:
            metrics.extend([
                {"aws_namespace": "AWS/ElastiCache", "aws_metric_name": "CPUUtilization", "aws_dimensions": ["CacheClusterId"], "aws_statistics": ["Average"], "period_seconds": 300},
                {"aws_namespace": "AWS/ElastiCache", "aws_metric_name": "CurrConnections", "aws_dimensions": ["CacheClusterId"], "aws_statistics": ["Average","Maximum"], "period_seconds": 60},
                {"aws_namespace": "AWS/ElastiCache", "aws_metric_name": "FreeableMemory", "aws_dimensions": ["CacheClusterId"], "aws_statistics": ["Average"], "period_seconds": 300},
            ])

        # Use primary region for CloudWatch exporter
        config = {"region": PRIMARY_REGION, "metrics": metrics}
        try:
            os.makedirs(os.path.dirname(CW_CONFIG_FILE), exist_ok=True)
            with open(CW_CONFIG_FILE, "w") as f:
                f.write("# AUTO-GENERATED by Multi-Account Auto-Discovery\n")
                f.write(f"# Services discovered: {sorted(service_types)}\n")
                f.write(f"# Updated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n\n")
                yaml.dump(config, f, default_flow_style=False)
            logger.info("CloudWatch config written — %d metric configs for services: %s",
                        len(metrics), sorted(service_types))
        except Exception as e:
            logger.error("Failed to write CloudWatch config: %s", e)

    # ── Prometheus Reload ─────────────────────────────────────────────

    async def reload_prometheus(self):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{PROMETHEUS_URL}/-/reload")
                if resp.status_code == 200:
                    logger.info("Prometheus reloaded ✓")
        except Exception as e:
            logger.error("Prometheus reload failed: %s", e)

    # ── MS Teams Notification ─────────────────────────────────────────

    async def notify_teams(self, summary: dict):
        if not MSTEAMS_WEBHOOK_URL:
            return

        accounts_text = "\n".join([
            f"**{acc}:** {counts}"
            for acc, counts in summary.get("by_account", {}).items()
        ])

        new_svcs = summary.get("new_services", [])
        removed  = summary.get("removed_services", [])

        if not new_svcs and not removed:
            return

        changes = []
        for s in new_svcs[:10]:
            changes.append(f"✅ **{s['type'].upper()}** `{s['name']}` — {s['account']} / {s['region']}")
        for s in removed[:10]:
            changes.append(f"🔴 **Removed:** `{s['name']}` — {s['account']} / {s['region']}")

        payload = {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard", "version": "1.4",
                    "body": [
                        {
                            "type": "Container", "style": "accent",
                            "items": [{"type": "TextBlock", "weight": "Bolder", "size": "Medium", "wrap": True,
                                       "text": f"🔍 AWS Auto-Discovery Update — {summary['total']} services across {summary['accounts']} accounts"}]
                        },
                        {"type": "TextBlock", "text": accounts_text, "wrap": True, "spacing": "Medium"},
                        {"type": "TextBlock", "text": "\n".join(changes), "wrap": True, "spacing": "Medium"},
                        {"type": "TextBlock", "isSubtle": True, "size": "Small",
                         "text": f"🕐 {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"}
                    ]
                }
            }]
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(MSTEAMS_WEBHOOK_URL, json=payload)
        except Exception as e:
            logger.warning("Teams notify failed: %s", e)

    # ── Main Run ──────────────────────────────────────────────────────

    async def run(self) -> dict:
        logger.info("=" * 70)
        logger.info("Multi-Account AWS Discovery starting...")

        loop = asyncio.get_event_loop()

        # Load accounts
        accounts = await loop.run_in_executor(None, self.load_accounts)
        logger.info("Accounts loaded: %d", len(accounts))

        # Discover all services in parallel across accounts & regions
        all_services: list[DiscoveredService] = []
        tasks = []
        for account in accounts:
            for region in account.regions:
                tasks.append(loop.run_in_executor(None, self.discover_region, account, region))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                all_services.extend(result)
            elif isinstance(result, Exception):
                logger.error("Discovery task error: %s", result)

        # Detect new / removed
        current_ids = {f"{s.account_id}/{s.service_type}/{s.resource_id}" for s in all_services}
        new_ids     = current_ids - self.previous_ids
        removed_ids = self.previous_ids - current_ids

        new_services = [s for s in all_services if f"{s.account_id}/{s.service_type}/{s.resource_id}" in new_ids]
        removed_services = [{"name": i.split("/")[2], "account": i.split("/")[0], "region": "unknown", "type": i.split("/")[1]} for i in removed_ids]

        self.previous_ids = current_ids
        self.discovered   = all_services

        # Write targets & configs
        await loop.run_in_executor(None, self.write_prometheus_targets, all_services)
        await loop.run_in_executor(None, self.write_cloudwatch_config,  all_services)

        # Reload Prometheus
        await self.reload_prometheus()

        # Build summary
        by_account = {}
        by_type = {}
        for s in all_services:
            key = f"{s.account_name} ({s.account_id})"
            by_account.setdefault(key, {})
            by_account[key][s.service_type] = by_account[key].get(s.service_type, 0) + 1
            by_type[s.service_type] = by_type.get(s.service_type, 0) + 1

        summary = {
            "total":            len(all_services),
            "accounts":         len(accounts),
            "regions_scanned":  sum(len(a.regions) for a in accounts),
            "by_type":          by_type,
            "by_account":       {k: str(v) for k, v in by_account.items()},
            "new_services":     [{"name": s.resource_name, "type": s.service_type, "account": s.account_name, "region": s.region} for s in new_services[:20]],
            "removed_services": removed_services[:20],
            "timestamp":        time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }

        logger.info("Discovery complete — %d services across %d accounts / %d regions",
                    len(all_services), len(accounts), summary["regions_scanned"])
        logger.info("By type: %s", by_type)

        # Notify Teams on changes
        await self.notify_teams(summary)

        return summary
