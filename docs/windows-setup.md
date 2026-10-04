# 🪟 Windows Server Monitoring Setup

Install **Windows Exporter** on `windows-server` (i-00baf21d89dfb8504 / 54.80.223.94)

---

## Step 1 — Open Security Group Port

In **AWS Console → EC2 → Security Groups → `windows-server-sg`**

Add Inbound Rule:
| Type       | Protocol | Port | Source                        |
|------------|----------|------|-------------------------------|
| Custom TCP | TCP      | 9182 | `54.88.150.33/32` (observability-server) |

---

## Step 2 — Install Windows Exporter via PowerShell

RDP into `windows-server` (54.80.223.94) then open **PowerShell as Administrator**:

```powershell
# Download latest Windows Exporter
$version = "0.25.1"
$url = "https://github.com/prometheus-community/windows_exporter/releases/download/v$version/windows_exporter-$version-amd64.msi"
$dest = "$env:TEMP\windows_exporter.msi"

Write-Host "Downloading Windows Exporter v$version..."
Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing

# Install with collectors enabled
# Collectors: cpu, cs, logical_disk, memory, net, os, service, system, tcp, iis (if IIS installed)
$collectors = "cpu,cs,logical_disk,memory,net,os,service,system,tcp,process,scheduled_task"

Write-Host "Installing Windows Exporter..."
Start-Process msiexec.exe -ArgumentList "/i `"$dest`" /quiet ENABLED_COLLECTORS=$collectors LISTEN_PORT=9182" -Wait

Write-Host "Windows Exporter installed!"

# Verify it's running
Start-Sleep -Seconds 3
$svc = Get-Service -Name "windows_exporter" -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host "✅ Windows Exporter is RUNNING on port 9182"
} else {
    Write-Host "❌ Service not running — check Event Viewer"
    Start-Service "windows_exporter"
}
```

---

## Step 3 — Verify It's Working

Still in PowerShell on windows-server:

```powershell
# Test locally
Invoke-WebRequest -Uri "http://localhost:9182/metrics" -UseBasicParsing | Select-Object -First 5

# Check firewall allows port 9182
netsh advfirewall firewall add rule name="Windows Exporter" dir=in action=allow protocol=TCP localport=9182
```

---

## Step 4 — Test From Observability Server

Back on your **observability-server** Linux terminal:

```bash
# Test Windows Exporter is reachable
curl -s http://54.80.223.94:9182/metrics | head -10

# Should return metrics like:
# windows_os_info{...} 1
# windows_cpu_time_total{...} 12345
```

---

## Step 5 — Reload Prometheus

```bash
curl -X POST http://localhost:9090/-/reload && echo "✅ Prometheus reloaded"
```

Then check: `http://54.88.150.33:9090/targets` — `windows-exporter` should show **🟢 UP**

---

## Step 6 — Open Windows Dashboard in Grafana

`http://54.88.150.33:3000` → Dashboards → **🪟 Windows Server Status**

---

## Collectors Reference

| Collector | Metrics |
|-----------|---------|
| `cpu` | CPU usage per core and mode |
| `cs` | Computer system info (RAM total) |
| `logical_disk` | C: D: drive usage, I/O |
| `memory` | RAM free/used/virtual |
| `net` | Network bytes in/out, errors |
| `os` | OS info, processes, uptime |
| `service` | Windows services status |
| `system` | Boot time, CPU queue |
| `tcp` | TCP connections |
| `process` | Per-process CPU/memory |
| `iis` | IIS connections (if installed) |
| `scheduled_task` | Task Scheduler jobs |

---

## Upgrade Windows Exporter

```powershell
# Download new version and reinstall
$version = "0.25.1"
$url = "https://github.com/prometheus-community/windows_exporter/releases/download/v$version/windows_exporter-$version-amd64.msi"
Invoke-WebRequest -Uri $url -OutFile "$env:TEMP\windows_exporter.msi" -UseBasicParsing
Start-Process msiexec.exe -ArgumentList "/i `"$env:TEMP\windows_exporter.msi`" /quiet" -Wait
```

---

## Troubleshoot

```powershell
# Check service status
Get-Service windows_exporter

# View logs
Get-EventLog -LogName Application -Source "windows_exporter" -Newest 20

# Restart service
Restart-Service windows_exporter

# Check what port it's on
netstat -ano | findstr :9182
```
