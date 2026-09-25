# Complete Summary Report: Home Assistant Centralized Backup & Monitoring System
**Prepared for:** Chris (chris@lumiosolutions.com)  
**Date:** August 19, 2026  
**Infrastructure:** Office Raspberry Pi 5 + Tailscale Mesh + Healthchecks.io + Google Apps Script

---

## 1. Executive Summary & Problem Resolution

### Initial Issue:
* The Office Raspberry Pi storage was **99% full (only 480 MB free)**, blocking new services and Docker deployments.

### Remediation & Storage Recovery:
* Cleaned APT package archives (`/var/cache/apt/archives`): **~1.4 GB reclaimed**
* Pruned dangling Docker images, volumes, and build caches: **~4.2 GB reclaimed**
* Removed outdated `.vscode-server` builds: **~1.1 GB reclaimed**
* **Total Storage Reclaimed**: **~6.1 GB** (Reduced disk utilization to **76% / 6.6 GB free**).

---

## 2. Infrastructure Setup & Architecture

### Central Server (Office Raspberry Pi 5):
* **Healthchecks.io**: Installed natively with Python virtual environment (`/home/yaralumio1/healthchecks/hc-venv`), SQLite database, and Gunicorn WSGI.
* **Background Services**: Managed via systemd user units:
  * `healthchecks-web.service` (Web dashboard & ping endpoint, bound to port 8000).
  * `healthchecks-sendalerts.service` (Automated alert worker for missed pings).
* **Tailscale & Tailscale Serve**:
  * Hostname: `lumiosolutions-office.tail85d58c.ts.net`
  * Tailscale IP: `100.85.142.34`
  * Tailscale Serve provides automated HTTPS (`https://lumiosolutions-office.tail85d58c.ts.net`) accessible exclusively across your private Tailnet without exposing any public internet ports.
* **Admin Account**:
  * **Email**: `chris@lumiosolutions.com`
  * **Password**: `[REDACTED_PASSWORD]`

---

## 3. Client Onboarding (Mohammad Hatoum - Qebb Elias)

### Client Node Details:
* **Tailscale IP**: `100.75.230.87` (`mohamad-hatoum-qebb-elias`)
* **SSH Access**: `ssh root@100.75.230.87` (Password: `[REDACTED_PASSWORD]`)
* **Check UUID**: `<CLIENT_CHECK_UUID>`

### Configuration Applied:
1. **Tailscale Add-on**: Configured with `userspace_networking: false` (to route outbound pings via `tailscale0`) and `advertise_routes: []` (to keep client LAN private).
2. **REST Command (`/config/configuration.yaml`)**:
   ```yaml
   rest_command:
     report_backup_health:
       url: "http://100.85.142.34:8000/ping/<CLIENT_CHECK_UUID>{{ suffix | default('') }}"
       method: post
       content_type: "text/plain"
       payload: "{{ message | default('') }}"
       timeout: 15
   ```
3. **Backup Script (`/config/scripts.yaml`)**:
   * Creates a full backup (`hassio.backup_full`).
   * Sends success ping to Healthchecks (`rest_command.report_backup_health`).
   * Appends record to Google Sheets (`google_sheets.append_sheet`).
   * Removed daily success spam from Slack — Slack is now alerted **only on failures**.
4. **Daily Schedule (`/config/automations.yaml`)**:
   * Runs `script.run_backup_and_report` every morning at **03:00:00 AM**.

---

## 4. Google Apps Script Audit (Multi-Tab Sheet)

* **Architecture**: Automated cloud audit running on Google's infrastructure.
* **Multi-Tab Logic**: Automatically iterates through every tab in the Google Spreadsheet (where each tab represents one client).
* **Alert Criteria**: Dispatches an alert to Slack `#backups-alerts` if:
  * A client's latest backup status is `failed` (includes the exact error reason).
  * A client's last backup is older than 7 days (missed weekly backup).
  * A client's tab is empty.
* **Location**: Saved locally on Pi at `/home/yaralumio1/backup_audit.js`.

---

## 5. Standard Operating Procedure: Adding New Clients

1. **In Healthchecks Dashboard** (`https://lumiosolutions-office.tail85d58c.ts.net`):
   * Click **Add Check** → Name: `<Client Name>` → Period: `1 day` (or `7 days`), Grace: `4 hours` → Copy UUID.
2. **On Client Home Assistant**:
   * Install **Tailscale add-on** with `userspace_networking: false`.
   * Add `rest_command` in `configuration.yaml` with the new UUID.
   * Add `run_backup_and_report` in `scripts.yaml` and daily schedule in `automations.yaml`.
3. **Test**:
   * Run the script in Home Assistant and verify 🟢 **UP** in Healthchecks.

---

## 6. Testing & Failure Simulation

* **Simulate Failure**: `curl -d "Disk Full Error" http://100.85.142.34:8000/ping/<UUID>/fail` (Status turns 🔴 DOWN, Slack receives `[Device Name] - Backup incomplete`. Every repeated `/fail` ping triggers a Slack alert even if already down, and recurring reminders are dispatched every 24 hours while down).
* **Reset to Normal**: `curl http://100.85.142.34:8000/ping/<UUID>` (Status returns to 🟢 UP, Slack receives a single recovery alert `[Device Name] - Backup resolved`; subsequent successful backups remain silent).
