"""Google sign-in (OpenID Connect, authorization-code flow with PKCE).

Configuration (data-dir ``.env``):

- ``TWICC_GOOGLE_CLIENT_ID`` / ``TWICC_GOOGLE_CLIENT_SECRET`` — an OAuth client
  of type "Web application" in Google Cloud Console. Both set = Google sign-in
  offered on the login screen.
- ``TWICC_GOOGLE_REDIRECT_URI`` (optional) — the exact callback URL registered
  in the console, e.g. ``https://twicc.example.com/api/auth/google/callback/``.
  Defaults to the callback URL derived from the incoming request, which is only
  right when no proxy rewrites the scheme/host.
- ``TWICC_GOOGLE_ALLOWED_DOMAINS`` (optional, comma-separated) — Google
  Workspace domains whose verified addresses may sign in *without* an existing
  account; such a first sign-in creates a member with no access (an admin
  then grants projects). Without it, only addresses an admin already created
  an account for can sign in — Google proves the address, TwiCC decides the
  authority.

Security notes:

- ``state`` (CSRF), ``nonce`` (replay) and the PKCE verifier are kept in the
  Django session between the redirect and the callback, single-use.
- The ID token comes straight from Google's token endpoint over TLS, in
  exchange for the code + client secret + PKCE verifier. Per OpenID Connect
  Core §3.1.3.7 the TLS server validation then stands in for the signature
  check; the claims (``iss``, ``aud``, ``exp``, ``nonce``, ``email_verified``)
  are still all validated.
- An account is matched first by its latched Google ``sub``, then by verified
  email (first sign-in latches the ``sub``). A ``sub`` mismatch on an email
  already latched to another Google identity is refused.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from typing import NamedTuple
from urllib.parse import urlencode

import httpx
import orjson

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
VALID_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
CALLBACK_PATH = "/api/auth/google/callback/"

SESSION_FLOW_KEY = "twicc_google_flow"
# Refusal code handed to the login screen after the callback redirect.
SESSION_ERROR_KEY = "twicc_google_error"
# A pending sign-in older than this is refused (the user went away mid-flow).
FLOW_MAX_AGE_SECONDS = 600
_CLOCK_SKEW_SECONDS = 120


class GoogleConfig(NamedTuple):
    client_id: str
    client_secret: str
    redirect_uri: str
    allowed_domains: frozenset[str]


def get_config() -> GoogleConfig | None:
    """The Google configuration, or None when sign-in with Google is disabled."""
    client_id = os.environ.get("TWICC_GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("TWICC_GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    domains = frozenset(
        d.strip().lower().lstrip("@") for d in os.environ.get("TWICC_GOOGLE_ALLOWED_DOMAINS", "").split(",")
        if d.strip()
    )
    return GoogleConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=os.environ.get("TWICC_GOOGLE_REDIRECT_URI", "").strip(),
        allowed_domains=domains,
    )


def is_enabled() -> bool:
    return get_config() is not None


def redirect_uri_for(config: GoogleConfig, request) -> str:
    return config.redirect_uri or request.build_absolute_uri(CALLBACK_PATH)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def start_flow(config: GoogleConfig, redirect_uri: str, next_path: str) -> tuple[str, dict]:
    """Build the Google authorization URL and the flow state to store in the session."""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    params = {
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    if len(config.allowed_domains) == 1:
        # UX hint only (Google pre-filters the account chooser); never trusted.
        params["hd"] = next(iter(config.allowed_domains))
    flow = {
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "redirect_uri": redirect_uri,
        "next": next_path,
        "started": time.time(),
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}", flow


class GoogleIdentity(NamedTuple):
    sub: str
    email: str
    name: str
    picture: str
    hosted_domain: str


class GoogleError(Exception):
    """A refused sign-in; ``code`` is the stable token shown to the user."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _decode_jwt_claims(id_token: str) -> dict:
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return orjson.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, orjson.JSONDecodeError) as exc:
        raise GoogleError("invalid_token", "Malformed ID token.") from exc


def validate_claims(claims: dict, *, config: GoogleConfig, nonce: str, now: float | None = None) -> GoogleIdentity:
    """Check every claim the trust decision relies on. Pure (unit-tested)."""
    now = time.time() if now is None else now
    if claims.get("iss") not in VALID_ISSUERS:
        raise GoogleError("invalid_token", "Unexpected token issuer.")
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if config.client_id not in audiences:
        raise GoogleError("invalid_token", "Token not issued for this client.")
    try:
        exp = float(claims.get("exp", 0))
    except (TypeError, ValueError):
        exp = 0
    if exp + _CLOCK_SKEW_SECONDS < now:
        raise GoogleError("invalid_token", "Token expired.")
    if not nonce or not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise GoogleError("invalid_token", "Nonce mismatch.")
    if claims.get("email_verified") not in (True, "true"):
        raise GoogleError("email_not_verified", "Google account email is not verified.")
    email = str(claims.get("email", "")).strip().lower()
    sub = str(claims.get("sub", "")).strip()
    if not email or not sub:
        raise GoogleError("invalid_token", "Token lacks email or subject.")
    return GoogleIdentity(
        sub=sub,
        email=email,
        name=str(claims.get("name", "") or ""),
        picture=str(claims.get("picture", "") or ""),
        hosted_domain=str(claims.get("hd", "") or "").lower(),
    )


async def exchange_code(config: GoogleConfig, flow: dict, code: str) -> GoogleIdentity:
    """Swap the authorization code for an ID token and validate it."""
    data = {
        "code": code,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "redirect_uri": flow["redirect_uri"],
        "grant_type": "authorization_code",
        "code_verifier": flow["verifier"],
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(TOKEN_URL, data=data)
    except httpx.HTTPError as exc:
        logger.warning("Google token exchange failed: %s", exc)
        raise GoogleError("google_unreachable", "Could not reach Google.") from exc
    if response.status_code != 200:
        logger.warning("Google token endpoint answered %s", response.status_code)
        raise GoogleError("exchange_failed", "Google refused the sign-in.")
    id_token = response.json().get("id_token")
    if not id_token:
        raise GoogleError("exchange_failed", "Google returned no ID token.")
    return validate_claims(_decode_jwt_claims(id_token), config=config, nonce=flow["nonce"])


def match_or_create_account(identity: GoogleIdentity, config: GoogleConfig):
    """The account this Google identity signs into (sync, ORM). Raises GoogleError."""
    from django.db import IntegrityError

    from twicc.accounts.principal import invalidate_accounts_enabled
    from twicc.core.models import User, UserRole

    user = User.objects.filter(google_sub=identity.sub).first()
    if user is None:
        user = User.objects.filter(email=identity.email).first()
        if user is not None:
            if user.google_sub and user.google_sub != identity.sub:
                raise GoogleError("identity_mismatch", "This account is linked to another Google identity.")
            user.google_sub = identity.sub
    if user is None:
        domain = identity.email.rsplit("@", 1)[-1]
        if domain not in config.allowed_domains or identity.hosted_domain != domain:
            raise GoogleError("no_account", "No TwiCC account exists for this Google address.")
        try:
            user = User.objects.create(
                email=identity.email,
                display_name=identity.name,
                role=UserRole.MEMBER,
                google_sub=identity.sub,
                avatar_url=identity.picture[:500],
            )
        except IntegrityError as exc:
            raise GoogleError("no_account", "Could not create the account.") from exc
        invalidate_accounts_enabled()
        logger.info("Account auto-created from Google sign-in: %s", identity.email)
        return user
    if not user.is_active:
        raise GoogleError("account_disabled", "This account is disabled.")
    if identity.picture and user.avatar_url != identity.picture[:500]:
        user.avatar_url = identity.picture[:500]
    if not user.display_name and identity.name:
        user.display_name = identity.name[:100]
    user.save()
    return user
