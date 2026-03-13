"""
User repository — CRUD operations for the users table.

Replaces data/users.json + data/allowed_emails.json file-based storage.
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from src.database.models import User

logger = logging.getLogger(__name__)


class UserRepo:
    """Database operations for User model."""

    def __init__(self, session: Session):
        self.session = session

    def find_by_id(self, user_id: str) -> Optional[User]:
        return self.session.query(User).filter(User.id == user_id).first()

    def find_by_email(self, email: str) -> Optional[User]:
        return (
            self.session.query(User)
            .filter(User.email == email.lower())
            .first()
        )

    def find_by_username(self, username: str) -> Optional[User]:
        return (
            self.session.query(User)
            .filter(User.username == username)
            .first()
        )

    def find_by_google_id(self, google_id: str) -> Optional[User]:
        return (
            self.session.query(User)
            .filter(User.google_id == google_id)
            .first()
        )

    def find_by_telegram_chat_id(self, chat_id: str) -> Optional[User]:
        return (
            self.session.query(User)
            .filter(User.telegram_chat_id == chat_id)
            .first()
        )

    def create(
        self,
        *,
        email: Optional[str] = None,
        name: Optional[str] = None,
        username: Optional[str] = None,
        password_hash: Optional[str] = None,
        google_id: Optional[str] = None,
    ) -> User:
        user = User(
            email=email.lower() if email else None,
            name=name,
            username=username,
            password_hash=password_hash,
            google_id=google_id,
        )
        self.session.add(user)
        self.session.flush()  # get ID without committing
        logger.info(f"[DB] Created user: {user.id} ({email or username})")
        return user

    def update_password(self, user_id: str, password_hash: str) -> bool:
        user = self.find_by_id(user_id)
        if not user:
            return False
        user.password_hash = password_hash
        return True

    def find_or_create_google_user(
        self, email: str, name: str, google_id: Optional[str] = None,
    ) -> User:
        """Find existing user by email or create new one for Google OAuth."""
        user = self.find_by_email(email)
        if user:
            # Update google_id if not set
            if google_id and not user.google_id:
                user.google_id = google_id
            return user

        return self.create(
            email=email,
            name=name,
            google_id=google_id,
        )

    def list_active(self, limit: int = 100) -> list[User]:
        return (
            self.session.query(User)
            .filter(User.is_active.is_(True))
            .limit(limit)
            .all()
        )
