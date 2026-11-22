"""Mock identity adapter: always resolves one fixed local user."""

MOCK_PHONE = "mock@example.local"
MOCK_INVITE_CODE = "TCH-MOCK0"


def authenticate(db, credential: str, *, subject: str | None = None):
    from app.models import User

    user = db.query(User).filter_by(phone=MOCK_PHONE).first()
    if user is None:
        user = User(
            phone=MOCK_PHONE,
            display_name="Mock User",
            role="user",
            is_active=True,
            invite_code=MOCK_INVITE_CODE,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user
