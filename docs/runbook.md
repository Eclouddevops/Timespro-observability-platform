# 📖 Observability Platform — Operations Runbook

> **Purpose:** Step-by-step procedures for operating, maintaining, and troubleshooting the observability platform on EC2 `observability-server` (`i-0b7b07809dc9007ac` / `54.88.150.33`)
>
> **Audience:** DevOps Engineers, SREs, On-Call Engineers
>
> **Last Updated:** October 2026

---

## 📋 Table of Contents

1. [Platform Architecture](#1-platform-architecture)
2. [Critical Files Reference](#2-critical-files-reference)
3. [Service Health Check](#3-service-health-check)
4. [Alert Response Procedures](#4-alert-response-procedures)
5. [Adding New Resources](#5-adding-new-resources)
6. [Day-to-Day Operations](#6-day-to-day-operations)
7. [Backup & Recovery](#7-backup--recovery)
8. [Troubleshooting Guide](#8-troubleshooting-guide)
9. [Escalation Matrix](#9-escalation-matrix)

---

## 1. Platform Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    observability-server (54.88.150.33)                   │
│                         Ubuntu · t3.medium                               │
│                                                                          │
│  ┌──────────────┐   ┌──────────────┐   ┌─────────────────────────────┐ │
│  │  Prometheus  │   │   Grafana    │   │      Alertmanager           │ │
│  │  :9090       │◄──│  :3000       │   │      :9093                  │ │
│  │              │   │  9 Dashboards│   │  P1/P2/P3 Routing           │ │
│  └──────┬───────┘   └──────────────┘   └──────────┬──────────────────┘ │
│         │                                          │                     │
│         │ scrapes metrics                          │ fires alerts        │
│         ▼                                          ▼                     │
│  ┌──────────────────────────────────┐  ┌──────────────────────────────┐ │
│  │        Exporters / Agents        │  │      Notification Channels   │ │
│  │                                  │  │                              │ │
│  │  node-exporter    :9100 (host)   │  │  📧 Email (AWS SES)         │ │
│  │  windows-exporter :9182 (Win)    │  │     Santosh.mirajkar@...    │ │
│  │  cadvisor         :8080          │  │     samirajkar99@gmail.com  │ │
│  │  cloudwatch-exp   :9106          │  │                              │ │
│  │  blackbox-exp     :9115          │  │  💬 MS Teams (webhook)      │ │
│  │  ssl-exporter     :9219          │  │                              │ │
│  │  auto-discovery   :9877          │  │  🤖 AI Agent analysis       │ │
│  │  ai-agent         :8888          │  └──────────────────────────────┘ │
│  └──────────────────────────────────┘                                   │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐│
│  │  Log Stack: Loki :3100  ←  Promtail (tails /var/log + containers)  ││
│  └─────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────┘
         │
         │ monitors
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    AWS Account: 496251222247                             │
│                                                                          │
│  EC2 Instances          ECS Clusters         Lambda Functions           │
│  ┌──────────────┐       ┌─────────────┐      ┌──────────────────┐      │
│  │observability │       │  Services   │      │   Functions      │      │
│  │-server       │       │  Tasks      │      │   Errors/Throttle│      │
│  │windows-server│       │  CPU/Mem    │      │   Duration       │      │
│  └──────────────┘       └─────────────┘      └──────────────────┘      │
│                                                                          │
│  API Gateway            WAF Policies          SSL Certificates          │
│  RDS Databases          SQS Queues            ALB / ELB                 │
│  Auto Scaling Groups    ElastiCache                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Critical Files Reference

> ⚠️ **These files control the entire platform. Handle with care.**

```
observability-platform/
│
├── ⭐ docker-compose.yml          ← CRITICAL: Defines all 12 services
│                                    Edit to add/remove services or change ports
│
├── ⭐ .env                        ← CRITICAL: All secrets & configuration
│       (copy from .env.example)    NEVER commit to git
│
├── prometheus/
│   ├── ⭐ prometheus.yml          ← CRITICAL: What gets scraped, how often
│   │                                Edit to add new scrape jobs
│   ├── rules/
│   │   ├── ⭐ node_alerts.yml     ← CRITICAL: EC2/Linux alert thresholds (P1/P2/P3)
│   │   ├── ⭐ aws_alerts.yml      ← CRITICAL: AWS service alerts (ECS/Lambda/WAF)
│   │   └── ⭐ windows_alerts.yml  ← CRITICAL: Windows Server alerts
│   └── targets/
│       ├── ⭐ ec2_nodes.yml       ← Add new Linux EC2 instances here
│       ├── ⭐ windows_nodes.yml   ← Add new Windows EC2 instances here
│       ├── ⭐ websites.yml        ← Add URLs to monitor here
│       ├── ⭐ ssl_targets.yml     ← Add domains for SSL expiry check
│       └──   ping_targets.yml     ← Add hosts for ICMP ping
│
├── alertmanager/
│   ├── ⭐ alertmanager.yml        ← CRITICAL: P1/P2/P3 routing + email recipients
│   │                                Edit to change alert destinations
│   └── templates/
│       └── ⭐ email.tmpl          ← HTML email template (rich formatting)
│
├── grafana/
│   ├── dashboards/
│   │   ├── ⭐ 06-ec2-status.json  ← Main EC2 dashboard (Linux + Windows)
│   │   ├── ⭐ 00-overview.json    ← AWS Infrastructure overview
│   │   └──   (7 more dashboards)
│   └── provisioning/
│       ├── ⭐ datasources/        ← Prometheus + Loki auto-configured
│       └── ⭐ dashboards/         ← Auto-loads all dashboard JSONs
│
├── exporters/
│   ├── ⭐ cloudwatch/config.yml   ← Which AWS metrics to pull (EC2/ECS/Lambda...)
│   └──   blackbox/config.yml      ← URL probe modules (HTTP/HTTPS/TCP/ICMP)
│
├── auto-discovery/
│   └── ⭐ app/multi_account.py    ← CRITICAL: Multi-account AWS discovery logic
│
├── scripts/
│   ├── ⭐ install.sh              ← One-command server bootstrap
│   ├── ⭐ manage.sh               ← Day-to-day operations (start/stop/backup)
│   ├── ⭐ setup-ses.sh            ← One-time AWS SES email setup
│   └── ⭐ load-smtp-secret.sh     ← Load email credentials from Secrets Manager
│
└── .github/workflows/
    ├── ⭐ deploy.yml              ← CI/CD: validates + deploys on git push
    └──   sync-dashboards.yml      ← Syncs dashboards to Grafana on push
```

---

## 3. Service Health Check

### Quick Status (run this first thing every morning)

```bash
ssh ubuntu@54.88.150.33
cd /observability-platform

# ── Full health check ──────────────────────────────────────────────
./scripts/manage.sh status
```

### Expected Output

```
NAME                    STATUS          PORTS
prometheus              Up 2 days       0.0.0.0:9090->9090/tcp
grafana                 Up 2 days       0.0.0.0:3000->3000/tcp
alertmanager            Up 2 days       0.0.0.0:9093->9093/tcp
node-exporter           Up 2 days       (host service on :9100)
cadvisor                Up 2 days       0.0.0.0:8080->8080/tcp
cloudwatch-exporter     Up 2 days       0.0.0.0:9106->9106/tcp
blackbox-exporter       Up 2 days       0.0.0.0:9115->9115/tcp
ssl-exporter            Up 2 days       0.0.0.0:9219->9219/tcp
loki                    Up 2 days       0.0.0.0:3100->3100/tcp
ai-observability-agent  Up 2 days       0.0.0.0:8888->8888/tcp
alertmanager-msteams    Up 2 days       0.0.0.0:2000->2000/tcp
ec2-auto-discovery      Up 2 days       0.0.0.0:9877->9877/tcp
nginx-proxy             Up 2 days       0.0.0.0:80->80, 443->443/tcp
```

### Deep Health Check

```bash
# Check all Prometheus targets are UP
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import json, sys
data = json.load(sys.stdin)
all_up = True
for t in data['data']['activeTargets']:
    job    = t['labels'].get('job','?')
    health = t['health']
    error  = t.get('lastError','')[:60]
    icon   = '✅' if health == 'up' else '❌'
    if health != 'up': all_up = False
    print(f'{icon}  {job:30} {health}  {error}')
print()
print('Overall:', '✅ ALL UP' if all_up else '❌ ISSUES FOUND')
"
```

### Service Endpoints

| Service | URL | Expected Response |
|---------|-----|------------------|
| Grafana | http://54.88.150.33:3000 | Login page |
| Prometheus | http://54.88.150.33:9090 | Graph UI |
| Alertmanager | http://54.88.150.33:9093 | Alert list |
| AI Agent | http://54.88.150.33:8888/health | `{"status":"ok"}` |
| Auto-Discovery | http://54.88.150.33:9877/health | `{"status":"ok"}` |
| Node Exporter | http://54.88.150.33:9100/metrics | Raw metrics text |

---

## 4. Alert Response Procedures

### Alert Priority Overview

```
ALERT RECEIVED
      │
      ▼
┌─────────────────┐
│  Check Priority  │
└─────────────────┘
      │
      ├──── P1 (CRITICAL) ──► Server down / SSL expired / Service outage
      │         │               Response: IMMEDIATE (< 15 minutes)
      │         ▼               Email: Every 1 hour until resolved
      │    Escalate NOW
      │
      ├──── P2 (HIGH) ──────► High CPU/RAM / API errors / ECS issues
      │         │               Response: Within 1 hour
      │         ▼               Email: Every 4 hours until resolved
      │    Investigate
      │
      └──── P3 (MEDIUM) ───► Disk warning / Slow response / Load
                │               Response: Within 4 hours
                ▼               Email: Every 12 hours until resolved
           Monitor & Plan
```

---

### P1: Instance Down (`InstanceDown`)

**Symptoms:** Server completely unreachable, no metrics

```bash
# Step 1 — Verify in Prometheus
curl -s "http://localhost:9090/api/v1/query?query=up{instance='<SERVER>'}" | python3 -m json.tool

# Step 2 — Check EC2 status in AWS Console
aws ec2 describe-instance-status \
    --instance-ids i-0b7b07809dc9007ac \
    --region us-east-1 \
    --output table

# Step 3 — Try to ping
ping -c 3 54.88.150.33

# Step 4 — Check security group allows port 9100
aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=default" \
    --region us-east-1 \
    --query 'SecurityGroups[].IpPermissions'

# Step 5 — If EC2 is running but node-exporter is down
ssh ubuntu@54.88.150.33 "sudo systemctl status node_exporter"
ssh ubuntu@54.88.150.33 "sudo systemctl restart node_exporter"

# Step 6 — Confirm recovery
curl -s http://54.88.150.33:9100/metrics | head -3
```

---

### P1: Website Down (`WebsiteDown`)

**Symptoms:** URL probe failing, users cannot access the site

```bash
# Step 1 — Manual probe test
curl -I https://aiobz.com
curl -I https://www.aiobz.com

# Step 2 — Check Blackbox Exporter directly
curl -s "http://localhost:9115/probe?target=https://aiobz.com&module=http_2xx"

# Step 3 — DNS check
nslookup aiobz.com
dig aiobz.com

# Step 4 — Check if it's behind an ALB/ECS — verify target health
aws elbv2 describe-target-health \
    --target-group-arn <YOUR_TG_ARN> \
    --region us-east-1

# Step 5 — Reload Prometheus if probe config changed
curl -X POST http://localhost:9090/-/reload && echo "Reloaded"
```

---

### P1: SSL Certificate Expired / Expiring (`SSLCertExpiringCritical`)

**Symptoms:** SSL alert firing, browsers show security warning

```bash
# Step 1 — Check expiry manually
echo | openssl s_client -connect aiobz.com:443 2>/dev/null | \
    openssl x509 -noout -dates

# Step 2 — Check all monitored certs
curl -s "http://localhost:9090/api/v1/query?query=(ssl_cert_not_after-time())/86400" | \
    python3 -c "
import json, sys
d = json.load(sys.stdin)
for r in d['data']['result']:
    domain = r['metric'].get('instance','?')
    days   = float(r['value'][1])
    icon   = '🔴' if days < 7 else '🟡' if days < 30 else '✅'
    print(f'{icon}  {domain:40} {days:.0f} days remaining')
"

# Step 3 — Renew via Let's Encrypt (if applicable)
sudo certbot renew --dry-run
sudo certbot renew

# Step 4 — Update ssl_targets.yml if new domain added
nano prometheus/targets/ssl_targets.yml
curl -X POST http://localhost:9090/-/reload
```

---

### P1: ECS Service Has No Tasks (`ECSTaskStopped`)

```bash
# Step 1 — List cluster and services
aws ecs list-clusters --region us-east-1
aws ecs list-services --cluster <CLUSTER_NAME> --region us-east-1

# Step 2 — Check service events (shows why tasks stopped)
aws ecs describe-services \
    --cluster <CLUSTER_NAME> \
    --services <SERVICE_NAME> \
    --region us-east-1 \
    --query 'services[0].events[:5]'

# Step 3 — Check stopped task reason
aws ecs list-tasks \
    --cluster <CLUSTER_NAME> \
    --desired-status STOPPED \
    --region us-east-1

aws ecs describe-tasks \
    --cluster <CLUSTER_NAME> \
    --tasks <TASK_ARN> \
    --region us-east-1 \
    --query 'tasks[0].stoppedReason'

# Step 4 — Force new deployment
aws ecs update-service \
    --cluster <CLUSTER_NAME> \
    --service <SERVICE_NAME> \
    --force-new-deployment \
    --region us-east-1
```

---

### P2: High CPU Usage (`HighCPUUsage` / `CriticalCPUUsage`)

```bash
# Step 1 — See which instance
curl -s "http://localhost:9090/api/v1/query?query=100-(avg+by(instance)(irate(node_cpu_seconds_total{mode='idle'}[5m]))*100)" | \
    python3 -c "
import json, sys
d = json.load(sys.stdin)
for r in d['data']['result']:
    inst = r['metric'].get('instance','?')
    val  = float(r['value'][1])
    print(f'{inst}: {val:.1f}%')
"

# Step 2 — SSH and find top processes
ssh ubuntu@54.88.150.33 "top -bn1 | head -20"
ssh ubuntu@54.88.150.33 "ps aux --sort=-%cpu | head -10"

# Step 3 — Check if it's a container
docker stats --no-stream

# Step 4 — If auto-discovery is scanning too fast, slow it down
# Edit .env: DISCOVERY_INTERVAL_MINUTES=5
nano .env
docker compose restart auto-discovery
```

---

### P2: High Memory Usage (`HighMemoryUsage`)

```bash
# Step 1 — Check current usage
curl -s "http://localhost:9090/api/v1/query?query=(1-(node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes))*100" | \
    python3 -c "import json,sys; [print(f\"{r['metric'].get('instance','?')}: {float(r['value'][1]):.1f}%\") for r in json.load(sys.stdin)['data']['result']]"

# Step 2 — SSH and investigate
ssh ubuntu@54.88.150.33 "free -h"
ssh ubuntu@54.88.150.33 "ps aux --sort=-%mem | head -10"

# Step 3 — Check Docker container memory
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}"

# Step 4 — Restart memory-heavy container if needed
docker compose restart grafana  # or whichever is high
```

---

### P1: Disk Space Critical (`DiskSpaceCritical`)

```bash
# Step 1 — Check usage
df -h

# Step 2 — Find large files/directories
du -sh /var/log/* | sort -rh | head -10
du -sh /var/lib/docker/* | sort -rh | head -5

# Step 3 — Clean Docker unused images and containers
docker system prune -f
docker image prune -f

# Step 4 — Rotate logs
sudo logrotate -f /etc/logrotate.conf

# Step 5 — Check Prometheus data size
du -sh /var/lib/docker/volumes/*prometheus*

# Step 6 — If critical, reduce Prometheus retention
# Edit docker-compose.yml:
#   --storage.tsdb.retention.time=15d  (reduce from 30d)
nano docker-compose.yml
docker compose restart prometheus
```

---

## 5. Adding New Resources

### Add a New Linux EC2 Instance

```
WORKFLOW:
  ┌─────────────────────────────────────┐
  │  1. Install Node Exporter on EC2    │
  │     scripts/install-node-exporter.sh│
  └──────────────┬──────────────────────┘
                 │
  ┌──────────────▼──────────────────────┐
  │  2. Add IP to targets file          │
  │     prometheus/targets/ec2_nodes.yml│
  └──────────────┬──────────────────────┘
                 │
  ┌──────────────▼──────────────────────┐
  │  3. Open port 9100 in Security Group│
  │     AWS Console → EC2 → SG → Rules  │
  └──────────────┬──────────────────────┘
                 │
  ┌──────────────▼──────────────────────┐
  │  4. Reload Prometheus (no restart)  │
  │     curl -XPOST localhost:9090/-/.. │
  └──────────────┬──────────────────────┘
                 │
  ┌──────────────▼──────────────────────┐
  │  5. Verify in Prometheus Targets UI │
  │     http://54.88.150.33:9090/targets│
  └─────────────────────────────────────┘
```

```bash
# Step 1 — Install Node Exporter remotely
bash scripts/install-node-exporter.sh <private-ip>

# Step 2 — Add to targets file
cat >> prometheus/targets/ec2_nodes.yml << EOF
- targets:
    - '<private-ip>:9100'
  labels:
    instance: 'my-new-server'
    env: production
    region: us-east-1
EOF

# Step 3 — Reload Prometheus
curl -X POST http://localhost:9090/-/reload && echo "✅ Reloaded"

# Step 4 — Verify
sleep 10
curl -s "http://localhost:9090/api/v1/query?query=up{instance='<private-ip>:9100'}" | \
    python3 -c "import json,sys; d=json.load(sys.stdin); print('✅ UP' if d['data']['result'][0]['value'][1]=='1' else '❌ DOWN')"
```

---

### Add a New Website to Monitor

```bash
# Edit websites.yml
nano prometheus/targets/websites.yml

# Add entry:
# - targets:
#     - 'https://newwebsite.com'
#   labels:
#     env: production
#     service: website

# Edit ssl_targets.yml (for SSL expiry check)
nano prometheus/targets/ssl_targets.yml

# Add entry:
# - targets:
#     - 'newwebsite.com:443'
#   labels:
#     env: production

# Reload
curl -X POST http://localhost:9090/-/reload
```

---

### Add a New AWS Account

```bash
# 1. Create IAM role in target account (trust policy):
#    Principal: arn:aws:iam::496251222247:role/ObservabilityServerRole
#    Permissions: ObservabilityPlatformPolicy

# 2. Add to .env
nano .env

# Append to AWS_ACCOUNTS:
# AWS_ACCOUNTS=[
#   {
#     "id": "NEW_ACCOUNT_ID",
#     "name": "my-new-account",
#     "role_arn": "arn:aws:iam::NEW_ACCOUNT_ID:role/ObservabilityReadOnlyRole",
#     "regions": ["us-east-1", "eu-west-1"]
#   }
# ]

# 3. Restart auto-discovery
docker compose restart auto-discovery

# 4. Watch it discover the new account
docker logs ec2-auto-discovery -f
```

---

### Add a New Windows EC2 Instance

```bash
# Step 1: Install Windows Exporter on the Windows server (via RDP)
# PowerShell on Windows:
# $url = "https://github.com/prometheus-community/windows_exporter/releases/download/v0.25.1/windows_exporter-0.25.1-amd64.msi"
# Invoke-WebRequest -Uri $url -OutFile "$env:TEMP\we.msi" -UseBasicParsing
# Start-Process msiexec.exe -ArgumentList "/i `"$env:TEMP\we.msi`" /quiet LISTEN_PORT=9182" -Wait

# Step 2: Add to windows_nodes.yml
cat >> prometheus/targets/windows_nodes.yml << EOF
- targets:
    - '<windows-public-ip>:9182'
  labels:
    instance: 'my-windows-server'
    instance_type: 't3.large'
    env: production
    region: us-east-1
    os: windows
EOF

# Step 3: Open port 9182 in windows server Security Group
# AWS Console → EC2 → Security Groups → Add Inbound: TCP 9182 from 54.88.150.33/32

# Step 4: Reload
curl -X POST http://localhost:9090/-/reload && echo "✅ Reloaded"
```

---

## 6. Day-to-Day Operations

### Daily Checklist (5 minutes)

```
□  Open http://54.88.150.33:3000 → Overview dashboard
□  Check "Instances DOWN" stat = 0
□  Check "SSL Certs Expiring" stat = 0 (or handled)
□  Check "Websites DOWN" stat = 0
□  Check Alertmanager: http://54.88.150.33:9093 — no unacknowledged P1s
□  Verify no new emails from alerts@timesgroup.com
```

### Useful Daily Commands

```bash
# Daily health snapshot
curl -s http://localhost:9877/status | python3 -m json.tool | grep -E '"total|"accounts|"by_type'

# Check firing alerts
curl -s http://localhost:9093/api/v2/alerts | python3 -c "
import json, sys
alerts = json.load(sys.stdin)
if not alerts:
    print('✅ No firing alerts')
else:
    for a in alerts:
        print(f\"🚨 {a['labels'].get('alertname')} [{a['labels'].get('priority','?')}] — {a['labels'].get('instance','')}\")
"

# Check SSL expiry
curl -s "http://localhost:9090/api/v1/query?query=(ssl_cert_not_after-time())/86400" | \
    python3 -c "
import json, sys
d = json.load(sys.stdin)
for r in d['data']['result']:
    days = float(r['value'][1])
    icon = '🔴' if days<7 else '🟡' if days<30 else '✅'
    print(f\"{icon} {r['metric'].get('instance','?'):40} {days:.0f} days\")
"
```

### Updating Configuration via Git

```
WORKFLOW: Git-based configuration change
  ┌──────────────────────────────────┐
  │  1. Make changes locally         │
  │     (edit YAML/JSON files)       │
  └──────────────┬───────────────────┘
                 │
  ┌──────────────▼───────────────────┐
  │  2. Commit and push to GitHub    │
  │     git add . && git commit -m   │
  │     git push origin main         │
  └──────────────┬───────────────────┘
                 │
  ┌──────────────▼───────────────────┐
  │  3. GitHub Actions validates     │
  │     - Prometheus rule check      │
  │     - Config validation          │
  │     - JSON schema check          │
  └──────────────┬───────────────────┘
                 │
  ┌──────────────▼───────────────────┐
  │  4. Auto-deploy via SSH to EC2   │
  │     (on merge to main branch)    │
  └──────────────┬───────────────────┘
                 │
  ┌──────────────▼───────────────────┐
  │  5. Prometheus hot-reload        │
  │     (no downtime)                │
  └──────────────┬───────────────────┘
                 │
  ┌──────────────▼───────────────────┐
  │  6. Slack/Teams notification     │
  │     "Deployment successful"      │
  └──────────────────────────────────┘
```

```bash
# On server — pull latest changes manually
cd /observability-platform
git pull origin main
curl -X POST http://localhost:9090/-/reload   # for prometheus changes
docker compose restart grafana                 # for dashboard changes
docker compose restart alertmanager           # for alert routing changes
```

---

## 7. Backup & Recovery

### Backup Everything

```bash
cd /observability-platform
./scripts/manage.sh backup

# Manual backup location:
ls -lh backups/
```

### What Gets Backed Up

```
backups/
└── 20261005_083000/
    ├── grafana/          ← All dashboards + data sources + users
    └── prometheus/       ← TSDB snapshot (metric history)
```

### Restore Grafana Dashboards

```bash
# From backup
docker cp backups/<DATE>/grafana/. grafana:/var/lib/grafana/
docker restart grafana

# From git (faster — dashboards are in code)
git pull origin main
docker restart grafana
```

### Restore Prometheus Data

```bash
# Stop prometheus
docker compose stop prometheus

# Restore from snapshot
docker cp backups/<DATE>/prometheus/. prometheus:/prometheus/

# Restart
docker compose start prometheus
```

### Full Platform Recovery (from scratch)

```bash
# If server is completely lost — rebuild in < 10 minutes
git clone https://github.com/Eclouddevops/observability-platform.git
cd observability-platform

# Restore .env (keep a secure copy in AWS Secrets Manager)
aws secretsmanager get-secret-value \
    --secret-id observability/env-backup \
    --query SecretString \
    --output text > .env

sudo bash scripts/install.sh
```

---

## 8. Troubleshooting Guide

### Prometheus Targets Showing DOWN

```
Problem → Target DOWN in Prometheus UI
         http://54.88.150.33:9090/targets

Diagnosis tree:
  Is the EC2 instance running?
  ├── NO  → Start instance in AWS Console
  └── YES → Is Node Exporter running?
             ├── NO  → sudo systemctl start node_exporter
             └── YES → Is port 9100 open in Security Group?
                        ├── NO  → Add inbound rule TCP:9100
                        └── YES → Is IP correct in targets file?
                                   └── Check prometheus/targets/ec2_nodes.yml
```

```bash
# Debug commands
docker logs prometheus --tail 50             # Check Prometheus errors
curl http://<target-ip>:9100/metrics        # Test from observability server
docker exec prometheus wget -qO- http://<ip>:9100/metrics | head  # Test from container
```

---

### Grafana Shows "No Data"

```bash
# Step 1 — Verify Prometheus has data
curl -s "http://localhost:9090/api/v1/query?query=up" | python3 -m json.tool | head -20

# Step 2 — Check datasource in Grafana
# Grafana → Configuration → Data Sources → Prometheus → Test

# Step 3 — Check time range in dashboard
# Click time picker → change to "Last 1 hour"

# Step 4 — Restart Grafana
docker compose restart grafana
```

---

### Email Alerts Not Sending

```bash
# Step 1 — Check SMTP credentials are loaded
grep SMTP /observability-platform/.env

# Step 2 — Reload credentials from Secrets Manager
sudo bash scripts/load-smtp-secret.sh

# Step 3 — Check Alertmanager logs
docker logs alertmanager --tail 30 | grep -i "email\|smtp\|error"

# Step 4 — Test manually
curl -X POST http://localhost:9093/api/v2/alerts \
-H "Content-Type: application/json" \
-d '[{"labels":{"alertname":"TestEmail","priority":"P1","severity":"critical","instance":"test"},"annotations":{"summary":"Test","description":"Test email","runbook":"No action"}}]'

# Step 5 — Verify SES sending quota
aws ses get-send-quota --region us-east-1
aws ses get-send-statistics --region us-east-1
```

---

### Auto-Discovery Not Finding Instances

```bash
# Step 1 — Check discovery status
curl -s http://localhost:9877/status | python3 -m json.tool

# Step 2 — Trigger immediate scan
curl -X POST http://localhost:9877/discover

# Step 3 — Check logs for AWS errors
docker logs ec2-auto-discovery --tail 30

# Step 4 — Verify IAM permissions
aws sts get-caller-identity
aws ec2 describe-instances --region us-east-1 --query 'Reservations[].Instances[].InstanceId'

# Step 5 — Check if instances have required tags (if TAG_FILTER_KEY set)
grep TAG_FILTER .env
```

---

### Container Not Starting

```bash
# Check what went wrong
docker compose ps -a                    # Shows exit codes
docker logs <container-name> --tail 50  # Shows error messages

# Common fixes:
docker compose down && docker compose up -d            # Full restart
docker compose build --no-cache <service>              # Rebuild image
docker system prune -f && docker compose up -d        # Clear cache
```

---

## 9. Escalation Matrix

```
┌─────────────────────────────────────────────────────────────────┐
│                    ALERT ESCALATION MATRIX                       │
├──────────┬───────────────────┬────────────────┬─────────────────┤
│ Priority │ First Notify      │ Escalate To     │ Response SLA   │
├──────────┼───────────────────┼────────────────┼─────────────────┤
│   P1     │ Santosh Mirajkar  │ Team Lead       │ 15 minutes     │
│ Critical │ (both emails)     │ (if no ack 30m) │                │
├──────────┼───────────────────┼────────────────┼─────────────────┤
│   P2     │ Santosh Mirajkar  │ P1 escalation   │ 1 hour         │
│  High    │ (both emails)     │ (if no ack 2h)  │                │
├──────────┼───────────────────┼────────────────┼─────────────────┤
│   P3     │ Santosh Mirajkar  │ P2 escalation   │ 4 hours        │
│  Medium  │ (both emails)     │ (if no ack 6h)  │                │
└──────────┴───────────────────┴────────────────┴─────────────────┘

Alert Recipients:
  📧 Santosh.mirajkar@timesgroup.com  (primary)
  📧 samirajkar99@gmail.com           (secondary/personal)
  💬 MS Teams — #monitoring channel
```

### Alert Flow

```
Alert fires in Prometheus
        │
        ▼
Alertmanager receives alert
        │
        ├── P1? ──► Email immediately + Teams + repeat 1h
        ├── P2? ──► Email within 30s + Teams + repeat 4h
        └── P3? ──► Email within 1m + Teams + repeat 12h
        │
        ▼
Email sent via AWS SES (no password stored)
  → Santosh.mirajkar@timesgroup.com
  → samirajkar99@gmail.com
        │
        ▼
AI Agent auto-investigates (if ANTHROPIC/OPENAI key set)
        │
        ▼
Root cause analysis posted to MS Teams
```

---

*Last updated: October 2026 | Maintained by: Infrastructure Team*
*Platform: Eclouddevops/observability-platform | Server: i-0b7b07809dc9007ac*
