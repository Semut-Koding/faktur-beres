"""Typed errors of the agent tools.

Odoo errors are mapped onto a few kinds the graph (M4) can act on: refused by the gateway itself,
access denied, not found, conflict, rejected data, or Odoo not available (retry later, BR-27).
"""

from typing import ClassVar

from faktur_beres.odoo import (
    OdooAccessError,
    OdooAuthError,
    OdooConnectionError,
    OdooError,
    OdooNotFoundError,
    OdooServerError,
    OdooValidationError,
)


class ToolError(Exception):
    kind: ClassVar[str] = "error"

    def __init__(
        self,
        message: str,
        *,
        model: str | None = None,
        method: str | None = None,
        status: int | None = None,
        odoo_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.model = model
        self.method = method
        self.status = status
        self.odoo_name = odoo_name


class ForbiddenOperation(ToolError):
    """Refused by ``AgentOdooGateway`` before any request is sent."""

    kind = "forbidden"


class AccessDenied(ToolError):
    """Odoo refused the operation for the agent user (HTTP 401/403)."""

    kind = "access_denied"


class NotFound(ToolError):
    """Model, method, or record does not exist (HTTP 404)."""

    kind = "not_found"


class Conflict(ToolError):
    """A concurrent change (HTTP 409) or an existing record with the same natural key."""

    kind = "conflict"


class Rejected(ToolError):
    """Odoo rejected the data (UserError or ValidationError, HTTP 422)."""

    kind = "rejected"


class VerificationFailed(ToolError):
    """What Odoo stored differs from what was sent (e.g. an attachment's checksum).

    In M4 the action node turns this into ``validation_failed`` (C4).
    """

    kind = "verification_failed"


class Unavailable(ToolError):
    """Odoo could not be reached or failed (connection error, HTTP 5xx)."""

    kind = "unavailable"


def from_odoo(exc: OdooError) -> ToolError:
    """The tool error for an error of the JSON-2 client."""
    if isinstance(exc, OdooAuthError | OdooAccessError):
        cls: type[ToolError] = AccessDenied
    elif isinstance(exc, OdooNotFoundError):
        cls = NotFound
    elif exc.status == 409:
        cls = Conflict
    elif isinstance(exc, OdooValidationError):
        cls = Rejected
    elif isinstance(exc, OdooConnectionError | OdooServerError):
        cls = Unavailable
    else:
        cls = Unavailable
    return cls(
        exc.message, model=exc.model, method=exc.method, status=exc.status, odoo_name=exc.name
    )
