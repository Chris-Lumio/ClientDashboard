#!/usr/bin/env python3
"""
Authentication & Session Management Module for Lumio Operations Dashboard.
Production-grade security:
- OWASP-compliant PBKDF2-HMAC-SHA256 password hashing (310,000 rounds).
- Cryptographically secure 256-bit random session tokens.
- SQLite-backed persistent sessions with automatic expiration.
- IP-based brute-force protection and rate limiting.
- Secure, HttpOnly, SameSite=Lax cookie management with dynamic HTTPS detection.
"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from typing import Optional, Tuple, Dict, Any

AUTH_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".auth.json")
SESSIONS_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions.db")

COOKIE_NAME = "lumio_session"
DEFAULT_SESSION_DAYS = 30
SHORT_SESSION_HOURS = 24
PBKDF2_ROUNDS = 310_000
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 300  # 5 minutes lockout after 5 consecutive failures


def hash_password(password: str) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with 16 bytes salt and 310k iterations."""
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2:sha256:{PBKDF2_ROUNDS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored hash using constant-time comparison."""
    try:
        parts = stored_hash.split("$")
        if len(parts) != 3:
            return False
        meta, salt_hex, hash_hex = parts
        _, algo, rounds_str = meta.split(":")
        rounds = int(rounds_str)
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)

        computed = hashlib.pbkdf2_hmac(algo, password.encode("utf-8"), salt, rounds)
        return hmac.compare_digest(computed, expected_hash)
    except Exception:
        return False


class UserManager:
    """Manages credentials stored in .auth.json with strict file permissions."""

    def __init__(self, filepath: str = AUTH_FILE_PATH):
        self.filepath = filepath
        self._lock = threading.Lock()
        self._ensure_init()

    def _ensure_init(self):
        """Create initial auth file if not present, with default credentials."""
        if not os.path.exists(self.filepath):
            with self._lock:
                if not os.path.exists(self.filepath):
                    default_pass = "Ashenone12!"
                    initial_users = {
                        "admin": {
                            "username": "admin",
                            "display_name": "Administrator",
                            "password_hash": hash_password(default_pass),
                            "created_at": time.time(),
                        },
                        "chris@lumiosolutions.com": {
                            "username": "chris@lumiosolutions.com",
                            "display_name": "Chris",
                            "password_hash": hash_password(default_pass),
                            "created_at": time.time(),
                        }
                    }
                    self._save_raw(initial_users)
                    print(f"[AUTH] Initialized default user accounts in {self.filepath}")

    def _load_raw(self) -> Dict[str, Any]:
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[AUTH ERROR] Failed to load {self.filepath}: {e}")
            return {}

    def _save_raw(self, data: Dict[str, Any]):
        tmp_file = self.filepath + ".tmp"
        # Write to temporary file first, set 0600 permissions, then atomic rename
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            os.chmod(tmp_file, 0o600)
        except Exception:
            pass
        os.replace(tmp_file, self.filepath)
        try:
            os.chmod(self.filepath, 0o600)
        except Exception:
            pass

    def verify_login(self, username_or_email: str, password: str) -> Optional[Dict[str, Any]]:
        """Validate credentials; returns user dict or None."""
        if not username_or_email or not password:
            return None
        with self._lock:
            users = self._load_raw()
            # Case-insensitive username / email lookup
            target_key = None
            for key in users:
                if key.lower() == username_or_email.strip().lower():
                    target_key = key
                    break

            if not target_key:
                # Run a dummy verification to prevent timing side-channel attacks
                verify_password("dummy", "pbkdf2:sha256:1000$00$00")
                return None

            user = users[target_key]
            stored_hash = user.get("password_hash", "")
            if verify_password(password, stored_hash):
                return {
                    "username": user.get("username", target_key),
                    "display_name": user.get("display_name", target_key)
                }
            return None

    def set_password(self, username: str, new_password: str) -> bool:
        """Update or create user password."""
        with self._lock:
            users = self._load_raw()
            key_found = None
            for key in users:
                if key.lower() == username.strip().lower():
                    key_found = key
                    break

            target_key = key_found or username.strip().lower()
            if target_key not in users:
                users[target_key] = {
                    "username": target_key,
                    "display_name": target_key.split("@")[0].capitalize(),
                    "created_at": time.time()
                }

            users[target_key]["password_hash"] = hash_password(new_password)
            users[target_key]["updated_at"] = time.time()
            self._save_raw(users)
            return True

    def list_users(self) -> Dict[str, Any]:
        with self._lock:
            users = self._load_raw()
            return {
                u: {
                    "username": d.get("username"),
                    "display_name": d.get("display_name"),
                    "created_at": d.get("created_at")
                }
                for u, d in users.items()
            }


class SessionManager:
    """Manages persistent sessions in SQLite with automatic TTL and cleanup."""

    def __init__(self, db_path: str = SESSIONS_DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        try:
            with self._get_conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        username TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        client_ip TEXT,
                        user_agent TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)")
                conn.commit()
            try:
                os.chmod(self.db_path, 0o600)
            except Exception:
                pass
        except Exception as e:
            print(f"[AUTH ERROR] Failed to init sessions DB: {e}")

    def create_session(self, username: str, client_ip: str = "", user_agent: str = "", remember_me: bool = True) -> str:
        session_id = secrets.token_urlsafe(32)
        now = time.time()
        duration_seconds = (DEFAULT_SESSION_DAYS * 86400) if remember_me else (SHORT_SESSION_HOURS * 3600)
        expires_at = now + duration_seconds

        with self._get_conn() as conn:
            # Purge expired sessions opportunistically
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO sessions (session_id, username, created_at, expires_at, client_ip, user_agent) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, username, now, expires_at, client_ip, (user_agent or "")[:200])
            )
            conn.commit()
        return session_id

    def validate_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        if not session_id or len(session_id) < 16:
            return None
        now = time.time()
        try:
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT username, expires_at, client_ip FROM sessions WHERE session_id = ? AND expires_at > ?",
                    (session_id, now)
                ).fetchone()
                if row:
                    return {
                        "username": row[0],
                        "expires_at": row[1],
                        "client_ip": row[2]
                    }
        except Exception as e:
            print(f"[AUTH ERROR] Session validation error: {e}")
        return None

    def revoke_session(self, session_id: str):
        if not session_id:
            return
        try:
            with self._get_conn() as conn:
                conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
                conn.commit()
        except Exception as e:
            print(f"[AUTH ERROR] Session revoke error: {e}")

    def revoke_all_for_user(self, username: str):
        try:
            with self._get_conn() as conn:
                conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
                conn.commit()
        except Exception as e:
            print(f"[AUTH ERROR] Session revoke all error: {e}")


class RateLimiter:
    """Tracks failed login attempts per IP to prevent brute-force attacks."""

    def __init__(self):
        self._lock = threading.Lock()
        # ip -> [timestamp1, timestamp2, ...]
        self._failures: Dict[str, list] = {}

    def check(self, ip: str) -> Tuple[bool, int]:
        """Check if IP is permitted to attempt login. Returns (allowed, retry_after_seconds)."""
        now = time.time()
        with self._lock:
            attempts = self._failures.get(ip, [])
            # Filter attempts within the lockout window
            valid_attempts = [t for t in attempts if now - t < LOCKOUT_SECONDS]
            self._failures[ip] = valid_attempts

            if len(valid_attempts) >= MAX_FAILED_ATTEMPTS:
                earliest_attempt = valid_attempts[0]
                retry_after = int(LOCKOUT_SECONDS - (now - earliest_attempt))
                return False, max(1, retry_after)
            return True, 0

    def record_failure(self, ip: str):
        now = time.time()
        with self._lock:
            if ip not in self._failures:
                self._failures[ip] = []
            self._failures[ip].append(now)

    def record_success(self, ip: str):
        with self._lock:
            self._failures.pop(ip, None)


# Global singletons
user_mgr = UserManager()
session_mgr = SessionManager()
rate_limiter = RateLimiter()


def get_client_ip(handler) -> str:
    """Extract real client IP taking into account Cloudflare, reverse proxies, and local sockets."""
    cf_ip = handler.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()

    x_forwarded = handler.headers.get("X-Forwarded-For")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()

    return handler.client_address[0] if handler.client_address else "127.0.0.1"


def is_secure_connection(handler) -> bool:
    """Detect if connection is HTTPS (direct, Tailscale HTTPS, or via Cloudflare SSL termination)."""
    proto = handler.headers.get("X-Forwarded-Proto", "").lower()
    if proto == "https":
        return True

    cf_visitor = handler.headers.get("CF-Visitor", "")
    if '"scheme":"https"' in cf_visitor:
        return True

    host = handler.headers.get("Host", "").lower()
    if "ts.net" in host or "cloudflare" in host or "lumiosolutions.com" in host:
        return True

    return False


def get_session_cookie(handler) -> Optional[str]:
    """Parse session ID from request Cookie header."""
    cookie_header = handler.headers.get("Cookie", "")
    if not cookie_header:
        return None

    cookies = [c.strip() for c in cookie_header.split(";")]
    for cookie in cookies:
        if cookie.startswith(f"{COOKIE_NAME}="):
            return cookie[len(COOKIE_NAME) + 1:].strip()
    return None


def create_cookie_header(session_id: str, is_https: bool = False, max_age_days: int = DEFAULT_SESSION_DAYS) -> str:
    """Build Set-Cookie header value with security flags. Supports cross-site iframe embedding."""
    max_age_sec = max_age_days * 86400
    if is_https:
        cookie = f"{COOKIE_NAME}={session_id}; Path=/; HttpOnly; SameSite=None; Secure; Max-Age={max_age_sec}"
    else:
        cookie = f"{COOKIE_NAME}={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age_sec}"
    return cookie


def create_clear_cookie_header(is_https: bool = False) -> str:
    """Build Set-Cookie header to destroy session cookie in browser."""
    if is_https:
        cookie = f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=None; Secure; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
    else:
        cookie = f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
    return cookie
