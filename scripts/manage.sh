#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Observability Platform Management Script
#
# Usage:
#   ./scripts/manage.sh start       # Start all services
#   ./scripts/manage.sh stop        # Stop all services
#   ./scripts/manage.sh restart     # Restart all services
#   ./scripts/manage.sh status      # Show service status
#   ./scripts/manage.sh logs [svc]  # Show logs (optional: service name)
#   ./scripts/manage.sh update      # Pull latest images & restart
#   ./scripts/manage.sh backup      # Backup Grafana dashboards & Prometheus data
#   ./scripts/manage.sh reload-prom # Hot-reload Prometheus config
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

CMD="${1:-help}"

case "$CMD" in
  start)
    echo "Starting observability stack..."
    docker compose up -d
    echo "Done. Run './scripts/manage.sh status' to check."
    ;;

  stop)
    echo "Stopping observability stack..."
    docker compose down
    ;;

  restart)
    echo "Restarting observability stack..."
    docker compose restart "${2:-}"
    ;;

  status)
    docker compose ps
    echo ""
    echo "Service endpoints:"
    echo "  Grafana:      http://localhost:3000"
    echo "  Prometheus:   http://localhost:9090"
    echo "  Alertmanager: http://localhost:9093"
    echo "  AI Agent:     http://localhost:8888"
    ;;

  logs)
    docker compose logs -f --tail=100 "${2:-}"
    ;;

  update)
    echo "Pulling latest images..."
    docker compose pull
    echo "Restarting with new images..."
    docker compose up -d --remove-orphans
    echo "Cleaning up old images..."
    docker image prune -f
    ;;

  backup)
    BACKUP_DIR="$REPO_DIR/backups/$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$BACKUP_DIR"
    echo "Backing up to $BACKUP_DIR ..."

    # Grafana dashboards
    docker cp grafana:/var/lib/grafana/. "$BACKUP_DIR/grafana/" 2>/dev/null || true

    # Prometheus data snapshot
    curl -sf -XPOST http://localhost:9090/api/v1/admin/tsdb/snapshot | jq -r '.data.name' | \
        xargs -I{} docker cp "prometheus:/prometheus/snapshots/{}" "$BACKUP_DIR/prometheus/" 2>/dev/null || true

    echo "Backup complete: $BACKUP_DIR"
    ;;

  reload-prom)
    echo "Reloading Prometheus configuration..."
    curl -sf -XPOST http://localhost:9090/-/reload
    echo "Prometheus config reloaded."
    ;;

  add-target)
    # Usage: ./scripts/manage.sh add-target <ip> <type>
    # type: ec2 | website | ssl | ping
    IP="${2:-}"
    TYPE="${3:-ec2}"
    [[ -z "$IP" ]] && { echo "Usage: $0 add-target <ip-or-url> <type>"; exit 1; }

    case "$TYPE" in
      ec2)
        echo "Adding EC2 node target: ${IP}:9100"
        # Append to YAML (simple sed approach)
        sed -i "s|# - '10.0.1.100:9100'|# - '10.0.1.100:9100'\n    - '${IP}:9100'|" \
            "$REPO_DIR/prometheus/targets/ec2_nodes.yml" 2>/dev/null || \
            echo "    - '${IP}:9100'" >> "$REPO_DIR/prometheus/targets/ec2_nodes.yml"
        ;;
      website)
        echo "Adding website target: $IP"
        echo "    - '${IP}'" >> "$REPO_DIR/prometheus/targets/websites.yml"
        ;;
      ssl)
        echo "Adding SSL target: $IP"
        echo "    - '${IP}:443'" >> "$REPO_DIR/prometheus/targets/ssl_targets.yml"
        ;;
      ping)
        echo "Adding ping target: $IP"
        echo "    - '${IP}'" >> "$REPO_DIR/prometheus/targets/ping_targets.yml"
        ;;
    esac
    echo "Reloading Prometheus..."
    curl -sf -XPOST http://localhost:9090/-/reload || true
    echo "Done! Target added for: $IP"
    ;;

  help|*)
    echo "Usage: $0 {start|stop|restart|status|logs|update|backup|reload-prom|add-target}"
    ;;
esac
