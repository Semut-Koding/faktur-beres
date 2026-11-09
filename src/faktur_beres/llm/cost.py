"""Equivalent cost of LLM calls in USD (SPEC §12.2, BR-23), from ``data/llm/harga.yaml``.

The free tier bills nothing. The figure is what the same tokens would cost at the paid-tier price,
for the "biaya LLM per faktur" metric. Thinking tokens count as output. A model without a price
gives ``UNKNOWN_COST`` ("tidak diketahui"), never 0. Every amount is a ``Decimal``.
"""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from faktur_beres.config import REPO_ROOT
from faktur_beres.domain.base import DomainModel
from faktur_beres.llm.contracts import UNKNOWN_COST, LlmCallRecord

PRICES_PATH = REPO_ROOT / "data" / "llm" / "harga.yaml"
MILLION = Decimal(1_000_000)

type Cost = Decimal | Literal["tidak diketahui"] | None


class PricePeriod(DomainModel):
    starts: date | None = Field(default=None, alias="mulai")
    ends: date | None = Field(default=None, alias="sampai")
    input_per_million: Decimal = Field(alias="masukan_per_juta", ge=0)
    output_per_million: Decimal = Field(alias="keluaran_per_juta", ge=0)

    def covers(self, day: date) -> bool:
        started = self.starts is None or self.starts <= day
        return started and (self.ends is None or day <= self.ends)


class PriceList(DomainModel):
    source: str = Field(alias="sumber", min_length=1)
    updated: date = Field(alias="diperbarui")
    currency: Literal["USD"] = Field(alias="mata_uang")
    models: dict[str, tuple[PricePeriod, ...]] = Field(alias="model")

    @model_validator(mode="after")
    def _periods_do_not_overlap(self) -> "PriceList":
        for model, periods in self.models.items():
            if not periods:
                raise ValueError(f"{model}: tidak ada harga")
            for period in periods:
                if period.starts and period.ends and period.starts > period.ends:
                    raise ValueError(f"{model}: mulai setelah sampai")
            for earlier, later in zip(periods, periods[1:], strict=False):
                if earlier.ends is None or later.starts is None or later.starts <= earlier.ends:
                    raise ValueError(f"{model}: periode harga harus berurutan dan tidak tumpang")
        return self

    def period(self, model: str, day: date) -> PricePeriod | None:
        return next((p for p in self.models.get(model, ()) if p.covers(day)), None)

    def cost(
        self,
        model: str,
        day: date,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        thinking_tokens: int | None,
    ) -> Cost:
        """USD for one call; None when no token was reported (nothing was billed)."""
        if input_tokens is None and output_tokens is None and thinking_tokens is None:
            return None
        period = self.period(model, day)
        if period is None:
            return UNKNOWN_COST
        billed_output = (output_tokens or 0) + (thinking_tokens or 0)
        return (
            Decimal(input_tokens or 0) * period.input_per_million
            + Decimal(billed_output) * period.output_per_million
        ) / MILLION


def load_prices(path: Path = PRICES_PATH) -> PriceList:
    return PriceList.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def total_cost(calls: Iterable[LlmCallRecord]) -> Decimal | Literal["tidak diketahui"]:
    """The sum over the calls; unknown as soon as one billed call has no price."""
    total = Decimal(0)
    for call in calls:
        if call.cost_usd == UNKNOWN_COST:
            return UNKNOWN_COST
        if isinstance(call.cost_usd, Decimal):
            total += call.cost_usd
    return total
