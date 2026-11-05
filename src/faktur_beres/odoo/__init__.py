"""The only place where Faktur Beres talks to Odoo (External JSON-2 API, ADR-0004)."""

from faktur_beres.odoo.client import DEFAULT_USER_AGENT, READ_METHODS, OdooClient
from faktur_beres.odoo.errors import (
    OdooAccessError,
    OdooAuthError,
    OdooConnectionError,
    OdooError,
    OdooNotFoundError,
    OdooServerError,
    OdooValidationError,
)

__all__ = [
    "DEFAULT_USER_AGENT",
    "READ_METHODS",
    "OdooAccessError",
    "OdooAuthError",
    "OdooClient",
    "OdooConnectionError",
    "OdooError",
    "OdooNotFoundError",
    "OdooServerError",
    "OdooValidationError",
]
