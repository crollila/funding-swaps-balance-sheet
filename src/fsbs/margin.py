"""Collateral and margin arithmetic shared by repo and derivative trades.

Conventions
-----------
* Haircut ``h`` is quoted as a fraction of collateral market value:
  cash lent = MV * (1 - h). The equivalent "margin ratio" m with
  cash = MV / (1 + m) satisfies (1 - h) = 1 / (1 + m).
* A margin call is positive when the *collateral giver* must deliver more,
  negative when excess collateral should be returned.
"""
from __future__ import annotations

import math


def cash_against_collateral(mv: float, haircut: float) -> float:
    """Cash that collateral of market value ``mv`` supports at haircut ``h``."""
    _check_haircut(haircut)
    return mv * (1.0 - haircut)


def collateral_required(exposure: float, haircut: float) -> float:
    """Collateral market value needed to cover ``exposure`` after haircut."""
    _check_haircut(haircut)
    return exposure / (1.0 - haircut)


def haircut_to_margin_ratio(haircut: float) -> float:
    _check_haircut(haircut)
    return 1.0 / (1.0 - haircut) - 1.0


def margin_ratio_to_haircut(m: float) -> float:
    if m < 0:
        raise ValueError("margin ratio must be >= 0")
    return 1.0 - 1.0 / (1.0 + m)


def margin_call(required: float, held: float, mta: float = 0.0, threshold: float = 0.0,
                rounding: float = 0.0) -> float:
    """Amount the collateral giver must deliver (>0) or receive back (<0).

    ``threshold`` is unsecured exposure tolerated before a call; ``mta`` is the
    minimum transfer amount below which no call is made; deliveries round up
    and returns round down to ``rounding`` (both in favour of the secured party).
    """
    if mta < 0 or threshold < 0 or rounding < 0:
        raise ValueError("mta, threshold and rounding must be non-negative")
    deficit = required - held
    if deficit > 0:
        deficit = max(deficit - threshold, 0.0)
    if abs(deficit) < mta or deficit == 0.0:
        return 0.0
    if rounding > 0:
        if deficit > 0:
            deficit = math.ceil(deficit / rounding) * rounding
        else:
            deficit = -math.floor(-deficit / rounding) * rounding
    return deficit


def _check_haircut(h: float) -> None:
    if not 0.0 <= h < 1.0:
        raise ValueError(f"haircut must be in [0, 1), got {h}")
