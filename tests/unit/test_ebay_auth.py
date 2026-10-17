# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Unit tests for the eBay OAuth client. Strictly offline: HTTP, prompts, and state are all faked."""

import json
import logging
import os
import stat
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from kleinanzeigen_bot import ebay_auth
from kleinanzeigen_bot.ebay_auth import DEFAULT_SCOPE, EbayAuthError, OAuthClient, default_store_dir

pytestmark = pytest.mark.unit

_FIXED_STATE = "fixed-test-state-0123456789abcdef"
_AUTH_CODE = "test-auth-code-12345"
_CLIENT_ID = "test-client-id"
_REDIRECT_URI = "test-redirect-uri"
_ACCOUNT = "test-account"

_SANDBOX_TOKEN_URL = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"  # noqa: S105
_PRODUCTION_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"  # noqa: S105

_SENTINEL_SECRET = "SENTINEL-secret-value"  # noqa: S105
_SENTINEL_CODE = "SENTINEL-code-value"
_SENTINEL_ACCESS = "SENTINEL-access-value"
_SENTINEL_REFRESH = "SENTINEL-refresh-value"
_ALL_SENTINELS = (_SENTINEL_SECRET, _SENTINEL_CODE, _SENTINEL_ACCESS, _SENTINEL_REFRESH)


def _make_client(
    tmp_path:Path,
    environment:str = "sandbox",
    account:str = _ACCOUNT,
    client_id:str = _CLIENT_ID,
    redirect_uri:str = _REDIRECT_URI,
) -> OAuthClient:
    return OAuthClient(environment, account, client_id, redirect_uri, store_dir = tmp_path / "store")


def _callback_url(state:str = _FIXED_STATE, code:str = _AUTH_CODE) -> str:
    return f"https://localhost/callback?code={code}&state={state}"


class _FakeResponse:
    """Minimal requests.Response double exposing only what the client uses."""

    def __init__(self, status_code:int, payload:Any = None, *, json_error:BaseException | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def _patch_post(
    monkeypatch:pytest.MonkeyPatch,
    response:_FakeResponse | None = None,
    *,
    error:BaseException | None = None,
    captured:dict[str, Any] | None = None,
) -> None:
    def fake_post(url:str, **kwargs:Any) -> _FakeResponse:
        if captured is not None:
            captured["url"] = url
            captured.update(kwargs)
        if error is not None:
            raise error
        if response is None:
            raise AssertionError("no fake response configured")
        return response

    monkeypatch.setattr("requests.post", fake_post)


def _patch_getpass(monkeypatch:pytest.MonkeyPatch, answers:list[str], captured_prompts:list[str] | None = None) -> None:
    pending = list(answers)

    def fake_getpass(prompt:str = "") -> str:
        if captured_prompts is not None:
            captured_prompts.append(prompt)
        return pending.pop(0)

    monkeypatch.setattr("getpass.getpass", fake_getpass)


def _fail_input(prompt:str = "") -> str:
    raise AssertionError(f"input() must not be used for secrets: {prompt}")


def _fail_getpass(prompt:str = "") -> str:
    raise AssertionError(f"unexpected secret prompt: {prompt}")


def _raise_eof(prompt:str = "") -> str:
    raise EOFError("no tty")


def _raise_keyboard_interrupt(prompt:str = "") -> str:
    raise KeyboardInterrupt("aborted-by-test")


def _seed_token_file(
    client:OAuthClient,
    *,
    access_token:str = "stored-access-token",  # noqa: S107
    refresh_token:str = "stored-refresh-token",  # noqa: S107
    expires_at:float | None = None,
    account:str | None = None,
    environment:str | None = None,
    client_id:str | None = None,
) -> None:
    record:dict[str, Any] = {
        "access_token": access_token,
        "account": account if account is not None else client.account,
        "client_id": client_id if client_id is not None else client.client_id,
        "environment": environment if environment is not None else client.environment,
        "expires_at": expires_at if expires_at is not None else time.time() + 3600,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
    }
    client.token_file.parent.mkdir(parents = True, exist_ok = True)
    client.token_file.write_text(json.dumps(record), encoding = "utf-8")


def _stored_record(client:OAuthClient) -> dict[str, Any]:
    data:dict[str, Any] = json.loads(client.token_file.read_text(encoding = "utf-8"))
    return data


def _printed_authorize_url(capsys:pytest.CaptureFixture[str]) -> str:
    lines = capsys.readouterr().out.splitlines()
    urls = [line for line in lines if line.startswith("https://")]
    assert len(urls) == 1
    return urls[0]


def _formatted_traceback(exc:BaseException) -> str:
    return "".join(traceback.format_exception(exc))


def _assert_no_sentinels(text:str) -> None:
    for sentinel in _ALL_SENTINELS:
        assert sentinel not in text


@pytest.fixture(autouse = True)
def _clean_secret_env(monkeypatch:pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EBAY_SANDBOX_CLIENT_SECRET", raising = False)
    monkeypatch.delenv("EBAY_PRODUCTION_CLIENT_SECRET", raising = False)


@pytest.fixture(autouse = True)
def _identity_translation(monkeypatch:pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ebay_auth, "_", lambda message: message)


@pytest.fixture(autouse = True)
def _no_network(monkeypatch:pytest.MonkeyPatch) -> None:
    def fail_post(url:str, **kwargs:Any) -> Any:
        raise AssertionError(f"network disabled in unit tests: {url}")

    monkeypatch.setattr("requests.post", fail_post)


@pytest.fixture(autouse = True)
def _fixed_state(monkeypatch:pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("secrets.token_urlsafe", lambda _nbytes: _FIXED_STATE)


class TestConstructor:
    @pytest.mark.parametrize("environment", ["staging", "", "sandbox2"])
    def test_rejects_unknown_environment(self, tmp_path:Path, environment:str) -> None:
        with pytest.raises(ValueError, match = "Invalid eBay environment"):
            _make_client(tmp_path, environment = environment)

    @pytest.mark.parametrize(("given", "expected"), [("Sandbox", "sandbox"), (" PRODUCTION ", "production")])
    def test_normalizes_environment(self, tmp_path:Path, given:str, expected:str) -> None:
        client = _make_client(tmp_path, environment = given)

        assert client.environment == expected
        assert client.token_file.parent.name == expected

    @pytest.mark.parametrize(
        ("field", "match"),
        [("account", "Invalid eBay account label"), ("client_id", "Invalid eBay client ID"), ("redirect_uri", "Invalid eBay redirect URI")],
    )
    def test_rejects_blank_fields(self, tmp_path:Path, field:str, match:str) -> None:
        kwargs:dict[str, Any] = {"environment": "sandbox", "account": _ACCOUNT, "client_id": _CLIENT_ID, "redirect_uri": _REDIRECT_URI}
        kwargs[field] = "  "

        with pytest.raises(ValueError, match = match):
            OAuthClient(store_dir = tmp_path / "store", **kwargs)

    def test_default_store_dir_uses_platformdirs(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("platformdirs.user_config_path", lambda _app: tmp_path / "config")

        client = OAuthClient("sandbox", _ACCOUNT, _CLIENT_ID, _REDIRECT_URI)

        assert client.store_dir == tmp_path / "config" / "ebay"
        assert client.token_file == tmp_path / "config" / "ebay" / "sandbox" / f"{_ACCOUNT}.json"

    def test_default_store_dir_function(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("platformdirs.user_config_path", lambda _app: tmp_path / "config")

        assert default_store_dir() == tmp_path / "config" / "ebay"

    def test_default_store_dir_is_absolute_and_outside_repo(self) -> None:
        store = default_store_dir()

        assert store.is_absolute()
        assert Path.cwd().resolve() not in store.resolve().parents

    def test_token_file_layout(self, tmp_path:Path) -> None:
        client = _make_client(tmp_path)

        assert client.token_file == tmp_path / "store" / "sandbox" / f"{_ACCOUNT}.json"


class TestLogin:
    def test_success_sandbox(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch, capsys:pytest.CaptureFixture[str]) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        client = _make_client(tmp_path)
        _patch_getpass(monkeypatch, [_callback_url()])
        captured:dict[str, Any] = {}
        _patch_post(
            monkeypatch,
            _FakeResponse(200, {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 7200}),
            captured = captured,
        )
        before = time.time()

        client.login()

        assert captured["url"] == _SANDBOX_TOKEN_URL
        assert captured["auth"] == (_CLIENT_ID, "sandbox-secret")
        assert captured["data"] == {"grant_type": "authorization_code", "code": _AUTH_CODE, "redirect_uri": _REDIRECT_URI}
        assert captured["timeout"] == 30
        assert captured["allow_redirects"] is False
        stored = _stored_record(client)
        assert stored["access_token"] == "new-access"  # noqa: S105
        assert stored["refresh_token"] == "new-refresh"  # noqa: S105
        assert stored["account"] == _ACCOUNT
        assert stored["environment"] == "sandbox"
        assert stored["client_id"] == _CLIENT_ID
        assert before + 7000 < stored["expires_at"] <= time.time() + 7200
        auth_url = _printed_authorize_url(capsys)
        assert auth_url.startswith("https://auth.sandbox.ebay.com/oauth2/authorize?")
        params = parse_qs(urlparse(auth_url).query)
        assert params["client_id"] == [_CLIENT_ID]
        assert params["redirect_uri"] == [_REDIRECT_URI]
        assert params["response_type"] == ["code"]
        assert params["state"] == [_FIXED_STATE]
        assert params["scope"] == [DEFAULT_SCOPE]

    def test_success_production_endpoints(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch, capsys:pytest.CaptureFixture[str]) -> None:
        monkeypatch.setenv("EBAY_PRODUCTION_CLIENT_SECRET", "production-secret")
        client = _make_client(tmp_path, environment = "production")
        _patch_getpass(monkeypatch, [_callback_url()])
        captured:dict[str, Any] = {}
        _patch_post(
            monkeypatch,
            _FakeResponse(200, {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 7200}),
            captured = captured,
        )

        client.login()

        assert captured["url"] == _PRODUCTION_TOKEN_URL
        assert _printed_authorize_url(capsys).startswith("https://auth.ebay.com/oauth2/authorize?")

    def test_requests_base_and_inventory_scopes(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch, capsys:pytest.CaptureFixture[str]) -> None:
        assert DEFAULT_SCOPE.split(" ") == ["https://api.ebay.com/oauth/api_scope", "https://api.ebay.com/oauth/api_scope/sell.inventory"]
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        client = _make_client(tmp_path)
        _patch_getpass(monkeypatch, [_callback_url()])
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "a", "refresh_token": "r", "expires_in": 7200}))

        client.login()

        params = parse_qs(urlparse(_printed_authorize_url(capsys)).query)
        assert params["scope"] == [DEFAULT_SCOPE]

    def test_uses_env_secret_without_prompting(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        client = _make_client(tmp_path)
        prompts:list[str] = []
        _patch_getpass(monkeypatch, [_callback_url()], prompts)
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "a", "refresh_token": "r", "expires_in": 7200}))

        client.login()

        assert prompts == ["eBay callback URL: "]

    def test_prompts_secret_via_getpass_when_env_missing(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        client = _make_client(tmp_path)
        prompts:list[str] = []
        _patch_getpass(monkeypatch, ["prompted-secret", _callback_url()], prompts)
        monkeypatch.setattr("builtins.input", _fail_input)
        captured:dict[str, Any] = {}
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "a", "refresh_token": "r", "expires_in": 7200}), captured = captured)

        client.login()

        assert prompts == ["eBay client secret: ", "eBay callback URL: "]
        assert captured["auth"] == (_CLIENT_ID, "prompted-secret")

    def test_blank_prompted_secret_aborts(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        client = _make_client(tmp_path)
        _patch_getpass(monkeypatch, ["   "])

        with pytest.raises(EbayAuthError, match = "no client secret provided"):
            client.login()

    def test_secret_prompt_eof_aborts(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        client = _make_client(tmp_path)
        monkeypatch.setattr("getpass.getpass", _raise_eof)

        with pytest.raises(EbayAuthError, match = "no client secret provided"):
            client.login()

    def test_callback_prompt_eof_aborts(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        client = _make_client(tmp_path)
        monkeypatch.setattr("getpass.getpass", _raise_eof)

        with pytest.raises(EbayAuthError, match = "no callback URL provided"):
            client.login()

    @pytest.mark.parametrize(
        "callback",
        [
            f"https://localhost/callback?code={_AUTH_CODE}&state=wrong-state",
            f"https://localhost/callback?state={_FIXED_STATE}",
            f"https://localhost/callback?code={_AUTH_CODE}",
            f"https://localhost/callback?error=access_denied&state={_FIXED_STATE}",
            "not-a-url",
            "",
        ],
    )
    def test_invalid_callback_aborts(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch, callback:str) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", _SENTINEL_SECRET)
        client = _make_client(tmp_path)
        _patch_getpass(monkeypatch, [callback])

        with pytest.raises(EbayAuthError, match = "invalid callback") as exc_info:
            client.login()

        _assert_no_sentinels(str(exc_info.value))
        assert not client.token_file.exists()

    def test_accepts_callback_with_fragment(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        client = _make_client(tmp_path)
        _patch_getpass(monkeypatch, [f"https://localhost/callback#code={_AUTH_CODE}&state={_FIXED_STATE}"])
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 7200}))

        client.login()

        assert _stored_record(client)["access_token"] == "new-access"  # noqa: S105

    @pytest.mark.parametrize("status", [302, 307, 400, 401, 500])
    def test_http_error_aborts(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch, status:int) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", _SENTINEL_SECRET)
        client = _make_client(tmp_path)
        _patch_getpass(monkeypatch, [_callback_url(code = _SENTINEL_CODE)])
        _patch_post(monkeypatch, _FakeResponse(status, {"error": "invalid_grant"}))

        with pytest.raises(EbayAuthError, match = "token request failed") as exc_info:
            client.login()

        _assert_no_sentinels(str(exc_info.value))
        assert not client.token_file.exists()

    @pytest.mark.parametrize("error_kind", ["connection", "timeout", "builtin-timeout"])
    def test_network_error_aborts_without_leak(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch, error_kind:str) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", _SENTINEL_SECRET)
        client = _make_client(tmp_path)
        _patch_getpass(monkeypatch, [_callback_url(code = _SENTINEL_CODE)])
        error:BaseException
        if error_kind == "connection":
            error = requests.exceptions.ConnectionError(f"conn reset {_SENTINEL_SECRET} {_SENTINEL_CODE}")
        elif error_kind == "timeout":
            error = requests.exceptions.Timeout(f"timed out {_SENTINEL_SECRET}")
        else:
            error = TimeoutError(f"timed out {_SENTINEL_SECRET}")
        _patch_post(monkeypatch, error = error)

        with pytest.raises(EbayAuthError, match = "token request failed") as exc_info:
            client.login()

        assert exc_info.value.__cause__ is None
        assert exc_info.value.__suppress_context__ is True
        _assert_no_sentinels(_formatted_traceback(exc_info.value))

    @pytest.mark.parametrize(
        "payload",
        [
            {"refresh_token": "r", "expires_in": 7200},
            {"access_token": "", "refresh_token": "r", "expires_in": 7200},
            {"access_token": "a", "expires_in": 7200},
            {"access_token": "a", "refresh_token": 123, "expires_in": 7200},
            {"access_token": "a", "refresh_token": "r"},
            {"access_token": "a", "refresh_token": "r", "expires_in": 0},
            {"access_token": "a", "refresh_token": "r", "expires_in": -5},
            {"access_token": "a", "refresh_token": "r", "expires_in": "soon"},
            ["not-a-dict"],
            "not-a-dict",
        ],
    )
    def test_invalid_token_payload_aborts(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch, payload:Any) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        client = _make_client(tmp_path)
        _patch_getpass(monkeypatch, [_callback_url()])
        _patch_post(monkeypatch, _FakeResponse(200, payload))

        with pytest.raises(EbayAuthError, match = "token request failed"):
            client.login()

    def test_json_error_aborts_without_leak(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", _SENTINEL_SECRET)
        client = _make_client(tmp_path)
        _patch_getpass(monkeypatch, [_callback_url(code = _SENTINEL_CODE)])
        _patch_post(monkeypatch, _FakeResponse(200, json_error = ValueError(f"bad json {_SENTINEL_ACCESS}")))

        with pytest.raises(EbayAuthError, match = "token request failed") as exc_info:
            client.login()

        _assert_no_sentinels(_formatted_traceback(exc_info.value))

    def test_keyboard_interrupt_propagates(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        client = _make_client(tmp_path)
        monkeypatch.setattr("getpass.getpass", _raise_keyboard_interrupt)

        with pytest.raises(KeyboardInterrupt, match = "aborted-by-test"):
            client.login()

    def test_login_and_refresh_never_use_input(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        monkeypatch.setattr("builtins.input", _fail_input)
        client = _make_client(tmp_path)
        _patch_getpass(monkeypatch, [_callback_url()])
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "a1", "refresh_token": "r1", "expires_in": 7200}))

        client.login()

        _seed_token_file(client, access_token = "a1", refresh_token = "r1", expires_at = time.time() - 10)  # noqa: S106
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "a2", "expires_in": 7200}))

        assert client.access_token() == "a2"


class TestAccessToken:
    def test_returns_fresh_token_without_network(self, tmp_path:Path) -> None:
        client = _make_client(tmp_path)
        _seed_token_file(client, access_token = "fresh-access")  # noqa: S106

        assert client.access_token() == "fresh-access"

    def test_skips_refresh_outside_skew(self, tmp_path:Path) -> None:
        client = _make_client(tmp_path)
        _seed_token_file(client, expires_at = time.time() + 3600)

        assert client.access_token() == "stored-access-token"

    def test_refreshes_within_skew_and_preserves_refresh_token(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        client = _make_client(tmp_path)
        _seed_token_file(client, access_token = "old-access", refresh_token = "old-refresh", expires_at = time.time() + 60)  # noqa: S106
        captured:dict[str, Any] = {}
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "new-access", "expires_in": 7200}), captured = captured)

        assert client.access_token() == "new-access"

        assert captured["url"] == _SANDBOX_TOKEN_URL
        assert captured["data"] == {"grant_type": "refresh_token", "refresh_token": "old-refresh"}
        assert "scope" not in captured["data"]
        stored = _stored_record(client)
        assert stored["access_token"] == "new-access"  # noqa: S105
        assert stored["refresh_token"] == "old-refresh"  # noqa: S105

    def test_refresh_stores_rotated_refresh_token(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        client = _make_client(tmp_path)
        _seed_token_file(client, access_token = "old-access", refresh_token = "old-refresh", expires_at = time.time() - 10)  # noqa: S106
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "new-access", "refresh_token": "rotated-refresh", "expires_in": 7200}))

        assert client.access_token() == "new-access"

        assert _stored_record(client)["refresh_token"] == "rotated-refresh"  # noqa: S105

    def test_refresh_uses_production_endpoint(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_PRODUCTION_CLIENT_SECRET", "production-secret")
        client = _make_client(tmp_path, environment = "production")
        _seed_token_file(client, expires_at = time.time() - 10)
        captured:dict[str, Any] = {}
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "new-access", "expires_in": 7200}), captured = captured)

        assert client.access_token() == "new-access"

        assert captured["url"] == _PRODUCTION_TOKEN_URL

    def test_refresh_http_failure_aborts(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        client = _make_client(tmp_path)
        _seed_token_file(client, expires_at = time.time() - 10)
        _patch_post(monkeypatch, _FakeResponse(400, {"error": "invalid_grant"}))

        with pytest.raises(EbayAuthError, match = "token request failed"):
            client.access_token()

    def test_refresh_network_error_aborts_without_leak(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", _SENTINEL_SECRET)
        client = _make_client(tmp_path)
        _seed_token_file(client, access_token = _SENTINEL_ACCESS, refresh_token = _SENTINEL_REFRESH, expires_at = time.time() - 10)
        _patch_post(monkeypatch, error = requests.exceptions.ConnectionError(f"down {_SENTINEL_SECRET} {_SENTINEL_REFRESH}"))

        with pytest.raises(EbayAuthError, match = "token request failed") as exc_info:
            client.access_token()

        _assert_no_sentinels(_formatted_traceback(exc_info.value))

    def test_missing_file_requires_login(self, tmp_path:Path) -> None:
        client = _make_client(tmp_path)

        with pytest.raises(EbayAuthError, match = "No eBay authorization found"):
            client.access_token()

    @pytest.mark.parametrize("raw", ["not-json{{{", "[]", "5", '"just-a-string"'])
    def test_unparseable_file_requires_login(self, tmp_path:Path, raw:str) -> None:
        client = _make_client(tmp_path)
        client.token_file.parent.mkdir(parents = True, exist_ok = True)
        client.token_file.write_text(raw, encoding = "utf-8")

        with pytest.raises(EbayAuthError, match = "Stored eBay authorization is invalid"):
            client.access_token()

    @pytest.mark.parametrize(
        "mutation",
        [
            {"access_token": ""},
            {"access_token": None},
            {"refresh_token": ""},
            {"refresh_token": None},
            {"expires_at": "soon"},
            {"expires_at": True},
            {"expires_at": None},
            {"expires_at": float("nan")},
            {"expires_at": float("inf")},
            {"expires_at": float("-inf")},
            {"account": "other-account"},
            {"environment": "production"},
            {"client_id": "other-client-id"},
        ],
    )
    def test_invalid_stored_record_requires_login(self, tmp_path:Path, mutation:dict[str, Any]) -> None:
        client = _make_client(tmp_path)
        record:dict[str, Any] = {
            "access_token": "stored-access",
            "account": _ACCOUNT,
            "client_id": _CLIENT_ID,
            "environment": "sandbox",
            "expires_at": time.time() + 3600,
            "refresh_token": "stored-refresh",
            "token_type": "Bearer",
        }
        record.update(mutation)
        client.token_file.parent.mkdir(parents = True, exist_ok = True)
        client.token_file.write_text(json.dumps(record), encoding = "utf-8")

        with pytest.raises(EbayAuthError, match = "Stored eBay authorization is invalid"):
            client.access_token()

    def test_record_missing_client_id_requires_login(self, tmp_path:Path) -> None:
        client = _make_client(tmp_path)
        _seed_token_file(client)
        record = _stored_record(client)
        del record["client_id"]
        client.token_file.write_text(json.dumps(record), encoding = "utf-8")

        with pytest.raises(EbayAuthError, match = "Stored eBay authorization is invalid"):
            client.access_token()

    def test_changed_client_id_requires_relogin(self, tmp_path:Path) -> None:
        _seed_token_file(_make_client(tmp_path))
        other_app = _make_client(tmp_path, client_id = "other-client-id")

        with pytest.raises(EbayAuthError, match = "Stored eBay authorization is invalid"):
            other_app.access_token()

    def test_corrupt_file_traceback_contains_no_tokens(self, tmp_path:Path) -> None:
        client = _make_client(tmp_path)
        client.token_file.parent.mkdir(parents = True, exist_ok = True)
        client.token_file.write_text(f'{{"access_token": "{_SENTINEL_ACCESS}", broken', encoding = "utf-8")

        with pytest.raises(EbayAuthError, match = "Stored eBay authorization is invalid") as exc_info:
            client.access_token()

        _assert_no_sentinels(_formatted_traceback(exc_info.value))

    def test_expired_token_uses_cached_secret_from_login(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        client = _make_client(tmp_path)
        _patch_getpass(monkeypatch, ["prompted-secret", _callback_url()])
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "a1", "refresh_token": "r1", "expires_in": 7200}))
        client.login()

        _seed_token_file(client, access_token = "a1", refresh_token = "r1", expires_at = time.time() - 10)  # noqa: S106
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "a2", "expires_in": 7200}))
        monkeypatch.setattr("getpass.getpass", _fail_getpass)

        assert client.access_token() == "a2"

    def test_refresh_prompts_secret_when_env_missing(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        client = _make_client(tmp_path)
        _seed_token_file(client, expires_at = time.time() - 10)
        prompts:list[str] = []
        _patch_getpass(monkeypatch, ["prompted-secret"], prompts)
        captured:dict[str, Any] = {}
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "a2", "expires_in": 7200}), captured = captured)

        assert client.access_token() == "a2"

        assert prompts == ["eBay client secret: "]
        assert captured["auth"] == (_CLIENT_ID, "prompted-secret")


@pytest.mark.skipif(os.name != "posix", reason = "POSIX permission bits")
class TestTokenFilePermissions:
    def test_private_permissions_after_login(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        client = _make_client(tmp_path)
        _patch_getpass(monkeypatch, [_callback_url()])
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "a", "refresh_token": "r", "expires_in": 7200}))

        client.login()

        assert stat.S_IMODE(os.stat(client.token_file).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(client.token_file.parent).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(client.store_dir).st_mode) == 0o700

    def test_save_restores_private_permissions(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        client = _make_client(tmp_path)
        client.token_file.parent.mkdir(parents = True, exist_ok = True)
        client.token_file.write_text("stale", encoding = "utf-8")
        os.chmod(client.token_file, 0o700)
        os.chmod(client.token_file.parent, 0o500)
        os.chmod(client.store_dir, 0o500)
        _patch_getpass(monkeypatch, [_callback_url()])
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "a", "refresh_token": "r", "expires_in": 7200}))

        client.login()

        assert stat.S_IMODE(os.stat(client.token_file).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(client.token_file.parent).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(client.store_dir).st_mode) == 0o700


class TestNoLeaks:
    def test_no_temp_files_left(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        client = _make_client(tmp_path)
        _patch_getpass(monkeypatch, [_callback_url()])
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "a", "refresh_token": "r", "expires_in": 7200}))

        client.login()

        assert sorted(entry.name for entry in client.token_file.parent.iterdir()) == [client.token_file.name]

    def test_repr_and_logs_contain_no_secrets(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch, caplog:pytest.LogCaptureFixture) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", _SENTINEL_SECRET)
        client = _make_client(tmp_path)
        _patch_getpass(monkeypatch, [_callback_url(code = _SENTINEL_CODE)])
        _patch_post(
            monkeypatch,
            _FakeResponse(200, {"access_token": _SENTINEL_ACCESS, "refresh_token": _SENTINEL_REFRESH, "expires_in": 7200}),
        )

        with caplog.at_level(logging.DEBUG, logger = "kleinanzeigen_bot.ebay_auth"):
            client.login()
            assert client.access_token() == _SENTINEL_ACCESS

        _assert_no_sentinels(repr(client))
        _assert_no_sentinels(caplog.text)


class TestIsolation:
    def test_environments_are_isolated(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        monkeypatch.setenv("EBAY_PRODUCTION_CLIENT_SECRET", "production-secret")
        sandbox = _make_client(tmp_path, environment = "sandbox")
        production = _make_client(tmp_path, environment = "production")
        assert sandbox.token_file != production.token_file
        assert sandbox.token_file.parent.name == "sandbox"
        assert production.token_file.parent.name == "production"
        _patch_getpass(monkeypatch, [_callback_url(), _callback_url()])
        responses = iter([
            _FakeResponse(200, {"access_token": "sandbox-access", "refresh_token": "sandbox-refresh", "expires_in": 7200}),
            _FakeResponse(200, {"access_token": "production-access", "refresh_token": "production-refresh", "expires_in": 7200}),
        ])
        monkeypatch.setattr("requests.post", lambda url, **kwargs: next(responses))

        sandbox.login()
        production.login()

        assert sandbox.access_token() == "sandbox-access"
        assert production.access_token() == "production-access"

    def test_accounts_are_isolated(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        first = _make_client(tmp_path, account = "first-account")
        second = _make_client(tmp_path, account = "second-account")
        assert first.token_file != second.token_file
        _patch_getpass(monkeypatch, [_callback_url(), _callback_url()])
        responses = iter([
            _FakeResponse(200, {"access_token": "first-access", "refresh_token": "first-refresh", "expires_in": 7200}),
            _FakeResponse(200, {"access_token": "second-access", "refresh_token": "second-refresh", "expires_in": 7200}),
        ])
        monkeypatch.setattr("requests.post", lambda url, **kwargs: next(responses))

        first.login()
        second.login()

        assert first.access_token() == "first-access"
        assert second.access_token() == "second-access"

    @pytest.mark.parametrize("account", ["../../evil", "a/b", "..", "...", "/absolute", "trailing.", ".leading", "x" * 100])
    def test_traversal_labels_stay_inside_store(self, tmp_path:Path, account:str) -> None:
        client = _make_client(tmp_path, account = account)

        assert client.token_file.resolve().is_relative_to((tmp_path / "store").resolve())
        assert client.token_file.suffix == ".json"

    def test_traversal_label_login_usable(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
        client = _make_client(tmp_path, account = "../../evil")
        _patch_getpass(monkeypatch, [_callback_url()])
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 7200}))

        client.login()

        assert client.access_token() == "new-access"

    def test_similar_labels_do_not_collide(self, tmp_path:Path) -> None:
        first = _make_client(tmp_path, account = "a/b")
        second = _make_client(tmp_path, account = "a_b")

        assert first.token_file != second.token_file

    def test_env_vars_are_per_environment(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EBAY_PRODUCTION_CLIENT_SECRET", "production-secret")
        sandbox = _make_client(tmp_path, environment = "sandbox")
        prompts:list[str] = []
        _patch_getpass(monkeypatch, ["prompted-secret", _callback_url()], prompts)
        captured:dict[str, Any] = {}
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "a", "refresh_token": "r", "expires_in": 7200}), captured = captured)

        sandbox.login()

        assert prompts[0] == "eBay client secret: "
        assert captured["auth"] == (_CLIENT_ID, "prompted-secret")

        production = _make_client(tmp_path, environment = "production")
        production_prompts:list[str] = []
        _patch_getpass(monkeypatch, [_callback_url()], production_prompts)
        _patch_post(monkeypatch, _FakeResponse(200, {"access_token": "a", "refresh_token": "r", "expires_in": 7200}))

        production.login()

        assert production_prompts == ["eBay callback URL: "]
