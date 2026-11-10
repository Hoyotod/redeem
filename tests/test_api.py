from typing import Any

import httpx
import pytest

import utils
from utils import get_cookies_from_api, is_safe_cookie_value


class FakeResponse:
    def __init__(self, payload: Any = None, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.request = httpx.Request("GET", "https://hoyo-api.test")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "request failed",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self) -> Any:
        return self._payload


class FakeClient:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    async def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        self.calls.append((url, headers or {}))
        if self.error:
            raise self.error
        return self.response


def configure(monkeypatch: pytest.MonkeyPatch, key: str = "secret-key") -> None:
    monkeypatch.setattr(utils.settings, "HOYO_API_URL", "https://hoyo-api.test")
    monkeypatch.setattr(utils.settings, "HOYO_API_KEY", key)


def install_client(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> FakeClient:
    monkeypatch.setattr(utils.httpx, "AsyncClient", lambda **_kw: client)
    return client


class TestGetCookiesFromApi:
    async def test_loads_all_accounts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        configure(monkeypatch)
        install_client(
            monkeypatch,
            FakeClient(
                FakeResponse(
                    {
                        "accounts": [
                            {
                                "name": "Alice",
                                "accountId": "123456789",
                                "cookieToken": "abc123token",
                            },
                            {
                                "name": "Bob_Test",
                                "accountId": "987654321",
                                "cookieToken": "xyz789token",
                            },
                        ]
                    }
                )
            ),
        )

        cookies = await get_cookies_from_api()

        assert [c.env_name for c in cookies] == ["ACC1_ALICE", "ACC2_BOB_TEST"]
        assert cookies[0].cookies == (
            "account_id_v2=123456789; cookie_token_v2=abc123token"
        )

    async def test_requests_internal_endpoint_with_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch)
        client = install_client(monkeypatch, FakeClient(FakeResponse({"accounts": []})))

        await get_cookies_from_api()

        assert client.calls[0][0] == "https://hoyo-api.test/api/internal/accounts"
        assert client.calls[0][1] == {"X-API-Key": "secret-key"}

    async def test_skips_invalid_and_incomplete_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch)
        install_client(
            monkeypatch,
            FakeClient(
                FakeResponse(
                    {
                        "accounts": [
                            {"name": "NoToken", "accountId": "1"},
                            {
                                "name": "Injection",
                                "accountId": "1; DROP TABLE",
                                "cookieToken": "token",
                            },
                            {
                                "name": "Good",
                                "accountId": "123",
                                "cookieToken": "456",
                            },
                        ]
                    }
                )
            ),
        )

        cookies = await get_cookies_from_api()

        assert [c.env_name for c in cookies] == ["ACC3_GOOD"]

    async def test_accepts_base64_and_encoded_tokens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch)
        install_client(
            monkeypatch,
            FakeClient(
                FakeResponse(
                    {
                        "accounts": [
                            {
                                "name": "Fadel",
                                "accountId": "123456789",
                                "cookieToken": "abc+/=def%2Fghi-jkl_mno",
                            }
                        ]
                    }
                )
            ),
        )

        cookies = await get_cookies_from_api()

        assert [c.env_name for c in cookies] == ["ACC1_FADEL"]
        assert cookies[0].cookies == (
            "account_id_v2=123456789; cookie_token_v2=abc+/=def%2Fghi-jkl_mno"
        )

    async def test_http_error_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch)
        install_client(monkeypatch, FakeClient(FakeResponse({}, status_code=401)))

        assert await get_cookies_from_api() == []

    async def test_network_error_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch)
        install_client(monkeypatch, FakeClient(error=httpx.ConnectError("boom")))

        assert await get_cookies_from_api() == []

    async def test_invalid_payload_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch)
        install_client(monkeypatch, FakeClient(FakeResponse({"accounts": "nope"})))

        assert await get_cookies_from_api() == []


class TestIsSafeCookieValue:
    @pytest.mark.parametrize(
        "value",
        [
            "abc123",
            "abc+/=def",
            "abc%2Fdef",
            "a.b~c!d*e'f(g)h",
            "v2_abc-DEF.123",
        ],
    )
    def test_accepts_rfc6265_values(self, value: str) -> None:
        assert is_safe_cookie_value(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "has space",
            "semi;colon",
            "comma,value",
            'quote"value',
            "back\\slash",
            "tab\tvalue",
            "newline\nvalue",
            "unicode-\u00e9",
        ],
    )
    def test_rejects_dangerous_values(self, value: str) -> None:
        assert is_safe_cookie_value(value) is False
