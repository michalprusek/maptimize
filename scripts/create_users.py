#!/usr/bin/env python3
"""
Create users in Maptimize database.

SECURITY NOTE: This script requires credentials via environment variables.
Never hardcode credentials in this file.

Usage (from host):
    export DB_HOST=localhost
    export DB_PORT=7432
    export DB_PASSWORD=your_password
    export NEW_USER_PASSWORD=password_for_new_users
    python scripts/create_users.py

Usage (from inside container):
    docker exec -it maptimize-backend python -c "
    import os
    os.environ['DB_HOST'] = 'maptimize-db'
    os.environ['DB_PORT'] = '5432'
    os.environ['DB_PASSWORD'] = 'your_password'
    os.environ['NEW_USER_PASSWORD'] = 'password_for_new_users'
    exec(open('/app/scripts/create_users.py').read())
    "
"""

import os
import sys
import bcrypt
import psycopg2
from datetime import datetime


def get_db_config():
    """Get database configuration from environment variables."""
    password = os.environ.get("DB_PASSWORD")
    if not password:
        print("ERROR: DB_PASSWORD environment variable is required")
        sys.exit(1)

    return {
        "host": os.environ.get("DB_HOST", "maptimize-db"),
        "port": int(os.environ.get("DB_PORT", "5432")),
        "database": os.environ.get("DB_NAME", "maptimize"),
        "user": os.environ.get("DB_USER", "maptimize"),
        "password": password,
    }


def get_user_password():
    """Get password for new users from environment variable."""
    password = os.environ.get("NEW_USER_PASSWORD")
    if not password:
        print("ERROR: NEW_USER_PASSWORD environment variable is required")
        sys.exit(1)
    return password


# Users to create (email, name)
USERS = [
    ("sroubekf@utia.cas.cz", "Filip Šroubek"),
    ("novozamsky@utia.cas.cz", "Adam Novozámský"),
    ("Carsten.Janke@curie.fr", "Carsten Janke"),
    ("eva.desvigne-hansch@curie.fr", "Eva Desvigne-Hansch"),
    ("s.m.sewnarainsukul@students.uu.nl", "S.M. Sewnarainsukul"),
    ("Zdenek.Lansky@ibt.cas.cz", "Zdeněk Lanský"),
]


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


def create_users():
    """Create users in the database with proper error handling."""
    db_config = get_db_config()
    user_password = get_user_password()

    conn = None
    cur = None
    created = []
    skipped = []
    failed = []

    try:
        conn = psycopg2.connect(**db_config)
        cur = conn.cursor()
    except psycopg2.Error as e:
        print(f"ERROR: Failed to connect to database: {e}")
        print(f"Config: host={db_config['host']}, port={db_config['port']}")
        return [], [], [("connection", str(e))]

    password_hash = hash_password(user_password)

    for email, name in USERS:
        try:
            # Check if user exists
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                skipped.append(email)
                continue

            # Create user with 'RESEARCHER' role
            cur.execute(
                """
                INSERT INTO users (email, name, password_hash, role, created_at)
                VALUES (%s, %s, %s, 'RESEARCHER', %s)
                RETURNING id
                """,
                (email, name, password_hash, datetime.utcnow())
            )
            user_id = cur.fetchone()[0]
            created.append((email, user_id))

        except psycopg2.Error as e:
            print(f"ERROR: Failed to create user {email}: {e}")
            failed.append((email, str(e)))
            conn.rollback()
            continue

    # Commit all successful inserts
    try:
        conn.commit()
    except psycopg2.Error as e:
        print(f"ERROR: Failed to commit transaction: {e}")
        conn.rollback()
        # Mark all as failed since commit failed
        failed.extend([(email, "commit failed") for email, _ in created])
        created = []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return created, skipped, failed


if __name__ == "__main__":
    print("Creating users in Maptimize database...")
    print("-" * 50)

    created, skipped, failed = create_users()

    if created:
        print("\n✅ Created users:")
        for email, user_id in created:
            print(f"  - {email} (ID: {user_id})")

    if skipped:
        print("\n⚠️  Skipped (already exist):")
        for email in skipped:
            print(f"  - {email}")

    if failed:
        print("\n❌ Failed:")
        for email, error in failed:
            print(f"  - {email}: {error}")
        sys.exit(1)

    print("\nDone!")
