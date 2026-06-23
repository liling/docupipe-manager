from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    manager_schema: str = "docupipe_manager"

    data_dir: str = "/var/lib/docupipe-manager"
    dws_cli_path: str = "dws"
    docupipe_python: str = "python"
    docupipe_working_dir: str = ""
    run_timeout_seconds: int = 0
    max_concurrent_runs: int = 3
    run_log_max_bytes: int = 10 * 1024 * 1024

    jwt_secret: str
    encryption_key: str = ""

    platform_url: str = "http://xinyi-platform:8000"
    oauth_client_id: str = "docupipe-prod"
    oauth_client_secret: str = ""
    oauth_redirect_uri: str = "http://localhost:8002/auth/callback"

    refresh_token_ttl_days: int = 7
    access_token_ttl_seconds: int = 900
    platform_request_timeout_seconds: int = 10
    user_cache_ttl_seconds: int = 300

    host: str = "0.0.0.0"
    port: int = 8002
    base_url: str = "http://localhost:8002"
    dev_mode: bool = False

    model_config = {"env_prefix": "DOCUPIPE_MANAGER_", "env_file": ".env", "extra": "ignore"}

    @model_validator(mode="after")
    def _validate_encryption_key(self) -> "Settings":
        if not self.encryption_key:
            raise ValueError(
                "DOCUPIPE_MANAGER_ENCRYPTION_KEY must be set. "
                'Generate one with: python -c "import secrets; print(secrets.token_hex(16))"'
            )
        if len(bytes.fromhex(self.encryption_key)) != 16:
            raise ValueError("DOCUPIPE_MANAGER_ENCRYPTION_KEY must be 16 bytes (32 hex chars)")
        return self
