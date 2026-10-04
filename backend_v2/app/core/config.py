"""
Sentinel v2 configuration.

Phase 0 scope was connection settings only. Phase 1 adds `data_dir`, the
path to the repo-root `data/` folder the ingestion pipeline reads raw files
from -- it lives outside backend_v2/ (and outside the Docker build context;
see docker-compose.yml / scripts/run_ingest.py for how it's bind-mounted).
No weights, thresholds, or scoring config yet -- those get ported (and
reconsidered) once business logic phases start. See
backend/app/core/config.py for the current provisional values this will
eventually need to account for.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://sentinel:sentinel@localhost:5432/sentinel"
    log_level: str = "info"
    # Default assumes `backend_v2/` sits beside `data/` at the repo root
    # (true for a local, non-Docker run); overridden to /data by the
    # container-based ingest runner.
    data_dir: str = str(Path(__file__).resolve().parents[3] / "data")
    # Phase 12: precompute the dataset-wide summary/analytics/data-health
    # payloads in a background thread at startup (off by default so tests
    # don't race it; docker-compose turns it on).
    warm_cache: bool = False
    # Connection pool per API process (app/db/session.py). Small by default:
    # on Vercel every warm instance holds its own pool against one hosted
    # Postgres, so 5 + 10 per instance runs out of connections under
    # autoscale. The persistent compose stack and CI set 5 / 10 (the old
    # SQLAlchemy defaults). pool_recycle drops connections older than this
    # many seconds, under the idle cut-off of hosted Postgres providers.
    db_pool_size: int = 2
    db_max_overflow: int = 0
    db_pool_recycle: int = 300

    # ---- Phase 13: security (BLUEPRINT.md §11). Every value comes from the
    # environment; no secret has a usable default. ----------------------------------------
    # HS256 signing key for access tokens. Unset: development generates a
    # random per-process key (tokens die with the process); any other
    # APP_ENV refuses to start (app/auth/tokens.py).
    jwt_secret: str = ""
    jwt_issuer: str = "mplads-sentinel"
    jwt_audience: str = "mplads-sentinel-api"
    access_token_minutes: int = 15  # short-lived; there is no refresh token
    # Read access without a token, as the built-in "public" role (national,
    # read-only, no personal-data or case/audit endpoints). ON by default
    # because the protected frontend sends no token (its login page is a
    # client-side demo); set false to require a token on every data endpoint.
    anonymous_read: bool = True
    # CORS: explicit allowlist, comma-separated; never "*". Defaults are the
    # two local dev origins in this repo (compose/vite dev :3000, vite preview
    # :4173). The deployed frontend's origin is added in Phase 14.
    cors_origins: str = "http://localhost:3000,http://localhost:4173"
    # Optional regex for preview-deployment origins (Phase 14), e.g.
    # ^https://mplads-sentinel-[a-z0-9-]+\.vercel\.app$ -- unset here.
    cors_origin_regex: str = ""
    # Rate limits, requests per minute per client (and per user where one
    # is authenticated). In-process: correct for one API process; on Vercel
    # each instance counts on its own, so the effective limit is N x this. A
    # hard global limit needs a shared store (docs/security.md).
    rate_limit_login_per_minute: int = 10
    rate_limit_chat_per_minute: int = 30
    rate_limit_search_per_minute: int = 300
    # Phase 14: the reverse proxy(ies) in front of the API (e.g. the cloudflared
    # container), comma-separated IPs or CIDRs. Forwarded client-IP headers
    # (CF-Connecting-IP, X-Forwarded-For) are honoured ONLY when the TCP peer is
    # one of these; any other caller's headers are ignored (they are spoofable).
    trusted_proxies: str = ""
    # Behind a platform proxy with no fixed peer address (Vercel), forwarded
    # client-IP headers are trusted from any peer: when VERCEL is set (the
    # platform sets VERCEL=1) or TRUST_PROXY_HEADERS=1 (Dockerfile.vercel sets
    # it). Never set either on a server clients can reach directly -- they
    # could then pick their own rate-limit key.
    vercel: str = ""
    trust_proxy_headers: bool = False

    @property
    def behind_platform_proxy(self) -> bool:
        return self.trust_proxy_headers or self.vercel.strip() not in ("", "0")

    def check_configuration(self) -> None:
        """Called at startup (app/main.py). Outside development no setting may
        fall back to a credential-bearing default: database_url's default
        carries the local dev password, so DATABASE_URL must be supplied.
        (JWT_SECRET has no default at all; app/auth/tokens.py checks it.)"""
        supplied = "database_url" in self.model_fields_set and bool(self.database_url)
        if self.app_env != "development" and not supplied:
            raise RuntimeError("DATABASE_URL is not set (required outside APP_ENV=development)")

    @property
    def trusted_proxy_networks(self) -> list:
        import ipaddress

        parts = [p.strip() for p in self.trusted_proxies.split(",")]
        return [ipaddress.ip_network(p, strict=False) for p in parts if p]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
