"""Base class of every domain model.

* ``extra="forbid"``: an unknown key is an error, not silently dropped.
* ``frozen=True``: values are immutable once validated; sequences are tuples.
* JSON round trip: every model serialises with ``model_dump(mode="json")`` and validates back,
  because the LangGraph state (M4) lives in a JSON checkpointer.
* No ``float``: money, quantities, and percentages are ``Decimal``. A float input is refused so that
  a binary fraction never enters a money computation (strings and ints are converted exactly).
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


def _contains_float(value: Any) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, list | tuple):
        return any(_contains_float(item) for item in value)
    return False


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _no_float(cls, data: Any) -> Any:
        # Nested models run this check on their own input.
        if isinstance(data, dict):
            for key, value in data.items():
                if _contains_float(value):
                    raise ValueError(
                        f"{key}: pakai Decimal, bilangan bulat, atau string desimal, bukan float"
                    )
        return data
