"""Exceptions raised by the Odoo JSON-2 client.

Odoo 19 answers a failed ``/json/2`` call with a 4xx/5xx status and a JSON object such as::

    {"name": "odoo.exceptions.AccessError", "message": "...", "arguments": [...],
     "context": {}, "debug": "Traceback ..."}

Status codes used by Odoo 19 (``odoo/http.py`` ``Json2Dispatcher.handle_error`` and
``odoo/exceptions.py``): 401 invalid or expired API key (``werkzeug.exceptions.Unauthorized``),
403 ``AccessError``/``AccessDenied``, 404 unknown model or method (``NotFound``) and
``MissingError``, 409 ``LockError``, 422 ``UserError``/``ValidationError`` and bad arguments
(``UnprocessableEntity``), 500 anything else.

``error_from_response`` maps that object (and the HTTP status) onto the classes below.
"""

from typing import Any


class OdooError(Exception):
    """Base class for every failed Odoo call."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        name: str | None = None,
        model: str | None = None,
        method: str | None = None,
        debug: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.name = name
        self.model = model
        self.method = method
        self.debug = debug

    def __str__(self) -> str:
        where = f"{self.model}/{self.method}" if self.model and self.method else "odoo"
        status = f" HTTP {self.status}" if self.status else ""
        name = f" {self.name}" if self.name else ""
        return f"[{where}{status}{name}] {self.message}"


class OdooConnectionError(OdooError):
    """The server could not be reached or did not answer in time."""


class OdooServerError(OdooError):
    """The server answered with a 5xx status."""


class OdooAuthError(OdooError):
    """The API key is missing, invalid, or expired (HTTP 401)."""


class OdooAccessError(OdooError):
    """The user is not allowed to perform the operation (AccessError, HTTP 403)."""


class OdooNotFoundError(OdooError):
    """The model, method, or record does not exist (MissingError, HTTP 404)."""


class OdooValidationError(OdooError):
    """Odoo rejected the data or the operation (UserError, ValidationError, HTTP 422)."""


_NAME_SUFFIXES: tuple[tuple[str, type[OdooError]], ...] = (
    ("Unauthorized", OdooAuthError),
    ("AccessDenied", OdooAccessError),
    ("AccessError", OdooAccessError),
    ("Forbidden", OdooAccessError),
    ("MissingError", OdooNotFoundError),
    ("NotFound", OdooNotFoundError),
    ("ValidationError", OdooValidationError),
    ("UserError", OdooValidationError),
    ("RedirectWarning", OdooValidationError),
    ("UnprocessableEntity", OdooValidationError),
)

_STATUS_CLASSES: dict[int, type[OdooError]] = {
    401: OdooAuthError,
    403: OdooAccessError,
    404: OdooNotFoundError,
    422: OdooValidationError,
}


def error_class_for(status: int, name: str | None) -> type[OdooError]:
    """Pick the most specific error class for an HTTP status and an Odoo error name."""
    if status == 401:
        return OdooAuthError
    if name:
        short = name.rsplit(".", 1)[-1]
        for suffix, cls in _NAME_SUFFIXES:
            if short == suffix:
                return cls
    if status in _STATUS_CLASSES:
        return _STATUS_CLASSES[status]
    if status >= 500:
        return OdooServerError
    return OdooError


def error_from_response(
    status: int,
    payload: Any,
    *,
    model: str,
    method: str,
    redact: tuple[str, ...] = (),
) -> OdooError:
    """Build an ``OdooError`` from a JSON-2 error response.

    ``redact`` lists strings (e.g. the API key) that must never end up in the message, even if a
    misbehaving server or proxy echoes them back.
    """
    name: str | None = None
    message = f"HTTP {status}"
    debug: str | None = None
    if isinstance(payload, dict):
        name = payload.get("name") if isinstance(payload.get("name"), str) else None
        if isinstance(payload.get("message"), str) and payload["message"]:
            message = payload["message"]
        if isinstance(payload.get("debug"), str):
            debug = payload["debug"]
    elif isinstance(payload, str) and payload.strip():
        message = payload.strip()[:500]
    for secret in redact:
        if secret:
            message = message.replace(secret, "***")
            if debug:
                debug = debug.replace(secret, "***")
    cls = error_class_for(status, name)
    return cls(message, status=status, name=name, model=model, method=method, debug=debug)
