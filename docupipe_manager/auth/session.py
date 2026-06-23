"""JWT decode for xinyi-platform-issued access tokens."""
from jose import JWTError, jwt


SELF_AUDIENCE = "docupipe-prod"


def decode_access_token(token: str, secret: str, audience: str = SELF_AUDIENCE) -> dict:
    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience=audience,
        issuer="xinyi-platform",
    )
