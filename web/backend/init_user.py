"""
CLI script to create or reset the admin user.

Usage:
  python -m web.backend.init_user --username admin --password yourpass
"""

import argparse
import sys
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.backend.auth import create_user, USERS_FILE


def main():
    parser = argparse.ArgumentParser(description="Create/reset dashboard admin user")
    parser.add_argument("--username", required=True, help="Admin username")
    parser.add_argument("--password", required=True, help="Admin password")
    args = parser.parse_args()

    if len(args.password) < 6:
        print("Error: password must be at least 6 characters")
        sys.exit(1)

    create_user(args.username, args.password)
    print(f"User '{args.username}' created/updated successfully.")
    print(f"Stored in: {USERS_FILE}")


if __name__ == "__main__":
    main()
