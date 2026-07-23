"""OAuth clients registered via Dynamic Client Registration.

Claude's remote MCP connector registers itself here (or the user pastes a
pre-registered client_id). We store the allowed redirect_uris so /authorize
can reject open-redirect attempts. Public clients (PKCE, no secret).
"""
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class OAuthClient(Base):
    __tablename__ = "oauth_clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    redirect_uris: Mapped[str] = mapped_column(Text)  # JSON array of allowed URIs
    client_name: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
