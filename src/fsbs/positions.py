"""A desk position: one instrument plus its desk-level context (hedge, sales spread)."""
from __future__ import annotations

from dataclasses import dataclass, replace

from .irs import IRS
from .repo import Repo
from .trs import EquityTRS

PRODUCTS = ("reverse_repo", "repo", "trs", "irs")


@dataclass(frozen=True)
class Position:
    inst: Repo | IRS | EquityTRS
    product: str
    hedge: IRS | None = None  # CCP hedge of a client IRS (desk takes the opposite side at mid)
    sales_spread_bp: float = 0.0  # IRS: running spread earned vs. mid on the client swap
    desc: str = ""

    def __post_init__(self):
        if self.product not in PRODUCTS:
            raise ValueError(self.product)

    @property
    def trade_id(self) -> str:
        return self.inst.trade_id

    @property
    def cpty(self) -> str:
        return self.inst.cpty

    def scaled(self, x: float, trade_id: str | None = None) -> "Position":
        tid = trade_id or self.trade_id
        hedge = self.hedge.scaled(x, trade_id=self.hedge.trade_id) if self.hedge is not None else None
        return replace(self, inst=self.inst.scaled(x, trade_id=tid), hedge=hedge)
