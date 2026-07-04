"""
VIGIL-AI Cameroun — Database Seed Script
Creates default roles (if missing) and an initial Admin user.

Usage:
    python scripts/seed_db.py
    python scripts/seed_db.py --with-demo-data   # also creates sample analyst/viewer + demo submissions
"""
import asyncio
import sys
from pathlib import Path

# Allow running as a standalone script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.security import hash_password
from app.database import AsyncSessionLocal
from app.models.user import Role, User

DEFAULT_ROLES = [
    ("admin", "Full system access — user management, all cases, audit logs"),
    ("analyst", "Submit content, manage and investigate cases"),
    ("viewer", "Read-only access to dashboards and reports"),
]

ADMIN_EMAIL = "admin@vigilai.cm"
ADMIN_PASSWORD = "VigilAdmin2026!"  # CHANGE THIS after first login

DEMO_USERS = [
    {
        "email": "analyst@antic.cm",
        "password": "AnalystPass2026!",
        "full_name": "Marie Ngo Bilong",
        "role": "analyst",
        "organization": "ANTIC",
    },
    {
        "email": "viewer@minpostel.cm",
        "password": "ViewerPass2026!",
        "full_name": "Paul Atangana",
        "role": "viewer",
        "organization": "MINPOSTEL",
    },
]


async def seed_roles(db) -> dict[str, Role]:
    """Create default roles if they don't already exist."""
    role_map = {}
    for name, description in DEFAULT_ROLES:
        result = await db.execute(select(Role).where(Role.name == name))
        role = result.scalar_one_or_none()
        if not role:
            role = Role(name=name, description=description)
            db.add(role)
            await db.flush()
            print(f"  ✅ Created role: {name}")
        else:
            print(f"  ⏭️  Role already exists: {name}")
        role_map[name] = role
    return role_map


async def seed_admin(db, role_map: dict[str, Role]) -> None:
    """Create the default admin user."""
    result = await db.execute(select(User).where(User.email == ADMIN_EMAIL))
    existing = result.scalar_one_or_none()
    if existing:
        print(f"  ⏭️  Admin user already exists: {ADMIN_EMAIL}")
        return

    admin = User(
        email=ADMIN_EMAIL,
        password_hash=hash_password(ADMIN_PASSWORD),
        full_name="System Administrator",
        role_id=role_map["admin"].id,
        organization="VIGIL-AI Cameroun",
        is_active=True,
    )
    db.add(admin)
    print(f"  ✅ Created admin user: {ADMIN_EMAIL}")
    print(f"     Password: {ADMIN_PASSWORD}")
    print(f"     ⚠️  CHANGE THIS PASSWORD after first login!")


async def seed_demo_users(db, role_map: dict[str, Role]) -> None:
    """Create demo analyst and viewer accounts for testing."""
    for demo in DEMO_USERS:
        result = await db.execute(select(User).where(User.email == demo["email"]))
        if result.scalar_one_or_none():
            print(f"  ⏭️  Demo user already exists: {demo['email']}")
            continue

        user = User(
            email=demo["email"],
            password_hash=hash_password(demo["password"]),
            full_name=demo["full_name"],
            role_id=role_map[demo["role"]].id,
            organization=demo["organization"],
            is_active=True,
        )
        db.add(user)
        print(f"  ✅ Created demo user: {demo['email']} (role={demo['role']})")
        print(f"     Password: {demo['password']}")


async def main():
    with_demo = "--with-demo-data" in sys.argv

    print("=" * 60)
    print("VIGIL-AI Cameroun — Database Seeding")
    print("=" * 60)

    async with AsyncSessionLocal() as db:
        print("\n📋 Seeding roles...")
        role_map = await seed_roles(db)
        await db.commit()

        print("\n👤 Seeding admin user...")
        await seed_admin(db, role_map)
        await db.commit()

        if with_demo:
            print("\n👥 Seeding demo users...")
            await seed_demo_users(db, role_map)
            await db.commit()

    print("\n" + "=" * 60)
    print("✅ Database seeding complete!")
    print("=" * 60)
    print(f"\nLogin with:")
    print(f"  Email:    {ADMIN_EMAIL}")
    print(f"  Password: {ADMIN_PASSWORD}")
    print(f"\nAPI Docs: http://localhost:8000/docs")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
