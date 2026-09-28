# Lumio Solutions — Centralized Backup Monitoring System
> **Comprehensive Guide & Client Onboarding Manual**  
> *Healthchecks.io + Tailscale Architecture for Home Assistant Fleet*

---

## 1. System Overview & Architecture

This monitoring system provides centralized, zero-trust backup monitoring for all client Home Assistant deployments. It operates as a **Dead-Man's Switch**: clients report successful daily backups over a secure, private Tailscale mesh network. If a backup fails or a client goes completely offline (power outage, hardware failure, internet downtime), the central server flags the failure and dispatches alerts.

```text
┌───────────────────────────┐         ┌───────────────────────────┐
│ Client A (Home Assistant) │         │ Client B (Home Assistant) │
└─────────────┬─────────────┘         └─────────────┬─────────────┘
              │ Tailscale (100.x.x.x)               │ Tailscale (100.x.x.x)
              │ Encrypted Mesh                      │ Encrypted Mesh
              ▼                                     ▼
     ┌─────────────────────────────────────────────────────────────┐
     │                Office Raspberry Pi (Host)                   │
     │  Tailscale IP: 100.85.142.34                                │
     │  MagicDNS: lumiosolutions-office.tail85d58c.ts.net         │
     │                                                             │
     │  ┌───────────────────────┐       ┌───────────────────────┐  │
     │  │    Tailscale Serve    │       │     Healthchecks      │  │
     │  │   (HTTPS Web Portal)  │◄─────►│    (Gunicorn / WSGI)  │  │
     │  │  Port 443 (Tailnet)   │       │    Port 8000          │  │
     │  └───────────────────────┘       └───────────┬───────────┘  │
     └──────────────────────────────────────────────┼──────────────┘
                                                    │
                                                    ▼ (Alerts on Failures Only)
                                        ┌───────────────────────┐
                                        │  Slack #backup-alerts │
                                        │  Telegram / Email     │
                                        └───────────────────────┘
```

### Key Security & Design Principles:
1. **100% Private (No Public Ports)**: Healthchecks is never exposed to the public internet. It is accessible exclusively through Tailscale.
2. **End-to-End Encryption**: All ping data travels over WireGuard point-to-point tunnels.
3. **No Notification Noise**: Daily successful backups are processed silently; alerts are triggered **only on failure or missed schedules**.
4. **Resilient Failure Detection**: If a client suffers a complete power loss or network cut, the central server alerts you once the grace period expires.

---

## 2. Central Server Access & Details

* **Dashboard URL**: [https://lumiosolutions-office.tail85d58c.ts.net](https://lumiosolutions-office.tail85d58c.ts.net)
* **Direct Tailscale IP**: `100.85.142.34`
* **Admin Login**:
  * **Email**: `chris@lumiosolutions.com`
* **Server Location**: `/home/yaralumio1/healthchecks`
* **Background Services** (managed via systemd user services):
  * `healthchecks-web.service` (Web UI & Ping endpoint)
  * `healthchecks-sendalerts.service` (Alert monitoring worker)

---

## 3. Step-by-Step: Adding a New Client

Follow these 5 steps for every new Home Assistant installation you want to monitor.

---

### Step 1: Create a Check in Healthchecks Dashboard

1. Open **[https://lumiosolutions-office.tail85d58c.ts.net](https://lumiosolutions-office.tail85d58c.ts.net)** from a device connected to Tailscale.
2. Click **Add Check**.
3. Configure the check:
   * **Name**: `<Client Name> - <Location> (<Site Code>)`  
     *(e.g., `Mohammad Hatoum - Qebb Elias (LB-QE-0003)`)*
   * **Tags**: `backup`, `<client-code>`
   * **Period**: `1 day` (or expected backup frequency)
   * **Grace Time**: `8 hours` (buffer window before declaring the client down; prevents morning false alarms)
4. Click **Save** and copy the generated **Check UUID**:
   ```text
   Example UUID: e2d758f3-d8db-4d91-8ebb-284456cf3d96
   ```

---

### Step 2: Install & Configure Tailscale on Client HA

1. In the client's Home Assistant, navigate to **Settings** → **Add-ons** → **Add-on Store**.
2. Search for and install the **Tailscale** add-on.
3. In the **Configuration** tab of the add-on, switch to YAML mode and paste the following isolated configuration:

```yaml
accept_dns: true
accept_routes: false
advertise_connector: false
advertise_exit_node: false
advertise_routes: []
always_use_derp: false
log_level: info
login_server: https://controlplane.tailscale.com
share_homeassistant: disabled
share_on_port: 443
snat_subnet_routes: true
stateful_filtering: false
tags: []
taildrive:
  addon_configs: false
  addons: false
  backup: false
  config: false
  media: false
  share: false
  ssl: false
taildrop: false
userspace_networking: false
```

> [!IMPORTANT]
> * `userspace_networking: false` is required so Home Assistant creates the `tailscale0` network interface and allows Core to send outbound pings across Tailscale.
> * `advertise_routes: []` ensures the client's local home/office network is **not** broadcast into your Tailscale network.

4. Start the add-on, open the **Log** tab, click the login URL, and approve the device into your Tailnet.

---

### Step 3: Add `rest_command` to Client's `configuration.yaml`

In the client's `/config/configuration.yaml`, add the `rest_command` block:

```yaml
rest_command:
  report_backup_health:
    url: "http://100.85.142.34:8000/ping/<CLIENT-UUID>{{ suffix | default('') }}"
    method: post
    content_type: "text/plain"
    payload: "{{ message | default('') }}"
    timeout: 15
```
*(Replace `<CLIENT-UUID>` with the UUID generated in Step 1).*

> [!TIP]
> Using the direct Tailscale IP `http://100.85.142.34:8000` avoids local DNS lookup delays inside Docker containers while remaining fully encrypted across WireGuard. Never use HTTPS domain names for the ping endpoint inside HA to avoid DNS and TLS handshake drops.

---

### Step 4: Add Bulletproof Backup Automation (With Retries)

This automation listens natively to Home Assistant's automatic backup event (`event.backup_automatic_backup`), reports successes and failures with exact error reasons, and automatically retries up to 5 times if the network is momentarily down at 3:00 AM:

#### In `/config/automations.yaml`:
```yaml
- id: 'backup_monitor_healthchecks_robust'
  alias: "Backup Monitor - Healthchecks with Retries"
  description: "Monitors automatic and manual backups, retries on network blips, and reports reasons"
  triggers:
    - trigger: state
      entity_id: event.backup_automatic_backup
    - trigger: time
      at: "08:00:00"  # Morning sync heartbeat
  conditions:
    - condition: template
      value_template: >-
        {{ state_attr('event.backup_automatic_backup', 'event_type') in ['completed', 'failed'] 
           or trigger.platform == 'time' }}
  actions:
    - variables:
        status: "{{ state_attr('event.backup_automatic_backup', 'event_type') | default('completed') }}"
        reason: "{{ state_attr('event.backup_automatic_backup', 'failed_reason') | default('', true) }}"
    - repeat:
        count: 5
        sequence:
          - choose:
              - conditions:
                  - condition: template
                    value_template: "{{ status == 'completed' }}"
                then:
                  - action: rest_command.report_backup_health
                    data:
                      suffix: ""
                      message: "Location: <Client Name> | Backup completed successfully at {{ now().strftime('%Y-%m-%d %H:%M:%S') }}"
              - conditions:
                  - condition: template
                    value_template: "{{ status == 'failed' }}"
                then:
                  - action: rest_command.report_backup_health
                    data:
                      suffix: "/fail"
                      message: "Location: <Client Name> | Backup failed: {{ reason }}"
          - delay:
              seconds: 60
  mode: single
```

---

### Step 5: Reload & Verify

1. In Home Assistant: **Developer Tools** → **YAML** → **Restart** (or click **All YAML Configuration**).
2. Go to **Settings** → **Automations & Scenes** → **Scripts**.
3. Click **Run (▶)** next to **"Run Backup and Report to Healthchecks"**.
4. Check your Healthchecks dashboard: the status will change to 🟢 **UP** with the last ping timestamp updated.

---

## 4. Alert Channels (Slack / Email)

Healthchecks is configured to notify you every time a backup is incomplete or fails, and sends a single recovery notification when the device backs up successfully again (🟢 **UP**). Routine daily backups while healthy remain completely silent to eliminate notification noise.

### Slack Alerts:
* **Alert Rule**:
  * **Every Backup Failure**: Triggers immediately on every `/fail` ping reported by Home Assistant, even if the check is already in the incomplete/DOWN state.
  * **Recurring Missed Backup Reminders**: If a backup is missed or remains incomplete, Healthchecks automatically sends a recurring notification every 24 hours (on each daily backup window) for as long as it remains incomplete.
  * **Recovery**: Sends a single resolution alert once a successful backup ping arrives.
* **Format**:
  * On failure / incomplete: `[Device Name] - Backup incomplete` (🔴 Red)
  * On resolution: `[Device Name] - Backup resolved` (🟢 Green, sent once upon recovery)
* **Webhook**: Configured at [https://lumiosolutions-office.tail85d58c.ts.net/integrations/](https://lumiosolutions-office.tail85d58c.ts.net/integrations/).

---

## 5. Failure Simulation & Testing

You can simulate failure scenarios to test your alert pipeline:

### A. Simulating an Explicit Backup Failure
Run this command from any terminal on Tailscale:
```bash
curl -d "SIMULATION: Drive full - backup failed" http://100.85.142.34:8000/ping/<CLIENT-UUID>/fail
```
* **Result**: Healthchecks switches to 🔴 **DOWN** and dispatches the Slack alert: `[Device Name] - Backup incomplete`.

### B. Resetting Status Back to Normal (UP)
```bash
curl http://100.85.142.34:8000/ping/<CLIENT-UUID>
```
* **Result**: Healthchecks switches back to 🟢 **UP** and dispatches a single Slack recovery alert: `[Device Name] - Backup resolved`. (Subsequent successful backups remain silent).

---

## 6. Server Maintenance Commands (Office Raspberry Pi)

```bash
# Check service status
systemctl --user status healthchecks-web healthchecks-sendalerts

# Restart Healthchecks services
systemctl --user restart healthchecks-web healthchecks-sendalerts

# Check Tailscale status
tailscale status
tailscale serve status

# View live application logs
journalctl --user -u healthchecks-web -f
```
