"""Client for the Odoo 19 External JSON-2 API.

Every call is ``POST /json/2/<model>/<method>`` with all arguments sent by name, the API key as
``Authorization: bearer <key>``, and the database in ``X-Odoo-Database``. Each call runs in its own
transaction on the server, so callers that need several steps must make them safe to repeat.

Only read-only methods are retried automatically (connection errors and 5xx answers). Methods that
change data (``create``, ``write``, ``action_*``, ``button_*`` ...) are never retried here, because
a retry after a lost answer could apply the change twice.

Reference: Odoo 19.0 documentation, "External JSON-2 API".
"""

import json
import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

import httpx
from pydantic import SecretStr

from faktur_beres.odoo.errors import (
    OdooConnectionError,
    OdooError,
    OdooServerError,
    error_from_response,
)

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "faktur-beres/0.1"

#: Methods that only read data and are therefore safe to retry.
READ_METHODS: frozenset[str] = frozenset(
    {
        "context_get",
        "default_get",
        "fields_get",
        "formatted_read_group",
        "has_access",
        "name_search",
        "read",
        "read_group",
        "search",
        "search_count",
        "search_fetch",
        "search_read",
        "web_read",
        "web_search_read",
    }
)

Domain = Sequence[Any]


class _BearerAuth(httpx.Auth):
    """Adds the API key to each request without exposing it in ``repr``."""

    def __init__(self, token: str) -> None:
        self._token = token

    def auth_flow(self, request: httpx.Request):  # type: ignore[override]
        request.headers["Authorization"] = f"bearer {self._token}"
        yield request

    def __repr__(self) -> str:
        return "_BearerAuth(***)"


def _json_default(value: Any) -> Any:
    """Serialize dates the way Odoo expects them (datetimes as naive UTC)."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"nilai bertipe {type(value).__name__} tidak bisa dikirim ke Odoo")


class OdooClient:
    """Thin, typed wrapper around ``/json/2``. One instance per identity (admin or agent)."""

    def __init__(
        self,
        base_url: str,
        db: str,
        api_key: str | SecretStr,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
        *,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 3,
        backoff: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not key:
            raise ValueError("API key Odoo kosong")
        self.base_url = base_url.rstrip("/")
        self.db = db
        self._key = key
        self._max_attempts = max(1, max_attempts)
        self._backoff = backoff
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            auth=_BearerAuth(key),
            headers={
                "X-Odoo-Database": db,
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": user_agent,
            },
        )

    def __repr__(self) -> str:
        return f"OdooClient(base_url={self.base_url!r}, db={self.db!r}, api_key=***)"

    def __enter__(self) -> "OdooClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- core ---------------------------------------------------------------------------------

    def call(
        self,
        model: str,
        method: str,
        *,
        ids: Iterable[int] | None = None,
        context: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Call ``method`` on ``model``. Keyword arguments are sent as named parameters."""
        body: dict[str, Any] = {}
        if ids is not None:
            body["ids"] = list(ids)
        if context is not None:
            body["context"] = dict(context)
        body.update(kwargs)
        content = json.dumps(body, default=_json_default, ensure_ascii=False).encode("utf-8")
        path = f"/json/2/{model}/{method}"
        attempts = self._max_attempts if method in READ_METHODS else 1

        for attempt in range(1, attempts + 1):
            log.debug("odoo %s/%s (percobaan %d/%d)", model, method, attempt, attempts)
            try:
                response = self._http.post(path, content=content)
            except httpx.TransportError as exc:
                if attempt < attempts:
                    self._wait(attempt)
                    continue
                raise OdooConnectionError(
                    f"tidak bisa menghubungi Odoo di {self.base_url} ({type(exc).__name__})",
                    model=model,
                    method=method,
                ) from exc

            if response.status_code == 200:
                if not response.content:
                    return None
                return response.json()

            error = self._error(response, model, method)
            if isinstance(error, OdooServerError) and attempt < attempts:
                self._wait(attempt)
                continue
            raise error

        raise AssertionError("unreachable")  # pragma: no cover

    def _wait(self, attempt: int) -> None:
        self._sleep(self._backoff * (2 ** (attempt - 1)))

    def _error(self, response: httpx.Response, model: str, method: str) -> OdooError:
        try:
            payload: Any = response.json()
        except ValueError:
            payload = response.text
        return error_from_response(
            response.status_code, payload, model=model, method=method, redact=(self._key,)
        )

    # -- helpers ------------------------------------------------------------------------------

    def search_read(
        self,
        model: str,
        domain: Domain | None = None,
        fields: Sequence[str] | None = None,
        *,
        offset: int = 0,
        limit: int | None = None,
        order: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"domain": list(domain or [])}
        if fields is not None:
            kwargs["fields"] = list(fields)
        if offset:
            kwargs["offset"] = offset
        if limit is not None:
            kwargs["limit"] = limit
        if order is not None:
            kwargs["order"] = order
        return self.call(model, "search_read", context=context, **kwargs)

    def search(
        self,
        model: str,
        domain: Domain | None = None,
        *,
        offset: int = 0,
        limit: int | None = None,
        order: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> list[int]:
        kwargs: dict[str, Any] = {"domain": list(domain or [])}
        if offset:
            kwargs["offset"] = offset
        if limit is not None:
            kwargs["limit"] = limit
        if order is not None:
            kwargs["order"] = order
        return self.call(model, "search", context=context, **kwargs)

    def read(
        self,
        model: str,
        ids: Iterable[int],
        fields: Sequence[str] | None = None,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {}
        if fields is not None:
            kwargs["fields"] = list(fields)
        return self.call(model, "read", ids=ids, context=context, **kwargs)

    def search_count(
        self,
        model: str,
        domain: Domain | None = None,
        *,
        limit: int | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> int:
        kwargs: dict[str, Any] = {"domain": list(domain or [])}
        if limit is not None:
            kwargs["limit"] = limit
        return self.call(model, "search_count", context=context, **kwargs)

    def create(
        self,
        model: str,
        vals_list: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[int]:
        """Create one or more records and return their ids. Never retried automatically."""
        if isinstance(vals_list, Mapping):
            vals_list = [vals_list]
        result = self.call(model, "create", context=context, vals_list=[dict(v) for v in vals_list])
        return [result] if isinstance(result, int) else list(result)

    def write(
        self,
        model: str,
        ids: Iterable[int],
        vals: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        """Update records. Never retried automatically."""
        return self.call(model, "write", ids=ids, context=context, vals=dict(vals))

    def fields_get(
        self,
        model: str,
        attributes: Sequence[str] | None = None,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        kwargs: dict[str, Any] = {}
        if attributes is not None:
            kwargs["attributes"] = list(attributes)
        return self.call(model, "fields_get", context=context, **kwargs)
