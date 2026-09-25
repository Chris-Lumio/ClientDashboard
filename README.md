# Lumio Fleet Operations Dashboard & Monitoring Architecture

Centralized real-time telemetry dashboard for monitoring Home Assistant client fleets, integrating **Healthchecks.io** (backup telemetry) and **Uptime Kuma** (device ping & availability) over a secure private **Tailscale** mesh network.

---

## 🌟 Overview

The **Lumio Operations Dashboard** correlates two monitoring sources:
1. **Healthchecks (`hc.sqlite`)**: Dead-man switch backup monitoring. Clients report daily backups via private WireGuard / Tailscale tunnels.
2. **Uptime Kuma (`kuma.db`)**: Live ping, uptime percentage, and latency monitoring for Home Assistant nodes and server services.

Features:
- **Real-Time Telemetry**: Instant live status of all client deployments.
- **Table & List Views**: Fast table layout with health indicators and detailed card view with heartbeat history.
- **Failures & Scheduling**: Automatically calculates missed backup counts and exact next scheduled attempt dates.
- **Secure Authentication**: OWASP PBKDF2 password hashing, SQLite session store, secure cookies, and rate-limiting.
- **Tailscale & Reverse Proxy Integration**: Zero public ports; accessible over Tailnet via HTTPS.

---

## 📁 Repository Structure

```text
├── server.py              # Main dashboard HTTP server & telemetry correlation engine
├── auth.py                # PBKDF2 authentication, session manager, rate limiter
├── auth_tool.py           # CLI tool for user management (create, reset, list users)
├── templates/
│   ├── index.html         # Main operations dashboard interface
│   └── login.html         # Secure login screen
├── static/                # Static assets (fonts, icons, styles)
├── deploy/
│   ├── systemd/           # Systemd service units (dashboard, healthchecks-web, sendalerts)
│   ├── healthchecks/      # Healthchecks configuration templates (local_settings.py.example)
│   └── uptime-kuma/       # Docker Compose setup for Uptime Kuma
└── docs/                  # Architectural guides & Standard Operating Procedures
    ├── HEALTHCHECKS_MONITORING_GUIDE.md  # Comprehensive guide for backup monitoring
    ├── NEW_CLIENT_ONBOARDING_SOP.md     # Step-by-step SOP for adding new clients
    └── FULL_BACKUP_SYSTEM_SUMMARY.md    # Fleet architecture & test verification report
```

---

## 🚀 Running the Dashboard

### Prerequisites
- Python 3.10+
- SQLite3
- Tailscale (for secure mesh network access)

### Starting the Service
```bash
python3 server.py --port 8090
```

Or manage via systemd:
```bash
systemctl --user start lumio-dashboard
systemctl --user status lumio-dashboard
```

### Managing Users
```bash
# Add a new dashboard admin
python3 auth_tool.py add-user username "Display Name" --role admin

# List users
python3 auth_tool.py list-users
```

---

## 🔒 Security & Privacy Notice
Production SQLite databases (`hc.sqlite`, `kuma.db`), live session tokens, and local secret keys are excluded via `.gitignore` to ensure no sensitive credentials or client data are stored in version control.
