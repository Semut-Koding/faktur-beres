"""Base class of every domain model.

* ``extra="forbid"``: an unknown key is an error, not silently dropped.
* ``frozen=True``: values are immutable once validated; sequences are tuples.
* JSON round trip: every model serialises with ``model_dump(mode="json")`` and validates back,
  because the LangGraph state (M4) lives in a JSON checkpointer.
* No ``float``: money, quantities, and percentages are ``Decimal``. A float input is refused so that
  a binary fraction never enters a money computation (strings and ints are converted exactly).
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @field_validator("*", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("pakai Decimal, bilangan bulat, atau string desimal, bukan float")
        return value
