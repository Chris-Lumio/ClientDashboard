# Standard Operating Procedure (SOP): Adding a New Client
> **Lumio Solutions — Home Assistant Centralized Backup & Monitoring System**  
> *Version 2.0 | Last Updated: August 19, 2026*

---

## 🎯 Objective
This document provides an exact, step-by-step procedure to onboard a new client's Home Assistant instance into Lumio Solutions' centralized backup monitoring system.

---

## 📋 Pre-Requisites Checklist
Before starting, ensure you have:
- [ ] Admin access to the client's Home Assistant web UI (`http://<client-local-ip>:8123`).
- [ ] SSH / Terminal access to client Home Assistant (via *Terminal & SSH* add-on).
- [ ] Access to the Lumio Office Healthchecks Dashboard: [https://lumiosolutions-office.tail85d58c.ts.net](https://lumiosolutions-office.tail85d58c.ts.net).
- [ ] Tailscale Admin Console access to approve new client devices.
- [ ] Access to the Lumio Master Backup Google Sheet.

---

## 🛠️ Step-by-Step Onboarding Procedure

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        6-PHASE ONBOARDING FLOW                         │
│                                                                        │
│  Phase 1: Create Check in Healthchecks Dashboard ──► Get UUID         │
│  Phase 2: Install & Configure Tailscale on Client HAOS                 │
│  Phase 3: Add rest_command, Script, & Automation to Client YAML        │
│  Phase 4: Create Client Tab in Google Sheet                            │
│  Phase 5: Run Verification Test & Confirm 🟢 UP Status                 │
│  Phase 6: Deploy Zero-Touch Remote Access (Cloudflare Tunnel)          │
└────────────────────────────────────────────────────────────────────────┘
```

---

### Phase 1: Create the Check in Healthchecks

1. Open **[https://lumiosolutions-office.tail85d58c.ts.net](https://lumiosolutions-office.tail85d58c.ts.net)** in your browser.
2. Click the blue **Add Check** button.
3. Configure the check details:
   * **Name**: `<Client Name> - <Location> (<Site Code>)`  
     *Example:* `John Doe - Beirut Central (LB-BE-0004)`
   * **Tags**: `backup`, `client`, `<site-code>`
   * **Period**: `1 day` (for daily backups) or `7 days` (for weekly backups).
   * **Grace Time**: `4 hours` (provides a safety window before alerting).
4. Click **Save**.
5. Copy the generated **Check UUID** from the check details (you will need this in Phase 3):
   ```text
   UUID Format Example: 4a8b9c1d-2e3f-4a5b-6c7d-8e9f0a1b2c3d
   ```

---

### Phase 2: Install & Configure Tailscale on Client HA

1. In the client's Home Assistant:
   * Navigate to **Settings** → **Add-ons** → **Add-on Store**.
   * Search for **Tailscale** and click **Install**.
2. Go to the **Configuration** tab of the Tailscale add-on.
3. Switch to **YAML Mode** (click the three dots in top right → *Edit in YAML*).
4. Replace the entire configuration with this isolated client template:

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
> * **`userspace_networking: false`**: MUST be `false` so Home Assistant creates a native `tailscale0` network interface to route HTTP pings directly to the office server.
> * **`advertise_routes: []`**: MUST be empty so the client's local home network is never broadcast to other tailnet nodes.

5. Click **Save**, then switch to the **Info** tab and click **Start**.
6. Open the **Log** tab, locate the login authentication URL, open it in your browser, and **Approve** the device into the Lumio Tailnet.
7. Note down the assigned **Tailscale IP** (e.g. `100.x.y.z`).

---

### Phase 3: Configure Home Assistant YAML Files

Using the **Studio Code Server** add-on, **File Editor**, or **SSH**, update the client's configuration files:

#### 1. Edit `/config/configuration.yaml`
Add the `rest_command` block at the bottom:

```yaml
rest_command:
  report_backup_health:
    url: "http://100.85.142.34:8000/ping/<CLIENT-UUID>{{ suffix | default('') }}"
    method: post
    content_type: "text/plain"
    payload: "{{ message | default('') }}"
    timeout: 15
```
*(Replace `<CLIENT-UUID>` with the UUID from Phase 1).*

---

#### 2. Edit `/config/scripts.yaml`
Add the backup execution script:

```yaml
run_backup_and_report:
  alias: "Run Backup and Report to Healthchecks"
  description: "Creates a full backup, pings Healthchecks, and logs to Google Sheets"
  sequence:
    - action: hassio.backup_full
      data:
        name: "Backup {{ now().strftime('%Y-%m-%d %H:%M') }}"
    - action: rest_command.report_backup_health
      data:
        suffix: ""
        message: "Home Assistant Location: <Client Name> | Backup completed successfully at {{ now().strftime('%Y-%m-%d %H:%M:%S') }}"
    - action: google_sheets.append_sheet
      data:
        config_entry: <CLIENT_GOOGLE_SHEETS_CONFIG_ENTRY_ID>
        add_created_column: true
        data:
          Location: "<Client Name>"
          Date: "{{ now().strftime('%Y-%m-%d') }}"
          Time: "{{ now().strftime('%H:%M:%S') }}"
          Status: "completed"
          Failed Reason: ""
  mode: single
```
*(Replace `<Client Name>` and `<CLIENT_GOOGLE_SHEETS_CONFIG_ENTRY_ID>` with client details).*

---

#### 3. Edit `/config/automations.yaml`
Add the daily automatic backup schedule:

```yaml
- id: 'backup_daily_scheduled_healthchecks'
  alias: "Daily Backup - Healthchecks and Google Sheets"
  description: "Runs automatic daily backup at 03:00 AM, pings Healthchecks, and logs to Google Sheets"
  triggers:
    - trigger: time
      at: "03:00:00"
  actions:
    - action: script.run_backup_and_report
  mode: single
```

---

#### 4. Reload Configuration
In Home Assistant:
1. Go to **Developer Tools** → **YAML**.
2. Click **Check Configuration** (must show *Configuration valid!*).
3. Click **Restart** (or click *Scripts*, *Automations*, and *REST Commands* to reload).

---

### Phase 4: Create Client Tab in Google Sheet

1. Open the **Lumio Master Backup Google Spreadsheet**.
2. Click **+ (Add Sheet)** at the bottom to create a new tab.
3. Rename the tab to the exact **Client Name** *(e.g. `John Doe - Beirut (LB-BE-0004)`)*.
4. Add the standard column headers in Row 1:
   | A | B | C | D | E |
   | :--- | :--- | :--- | :--- | :--- |
   | **Location** | **Date** | **Time** | **Status** | **Failed Reason** |

---

### Phase 5: Verification & Testing Checklist

Perform these 3 checks to confirm the onboarding is 100% complete:

#### Test 1: Manual Script Execution
1. In Home Assistant: **Settings** → **Automations & Scenes** → **Scripts**.
2. Find **"Run Backup and Report to Healthchecks"** and click **Run (▶)**.
3. Verify:
   - [ ] A new full backup appears in **Settings → System → Backups**.
   - [ ] A new row is appended to the client's tab in the **Google Sheet** (`Status: completed`).
   - [ ] The check on **[Healthchecks Dashboard](https://lumiosolutions-office.tail85d58c.ts.net)** changes to 🟢 **UP** with *Total Pings: 1*.

#### Test 2: Failure Alert Test
Run this simulation command from your terminal:
```bash
curl -d "SIMULATION: Insufficient storage" http://100.85.142.34:8000/ping/<CLIENT-UUID>/fail
```
- [ ] Check on Healthchecks dashboard turns **🔴 DOWN**.
- [ ] An instant alert arrives in the Slack `#backups-alerts` channel.

#### Test 3: Clear Incident
Reset the check back to healthy:
```bash
curl -d "Manual recovery verification" http://100.85.142.34:8000/ping/<CLIENT-UUID>
```
- [ ] Check returns to **🟢 UP**.

---

### Phase 6: Zero-Touch Remote Access Deployment (Cloudflare Tunnel)

If migrating the client away from Nabu Casa to a free, self-hosted Cloudflare Tunnel without requiring family members to reconfigure their mobile apps:

1. **Deploy Cloudflared Container**:
   Deploy the `cloudflared` Docker container on the host with the client's assigned tunnel token (see [`ZERO_TOUCH_CLOUDFLARE_MIGRATION_SOP.md`](file:///home/yaralumio1/ZERO_TOUCH_CLOUDFLARE_MIGRATION_SOP.md)).
2. **Update Core Configuration (`configuration.yaml`)**:
   Set `homeassistant.external_url`, configure `http.use_x_forwarded_for: true`, and add `remote_ui_override`.
3. **Deploy `remote_ui_override` Custom Component**:
   Installs the background helper that silently delivers the new Cloudflare URL to mobile apps and logs out of Nabu Casa.
4. **Verification**:
   Confirm `https://<client-subdomain>.lumiosolutions.com` returns `HTTP 200 OK` and mobile devices connect seamlessly on cellular data.

> *For complete copy-paste code and scripts, follow [`ZERO_TOUCH_CLOUDFLARE_MIGRATION_SOP.md`](file:///home/yaralumio1/ZERO_TOUCH_CLOUDFLARE_MIGRATION_SOP.md).*

---

## 🔧 Troubleshooting & Common Issues

| Issue | Root Cause | Solution |
| :--- | :--- | :--- |
| **Ping fails with connection timeout** | `userspace_networking` is still set to `true` in Tailscale add-on | Set `userspace_networking: false` in Tailscale configuration and restart the add-on. |
| **Ping fails with DNS resolution error** | Hostname used instead of IP | Ensure the `rest_command` URL uses the direct IP: `http://100.85.142.34:8000/ping/<UUID>`. |
| **Google Sheet row not appending** | Missing or incorrect `config_entry` ID | Verify the Google Sheets integration ID in Home Assistant under **Settings → Devices & Services → Google Sheets**. |
| **Check stays DOWN after test** | A `/fail` test was sent and needs a success ping to clear | Trigger a manual backup run or send a plain HTTP ping to the check URL. |

---

## 📞 Support & Maintenance
* **Central Server**: `lumiosolutions-office` (`100.85.142.34`)
* **Alerts Channel**: Slack `#backups-alerts`
* **Admin Contact**: `chris@lumiosolutions.com`
