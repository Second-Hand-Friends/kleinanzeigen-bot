# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""eBay OAuth Authorization Code Grant client with local token storage.

Implements the user-consent flow from the official eBay docs
(https://developer.ebay.com/api-docs/static/oauth-authorization-code-grant.html
and https://developer.ebay.com/api-docs/static/oauth-tokens.html):
the user opens a consent URL, approves access, and pastes the callback URL
back into the CLI. The client secret is never stored in config files or
passed via argv; it is read from a per-environment variable or prompted
securely via getpass. Tokens are stored per environment and account label
under the user config directory, outside the repository.
"""

import contextlib
import getpass
import hashlib
import hmac
import json
import math
import os
import secrets
import string
import tempfile
import time
from gettext import gettext as _
from http import HTTPStatus
from pathlib import Path
from typing import Any, Final, Literal, overload
from urllib.parse import parse_qs, urlencode, urlparse

import platformdirs
import requests

from kleinanzeigen_bot.utils import loggers

__all__ = [
    "DEFAULT_SCOPE",
    "EbayAuthError",
    "OAuthClient",
    "default_store_dir"
]

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)

# eBay OAuth endpoints (sandbox vs production are fully separate namespaces).
_AUTHORIZE_URLS:Final[dict[str, str]] = {
    "sandbox": "https://auth.sandbox.ebay.com/oauth2/authorize",
    "production": "https://auth.ebay.com/oauth2/authorize",
}
_TOKEN_URLS:Final[dict[str, str]] = {
    "sandbox": "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
    "production": "https://api.ebay.com/identity/v1/oauth2/token",
}

# Per-environment client-secret variables. No secret is ever read from config files.
_SECRET_ENV_VARS:Final[dict[str, str]] = {
    "sandbox": "EBAY_SANDBOX_CLIENT_SECRET",
    "production": "EBAY_PRODUCTION_CLIENT_SECRET",
}

# Requested scopes: base Trading API scope (seller identity checks) plus the Sell Inventory
# scope required for photo uploads via the Commerce Media API (UploadSiteHostedPictures was
# decommissioned 2026-09-30; Trading remains the listing API).
DEFAULT_SCOPE:Final[str] = "https://api.ebay.com/oauth/api_scope https://api.ebay.com/oauth/api_scope/sell.inventory"

# Refresh access tokens this many seconds before actual expiry (clock skew tolerance).
_REFRESH_SKEW_SECONDS:Final[int] = 300
_REQUEST_TIMEOUT_SECONDS:Final[int] = 30

_SAFE_FILENAME_CHARS:Final[frozenset[str]] = frozenset(string.ascii_letters + string.digits + "-_.")
_MAX_FILENAME_BASE_LEN:Final[int] = 48


class EbayAuthError(Exception):
    """Raised for eBay authorization failures. Messages are generic on purpose and never carry tokens, codes, or secrets."""


def default_store_dir() -> Path:
    """Return the default token store directory (user config dir, strictly outside the repository)."""
    return platformdirs.user_config_path("kleinanzeigen-bot") / "ebay"


def _account_filename(account:str) -> str:
    """
    Map an account label to a safe file name inside the environment directory.

    Labels with only safe characters keep their readable name; anything else is
    sanitized and suffixed with a hash so distinct labels can never collide.
    """
    label = account.strip()
    cleaned = "".join(char if char in _SAFE_FILENAME_CHARS else "_" for char in label).strip(".")[:_MAX_FILENAME_BASE_LEN].strip(".")
    if not cleaned:
        cleaned = "account"
    if cleaned == label and len(label) <= _MAX_FILENAME_BASE_LEN:
        return f"{cleaned}.json"
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:16]
    return f"{cleaned}_{digest}.json"


def _extract_code(callback_url:str, expected_state:str) -> str:
    """Parse the authorization code from the pasted callback URL and verify the state parameter."""
    try:
        parsed = urlparse(callback_url.strip())
        query = parse_qs(parsed.query)
        fragment = parse_qs(parsed.fragment)
        code = (query.get("code") or fragment.get("code") or [""])[0]
        returned_state = (query.get("state") or fragment.get("state") or [""])[0]
        denied = "error" in query or "error" in fragment
    except (AttributeError, TypeError, ValueError):
        raise EbayAuthError(_("eBay authorization failed: invalid callback.")) from None
    if denied or not code or not returned_state:
        raise EbayAuthError(_("eBay authorization failed: invalid callback."))
    try:
        valid = hmac.compare_digest(returned_state, expected_state)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise EbayAuthError(_("eBay authorization failed: invalid callback."))
    return code


def _fsync_directory(path:Path) -> None:
    """Best-effort directory fsync for durable atomic writes (POSIX only)."""
    if os.name != "posix":
        return
    try:
        dir_fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        LOG.debug("Failed to fsync directory: %s", path)
    finally:
        os.close(dir_fd)


def _atomic_write_json(path:Path, record:dict[str, Any]) -> None:
    """Write JSON atomically with private permissions and durable fsync."""
    parent = path.parent
    parent.mkdir(parents = True, exist_ok = True)
    fd, tmp_name = tempfile.mkstemp(prefix = ".tmp-", suffix = ".json", dir = str(parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding = "utf-8") as handle:
            json.dump(record, handle, indent = 2, sort_keys = True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
        _fsync_directory(parent)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok = True)
        raise


class OAuthClient:
    """eBay OAuth client. Secrets are prompted via getpass or read from env vars; tokens persist per environment and account."""

    def __init__(self, environment:str, account:str, client_id:str, redirect_uri:str, store_dir:Path | None = None) -> None:
        env = environment.strip().lower()
        if env not in _TOKEN_URLS:
            raise ValueError(_("Invalid eBay environment. Expected 'sandbox' or 'production'."))
        label = account.strip()
        if not label:
            raise ValueError(_("Invalid eBay account label."))
        app_id = client_id.strip()
        if not app_id:
            raise ValueError(_("Invalid eBay client ID."))
        ru_name = redirect_uri.strip()
        if not ru_name:
            raise ValueError(_("Invalid eBay redirect URI."))
        self._environment = env
        self._account = label
        self._client_id = app_id
        self._redirect_uri = ru_name
        self._store_dir = store_dir if store_dir is not None else default_store_dir()
        self._token_file = self._store_dir / self._environment / _account_filename(label)
        self._cached_secret:str | None = None

    @property
    def environment(self) -> str:
        return self._environment

    @property
    def account(self) -> str:
        return self._account

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def redirect_uri(self) -> str:
        return self._redirect_uri

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    @property
    def token_file(self) -> Path:
        return self._token_file

    def __repr__(self) -> str:
        # Never include tokens, codes, or secrets.
        return (
            f"OAuthClient(environment={self._environment!r}, account={self._account!r}, "
            f"client_id={self._client_id!r}, redirect_uri={self._redirect_uri!r}, store_dir={str(self._store_dir)!r})"
        )

    def login(self) -> None:
        """
        Run the interactive Authorization Code Grant flow.

        Prints the consent URL, prompts for the client secret and the pasted
        callback URL via getpass, verifies the state parameter, and stores
        the resulting tokens.
        """
        state = secrets.token_urlsafe(32)
        auth_url = self._build_authorize_url(state)
        print(_("Open this eBay authorization URL in your browser:"))
        print(auth_url)
        print(_("After approving access, paste the full callback URL below."))
        secret = self._client_secret()
        try:
            callback = getpass.getpass(_("eBay callback URL: "))
        except EOFError:
            raise EbayAuthError(_("eBay login aborted: no callback URL provided.")) from None
        code = _extract_code(callback, state)
        payload = {"grant_type": "authorization_code", "code": code, "redirect_uri": self._redirect_uri}
        access_token, refresh_token, expires_in = self._token_request(payload, secret, require_refresh_token = True)
        self._save_tokens(access_token, refresh_token, expires_in)
        LOG.info("eBay authorization completed")

    def access_token(self) -> str:
        """Return a valid access token, refreshing it automatically when expired or near expiry."""
        access_token, refresh_token, expires_at = self._load_tokens()
        if time.time() < expires_at - _REFRESH_SKEW_SECONDS:
            return access_token
        secret = self._client_secret()
        # Omit scope so the refresh keeps the originally granted scopes (compatible with
        # tokens granted before or after scope changes; an explicit scope must be a subset).
        payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}
        new_access, new_refresh, expires_in = self._token_request(payload, secret, require_refresh_token = False)
        # eBay does not rotate refresh tokens on refresh; keep the old one unless a new one is returned.
        self._save_tokens(new_access, new_refresh or refresh_token, expires_in)
        LOG.debug("Refreshed eBay access token")
        return new_access

    def _build_authorize_url(self, state:str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": DEFAULT_SCOPE,
            "state": state,
        }
        return f"{_AUTHORIZE_URLS[self._environment]}?{urlencode(params)}"

    def _client_secret(self) -> str:
        """Return the client secret from the per-environment variable or a secure prompt (never from config/argv)."""
        env_value = os.environ.get(_SECRET_ENV_VARS[self._environment], "").strip()
        if env_value:
            LOG.debug("Using eBay client secret from environment")
            return env_value
        if self._cached_secret:
            return self._cached_secret
        LOG.debug("Prompting for eBay client secret")
        try:
            secret = getpass.getpass(_("eBay client secret: ")).strip()
        except EOFError:
            raise EbayAuthError(_("eBay login aborted: no client secret provided.")) from None
        if not secret:
            raise EbayAuthError(_("eBay login aborted: no client secret provided."))
        self._cached_secret = secret
        return secret

    @overload
    def _token_request(self, payload:dict[str, str], secret:str, *, require_refresh_token:Literal[True]) -> tuple[str, str, int]:
        pass

    @overload
    def _token_request(self, payload:dict[str, str], secret:str, *, require_refresh_token:Literal[False]) -> tuple[str, str | None, int]:
        pass

    def _token_request(self, payload:dict[str, str], secret:str, *, require_refresh_token:bool) -> tuple[str, str | None, int]:
        """POST a token request; all failures map to one generic error with suppressed cause."""
        try:
            response = requests.post(
                _TOKEN_URLS[self._environment],
                auth = (self._client_id, secret),
                data = payload,
                headers = {"Accept": "application/json"},
                timeout = _REQUEST_TIMEOUT_SECONDS,
                allow_redirects = False,
            )
        except (requests.exceptions.RequestException, TimeoutError):
            raise EbayAuthError(_("eBay token request failed.")) from None
        if response.status_code != HTTPStatus.OK:
            raise EbayAuthError(_("eBay token request failed."))
        try:
            data = response.json()
        except ValueError:
            raise EbayAuthError(_("eBay token request failed.")) from None
        if not isinstance(data, dict):
            raise EbayAuthError(_("eBay token request failed."))
        access_token = data.get("access_token")
        if not access_token or not isinstance(access_token, str):
            raise EbayAuthError(_("eBay token request failed."))
        refresh_token = data.get("refresh_token")
        if require_refresh_token and (not refresh_token or not isinstance(refresh_token, str)):
            raise EbayAuthError(_("eBay token request failed."))
        if refresh_token is not None and not isinstance(refresh_token, str):
            raise EbayAuthError(_("eBay token request failed."))
        try:
            expires_in = int(str(data.get("expires_in")))
        except (TypeError, ValueError):
            raise EbayAuthError(_("eBay token request failed.")) from None
        if expires_in <= 0:
            raise EbayAuthError(_("eBay token request failed."))
        return access_token, refresh_token if isinstance(refresh_token, str) else None, expires_in

    def _load_tokens(self) -> tuple[str, str, float]:
        """Load stored tokens. Missing, foreign, or corrupt stores map to generic re-login errors."""
        try:
            raw = self._token_file.read_text(encoding = "utf-8")
        except FileNotFoundError:
            raise EbayAuthError(_("No eBay authorization found for this account. Run login first.")) from None
        except OSError:
            raise EbayAuthError(_("Stored eBay authorization is invalid. Run login again.")) from None
        try:
            data = json.loads(raw)
        except ValueError:
            raise EbayAuthError(_("Stored eBay authorization is invalid. Run login again.")) from None
        if not isinstance(data, dict):
            raise EbayAuthError(_("Stored eBay authorization is invalid. Run login again."))
        # Reject tokens stored for a different namespace or app so they can never be used cross-account/cross-app.
        if data.get("account") != self._account or data.get("environment") != self._environment or data.get("client_id") != self._client_id:
            raise EbayAuthError(_("Stored eBay authorization is invalid. Run login again."))
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        expires_at = data.get("expires_at")
        if not isinstance(access_token, str) or not access_token or not isinstance(refresh_token, str) or not refresh_token:
            raise EbayAuthError(_("Stored eBay authorization is invalid. Run login again."))
        if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)) or not math.isfinite(expires_at):
            raise EbayAuthError(_("Stored eBay authorization is invalid. Run login again."))
        return access_token, refresh_token, float(expires_at)

    def _save_tokens(self, access_token:str, refresh_token:str, expires_in:int) -> None:
        """Persist tokens with private permissions via atomic durable write."""
        self._store_dir.mkdir(parents = True, exist_ok = True)
        self._token_file.parent.mkdir(parents = True, exist_ok = True)
        os.chmod(self._store_dir, 0o700)
        os.chmod(self._token_file.parent, 0o700)
        record:dict[str, Any] = {
            "access_token": access_token,
            "account": self._account,
            "client_id": self._client_id,
            "environment": self._environment,
            "expires_at": time.time() + expires_in,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
        }
        _atomic_write_json(self._token_file, record)
        LOG.debug("Stored eBay authorization")
