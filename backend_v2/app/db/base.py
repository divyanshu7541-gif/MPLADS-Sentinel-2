"""
SQLAlchemy 2 declarative base. No models are defined against it yet -- Phase
0 only wires the base so Alembic autogenerate has something to diff against
starting Phase 1.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
