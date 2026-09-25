#!/usr/bin/env python3
"""
CLI Utility for managing Lumio Dashboard user accounts and passwords.
Usage:
  python3 auth_tool.py list
  python3 auth_tool.py set-password <username> <new_password>
  python3 auth_tool.py add-user <username> <password>
"""

import sys
import getpass
from auth import user_mgr, session_mgr


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 auth_tool.py list")
        print("  python3 auth_tool.py set-password <username> [new_password]")
        print("  python3 auth_tool.py add-user <username> [password]")
        print("  python3 auth_tool.py revoke-sessions <username>")
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "list":
        users = user_mgr.list_users()
        print(f"\nRegistered Users ({len(users)}):")
        print("-" * 50)
        for u, d in users.items():
            print(f" • Username: {d.get('username')}")
            print(f"   Display:  {d.get('display_name')}")
        print("-" * 50)

    elif command in ("set-password", "passwd"):
        if len(sys.argv) < 3:
            print("Error: Username required. Usage: python3 auth_tool.py set-password <username> [password]")
            sys.exit(1)
        username = sys.argv[2]
        if len(sys.argv) >= 4:
            password = sys.argv[3]
        else:
            password = getpass.getpass(f"Enter new password for '{username}': ")
            confirm = getpass.getpass("Confirm new password: ")
            if password != confirm:
                print("Error: Passwords do not match.")
                sys.exit(1)

        if len(password) < 8:
            print("Warning: Password is short (under 8 characters). Recommended: 12+ characters.")

        user_mgr.set_password(username, password)
        session_mgr.revoke_all_for_user(username)
        print(f"✓ Password successfully updated for user '{username}'.")
        print(f"✓ All active sessions for '{username}' have been revoked (must re-login).")

    elif command == "add-user":
        if len(sys.argv) < 3:
            print("Error: Username required. Usage: python3 auth_tool.py add-user <username> [password]")
            sys.exit(1)
        username = sys.argv[2]
        if len(sys.argv) >= 4:
            password = sys.argv[3]
        else:
            password = getpass.getpass(f"Enter password for '{username}': ")
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                print("Error: Passwords do not match.")
                sys.exit(1)

        user_mgr.set_password(username, password)
        print(f"✓ User '{username}' successfully created.")

    elif command == "revoke-sessions":
        if len(sys.argv) < 3:
            print("Error: Username required.")
            sys.exit(1)
        username = sys.argv[2]
        session_mgr.revoke_all_for_user(username)
        print(f"✓ All active sessions revoked for '{username}'.")

    else:
        print(f"Unknown command '{command}'.")
        sys.exit(1)


if __name__ == "__main__":
    main()
