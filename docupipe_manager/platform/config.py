from dataclasses import dataclass


@dataclass
class PlatformSettings:
    platform_url: str
    oauth_client_id: str
    oauth_client_secret: str
    oauth_redirect_uri: str
    request_timeout_seconds: int = 10

    @classmethod
    def from_app_settings(cls, settings) -> "PlatformSettings":
        return cls(
            platform_url=settings.platform_url,
            oauth_client_id=settings.oauth_client_id,
            oauth_client_secret=settings.oauth_client_secret,
            oauth_redirect_uri=settings.oauth_redirect_uri,
            request_timeout_seconds=settings.platform_request_timeout_seconds,
        )
