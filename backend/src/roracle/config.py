from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ROR_")

    database_url: str
    s3_endpoint: str
    s3_bucket: str = "roracle"
    s3_access_key: str
    s3_secret_key: str
    oidc_issuer: str
    oidc_audience: str = "roracle"
    oidc_jwks_url: str
    max_upload_bytes: int = 524288000


@lru_cache
def get_settings() -> Settings:
    return Settings()
