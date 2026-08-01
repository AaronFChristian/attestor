"""
Keycloak OIDC token verification + RBAC dependency.

Design point worth flagging: JWKS keys are fetched once and cached for the
process lifetime rather than refetched per request. That's a real tradeoff —
if Keycloak rotates its signing key, this process needs a restart to pick
it up. Acceptable for a 3-day portfolio build; the production fix is a
short TTL cache with background refresh, noted in the Day 1 gate answers.

Segregation of duties (a validator cannot validate their own model; a model
owner cannot close a finding) is enforced in the SERVICE layer against
object ownership, not just here — this dependency only proves "who is this
person and what role do they hold," not "are they allowed to touch this
specific resource."
"""
from functools import lru_cache

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import get_settings

settings = get_settings()
bearer_scheme = HTTPBearer(auto_error=True)


class AuthenticatedUser(BaseModel):
    subject: str  # Keycloak "sub" claim
    email: str
    role: str  # single realm role: model_owner | validator | mrm_head | auditor
    display_name: str = ""


@lru_cache
def _get_jwks() -> dict:
    """Fetched once per process. See module docstring for the rotation
    tradeoff this implies."""
    resp = httpx.get(
        f"{settings.keycloak_url}/realms/{settings.keycloak_realm}/protocol/openid-connect/certs",
        timeout=5.0,
    )
    resp.raise_for_status()
    return resp.json()


def _extract_role(claims: dict) -> str:
    realm_roles = claims.get("realm_access", {}).get("roles", [])
    governed_roles = {"model_owner", "validator", "mrm_head", "auditor"}
    matched = governed_roles.intersection(realm_roles)
    if not matched:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token has no recognized Attestor role assigned.",
        )
    if len(matched) > 1:
        # A user should hold exactly one governance role. More than one is
        # a segregation-of-duties violation at the identity-provider level,
        # not something the API should silently pick one of.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User holds multiple governance roles ({matched}); this violates segregation of duties.",
        )
    return matched.pop()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> AuthenticatedUser:
    token = credentials.credentials
    try:
        jwks = _get_jwks()
        # Not validating against the standard "aud" claim here: Keycloak's
        # default access tokens carry aud="account" regardless of which
        # client requested them, unless you add a dedicated audience
        # protocol mapper to the client (an extra piece of realm config).
        # "azp" (authorized party) is populated correctly out of the box
        # and identifies exactly which client the token was issued to — we
        # verify that instead, then check it explicitly below.
        claims = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if claims.get("azp") not in settings.keycloak_allowed_azp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"Token was not issued for a recognized Attestor client "
                f"(azp={claims.get('azp')!r} not in {settings.keycloak_allowed_azp})."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    role = _extract_role(claims)
    return AuthenticatedUser(
        subject=claims["sub"],
        email=claims.get("email", ""),
        role=role,
        display_name=claims.get("name", claims.get("preferred_username", "")),
    )


def require_role(*allowed_roles: str):
    """Dependency factory: require_role('mrm_head', 'validator')."""

    async def _check(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' is not permitted to perform this action. "
                f"Requires one of: {allowed_roles}.",
            )
        return user

    return _check
