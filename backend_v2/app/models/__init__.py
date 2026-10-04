"""
Import every model module here so Base.metadata is fully populated before
Alembic autogenerate or create_all runs.
"""

from . import (  # noqa: F401
    analytics,
    entities,
    geo,
    graph,
    provenance,
    reference,
    security,
    serving,
    work,
    work_related,
)
