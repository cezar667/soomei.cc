"""High-level data access helpers backed by SQLAlchemy."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from typing import Generic, Optional, TypeVar

from sqlalchemy import delete, func, or_, select, update

from api.db.models import (
    AdminSession,
    Card,
    CustomDomain,
    Profile,
    ResetToken,
    User,
    UserSession,
    VerifyToken,
)
from api.db.session import get_session

T = TypeVar("T")


@dataclass(frozen=True)
class PageResult(Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        if self.total <= 0:
            return 1
        return max(1, ceil(self.total / self.page_size))


class SQLRepository:
    """CRUD helpers wrapping the SQLAlchemy session."""

    # -------------------------- internal helpers --------------------------
    @staticmethod
    def _sanitize_page(page: int, page_size: int) -> tuple[int, int]:
        safe_page = max(1, int(page or 1))
        safe_page_size = max(1, min(int(page_size or 20), 100))
        return safe_page, safe_page_size

    @staticmethod
    def _domain_meta_text(key: str):
        return func.lower(func.coalesce(Card.custom_domain_meta[key].as_string(), ""))

    @classmethod
    def _card_query_filters(cls, q: str = "", status: str = "") -> list:
        filters: list = []
        qnorm = (q or "").strip().lower()
        if qnorm:
            pattern = f"%{qnorm}%"
            filters.append(
                or_(
                    func.lower(Card.uid).like(pattern),
                    func.lower(func.coalesce(Card.vanity, "")).like(pattern),
                    func.lower(func.coalesce(Card.owner_email, "")).like(pattern),
                )
            )
        status_norm = (status or "").strip().lower()
        if status_norm:
            filters.append(func.lower(Card.status) == status_norm)
        return filters

    @classmethod
    def _user_query_filters(cls, q: str = "") -> list:
        qnorm = (q or "").strip().lower()
        if not qnorm:
            return []
        pattern = f"%{qnorm}%"
        return [func.lower(User.email).like(pattern)]

    @classmethod
    def _domain_query_filters(cls, q: str = "") -> list:
        active_host = cls._domain_meta_text("active_host")
        requested_host = cls._domain_meta_text("requested_host")
        status = cls._domain_meta_text("status")
        admin_note = func.lower(func.coalesce(Card.custom_domain_meta["admin_note"].as_string(), ""))
        filters = [or_(active_host != "", requested_host != "", status != "")]
        qnorm = (q or "").strip().lower()
        if qnorm:
            pattern = f"%{qnorm}%"
            filters.append(
                or_(
                    func.lower(Card.uid).like(pattern),
                    func.lower(func.coalesce(Card.vanity, "")).like(pattern),
                    active_host.like(pattern),
                    requested_host.like(pattern),
                    status.like(pattern),
                    admin_note.like(pattern),
                )
            )
        return filters

    @staticmethod
    def _count_for_statement(statement) -> int:
        count_stmt = select(func.count()).select_from(statement.order_by(None).subquery())
        with get_session() as session:
            return int(session.execute(count_stmt).scalar_one() or 0)

    @classmethod
    def _page_from_statement(cls, statement, *, page: int, page_size: int, order_by) -> PageResult:
        safe_page, safe_page_size = cls._sanitize_page(page, page_size)
        total = cls._count_for_statement(statement)
        offset = (safe_page - 1) * safe_page_size
        paged_stmt = statement.order_by(*order_by).offset(offset).limit(safe_page_size)
        with get_session() as session:
            items = session.execute(paged_stmt).scalars().all()
        return PageResult(items=items, total=total, page=safe_page, page_size=safe_page_size)

    # -------------------------- users --------------------------
    def get_user(self, email: str) -> Optional[User]:
        with get_session() as session:
            return session.get(User, email)

    def list_users(self) -> list[User]:
        with get_session() as session:
            stmt = select(User).order_by(User.created_at.desc(), User.email.asc())
            return session.execute(stmt).scalars().all()

    def search_users(self, *, q: str = "", page: int = 1, page_size: int = 20) -> PageResult[User]:
        stmt = select(User).where(*self._user_query_filters(q))
        return self._page_from_statement(
            stmt,
            page=page,
            page_size=page_size,
            order_by=(User.created_at.desc(), User.email.asc()),
        )

    def upsert_user(self, email: str, password_hash: str, email_verified_at: datetime | None = None) -> User:
        now = datetime.now(timezone.utc)
        with get_session() as session:
            user = session.get(User, email)
            if not user:
                user = User(
                    email=email,
                    password_hash=password_hash,
                    email_verified_at=email_verified_at,
                    created_at=now,
                    updated_at=now,
                )
                session.add(user)
            else:
                user.password_hash = password_hash or user.password_hash
                if email_verified_at is not None:
                    user.email_verified_at = email_verified_at
                user.updated_at = now
            session.commit()
            session.refresh(user)
            return user

    def update_user_password(self, email: str, password_hash: str) -> None:
        with get_session() as session:
            stmt = (
                update(User)
                .where(User.email == email)
                .values(password_hash=password_hash, updated_at=datetime.now(timezone.utc))
            )
            session.execute(stmt)
            session.commit()

    def set_user_verified(self, email: str) -> None:
        now = datetime.now(timezone.utc)
        with get_session() as session:
            stmt = update(User).where(User.email == email).values(email_verified_at=now, updated_at=now)
            session.execute(stmt)
            session.commit()

    # -------------------------- cards --------------------------
    def get_card_by_uid(self, uid: str) -> Optional[Card]:
        with get_session() as session:
            return session.get(Card, uid)

    def get_card_by_vanity(self, vanity: str) -> Optional[Card]:
        with get_session() as session:
            stmt = select(Card).where(Card.vanity == vanity)
            return session.execute(stmt).scalar_one_or_none()

    def get_cards_by_owner(self, email: str) -> list[Card]:
        with get_session() as session:
            stmt = select(Card).where(Card.owner_email == email).order_by(Card.created_at.asc(), Card.uid.asc())
            return session.execute(stmt).scalars().all()

    def list_cards(self) -> list[Card]:
        with get_session() as session:
            stmt = select(Card).order_by(Card.created_at.desc(), Card.uid.asc())
            return session.execute(stmt).scalars().all()

    def search_cards(self, *, q: str = "", status: str = "", page: int = 1, page_size: int = 20) -> PageResult[Card]:
        stmt = select(Card).where(*self._card_query_filters(q, status))
        return self._page_from_statement(
            stmt,
            page=page,
            page_size=page_size,
            order_by=(Card.created_at.desc(), Card.uid.asc()),
        )

    def dashboard_card_counts(self) -> dict[str, int]:
        counts = {"active": 0, "pending": 0, "blocked": 0}
        with get_session() as session:
            stmt = select(func.lower(Card.status), func.count()).group_by(func.lower(Card.status))
            for status, total in session.execute(stmt):
                status_key = (status or "").lower()
                counts[status_key] = int(total or 0)
        counts["total"] = sum(counts.values())
        return counts

    def top_cards_by_views(self, *, limit: int = 5) -> list[tuple[str, int]]:
        with get_session() as session:
            stmt = (
                select(Card.uid, Card.vanity, Card.metrics_views)
                .where(Card.metrics_views > 0)
                .order_by(Card.metrics_views.desc(), Card.updated_at.desc(), Card.uid.asc())
                .limit(max(1, int(limit or 5)))
            )
            rows = session.execute(stmt).all()
        return [((vanity or uid or ""), int(views or 0)) for uid, vanity, views in rows]

    def list_cards_with_custom_domains(self, *, q: str = "", page: int = 1, page_size: int = 20) -> PageResult[Card]:
        stmt = select(Card).where(*self._domain_query_filters(q))
        return self._page_from_statement(
            stmt,
            page=page,
            page_size=page_size,
            order_by=(Card.updated_at.desc(), Card.uid.asc()),
        )

    def create_card(self, uid: str, pin: str, vanity: str | None = None, owner_email: str | None = None) -> Card:
        now = datetime.now(timezone.utc)
        entity = Card(
            uid=uid,
            status="pending",
            pin=str(pin),
            billing_status=None,
            owner_email=owner_email,
            vanity=vanity,
            metrics_views=0,
            custom_domain_meta={},
            created_at=now,
            updated_at=now,
        )
        with get_session() as session:
            session.add(entity)
            session.commit()
            session.refresh(entity)
            return entity

    def delete_card(self, uid: str) -> None:
        with get_session() as session:
            session.execute(delete(Card).where(Card.uid == uid))
            session.commit()

    def reset_card(
        self,
        uid: str,
        *,
        new_pin: str,
        clear_owner: bool = True,
        clear_vanity: bool = True,
        clear_custom_domain: bool = True,
    ) -> None:
        with get_session() as session:
            values = {
                "status": "pending",
                "pin": str(new_pin),
                "billing_status": None,
                "metrics_views": 0,
                "updated_at": datetime.now(timezone.utc),
            }
            if clear_owner:
                values["owner_email"] = None
            if clear_vanity:
                values["vanity"] = None
            if clear_custom_domain:
                values["custom_domain_meta"] = {}
            stmt = update(Card).where(Card.uid == uid).values(**values)
            session.execute(stmt)
            session.commit()

    def slug_exists(self, slug: str) -> bool:
        slug_value = (slug or "").strip()
        if not slug_value:
            return False
        with get_session() as session:
            stmt = select(Card.uid).where(Card.vanity == slug_value).limit(1)
            return session.execute(stmt).first() is not None

    def sync_card_from_json(self, uid: str, data: dict | None) -> None:
        if not data:
            return
        now = datetime.now(timezone.utc)
        status = data.get("status") or "pending"
        pin = str(data.get("pin") or "")
        billing_status = data.get("billing_status")
        owner_email = (data.get("user") or "").strip() or None
        vanity = (data.get("vanity") or "").strip() or None
        metrics = data.get("metrics") or {}
        views = int(metrics.get("views") or 0)
        custom_meta = data.get("custom_domain") or {}
        with get_session() as session:
            card = session.get(Card, uid)
            if not card:
                card = Card(
                    uid=uid,
                    status=status,
                    pin=pin,
                    billing_status=billing_status,
                    owner_email=owner_email,
                    vanity=vanity,
                    metrics_views=views,
                    custom_domain_meta=custom_meta,
                    created_at=now,
                    updated_at=now,
                )
                session.add(card)
            else:
                card.status = status
                card.pin = pin
                card.billing_status = billing_status
                card.owner_email = owner_email
                card.vanity = vanity
                card.metrics_views = views
                card.custom_domain_meta = custom_meta
                card.updated_at = now
            session.commit()

    def update_card_slug(self, uid: str, slug: str) -> None:
        with get_session() as session:
            stmt = update(Card).where(Card.uid == uid).values(vanity=slug, updated_at=datetime.now(timezone.utc))
            session.execute(stmt)
            session.commit()

    def assign_card_owner(
        self,
        uid: str,
        email: str,
        *,
        status: str = "active",
        vanity: str | None = None,
        billing_status: str | None = "ok",
    ) -> None:
        with get_session() as session:
            stmt = (
                update(Card)
                .where(Card.uid == uid)
                .values(
                    owner_email=email,
                    status=status,
                    billing_status=billing_status,
                    vanity=vanity,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            session.execute(stmt)
            session.commit()

    def increment_card_views(self, uid: str) -> int:
        with get_session() as session:
            card = session.get(Card, uid)
            if not card:
                return 0
            card.metrics_views = int(card.metrics_views or 0) + 1
            card.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(card)
            return int(card.metrics_views or 0)
        return 0

    def update_card_custom_domain_meta(self, uid: str, meta: dict) -> None:
        with get_session() as session:
            stmt = (
                update(Card)
                .where(Card.uid == uid)
                .values(custom_domain_meta=meta or {}, updated_at=datetime.now(timezone.utc))
            )
            session.execute(stmt)
            session.commit()

    def update_card_status(self, uid: str, status: str, billing_status: str | None = None) -> None:
        with get_session() as session:
            stmt = (
                update(Card)
                .where(Card.uid == uid)
                .values(
                    status=status,
                    billing_status=billing_status if billing_status is not None else Card.billing_status,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            session.execute(stmt)
            session.commit()

    # -------------------------- admin sessions --------------------------
    def create_admin_session(self, email: str, csrf_token: str, expires_at: datetime) -> str:
        token = secrets.token_urlsafe(32)
        entity = AdminSession(token=token, email=email, csrf_token=csrf_token, expires_at=expires_at)
        with get_session() as session:
            session.add(entity)
            session.commit()
        return token

    def get_admin_session(self, token: str) -> Optional[AdminSession]:
        with get_session() as session:
            return session.get(AdminSession, token)

    def delete_admin_session(self, token: str) -> None:
        with get_session() as session:
            session.execute(delete(AdminSession).where(AdminSession.token == token))
            session.commit()

    # -------------------------- profiles --------------------------
    def get_profile(self, email: str) -> Optional[dict]:
        with get_session() as session:
            profile = session.get(Profile, email)
            return profile.data if profile else None

    def upsert_profile(self, email: str, data: dict) -> None:
        now = datetime.now(timezone.utc)
        with get_session() as session:
            profile = session.get(Profile, email)
            if not profile:
                profile = Profile(email=email, data=data, updated_at=now)
                session.add(profile)
            else:
                profile.data = data
                profile.updated_at = now
            session.commit()

    def delete_profile(self, email: str) -> None:
        with get_session() as session:
            session.execute(delete(Profile).where(Profile.email == email))
            session.commit()

    # -------------------------- tokens --------------------------
    def create_verify_token(self, email: str, token: Optional[str] = None) -> str:
        token_value = token or secrets.token_urlsafe(24)
        entity = VerifyToken(token=token_value, email=email, created_at=datetime.now(timezone.utc))
        with get_session() as session:
            session.add(entity)
            session.commit()
        return token_value

    def get_verify_token(self, token: str) -> Optional[VerifyToken]:
        with get_session() as session:
            return session.get(VerifyToken, token)

    def get_verify_token_for_email(self, email: str) -> Optional[VerifyToken]:
        with get_session() as session:
            stmt = select(VerifyToken).where(VerifyToken.email == email).order_by(VerifyToken.created_at.desc())
            return session.execute(stmt).scalars().first()

    def delete_verify_token(self, token: str) -> None:
        with get_session() as session:
            session.execute(delete(VerifyToken).where(VerifyToken.token == token))
            session.commit()

    def delete_verify_tokens_for_email(self, email: str) -> None:
        with get_session() as session:
            session.execute(delete(VerifyToken).where(VerifyToken.email == email))
            session.commit()

    def create_reset_token(self, email: str, token: Optional[str] = None) -> str:
        token_value = token or secrets.token_urlsafe(24)
        entity = ResetToken(token=token_value, email=email, created_at=datetime.now(timezone.utc))
        with get_session() as session:
            session.add(entity)
            session.commit()
        return token_value

    def get_reset_token(self, token: str) -> Optional[ResetToken]:
        with get_session() as session:
            return session.get(ResetToken, token)

    def delete_reset_token(self, token: str) -> None:
        with get_session() as session:
            session.execute(delete(ResetToken).where(ResetToken.token == token))
            session.commit()

    def delete_reset_tokens_for_email(self, email: str) -> None:
        with get_session() as session:
            session.execute(delete(ResetToken).where(ResetToken.email == email))
            session.commit()

    # -------------------------- user sessions --------------------------
    def delete_user_sessions(self, email: str) -> None:
        with get_session() as session:
            session.execute(delete(UserSession).where(UserSession.user_email == email))
            session.commit()

    # -------------------------- users cleanup --------------------------
    def delete_user(self, email: str) -> None:
        with get_session() as session:
            session.execute(delete(User).where(User.email == email))
            session.commit()

    # -------------------------- custom domains --------------------------
    def get_custom_domain(self, host: str) -> Optional[CustomDomain]:
        with get_session() as session:
            return session.get(CustomDomain, host)

    def get_card_by_custom_domain(self, host: str) -> Optional[Card]:
        host_norm = (host or "").strip().lower()
        if not host_norm:
            return None
        active_host = self._domain_meta_text("active_host")
        with get_session() as session:
            entry = session.get(CustomDomain, host_norm)
            if entry:
                return session.get(Card, entry.card_uid)
            stmt = select(Card).where(active_host == host_norm).limit(1)
            return session.execute(stmt).scalar_one_or_none()

    def get_cards_for_domain_checks(self) -> list[Card]:
        with get_session() as session:
            stmt = select(Card).where(*self._domain_query_filters()).order_by(Card.updated_at.desc(), Card.uid.asc())
            return session.execute(stmt).scalars().all()

    def custom_domain_conflict_exists(self, host: str, *, exclude_uid: str | None = None) -> bool:
        host_norm = (host or "").strip().lower()
        if not host_norm:
            return False
        active_host = self._domain_meta_text("active_host")
        requested_host = self._domain_meta_text("requested_host")
        status = self._domain_meta_text("status")
        conditions = [active_host == host_norm, (requested_host == host_norm) & status.in_(("pending", "active"))]
        stmt = select(Card.uid).where(or_(*conditions))
        if exclude_uid:
            stmt = stmt.where(Card.uid != exclude_uid)
        stmt = stmt.limit(1)
        with get_session() as session:
            entry = session.get(CustomDomain, host_norm)
            if entry and entry.card_uid != exclude_uid:
                return True
            return session.execute(stmt).first() is not None

    def register_custom_domain(self, host: str, uid: str) -> None:
        entity = CustomDomain(host=host, card_uid=uid)
        with get_session() as session:
            session.merge(entity)
            session.commit()

    def unregister_custom_domain(self, host: str) -> None:
        with get_session() as session:
            session.execute(delete(CustomDomain).where(CustomDomain.host == host))
            session.commit()
