"""User account service: scrypt password hashing, authentication, creation.

Password storage: b"salt(16) || scrypt_hash(64)" in users.password_hash.
scrypt is stdlib (hashlib) — no new dependency, memory-hard, fine at this scale.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.db.models import TenantMembership, User
from disha.db.models.enums import MembershipRole

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_SALT_LEN, _HASH_LEN = 16, 64


def hash_password(password: str) -> bytes:
    salt = os.urandom(_SALT_LEN)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=_HASH_LEN,
    )
    return salt + digest


def verify_password(password: str, stored: bytes) -> bool:
    if not stored or len(stored) != _SALT_LEN + _HASH_LEN:
        return False
    salt, expected = stored[:_SALT_LEN], stored[_SALT_LEN:]
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=_HASH_LEN,
    )
    return hashlib.sha256(digest).digest() == hashlib.sha256(expected).digest()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    return (
        await session.execute(
            select(User).where(User.email == email.strip().lower())
        )
    ).scalar_one_or_none()


async def primary_tenant_id(session: AsyncSession, user_id: UUID) -> UUID | None:
    row = (
        await session.execute(
            select(TenantMembership.tenant_id)
            .where(TenantMembership.user_id == user_id)
            .order_by(TenantMembership.created_at)
        )
    ).scalars().first()
    return row


async def authenticate(
    session: AsyncSession, email: str, password: str
) -> tuple[User, UUID] | None:
    """Returns (user, tenant_id) on success, None on any failure."""
    user = await get_user_by_email(session, email)
    if user is None or user.password_hash is None:
        # Burn comparable CPU so user-exists is not timing-observable.
        hash_password(password)
        return None
    if not verify_password(password, user.password_hash):
        return None
    tenant_id = await primary_tenant_id(session, user.id)
    if tenant_id is None:
        return None
    user.last_login_at = datetime.now(UTC)
    return user, tenant_id


async def create_user(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    email: str,
    password: str,
    name: str | None = None,
    is_admin: bool = False,
    must_change_password: bool = False,
    role: MembershipRole = MembershipRole.OWNER,
) -> User:
    """Create (or claim) a user and attach to the tenant. If the email already
    exists without a password (pre-auth era), set its password instead.

    `must_change_password=True` forces a reset at first login — used for
    admin-provisioned accounts whose initial password was emailed to them."""
    email = email.strip().lower()
    user = await get_user_by_email(session, email)
    if user is None:
        user = User(
            email=email,
            name=name,
            password_hash=hash_password(password),
            is_admin=is_admin,
            must_change_password=must_change_password,
        )
        session.add(user)
        await session.flush()
    elif user.password_hash is None:
        user.password_hash = hash_password(password)
        user.is_admin = user.is_admin or is_admin
        user.must_change_password = must_change_password
    else:
        raise ValueError(f"User {email!r} already exists")

    membership = (
        await session.execute(
            select(TenantMembership).where(
                TenantMembership.user_id == user.id,
                TenantMembership.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        session.add(
            TenantMembership(tenant_id=tenant_id, user_id=user.id, role=role)
        )
    return user
