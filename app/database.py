"""
Async database engine and session management for the embedded SQLite instance
('transit_intel.db'). Uses SQLAlchemy 2.0 async style with the aiosqlite driver.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base

DATABASE_URL = "sqlite+aiosqlite:///./transit_intel.db"

engine = create_async_engine(DATABASE_URL, echo=False, future=True)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Create all tables if they do not already exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a scoped async session per request."""
    async with AsyncSessionLocal() as session:
        yield session
