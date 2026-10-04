"""
Phase 13 admin CLI: create or update a login account (there is no
self-registration and no user-management endpoint).

    python scripts/create_user.py --username asha --role investigator
    python scripts/create_user.py --username up-nodal --role state --state "Uttar Pradesh"
    python scripts/create_user.py --username da-464 --role district --district-authority-id 464
    python scripts/create_user.py --username jdoe --role mp --mp "RAJEEV RAI"
    python scripts/create_user.py --username x --disable

The password is read from the environment variable SENTINEL_NEW_PASSWORD if
set, otherwise prompted for (never taken from argv, which leaks into shell
history and process listings). Roles and scopes: app/models/security.py.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.auth.passwords import hash_password  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.models.security import ROLES, AppUser  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", required=True)
    ap.add_argument("--role", choices=ROLES)
    ap.add_argument("--state")
    ap.add_argument("--district-authority-id", type=int)
    ap.add_argument("--house", choices=("LS", "RS"))
    ap.add_argument("--mp")
    ap.add_argument("--disable", action="store_true")
    args = ap.parse_args()
    username = args.username.strip().lower()

    with get_session_factory()() as db:
        user = db.execute(select(AppUser).where(AppUser.username == username)).scalar_one_or_none()
        if args.disable:
            if user is None:
                print(f"no such user: {username}", file=sys.stderr)
                return 1
            user.is_active = False
            db.commit()
            print(f"disabled {username}")
            return 0
        if not args.role:
            ap.error("--role is required unless --disable")
        password = os.environ.get("SENTINEL_NEW_PASSWORD") or getpass.getpass(f"password for {username}: ")
        fields = dict(
            role=args.role,
            scope_state=args.state,
            scope_district_authority_id=args.district_authority_id,
            scope_house=args.house,
            scope_mp=args.mp,
            password_hash=hash_password(password),
            is_active=True,
        )
        if user is None:
            db.add(AppUser(username=username, **fields))
        else:
            for k, v in fields.items():
                setattr(user, k, v)
        db.commit()
    print(f"saved {username} ({args.role})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
