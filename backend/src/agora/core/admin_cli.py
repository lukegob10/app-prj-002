"""Operator-only account bootstrap and recovery commands.

Run with database credentials in the process environment, after verifying identity
out of band. Passwords are prompted without echo and never accepted as CLI args.
"""

import argparse
from getpass import getpass
import re
from uuid import uuid4

from agora.core.db import execute, query_one, transaction
from agora.core.security import hash_password


def _new_password() -> str:
    password = getpass("New password: ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("Passwords do not match")
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    return password


def bootstrap_admin(username: str, full_name: str) -> None:
    normalized = username.strip().lower()
    name = full_name.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,63}", username.strip()):
        raise ValueError("Username must be 3 to 64 letters, numbers, dots, underscores or hyphens")
    if not name or len(name.encode("utf-8")) > 200:
        raise ValueError("Full name must be 1 to 200 bytes")
    password_hash = hash_password(_new_password())
    with transaction() as conn:
        admin = query_one(conn, "SELECT id FROM TB_TA_AGORA_USERS WHERE is_admin = 1 FETCH FIRST 1 ROWS ONLY")
        if admin:
            raise ValueError("An administrator already exists; bootstrap is disabled")
        existing = query_one(
            conn,
            "SELECT id FROM TB_TA_AGORA_USERS WHERE username_norm = :username_norm",
            {"username_norm": normalized},
        )
        if existing:
            raise ValueError("Username already exists; choose a new bootstrap username")
        account_id = str(uuid4())
        execute(
            conn,
            "INSERT INTO TB_TA_AGORA_USERS(id,username,username_norm,full_name,password_hash,is_admin) "
            "VALUES (:id,:username,:username_norm,:full_name,:password_hash,1)",
            {
                "id": account_id,
                "username": username.strip(),
                "username_norm": normalized,
                "full_name": name,
                "password_hash": password_hash,
            },
        )
        execute(
            conn,
            "INSERT INTO TB_TA_AGORA_AUDIT(id,actor_id,action,subject_id,details) "
            "VALUES (:id,NULL,'bootstrap_admin',:subject_id,'{\"source\":\"operator CLI\"}')",
            {"id": str(uuid4()), "subject_id": account_id},
        )
    print("Administrator created.")


def reset_password(username: str) -> None:
    normalized = username.strip().lower()
    password_hash = hash_password(_new_password())
    with transaction() as conn:
        account = query_one(
            conn,
            "SELECT id FROM TB_TA_AGORA_USERS WHERE username_norm = :username_norm",
            {"username_norm": normalized},
        )
        if not account:
            raise ValueError("Account not found")
        execute(
            conn,
            "UPDATE TB_TA_AGORA_USERS SET password_hash = :password_hash, updated_at = SYSTIMESTAMP "
            "WHERE id = :account_id",
            {"password_hash": password_hash, "account_id": account["id"]},
        )
        execute(conn, "DELETE FROM TB_TA_AGORA_SESSIONS WHERE account_id = :account_id", {"account_id": account["id"]})
        execute(
            conn,
            "INSERT INTO TB_TA_AGORA_AUDIT(id,actor_id,action,subject_id,details) "
            "VALUES (:id,NULL,'operator_password_reset',:subject_id,'{\"source\":\"operator CLI\"}')",
            {"id": str(uuid4()), "subject_id": account["id"]},
        )
    print("Password reset; existing sessions revoked.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agora operator account administration")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-admin")
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument("--full-name", required=True)
    reset = commands.add_parser("reset-password")
    reset.add_argument("--username", required=True)
    args = parser.parse_args()
    if args.command == "bootstrap-admin":
        bootstrap_admin(args.username, args.full_name)
    else:
        reset_password(args.username)


if __name__ == "__main__":
    main()
