# 🔭 Observability Platform

A production-grade, Git-managed monitoring stack for AWS infrastructure — deployed on a single EC2 instance (`observability-server`).

---

## 📦 What's Included

| Component | Port | Purpose |
|-----------|------|---------|
| **Prometheus** | 9090 | Metrics collection & alerting |
| **Grafana** | 3000 | Dashboards & visualization |
| **Alertmanager** | 9093 | Alert routing (Slack, PagerDuty, Email) |
| **Node Exporter** | 9100 | Host OS metrics (CPU, RAM, Disk) |
| **cAdvisor** | 8080 | Container metrics |
| **CloudWatch Exporter** | 9106 | AWS metrics (ECS/EC2/Lambda/API GW/ASG/WAF) |
| **Blackbox Exporter** | 9115 | Website uptime & HTTP probing |
| **SSL Exporter** | 9219 | SSL/TLS certificate expiry |
| **Loki** | 3100 | Log aggregation |
| **Promtail** | — | Log shipper to Loki |
| **AI Observability Agent** | 8888 | LLM-powered analysis & alerts |
| **Nginx** | 80/443 | Reverse proxy with HTTPS |

---

## 🚀 Quick Start (One Command)

```bash
git clone https://github.com/<you>/observability-platform.git
cd observability-platform
cp .env.example .env
# Edit .env with your credentials
nano .env
sudo bash scripts/install.sh
```

After installation, open **http://\<your-ec2-public-ip\>:3000** in your browser.

Default credentials: `admin` / `ChangeMe2024!` *(change immediately!)*

---

## 📊 Dashboards

| Dashboard | Description |
|-----------|-------------|
| 🌐 **AWS Infrastructure Overview** | Multi-account overview — alerts, SSL status, website health |
| 🖥️ **EC2 Node Metrics** | CPU, RAM, Disk, Network per instance |
| 🐳 **ECS Services** | Task counts, CPU/Memory per service |
| λ **Lambda Functions** | Invocations, errors, duration, throttles |
| 🌐 **API Gateway & WAF** | Request rates, error rates, WAF block analysis |
| 🔐 **SSL Certificates & Websites** | Cert expiry calendar, uptime history |

All dashboards auto-provision on startup via Grafana provisioning.

---

## ⚙️ Configuration

### Add EC2 Instances

Edit `prometheus/targets/ec2_nodes.yml`:

```yaml
- targets:
    - '10.0.1.100:9100'   # web-server-01
    - '10.0.1.101:9100'   # web-server-02
  labels:
    env: production
    region: us-east-1
```

Install Node Exporter on each EC2:
```bash
./scripts/install-node-exporter.sh 10.0.1.100
```

### Add Websites / URLs

Edit `prometheus/targets/websites.yml`:

```yaml
- targets:
    - 'https://example.com'
    - 'https://api.example.com/health'
```

### Add SSL Certificates

Edit `prometheus/targets/ssl_targets.yml`:

```yaml
- targets:
    - 'example.com:443'
    - 'api.example.com:443'
```

### Reload Prometheus Config (no restart needed)

```bash
./scripts/manage.sh reload-prom
# or directly:
curl -XPOST http://localhost:9090/-/reload
```

---

## 🤖 AI Observability Agent

The AI agent at `http://localhost:8888` provides:

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Liveness check |
| `/analyze` | POST | Analyze a PromQL query with AI |
| `/investigate-alert` | POST | Root-cause analysis for a firing alert |
| `/summarize` | POST | Infrastructure health summary |
| `/daily-report` | GET | Trigger a fresh daily report |
| `/chat` | POST | Conversational interface |
| `/alertmanager-webhook` | POST | Auto-receive & investigate alerts |

### Configure Alertmanager Webhook

In `alertmanager/alertmanager.yml`, add the webhook receiver:

```yaml
receivers:
  - name: 'ai-investigation'
    webhook_configs:
      - url: 'http://ai-agent:8888/alertmanager-webhook'
```

### Example: Chat with your Infra

```bash
curl -X POST http://localhost:8888/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "What is the current CPU usage and are there any alerts firing?"}'
```

### Example: Daily Report

```bash
curl http://localhost:8888/daily-report
```

---

## 🔔 Alerting Rules

| Alert | Condition | Severity |
|-------|-----------|----------|
| InstanceDown | `up == 0` for 2m | critical |
| HighCPUUsage | CPU > 85% for 5m | warning |
| CriticalCPUUsage | CPU > 95% for 2m | critical |
| HighMemoryUsage | Memory > 85% | warning |
| DiskSpaceCritical | Disk > 90% | critical |
| WebsiteDown | Blackbox probe fails 2m | critical |
| SSLCertExpiringCritical | < 7 days remaining | critical |
| SSLCertExpiringSoon | < 30 days remaining | warning |
| ECSServiceHighCPU | ECS CPU > 80% | warning |
| ECSTaskStopped | Running tasks < 1 | critical |
| LambdaHighErrorRate | Error rate > 5% | warning |
| LambdaThrottling | Throttles > 10/s | warning |
| APIGatewayHighError5xx | 5xx rate > 1% | critical |
| WAFHighBlockedRequests | Blocked > 100/s | warning |

---

## 🔑 IAM Permissions (AWS)

Create an IAM policy for the CloudWatch Exporter:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricData",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics",
        "tag:GetResources",
        "sts:AssumeRole"
      ],
      "Resource": "*"
    }
  ]
}
```

**Best practice**: Attach this policy to the EC2 instance role (IAM Instance Profile) instead of using static credentials.

---

## 🗂️ Multi-Account Monitoring

For multiple AWS accounts, create a cross-account IAM role in each account:

```json
{
  "Effect": "Allow",
  "Principal": {
    "AWS": "arn:aws:iam::<OBSERVABILITY_ACCOUNT_ID>:role/ObservabilityServer"
  },
  "Action": "sts:AssumeRole"
}
```

Then configure `AWS_ROLE_ARN` in `.env` to assume the role.

---

## 🔄 Git Workflow

```bash
# Make changes to dashboards, rules, or targets
nano grafana/dashboards/01-ec2-nodes.json
nano prometheus/targets/ec2_nodes.yml

# Commit and push → GitHub Actions auto-deploys
git add .
git commit -m "feat: add new EC2 node targets"
git push

# GitHub Actions will:
# 1. Validate Prometheus rules & configs
# 2. Build & push AI Agent Docker image
# 3. SSH into EC2 and deploy
# 4. Notify Slack
```

---

## 📁 Directory Structure

```
observability-platform/
├── .env.example                  ← Copy to .env, fill in secrets
├── .gitignore
├── docker-compose.yml            ← Main stack definition
├── .github/
│   └── workflows/
│       ├── deploy.yml            ← CI/CD: validate + deploy on push
│       └── sync-dashboards.yml   ← Sync dashboards to Grafana API
├── prometheus/
│   ├── prometheus.yml            ← Scrape config
│   ├── rules/
│   │   ├── node_alerts.yml       ← EC2/host alerts
│   │   └── aws_alerts.yml        ← SSL/ECS/Lambda/API GW/WAF alerts
│   └── targets/
│       ├── ec2_nodes.yml         ← EC2 node exporter targets
│       ├── websites.yml          ← Website URL targets
│       ├── ssl_targets.yml       ← SSL cert targets
│       └── ping_targets.yml      ← ICMP ping targets
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/          ← Auto-configure Prometheus + Loki
│   │   └── dashboards/           ← Auto-load dashboards
│   └── dashboards/
│       ├── 00-overview.json      ← AWS Infrastructure Overview
│       ├── 01-ec2-nodes.json     ← EC2 Node Metrics
│       ├── 02-ecs.json           ← ECS Services
│       ├── 03-lambda.json        ← Lambda Functions
│       ├── 04-ssl-websites.json  ← SSL + Website uptime
│       └── 05-api-gateway-waf.json ← API Gateway + WAF
├── exporters/
│   ├── cloudwatch/config.yml     ← CloudWatch metrics config
│   └── blackbox/config.yml       ← Blackbox probe modules
├── alertmanager/alertmanager.yml ← Alert routing
├── docker/
│   ├── loki-config.yml
│   └── promtail-config.yml
├── nginx/nginx.conf              ← Reverse proxy
├── ai-agent/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py               ← FastAPI app
│       ├── ai_engine.py          ← OpenAI/Anthropic integration
│       ├── alert_handler.py      ← Alert investigation logic
│       ├── prometheus_client.py  ← Prometheus API client
│       └── notifier.py           ← Slack notifications
└── scripts/
    ├── install.sh                ← Bootstrap the server
    ├── install-node-exporter.sh  ← Install Node Exporter on remote EC2
    └── manage.sh                 ← Day-to-day management
```

---

## 🔐 GitHub Secrets Required

| Secret | Description |
|--------|-------------|
| `EC2_HOST` | Observability server IP/hostname |
| `EC2_USER` | SSH user (e.g., `ubuntu`) |
| `EC2_SSH_KEY` | Private SSH key for EC2 |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook URL |
| `GRAFANA_URL` | Grafana URL for dashboard sync |
| `GRAFANA_API_KEY` | Grafana service account API key |

---

## 🛠️ Management Commands

```bash
./scripts/manage.sh start          # Start all services
./scripts/manage.sh stop           # Stop all services
./scripts/manage.sh status         # Show running containers
./scripts/manage.sh logs grafana   # Tail Grafana logs
./scripts/manage.sh update         # Pull new images
./scripts/manage.sh backup         # Backup data
./scripts/manage.sh reload-prom    # Hot-reload Prometheus config

# Quickly add a target:
./scripts/manage.sh add-target 10.0.1.100 ec2
./scripts/manage.sh add-target https://newsite.com website
./scripts/manage.sh add-target newsite.com:443 ssl
```

---

## 📄 License

MIT
