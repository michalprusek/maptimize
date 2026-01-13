#!/usr/bin/env python3
"""One-time script to create users in Maptimize database."""

import bcrypt
import psycopg2
from datetime import datetime

# Database connection (from inside Docker container)
DB_CONFIG = {
    "host": "maptimize-db",
    "port": 5432,
    "database": "maptimize",
    "user": "maptimize",
    "password": "maptimize_secure_2024"
}

# Password for all new users
PASSWORD = "MAPtimize2026"

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
    """Create users in the database."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    password_hash = hash_password(PASSWORD)
    created = []
    skipped = []

    for email, name in USERS:
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

    conn.commit()
    cur.close()
    conn.close()

    return created, skipped


if __name__ == "__main__":
    print("Creating users in Maptimize database...")
    print(f"Password: {PASSWORD}")
    print("-" * 50)

    created, skipped = create_users()

    if created:
        print("\n✅ Created users:")
        for email, user_id in created:
            print(f"  - {email} (ID: {user_id})")

    if skipped:
        print("\n⚠️  Skipped (already exist):")
        for email in skipped:
            print(f"  - {email}")

    print("\nDone!")
