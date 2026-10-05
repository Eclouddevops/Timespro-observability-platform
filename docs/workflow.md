# 🔄 Observability Platform — Workflow Guide

> **Purpose:** End-to-end workflows for installation, configuration, monitoring operations, and incident management.
>
> **Server:** `observability-server` | `54.88.150.33` | `i-0b7b07809dc9007ac`
> **Repository:** https://github.com/Eclouddevops/observability-platform

---

## 📋 Table of Contents

1. [Fresh Installation Workflow](#1-fresh-installation-workflow)
2. [Day-to-Day Git Workflow](#2-day-to-day-git-workflow)
3. [Alert Lifecycle Workflow](#3-alert-lifecycle-workflow)
4. [EC2 Auto-Discovery Workflow](#4-ec2-auto-discovery-workflow)
5. [Multi-Account Onboarding Workflow](#5-multi-account-onboarding-workflow)
6. [Dashboard Update Workflow](#6-dashboard-update-workflow)
7. [Incident Response Workflow](#7-incident-response-workflow)
8. [Email Alert Setup Workflow](#8-email-alert-setup-workflow)
9. [Windows Server Onboarding Workflow](#9-windows-server-onboarding-workflow)
10. [SSL Certificate Workflow](#10-ssl-certificate-workflow)

---

## 1. Fresh Installation Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│                  FRESH INSTALL — COMPLETE WORKFLOW                   │
└─────────────────────────────────────────────────────────────────────┘

PREREQUISITES
─────────────
    AWS Account                     EC2 Instance
    ┌─────────────────┐             ┌──────────────────────────┐
    │ Account:        │             │ observability-server     │
    │ 496251222247    │             │ t3.medium, Ubuntu 22.04  │
    │                 │             │ i-0b7b07809dc9007ac      │
    │ IAM Role:       │             │ 54.88.150.33             │
    │ ObservatoryRole │─────────────► Port 22 open (SSH)       │
    │ attached to EC2 │             └──────────────────────────┘
    └─────────────────┘

STEP 1: SSH into server
─────────────────────────────────────────────────────────────────────
    ssh -i your-key.pem ubuntu@54.88.150.33

STEP 2: Clone repository
─────────────────────────────────────────────────────────────────────
    git clone https://github.com/Eclouddevops/observability-platform.git
    cd observability-platform

    📁 KEY FILES CREATED:
    ├── docker-compose.yml      ← All 12+ services defined here
    ├── prometheus/prometheus.yml
    ├── .env.example            ← Copy this to .env
    └── scripts/install.sh     ← Run this next

STEP 3: Configure credentials
─────────────────────────────────────────────────────────────────────
    cp .env.example .env
    nano .env

    ⭐ MINIMUM REQUIRED SETTINGS:
    ┌─────────────────────────────────────────────────┐
    │ GRAFANA_ADMIN_PASSWORD=YourStrongPassword!      │
    │ AWS_DEFAULT_REGION=us-east-1                    │
    │ AWS_REGIONS=us-east-1                           │
    │ MSTEAMS_WEBHOOK_URL=https://outlook.office.com/ │
    └─────────────────────────────────────────────────┘

STEP 4: Run one-command installer
─────────────────────────────────────────────────────────────────────
    sudo bash scripts/install.sh

    WHAT IT DOES:
    ├── Installs Docker + Docker Compose
    ├── Installs Node Exporter (host service, port 9100)
    ├── Generates self-signed SSL certificate
    ├── Pulls all Docker images
    └── Starts all containers

    ⏱  Takes 3-5 minutes on first run

STEP 5: Verify everything started
─────────────────────────────────────────────────────────────────────
    docker compose ps          ← All should show "Up"
    ./scripts/manage.sh status ← Quick status overview

STEP 6: Open AWS Security Group ports
─────────────────────────────────────────────────────────────────────
    AWS Console → EC2 → Security Groups → Edit Inbound Rules:

    ┌────────────┬──────────┬─────────────────────────────────┐
    │ Port       │ Protocol │ Purpose                         │
    ├────────────┼──────────┼─────────────────────────────────┤
    │ 3000       │ TCP      │ Grafana UI                      │
    │ 9090       │ TCP      │ Prometheus UI                   │
    │ 9093       │ TCP      │ Alertmanager UI                 │
    │ 8888       │ TCP      │ AI Agent API                    │
    │ 80/443     │ TCP      │ HTTPS via Nginx                 │
    └────────────┴──────────┴─────────────────────────────────┘

STEP 7: Access Grafana
─────────────────────────────────────────────────────────────────────
    http://54.88.150.33:3000
    Username: admin
    Password: <GRAFANA_ADMIN_PASSWORD from .env>

    ✅ INSTALLATION COMPLETE
```

---

## 2. Day-to-Day Git Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│              GIT-BASED CONFIGURATION MANAGEMENT                      │
│         Everything is code — changes go through git                  │
└─────────────────────────────────────────────────────────────────────┘

DEVELOPER LAPTOP                GITHUB                  EC2 SERVER
─────────────                   ──────                  ──────────
     │                             │                        │
     │  1. Clone repo              │                        │
     │─────────────────────────────►                        │
     │                             │                        │
     │  2. Make changes to files   │                        │
     │     (targets, dashboards,   │                        │
     │      alert rules, etc.)     │                        │
     │                             │                        │
     │  3. git add && git commit   │                        │
     │─────────────────────────────►                        │
     │                             │                        │
     │  4. git push origin main    │                        │
     │─────────────────────────────►                        │
     │                             │                        │
     │                             │  5. GitHub Actions:    │
     │                             │     ✅ Validate rules  │
     │                             │     ✅ Validate JSON   │
     │                             │     ✅ Build Docker    │
     │                             │     ✅ SSH deploy      │
     │                             │─────────────────────────►
     │                             │                        │
     │                             │                 6. git pull
     │                             │                 7. docker compose up
     │                             │                 8. prometheus reload
     │                             │                        │
     │  9. Teams notification      │                        │
     │◄─────────────────────────────────────────────────────
     │  "Deployment successful"    │                        │

⭐ IMPORTANT FILES CHANGED FREQUENTLY:
   prometheus/targets/ec2_nodes.yml    ← Add/remove Linux EC2s
   prometheus/targets/windows_nodes.yml ← Add/remove Windows EC2s
   prometheus/targets/websites.yml     ← Add/remove URLs
   prometheus/targets/ssl_targets.yml  ← Add/remove SSL domains
   prometheus/rules/node_alerts.yml    ← Change alert thresholds
   grafana/dashboards/*.json           ← Dashboard updates

COMMANDS ON SERVER (manual deploy):
─────────────────────────────────────
    cd /observability-platform
    git pull origin main

    # For prometheus config changes (no restart needed):
    curl -X POST http://localhost:9090/-/reload

    # For dashboard changes:
    docker compose restart grafana

    # For alerting changes:
    docker compose restart alertmanager
```

---

## 3. Alert Lifecycle Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ALERT LIFECYCLE — END TO END                      │
└─────────────────────────────────────────────────────────────────────┘

  AWS / SERVER                PROMETHEUS              ALERTMANAGER
  ────────────                ──────────              ────────────
       │                          │                        │
       │  Metric collected        │                        │
       │─────────────────────────►│                        │
       │  (every 60 seconds)      │                        │
       │                          │                        │
       │                          │  Evaluate rules        │
       │                          │  (node_alerts.yml      │
       │                          │   aws_alerts.yml)      │
       │                          │         │              │
       │                          │  FIRING?│              │
       │                          │◄────────┘              │
       │                          │                        │
       │                          │  Send to Alertmanager  │
       │                          │───────────────────────►│
       │                          │                        │
       │                          │               Route by priority:
       │                          │                   P1 → p1-critical
       │                          │                   P2 → p2-high
       │                          │                   P3 → p3-medium
       │                          │                        │
                                                           │
                    EMAIL VIA AWS SES                      │
                    ─────────────────                      │
                         │◄──────────────────────────────── │
                         │                                  │
                    Santosh.mirajkar@timesgroup.com         │
                    samirajkar99@gmail.com                  │
                                                           │
                    MS TEAMS WEBHOOK                        │
                    ────────────────                        │
                         │◄──────────────────────────────── │
                         │  Adaptive Card with:
                         │  • Priority badge (🔴/🟡/🟠)
                         │  • Server name
                         │  • Description
                         │  • Runbook steps
                                                           │
                    AI AGENT AUTO-INVESTIGATION             │
                    ──────────────────────────             │
                         │◄──────────────────────────────── │
                         │  Queries Prometheus for context
                         │  Runs LLM analysis
                         │  Posts root-cause to Teams

ALERT STATES:
────────────
  PENDING ──────► (for: duration passes) ──────► FIRING
     │                                              │
     │                                    (condition clears)
     │                                              │
     │                                           RESOLVED
     │                                              │
     │                                   Email sent: "✅ Resolved"
     │                                   Teams card: green header

PRIORITY TIMING:
────────────────
  ┌──────────┬───────────┬─────────────────────────────────────────┐
  │ Priority │ Group Wait│ Repeat Until Resolved                   │
  ├──────────┼───────────┼─────────────────────────────────────────┤
  │   P1     │ 0 seconds │ Every 1 hour                            │
  │   P2     │ 30 seconds│ Every 4 hours                           │
  │   P3     │ 60 seconds│ Every 12 hours                          │
  └──────────┴───────────┴─────────────────────────────────────────┘
```

---

## 4. EC2 Auto-Discovery Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│              EC2 AUTO-DISCOVERY — EVERY 2 MINUTES                    │
│         New instances appear in Grafana automatically                 │
└─────────────────────────────────────────────────────────────────────┘

YOU LAUNCH A NEW EC2 IN AWS
         │
         ▼
  T+0:00  EC2 starts, gets private IP

         │  (wait max 2 minutes)
         ▼

  T+2:00  Auto-Discovery Scan Starts
  ┌────────────────────────────────────────────┐
  │  ec2-auto-discovery container (port 9877)  │
  │                                            │
  │  1. boto3 → AWS API                        │
  │     DescribeInstances (all regions)        │
  │     → Finds new instance!                  │
  │                                            │
  │  2. Test port 9100 on new instance         │
  │     socket.connect(<ip>, 9100, timeout=3s) │
  │     → Node Exporter reachable? YES/NO      │
  │                                            │
  │  3. Write targets file                     │
  │     prometheus/targets/ec2_nodes.yml       │
  │     → Adds new instance entry              │
  │                                            │
  │  4. POST /prometheus/-/reload              │
  │     → No restart, instant pickup          │
  │                                            │
  │  5. Notify MS Teams                        │
  │     "🆕 New instance added: my-server"    │
  └────────────────────────────────────────────┘
         │
         ▼
  T+2:05  Prometheus starts scraping new instance
         │
         ▼
  T+2:30  Grafana dashboard shows new server
         │
         ▼
  T+3:00  Alerts active for new server

⭐ KEY FILE: auto-discovery/app/multi_account.py
   Contains: MultiAccountDiscovery class
   Discovers: EC2, ECS, Lambda, RDS, ALB, API GW, ASG, SQS, ElastiCache

WHAT GETS DISCOVERED AUTOMATICALLY:
────────────────────────────────────
  Service         What's Monitored
  ──────────────  ────────────────────────────────────
  EC2             CPU, Memory, Disk, Network (via Node Exporter)
  ECS             Running tasks, CPU%, Memory%
  Lambda          Invocations, Errors, Duration, Throttles
  RDS             CPU, Connections, Storage, Latency
  ALB/ELB         Requests, 4xx/5xx, Response time, Healthy hosts
  API Gateway     Request count, Error rates, Latency
  ASG             Desired/Running/Pending instances
  SQS             Queue depth, Message age
  ElastiCache     CPU, Connections, Memory

API ENDPOINTS:
──────────────
  GET  /health     → Status + next scan countdown
  GET  /status     → Last discovery summary
  GET  /instances  → All EC2 instances
  GET  /services   → All AWS services
  POST /discover   → Trigger immediate scan now

  # Check what was discovered
  curl http://54.88.150.33:9877/status

  # Trigger immediate scan
  curl -X POST http://54.88.150.33:9877/discover
```

---

## 5. Multi-Account Onboarding Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│            ADD A NEW AWS ACCOUNT TO MONITORING                       │
└─────────────────────────────────────────────────────────────────────┘

OBSERVABILITY ACCOUNT           NEW TARGET ACCOUNT
(496251222247)                  (e.g. 111122223333)
──────────────                  ───────────────────
      │                                  │
      │                                  │  STEP 1: In target account
      │                                  │  ─────────────────────────
      │                                  │  IAM → Create Role
      │                                  │
      │                                  │  Name: ObservabilityReadOnlyRole
      │                                  │
      │                                  │  Trust Policy:
      │                                  │  ┌────────────────────────────┐
      │                                  │  │ {                          │
      │                                  │  │  "Principal": {            │
      │                                  │  │    "AWS":                  │
      │                                  │  │    "arn:aws:iam::          │
      │                                  │  │     496251222247:role/     │
      │                                  │  │     ObservabilityServerRole│
      │                                  │  │  }                         │
      │                                  │  │ }                          │
      │                                  │  └────────────────────────────┘
      │                                  │
      │                                  │  Attach: ObservabilityPlatformPolicy
      │                                  │
      │                                  │  Copy the Role ARN:
      │                                  │  arn:aws:iam::111122223333:
      │                                  │  role/ObservabilityReadOnlyRole
      │                                  │
      │  STEP 2: On observability server │
      │  ────────────────────────────────│
      │                                  │
      │  nano /observability-platform/.env
      │                                  │
      │  AWS_ACCOUNTS=[                  │
      │    {                             │
      │      "id": "111122223333",       │
      │      "name": "staging",          │
      │      "role_arn": "arn:...",      │
      │      "regions": ["us-east-1"]    │
      │    }                             │
      │  ]                               │
      │                                  │
      │  STEP 3: Restart discovery       │
      │  ────────────────────────────────│
      │                                  │
      │  docker compose restart auto-discovery
      │                                  │
      │  STEP 4: Verify                  │
      │  ────────────────────────────────│
      │                                  │
      │  curl http://localhost:9877/accounts
      │  curl -X POST localhost:9877/discover
      │  docker logs ec2-auto-discovery -f
      │                                  │
      │  ✅ All services in new account  │
      │     appear in Grafana within     │
      │     2 minutes                    │
```

---

## 6. Dashboard Update Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    DASHBOARD UPDATE WORKFLOW                          │
└─────────────────────────────────────────────────────────────────────┘

METHOD A: Edit in Grafana UI (quick changes)
────────────────────────────────────────────

  1. Open http://54.88.150.33:3000
  2. Open dashboard → click ⚙️ → Edit
  3. Make changes (add panels, edit queries)
  4. Click 💾 Save
  5. Export JSON:
     Dashboard Settings → JSON Model → Copy
  6. Paste into grafana/dashboards/<name>.json
  7. Commit to git

METHOD B: Edit JSON directly (recommended)
──────────────────────────────────────────

  ⭐ DASHBOARD FILES:
  ┌────────────────────────────────────────────────────┐
  │ grafana/dashboards/                                │
  │   00-overview.json       AWS Overview              │
  │   01-ec2-nodes.json      EC2 Node Metrics          │
  │   02-ecs.json            ECS Services              │
  │   03-lambda.json         Lambda Functions          │
  │   04-ssl-websites.json   SSL + Website Uptime      │
  │   05-api-gateway-waf.json API Gateway + WAF        │
  │   06-ec2-status.json  ⭐ EC2 Status (Linux+Windows)│
  │   07-websites-ssl.json   Website Monitoring        │
  │   08-auto-discovery.json EC2 Auto-Discovery        │
  └────────────────────────────────────────────────────┘

  1. Edit JSON file locally
  2. git add grafana/dashboards/<name>.json
  3. git commit -m "dashboard: update EC2 status panel"
  4. git push
  → GitHub Actions auto-syncs to Grafana via API
  → OR manually: docker compose restart grafana

DASHBOARD AUTO-PROVISIONING:
─────────────────────────────
  ⭐ KEY FILE: grafana/provisioning/dashboards/dashboards.yml

  Grafana reads this on startup:
  → Scans grafana/dashboards/*.json
  → Auto-loads every dashboard
  → Updates every 30 seconds
  → No manual import needed

ADD A NEW DASHBOARD:
────────────────────
  1. Create grafana/dashboards/10-new-name.json
  2. Start with this structure:
     {
       "uid": "unique-id-here",
       "title": "🆕 My New Dashboard",
       "panels": [ ... ]
     }
  3. docker compose restart grafana
  → Appears automatically in Grafana
```

---

## 7. Incident Response Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    INCIDENT RESPONSE WORKFLOW                         │
│              P1 Critical — 15 minute response target                 │
└─────────────────────────────────────────────────────────────────────┘

📧 ALERT EMAIL RECEIVED
         │
         ▼ (< 5 minutes)
  ┌─────────────────────┐
  │  READ THE EMAIL     │
  │                     │
  │  Check:             │
  │  • Priority (P1?)   │
  │  • Server name      │
  │  • Alert type       │
  │  • Runbook steps    │
  └──────────┬──────────┘
             │
             ▼ (< 10 minutes)
  ┌─────────────────────────────────────────────────┐
  │  OPEN GRAFANA DASHBOARD                         │
  │  http://54.88.150.33:3000                       │
  │                                                 │
  │  → Overview dashboard: red panels?              │
  │  → EC2 Status: which server is down?            │
  │  → Check Alertmanager for full alert list       │
  └────────────────────────┬────────────────────────┘
                           │
             ┌─────────────┴─────────────┐
             │                           │
             ▼                           ▼
  [Server/EC2 issue]          [Application issue]
             │                           │
             ▼                           ▼
  Check EC2 Console           Check ECS/Lambda logs
  aws ec2 describe-           aws logs get-log-events
  instance-status             docker compose logs
             │                           │
             └─────────────┬─────────────┘
                           │
                           ▼ (< 15 minutes)
  ┌─────────────────────────────────────────────────┐
  │  TAKE ACTION                                    │
  │                                                 │
  │  Fix the issue:                                 │
  │  • Restart service                              │
  │  • Scale up                                     │
  │  • Revert bad deployment                        │
  │  • Renew certificate                            │
  └────────────────────────┬────────────────────────┘
                           │
                           ▼
  ┌─────────────────────────────────────────────────┐
  │  VERIFY RESOLUTION                              │
  │                                                 │
  │  • Prometheus target back to UP?                │
  │  • Alertmanager shows Resolved?                 │
  │  • Email received: "✅ Alert Resolved"          │
  │  • Grafana dashboard back to green?             │
  └────────────────────────┬────────────────────────┘
                           │
                           ▼
  ┌─────────────────────────────────────────────────┐
  │  POST-INCIDENT                                  │
  │                                                 │
  │  • Document what happened in git commit msg     │
  │  • Update alert thresholds if too noisy         │
  │  • Add runbook step if missing                  │
  │  • Consider adding auto-remediation             │
  └─────────────────────────────────────────────────┘

SEVERITY RESPONSE TIMES:
─────────────────────────
  🔴 P1 Critical  → Respond in 15 min  → Resolve in 1 hour
  🟡 P2 High      → Respond in 1 hour  → Resolve in 4 hours
  🟠 P3 Medium    → Respond in 4 hours → Resolve in 24 hours
```

---

## 8. Email Alert Setup Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│              EMAIL ALERT SETUP — AWS SES (no passwords)              │
└─────────────────────────────────────────────────────────────────────┘

SECURITY DESIGN:
────────────────
  ┌──────────────────────────────────────────────────────┐
  │  NO passwords stored anywhere on disk                │
  │  Credentials live ONLY in AWS Secrets Manager        │
  │  Accessed via EC2 IAM Role (already attached)        │
  └──────────────────────────────────────────────────────┘

  observability-server
  ├── .env               ← NO SMTP_PASSWORD here
  ├── alertmanager.yml   ← reads ${SMTP_PASSWORD} from env
  └── IAM Role ──────────────────────────────────────────►
                                                           AWS Secrets Manager
                                                           secret: observability/ses-smtp
                                                           ├── smtp_host
                                                           ├── smtp_from
                                                           ├── smtp_user
                                                           └── smtp_password (SES SMTP key)

SETUP STEPS (one time only):
──────────────────────────────

  STEP 1: Update IAM Policy (AWS Console)
  ────────────────────────────────────────
  IAM → Policies → ObservabilityPlatformPolicy → Edit → Add:
  {
    "Sid": "SecretsManager",
    "Effect": "Allow",
    "Action": ["secretsmanager:GetSecretValue","secretsmanager:DescribeSecret"],
    "Resource": "arn:aws:secretsmanager:*:496251222247:secret:observability/*"
  },
  {
    "Sid": "SES",
    "Effect": "Allow",
    "Action": ["ses:SendEmail","ses:SendRawEmail","ses:VerifyEmailIdentity"],
    "Resource": "*"
  }

  STEP 2: Run SES setup script
  ─────────────────────────────
  sudo bash scripts/setup-ses.sh

  ⭐ KEY FILE: scripts/setup-ses.sh
  This script:
  ├── Creates SES verified email identity
  ├── Creates IAM user for SMTP
  ├── Generates SES SMTP credentials
  └── Stores everything in Secrets Manager

  STEP 3: Load credentials
  ─────────────────────────
  sudo bash scripts/load-smtp-secret.sh

  ⭐ KEY FILE: scripts/load-smtp-secret.sh
  This script:
  ├── Fetches secret from Secrets Manager via IAM role
  ├── Writes SMTP vars to .env temporarily
  └── Restarts alertmanager automatically

  STEP 4: Add to crontab (auto-load on reboot)
  ─────────────────────────────────────────────
  crontab -e
  # Add:
  @reboot sleep 30 && bash /observability-platform/scripts/load-smtp-secret.sh

  STEP 5: Test
  ─────────────
  curl -X POST http://localhost:9093/api/v2/alerts \
  -H "Content-Type: application/json" \
  -d '[{"labels":{"alertname":"Test","priority":"P1","severity":"critical",
        "instance":"observability-server"},
       "annotations":{"summary":"Test Alert","description":"Test","runbook":"No action"}}]'

  → Check inbox for:
    Santosh.mirajkar@timesgroup.com
    samirajkar99@gmail.com

EMAIL TEMPLATE:
───────────────
  ⭐ KEY FILE: alertmanager/templates/email.tmpl
  Rich HTML email with:
  ┌──────────────────────────────────────────────┐
  │ 🔴 P1 — CRITICAL          [red header]       │
  │ ⚠️  Alert Firing                             │
  ├──────────────────────────────────────────────┤
  │ 🚨 Server DOWN: observability-server         │
  │                                              │
  │ Priority:   P1 — Critical (act immediately)  │
  │ Severity:   CRITICAL                         │
  │ Server:     observability-server             │
  │ Region:     us-east-1                        │
  │ Started:    Oct 5 2026 09:28:00              │
  │ Description: Server unreachable for 2 min... │
  │                                              │
  │ 🛠 Runbook: Check EC2 console...             │
  │                                              │
  │ [📊 Grafana] [🖥️ EC2 Status] [🔔 Alerts]   │
  └──────────────────────────────────────────────┘
```

---

## 9. Windows Server Onboarding Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│              WINDOWS SERVER MONITORING SETUP                         │
│  windows-server (i-00baf21d89dfb8504 / 54.80.223.94)               │
└─────────────────────────────────────────────────────────────────────┘

ARCHITECTURE:
─────────────

  windows-server (54.80.223.94)        observability-server (54.88.150.33)
  ────────────────────────────         ───────────────────────────────────
  │                          │         │                                 │
  │  Windows Exporter        │         │  Prometheus                     │
  │  (port 9182)             │◄────────│  scrapes every 60s              │
  │                          │         │                                 │
  │  Collects:               │         │  ⭐ KEY FILE:                   │
  │  • CPU (windows_cpu_*)   │         │  prometheus/targets/            │
  │  • Memory (windows_os_*) │         │  windows_nodes.yml              │
  │  • Disk (windows_logical_│         │                                 │
  │  • Network (windows_net_*│         │  Dashboard:                     │
  │  • Services status       │         │  grafana/dashboards/            │
  │  • IIS connections       │         │  06-ec2-status.json             │
  │                          │         │  (🪟 Windows section)           │
  └──────────────────────────┘         └─────────────────────────────────┘

STEP-BY-STEP SETUP:
────────────────────

  ON windows-server (RDP to 54.80.223.94):
  ──────────────────────────────────────────

  [1] Open PowerShell as Administrator

  [2] Install Windows Exporter:
      $url = "https://github.com/prometheus-community/windows_exporter/releases/download/v0.25.1/windows_exporter-0.25.1-amd64.msi"
      Invoke-WebRequest -Uri $url -OutFile "$env:TEMP\we.msi" -UseBasicParsing
      Start-Process msiexec.exe -ArgumentList "/i `"$env:TEMP\we.msi`" /quiet ENABLED_COLLECTORS=cpu,cs,logical_disk,memory,net,os,service,system LISTEN_PORT=9182" -Wait

  [3] Open Windows Firewall:
      netsh advfirewall firewall add rule name="Windows Exporter" dir=in action=allow protocol=TCP localport=9182

  [4] Verify:
      Invoke-WebRequest http://localhost:9182/metrics -UseBasicParsing | Select -First 5

  ON AWS CONSOLE:
  ───────────────

  [5] EC2 → Security Groups → windows-server-sg
      Add Inbound Rule:
      Type: Custom TCP | Port: 9182 | Source: 54.88.150.33/32

  ON observability-server:
  ────────────────────────

  [6] Verify connection:
      curl http://54.80.223.94:9182/metrics | head -3

  [7] Check Prometheus target:
      → http://54.88.150.33:9090/targets
      → windows-exporter should show 🟢 UP

  [8] View in Grafana:
      → http://54.88.150.33:3000
      → EC2 Instance Status dashboard
      → 🪟 Windows Instances section
      → windows-server appears with CPU, Memory, C: Drive, Uptime

⭐ KEY FILES:
  prometheus/targets/windows_nodes.yml    ← Windows server targets
  prometheus/rules/windows_alerts.yml     ← Windows-specific alert rules
  docs/windows-setup.md                   ← Full install guide
```

---

## 10. SSL Certificate Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SSL CERTIFICATE MONITORING                         │
└─────────────────────────────────────────────────────────────────────┘

HOW IT WORKS:
─────────────

  ssl-exporter                    Prometheus              Alertmanager
  (port 9219)                                             + Email
      │                               │                       │
      │ Every 5 minutes:              │                       │
      │ Connect to domain:443         │                       │
      │ Read certificate expiry       │                       │
      │ Expose as: ssl_cert_not_after │                       │
      │───────────────────────────────►                       │
      │                               │                       │
      │                      Calculate: (expiry - now) / 86400 = days
      │                               │                       │
      │                               │  < 30 days? ──────────►
      │                               │  → SSLCertExpiringSoon│
      │                               │  → P2 email alert     │
      │                               │                       │
      │                               │  < 7 days? ───────────►
      │                               │  → SSLCertExpiringCritical
      │                               │  → P1 email alert     │
      │                               │                       │
      │                               │  Expired? ────────────►
      │                               │  → SSLCertExpired     │
      │                               │  → P1 IMMEDIATE email │

⭐ KEY FILES:
  prometheus/targets/ssl_targets.yml    ← Domains to monitor
  prometheus/rules/aws_alerts.yml       ← SSL alert thresholds

ADD DOMAIN TO MONITOR:
────────────────────────

  [1] Edit ssl_targets.yml:
      nano prometheus/targets/ssl_targets.yml

      Add:
      - targets:
          - 'yourdomain.com:443'
          - 'api.yourdomain.com:443'
        labels:
          env: production
          service: ssl

  [2] Reload (no restart):
      curl -X POST http://localhost:9090/-/reload

  [3] Verify in Grafana:
      → SSL Certificates & Websites dashboard
      → Table shows: Domain, Days Left, Expiry Date

CHECK ALL CERT EXPIRY NOW:
───────────────────────────
  curl -s "http://localhost:9090/api/v1/query?query=(ssl_cert_not_after-time())/86400" | \
  python3 -c "
  import json, sys
  d = json.load(sys.stdin)
  print(f'{'Domain':<40} {'Days Left':<12} Status')
  print('-'*60)
  for r in sorted(d['data']['result'], key=lambda x: float(x['value'][1])):
      domain = r['metric'].get('instance','?')
      days   = float(r['value'][1])
      status = '🔴 CRITICAL' if days<7 else '🟡 WARNING' if days<30 else '✅ OK'
      print(f'{domain:<40} {days:<12.0f} {status}')
  "

MONITORED DOMAINS:
──────────────────
  Domain                    Current Status    Check Interval
  ─────────────────────     ─────────────     ──────────────
  aiobz.com:443             Active            Every 5 min
  www.aiobz.com:443         Active            Every 5 min
  (add more in ssl_targets) (pending)         Every 5 min
```

---

## Quick Reference Card

```
┌─────────────────────────────────────────────────────────────────────┐
│                     QUICK REFERENCE CARD                             │
│              Print this and keep it at your desk                     │
└─────────────────────────────────────────────────────────────────────┘

ACCESS URLs:
  Grafana:        http://54.88.150.33:3000   (admin / your-password)
  Prometheus:     http://54.88.150.33:9090
  Alertmanager:   http://54.88.150.33:9093
  AI Agent:       http://54.88.150.33:8888/docs
  Auto-Discovery: http://54.88.150.33:9877

DAILY COMMANDS:
  Status check:      ./scripts/manage.sh status
  View logs:         docker compose logs -f <service>
  Restart service:   docker compose restart <service>
  Reload Prometheus: curl -X POST http://localhost:9090/-/reload
  Trigger discovery: curl -X POST http://localhost:9877/discover
  Check SSL expiry:  curl -s "http://localhost:9090/api/v1/query?query=(ssl_cert_not_after-time())/86400"

CRITICAL FILES:
  ⭐ docker-compose.yml                  ← All services
  ⭐ .env                                ← All secrets
  ⭐ prometheus/prometheus.yml           ← Scrape config
  ⭐ prometheus/rules/node_alerts.yml    ← EC2 alert thresholds
  ⭐ prometheus/rules/aws_alerts.yml     ← AWS service alerts
  ⭐ prometheus/targets/ec2_nodes.yml    ← Linux EC2 targets
  ⭐ prometheus/targets/windows_nodes.yml← Windows targets
  ⭐ prometheus/targets/websites.yml     ← URL monitoring
  ⭐ prometheus/targets/ssl_targets.yml  ← SSL cert monitoring
  ⭐ alertmanager/alertmanager.yml       ← Alert routing + emails
  ⭐ alertmanager/templates/email.tmpl   ← Email HTML template

ALERT RECIPIENTS:
  📧 Santosh.mirajkar@timesgroup.com  (primary)
  📧 samirajkar99@gmail.com           (secondary)
  💬 MS Teams channel

PRIORITY LEVELS:
  🔴 P1 = Critical  → Email immediately, repeat hourly
  🟡 P2 = High      → Email in 30s, repeat every 4h
  🟠 P3 = Medium    → Email in 1m, repeat every 12h
  ✅ OK = Resolved  → Single email, green header

SERVER INFO:
  Observability:  54.88.150.33  (i-0b7b07809dc9007ac)
  Windows Server: 54.80.223.94  (i-00baf21d89dfb8504)
  AWS Account:    496251222247
  Region:         us-east-1
```

---

*Workflow Guide v1.0 | October 2026 | Eclouddevops/observability-platform*
