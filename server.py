#!/usr/bin/env python3
"""
Lumio Operations Dashboard — Fleet Availability & Backup Monitor
Combines live telemetry from Uptime Kuma (kuma.db) and Healthchecks (hc.sqlite).
Secured with OWASP-compliant session authentication, brute-force rate-limiting,
and security headers for safe online / Cloudflare deployment.
"""

import html
import json
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

try:
    from cronsim import CronSim
except ImportError:
    CronSim = None

from auth import (
    user_mgr,
    session_mgr,
    rate_limiter,
    get_client_ip,
    is_secure_connection,
    get_session_cookie,
    create_cookie_header,
    create_clear_cookie_header,
)

PORT = 8090
KUMA_DB_PATH = "/home/yaralumio1/uptime-kuma/data/kuma.db"
HC_DB_PATH = "/home/yaralumio1/healthchecks/hc.sqlite"
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def get_system_metrics():
    """Retrieve host metrics: CPU load, RAM usage, and Disk space."""
    metrics = {
        "load_1m": 0.0,
        "load_5m": 0.0,
        "ram_total_gb": 0.0,
        "ram_used_gb": 0.0,
        "ram_percent": 0.0,
        "disk_total_gb": 0.0,
        "disk_free_gb": 0.0,
        "disk_used_percent": 0.0,
        "uptime_str": "Unknown"
    }

    try:
        load = os.getloadavg()
        metrics["load_1m"] = round(load[0], 2)
        metrics["load_5m"] = round(load[1], 2)
    except Exception:
        pass

    try:
        with open("/proc/meminfo", "r") as f:
            mem = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    mem[parts[0].strip()] = int(parts[1].strip().split()[0])
            total = mem.get("MemTotal", 0)
            free = mem.get("MemAvailable", mem.get("MemFree", 0))
            used = total - free
            metrics["ram_total_gb"] = round(total / (1024 * 1024), 1)
            metrics["ram_used_gb"] = round(used / (1024 * 1024), 1)
            metrics["ram_percent"] = round((used / total * 100), 1) if total > 0 else 0.0
    except Exception:
        pass

    try:
        disk = shutil.disk_usage("/")
        metrics["disk_total_gb"] = round(disk.total / (1024**3), 1)
        metrics["disk_free_gb"] = round(disk.free / (1024**3), 1)
        metrics["disk_used_percent"] = round((disk.used / disk.total * 100), 1) if disk.total > 0 else 0.0
    except Exception:
        pass

    try:
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.readline().split()[0])
            days = int(uptime_seconds // 86400)
            hours = int((uptime_seconds % 86400) // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            if days > 0:
                metrics["uptime_str"] = f"{days}d {hours}h {minutes}m"
            else:
                metrics["uptime_str"] = f"{hours}h {minutes}m"
    except Exception:
        pass

    return metrics


def normalize_client_name(name):
    """Normalize string for fuzzy-matching between Kuma and Healthchecks."""
    if not name:
        return ""
    n = re.sub(r"\(.*?\)", "", name)
    n = re.sub(r" - Home Assistant", "", n, flags=re.IGNORECASE)
    n = re.sub(r" - Water Pump.*", "", n, flags=re.IGNORECASE)
    n = re.sub(r"[^a-zA-Z0-9]", "", n).lower()
    return n


def format_relative_time(dt_utc, is_future=False):
    """Format datetime into human-friendly relative and absolute time (Lebanon Local Time UTC+3)."""
    if not dt_utc:
        return "Never", "No records"

    now_utc = datetime.now(timezone.utc)
    diff_seconds = int((dt_utc - now_utc).total_seconds()) if is_future else int((now_utc - dt_utc).total_seconds())

    if is_future:
        if diff_seconds <= 0:
            rel = "Due now"
        elif diff_seconds < 60:
            rel = f"in {diff_seconds}s"
        elif diff_seconds < 3600:
            rel = f"in {diff_seconds // 60}m"
        elif diff_seconds < 86400:
            hours = diff_seconds // 3600
            rem_m = (diff_seconds % 3600) // 60
            rel = f"in {hours}h {rem_m}m" if rem_m > 0 and hours < 12 else f"in {hours}h"
        else:
            days = diff_seconds // 86400
            hours = (diff_seconds % 86400) // 3600
            rel = f"in {days}d {hours}h" if hours > 0 else f"in {days}d"
    else:
        diff_seconds = max(0, diff_seconds)
        if diff_seconds < 60:
            rel = f"{diff_seconds}s ago"
        elif diff_seconds < 3600:
            rel = f"{diff_seconds // 60}m ago"
        elif diff_seconds < 86400:
            rel = f"{diff_seconds // 3600}h ago"
        else:
            days = diff_seconds // 86400
            rel = f"{days}d ago"

    # Convert to Lebanon Local Time (UTC+3)
    local_dt = dt_utc + timedelta(hours=3)
    abs_str = local_dt.strftime("%Y-%m-%d %I:%M:%S %p")

    return rel, abs_str


def get_dashboard_data(user_info=None):
    """Extract and combine real-time status from Uptime Kuma and Healthchecks."""
    t0 = time.time()
    now_utc = datetime.now(timezone.utc)
    day_ago = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    kuma_monitors = {}
    try:
        k_uri = f"file:{KUMA_DB_PATH}?mode=ro"
        k_con = sqlite3.connect(k_uri, uri=True, timeout=2.0)
        k_cur = k_con.cursor()

        monitors_raw = k_cur.execute(
            "SELECT id, name, url, active FROM monitor ORDER BY id ASC"
        ).fetchall()

        for m_id, name, url, active in monitors_raw:
            latest_hb = k_cur.execute(
                "SELECT status, ping, time, msg FROM heartbeat WHERE monitor_id=? ORDER BY time DESC LIMIT 1",
                (m_id,),
            ).fetchone()

            stats_24h = k_cur.execute(
                "SELECT COUNT(*), SUM(CASE WHEN status=1 THEN 1 ELSE 0 END) FROM heartbeat WHERE monitor_id=? AND time >= ?",
                (m_id, day_ago),
            ).fetchone()
            total_24h = stats_24h[0] or 0
            up_24h = stats_24h[1] or 0
            uptime_pct = round((up_24h / total_24h * 100), 1) if total_24h > 0 else 0.0

            hbs = k_cur.execute(
                "SELECT status, ping, time, msg FROM heartbeat WHERE monitor_id=? ORDER BY time DESC LIMIT 30",
                (m_id,),
            ).fetchall()
            hbs.reverse()

            heartbeats = []
            for h in hbs:
                hb_raw = h[2]
                hb_time_str = hb_raw
                if hb_raw:
                    try:
                        clean_hb = hb_raw.split(".")[0]
                        dt_hb = datetime.strptime(clean_hb, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) + timedelta(hours=3)
                        hb_time_str = dt_hb.strftime("%Y-%m-%d %I:%M:%S %p")
                    except Exception:
                        pass

                ping_val = h[1] if h[1] is not None else 0
                heartbeats.append({
                    "status": h[0],
                    "ping": ping_val,
                    "time": hb_time_str,
                    "msg": h[3] or ("OK" if h[0] == 1 else "Down")
                })

            last_check_str = None
            if latest_hb and latest_hb[2]:
                try:
                    clean_lc = latest_hb[2].split(".")[0]
                    dt_lc = datetime.strptime(clean_lc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) + timedelta(hours=3)
                    last_check_str = dt_lc.strftime("%Y-%m-%d %I:%M:%S %p")
                except Exception:
                    last_check_str = latest_hb[2]

            is_up = latest_hb and latest_hb[0] == 1
            kuma_monitors[m_id] = {
                "id": m_id,
                "name": name,
                "url": url or "",
                "status": "online" if is_up else "offline",
                "ping": latest_hb[1] if (latest_hb and latest_hb[1] is not None) else 0,
                "last_check": last_check_str,
                "uptime_24h": uptime_pct,
                "heartbeats": heartbeats,
            }
        k_con.close()
    except Exception as e:
        print(f"Error querying Kuma DB: {e}")

    hc_checks = []
    try:
        h_uri = f"file:{HC_DB_PATH}?mode=ro"
        h_con = sqlite3.connect(h_uri, uri=True, timeout=2.0)
        h_cur = h_con.cursor()

        checks_raw = h_cur.execute(
            "SELECT id, name, code, status, last_ping, alert_after, timeout, grace, kind, schedule, tz, tags FROM api_check ORDER BY name ASC"
        ).fetchall()

        for c_id, name, code, status, last_ping, alert_after, timeout_us, grace_us, kind, schedule, tz_name, tags in checks_raw:
            last_body = h_cur.execute(
                "SELECT body_raw FROM api_ping WHERE owner_id=? ORDER BY created DESC LIMIT 1",
                (c_id,),
            ).fetchone()
            body_str = ""
            if last_body and last_body[0]:
                try:
                    body_str = last_body[0].decode("utf-8", errors="replace")[:250].strip()
                except Exception:
                    body_str = ""

            parsed_ping = None
            if last_ping:
                try:
                    clean_str = last_ping.split("+")[0]
                    if "." in clean_str:
                        parsed_ping = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
                    else:
                        parsed_ping = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except Exception:
                    pass

            last_rel, last_abs = format_relative_time(parsed_ping, is_future=False)

            # Determine next expected try to update / backup
            timeout_td = timedelta(microseconds=timeout_us) if timeout_us else timedelta(days=1)
            grace_td = timedelta(microseconds=grace_us) if grace_us else timedelta(hours=4)

            next_expected_dt = None
            if kind == "cron" and schedule and CronSim:
                try:
                    now_local = now_utc.astimezone(ZoneInfo(tz_name or "UTC"))
                    next_expected_dt = next(CronSim(schedule, now_local)).astimezone(timezone.utc)
                except Exception:
                    pass
            elif parsed_ping:
                target = parsed_ping + timeout_td
                while target <= now_utc:
                    target += timeout_td
                next_expected_dt = target

            next_rel, next_abs = format_relative_time(next_expected_dt, is_future=True)

            parsed_alert = None
            if alert_after:
                try:
                    clean_alert = alert_after.split("+")[0]
                    if "." in clean_alert:
                        parsed_alert = datetime.strptime(clean_alert, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
                    else:
                        parsed_alert = datetime.strptime(clean_alert, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except Exception:
                    pass
            next_deadline_rel, next_deadline_abs = format_relative_time(parsed_alert, is_future=True)

            # Determine failed automatic backups count
            timeout_sec = (timeout_us / 1000000.0) if timeout_us else 86400.0
            fail_pings_count = 0
            try:
                if parsed_ping:
                    clean_ping_str = last_ping.split("+")[0]
                    fail_pings_count = h_cur.execute(
                        "SELECT COUNT(*) FROM api_ping WHERE owner_id=? AND (kind='fail' OR exitstatus > 0) AND created > ?",
                        (c_id, clean_ping_str)
                    ).fetchone()[0] or 0
                else:
                    fail_pings_count = h_cur.execute(
                        "SELECT COUNT(*) FROM api_ping WHERE owner_id=? AND (kind='fail' OR exitstatus > 0)",
                        (c_id,)
                    ).fetchone()[0] or 0
            except Exception:
                pass

            missed_cycles = 0
            if status == "down":
                if parsed_ping:
                    first_missed = parsed_ping + timeout_td
                    if now_utc > first_missed:
                        elapsed = (now_utc - first_missed).total_seconds()
                        missed_cycles = 1 + int(elapsed // timeout_sec)
                    else:
                        missed_cycles = 1
                else:
                    missed_cycles = 2

            failed_backups_count = max(missed_cycles, fail_pings_count)
            has_multiple_failures = (failed_backups_count > 1)
            date_color = "red" if has_multiple_failures else "green"

            is_complete = (status == "up")
            hc_checks.append({
                "id": c_id,
                "name": name,
                "code": code,
                "raw_status": status,
                "backup_complete": is_complete,
                "backup_status_label": "Backup Complete" if is_complete else "Backup Incomplete",
                "last_backup_exact": last_abs if parsed_ping else "Never recorded",
                "last_backup_relative": last_rel if parsed_ping else "Never",
                "last_backup_message": body_str,
                "next_expected_date": next_abs if next_expected_dt else "Awaiting first run",
                "next_expected_relative": next_rel if next_expected_dt else "Pending",
                "next_deadline_date": next_deadline_abs if parsed_alert else "N/A",
                "next_deadline_relative": next_deadline_rel if parsed_alert else "N/A",
                "failed_backups_count": failed_backups_count,
                "has_multiple_failures": has_multiple_failures,
                "date_color": date_color,
                "tags": tags or ""
            })
        h_con.close()
    except Exception as e:
        print(f"Error querying Healthchecks DB: {e}")

    # Correlate client sites between Healthchecks and Kuma
    correlated_clients = []
    matched_kuma_ids = set()

    for hc in hc_checks:
        hc_norm = normalize_client_name(hc["name"])
        matched_kuma = None

        for k_id, k_data in kuma_monitors.items():
            k_norm = normalize_client_name(k_data["name"])
            if (hc_norm and k_norm) and (hc_norm in k_norm or k_norm in hc_norm or hc_norm[:5] == k_norm[:5]):
                matched_kuma = k_data
                matched_kuma_ids.add(k_id)
                break

        # Fallback if no direct match
        if not matched_kuma:
            for k_id, k_data in kuma_monitors.items():
                if k_id not in matched_kuma_ids:
                    first_token = hc_norm[:4]
                    if first_token and first_token in normalize_client_name(k_data["name"]):
                        matched_kuma = k_data
                        matched_kuma_ids.add(k_id)
                        break

        # Extract site code if present, e.g. LB-QE-0003
        site_code_match = re.search(r"LB-[A-Z]{2}-\d{4}", hc["name"])
        site_code = site_code_match.group(0) if site_code_match else ""

        correlated_clients.append({
            "client_name": hc["name"],
            "site_code": site_code,
            "hc_id": hc["id"],
            "backup_complete": hc["backup_complete"],
            "backup_status_label": hc["backup_status_label"],
            "last_backup_date": hc["last_backup_exact"],
            "last_backup_relative": hc["last_backup_relative"],
            "last_backup_message": hc["last_backup_message"],
            "next_expected_date": hc["next_expected_date"],
            "next_expected_relative": hc["next_expected_relative"],
            "next_deadline_date": hc["next_deadline_date"],
            "next_deadline_relative": hc["next_deadline_relative"],
            "next_expected": hc["next_expected_date"],
            "failed_backups_count": hc["failed_backups_count"],
            "has_multiple_failures": hc["has_multiple_failures"],
            "date_color": hc["date_color"],
            "kuma_monitor_id": matched_kuma["id"] if matched_kuma else None,
            "kuma_name": matched_kuma["name"] if matched_kuma else "No Kuma Monitor",
            "kuma_url": matched_kuma["url"] if matched_kuma else "",
            "device_online": (matched_kuma["status"] == "online") if matched_kuma else False,
            "device_status_label": ("ONLINE" if matched_kuma["status"] == "online" else "OFFLINE") if matched_kuma else "UNMONITORED",
            "device_ping_ms": matched_kuma["ping"] if matched_kuma else 0,
            "device_uptime_24h": matched_kuma["uptime_24h"] if matched_kuma else 0.0,
            "device_heartbeats": matched_kuma["heartbeats"] if matched_kuma else [],
        })

    # Sort clients: incomplete backups and offline devices first, then alphabetically
    correlated_clients.sort(
        key=lambda x: (
            0 if not x["backup_complete"] else 1,
            0 if not x["device_online"] else 1,
            x["client_name"]
        )
    )

    # Any standalone Kuma monitors not paired to a client (e.g. Office server)
    standalone_monitors = []
    for k_id, k_data in kuma_monitors.items():
        if k_id not in matched_kuma_ids:
            standalone_monitors.append(k_data)

    total_clients = len(correlated_clients)
    backups_complete_count = sum(1 for c in correlated_clients if c["backup_complete"])
    backups_incomplete_count = total_clients - backups_complete_count
    devices_online_count = sum(1 for c in correlated_clients if c["device_online"])
    devices_offline_count = total_clients - devices_online_count

    query_duration_ms = round((time.time() - t0) * 1000, 1)

    payload = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %I:%M:%S %p UTC"),
        "timestamp_local": (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d %I:%M:%S %p EEST"),
        "query_time_ms": query_duration_ms,
        "kpis": {
            "total_clients": total_clients,
            "backups_complete": backups_complete_count,
            "backups_incomplete": backups_incomplete_count,
            "devices_online": devices_online_count,
            "devices_offline": devices_offline_count,
        },
        "system": get_system_metrics(),
        "clients": correlated_clients,
        "standalone_monitors": standalone_monitors
    }

    if user_info:
        registered = user_mgr.list_users()
        u_record = registered.get(user_info.get("username", ""), {})
        payload["user"] = {
            "username": user_info.get("username"),
            "display_name": u_record.get("display_name", user_info.get("username"))
        }

    return payload


def sanitize_next_url(url: str) -> str:
    """Prevent Open Redirect attacks by enforcing local paths."""
    if not url:
        return "/"
    if not url.startswith("/") or url.startswith("//") or url.startswith("/\\"):
        return "/"
    return url


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence routine access logs
        pass

    def send_security_headers(self, is_cacheable: bool = False):
        self.send_header("X-Content-Type-Options", "nosniff")
        # Allow embedding inside Home Assistant and Lumio dashboards via CSP frame-ancestors
        csp_frame_ancestors = (
            "frame-ancestors 'self' "
            "http://192.168.0.129:8123 https://192.168.0.129:8123 "
            "http://homeassistant.local:8123 https://homeassistant.local:8123 "
            "http://localhost:8123 http://127.0.0.1:8123 "
            "https://*.lumiosolutions.com http://*.lumiosolutions.com "
            "https://*.ui.nabu.casa"
        )
        self.send_header("Content-Security-Policy", csp_frame_ancestors)
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        if not is_cacheable:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, private")

    def get_authenticated_user(self):
        """Validate session cookie and return user info dict if valid, else None."""
        session_id = get_session_cookie(self)
        if not session_id:
            return None
        session = session_mgr.validate_session(session_id)
        if not session:
            return None
        return session

    def render_login_page(self, error_message: str = None, next_url: str = "/", status_code: int = 200):
        login_path = os.path.join(TEMPLATES_DIR, "login.html")
        if not os.path.exists(login_path):
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"500 Internal Server Error: Missing login template.")
            return

        with open(login_path, "r", encoding="utf-8") as f:
            template = f.read()

        if error_message:
            escaped_msg = html.escape(error_message)
            banner = f'<div class="alert"><span class="alert-icon">⚠️</span><span>{escaped_msg}</span></div>'
            template = template.replace("<!-- ERROR_BANNER_PLACEHOLDER -->", banner)
        else:
            template = template.replace("<!-- ERROR_BANNER_PLACEHOLDER -->", "")

        template = template.replace("{{NEXT_URL}}", html.escape(sanitize_next_url(next_url)))

        content = template.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_security_headers(is_cacheable=False)
        self.end_headers()
        self.wfile.write(content)

    def do_HEAD(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health" or path == "/api/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_security_headers(is_cacheable=True)
            self.end_headers()
            return

        user = self.get_authenticated_user()
        if user or path == "/login":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_security_headers(is_cacheable=False)
            self.end_headers()
        else:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # 1. Unauthenticated Health Check (for Cloudflare / uptime checks)
        if path in ("/health", "/api/health"):
            data = json.dumps({"status": "ok", "service": "lumio-dashboard"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_security_headers(is_cacheable=True)
            self.end_headers()
            self.wfile.write(data)
            return

        # 2. Login Page
        if path == "/login":
            user = self.get_authenticated_user()
            if user:
                # Already authenticated -> redirect to dashboard
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return

            error = query.get("error", [None])[0]
            next_url = query.get("next", ["/"])[0]
            self.render_login_page(error_message=error, next_url=next_url, status_code=200)
            return

        # 3. Logout
        if path == "/logout":
            sid = get_session_cookie(self)
            if sid:
                session_mgr.revoke_session(sid)
            clear_cookie = create_clear_cookie_header(is_https=is_secure_connection(self))
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", clear_cookie)
            self.send_security_headers(is_cacheable=False)
            self.end_headers()
            return

        # 4. Authentication check for protected routes
        user = self.get_authenticated_user()

        # 5. Protected API Telemetry Endpoint
        if path == "/api/status":
            if not user:
                err_resp = json.dumps({
                    "error": "Unauthorized. Please authenticate.",
                    "login_url": "/login"
                }).encode("utf-8")
                self.send_response(401)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(err_resp)))
                self.send_security_headers(is_cacheable=False)
                self.end_headers()
                self.wfile.write(err_resp)
                return

            data = get_dashboard_data(user_info=user)
            content = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_security_headers(is_cacheable=False)
            self.end_headers()
            self.wfile.write(content)
            return

        # 6. Protected Dashboard Web Interface
        if path in ("/", "/index.html"):
            if not user:
                # Redirect unauthenticated browser requests to /login with next target
                self.send_response(302)
                self.send_header("Location", f"/login?next={sanitize_next_url(path)}")
                self.send_security_headers(is_cacheable=False)
                self.end_headers()
                return

            html_path = os.path.join(TEMPLATES_DIR, "index.html")
            if os.path.exists(html_path):
                with open(html_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_security_headers(is_cacheable=False)
                self.end_headers()
                self.wfile.write(content)
                return

        # 7. Fallback: 404
        self.send_response(404)
        self.send_header("Content-Type", "text/plain")
        self.send_security_headers(is_cacheable=False)
        self.end_headers()
        self.wfile.write(b"404 Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # 1. Login POST Submission
        if path == "/login":
            client_ip = get_client_ip(self)
            allowed, retry_after = rate_limiter.check(client_ip)
            if not allowed:
                time.sleep(1.0)
                self.render_login_page(
                    error_message=f"Too many failed login attempts. Please wait {retry_after} seconds before trying again.",
                    next_url="/",
                    status_code=429
                )
                return

            # Read POST body
            try:
                content_len = int(self.headers.get("Content-Length", 0))
            except (ValueError, TypeError):
                content_len = 0

            if content_len <= 0 or content_len > 16384:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"400 Bad Request")
                return

            body = self.rfile.read(content_len).decode("utf-8", errors="replace")
            params = parse_qs(body)

            username = params.get("username", [""])[0].strip()
            password = params.get("password", [""])[0]
            remember_me = params.get("remember", [""])[0] == "1"
            next_url = sanitize_next_url(params.get("next", ["/"])[0])

            user = user_mgr.verify_login(username, password)
            if not user:
                rate_limiter.record_failure(client_ip)
                # Anti-timing / brute-force delay
                time.sleep(1.0)
                self.render_login_page(
                    error_message="Invalid username or password. Please verify and try again.",
                    next_url=next_url,
                    status_code=401
                )
                return

            # Successful login
            rate_limiter.record_success(client_ip)
            user_agent = self.headers.get("User-Agent", "")
            session_id = session_mgr.create_session(
                username=user["username"],
                client_ip=client_ip,
                user_agent=user_agent,
                remember_me=remember_me
            )

            is_https = is_secure_connection(self)
            max_age_days = 30 if remember_me else 1
            cookie_header = create_cookie_header(session_id, is_https=is_https, max_age_days=max_age_days)

            self.send_response(302)
            self.send_header("Location", next_url)
            self.send_header("Set-Cookie", cookie_header)
            self.send_security_headers(is_cacheable=False)
            self.end_headers()
            return

        # 2. Logout POST Submission
        if path == "/logout":
            sid = get_session_cookie(self)
            if sid:
                session_mgr.revoke_session(sid)
            clear_cookie = create_clear_cookie_header(is_https=is_secure_connection(self))
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", clear_cookie)
            self.send_security_headers(is_cacheable=False)
            self.end_headers()
            return

        self.send_response(404)
        self.send_security_headers(is_cacheable=False)
        self.end_headers()
        self.wfile.write(b"404 Not Found")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    server_address = ("0.0.0.0", PORT)
    httpd = ThreadedHTTPServer(server_address, DashboardHandler)
    print(f"Lumio Operations Dashboard running on http://0.0.0.0:{PORT} [Authentication Active]")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.shutdown()


if __name__ == "__main__":
    main()
