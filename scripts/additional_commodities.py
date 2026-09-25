"""Preregistered new income source: the remaining affordable commodities.

In the current seven-year weekly replay commodities earn the most per
trade (COPPER +0.79, GOLD +0.24, SILVER +0.21, OIL_BRENT +0.12 USD against
+0.06 for the book) yet fire rarely: 106-673 signals each against roughly
1,000-1,700 per index or FX pair. Unlike the rejected indices of section
335, which all queue behind the risk_on cluster cap, further commodities
sit in their own clusters and should add trades instead of displacing them.

Fixed before any outcome was seen:
  - every tradeable Capital.com commodity outside the replay universe whose
    spread quoted 2026-09-24 18:55 UTC clears the 10 % cost ceiling at the
    1.05 % stop floor (spread <= 0.105 %); closed markets had no quote;
  - per-side cost is half that spread, never below the 0.01 % default;
  - energy products join the energy cluster; grains and livestock form
    their own clusters under the same direction cap;
  - the class-level commodity short block applies to all eight;
  - pins, vetoes, stops, sizing and every other cap stay unchanged.
Adoption: all four OOS samples better and pooled paired daily t > +2.
"""
import asyncio

import scripts.additional_indices as replay

SPREAD_PERCENT = {"WHEAT": 0.0352, "LIVECATTLE": 0.0542, "SOYBEANOIL": 0.0592,
                  "LEANHOGS": 0.0707, "SOYBEANMEAL": 0.0810, "GASOIL": 0.0845,
                  "HEATINGOIL": 0.0877, "GASOLINE": 0.0900}
CLUSTERS = {"GASOIL": "energy", "HEATINGOIL": "energy", "GASOLINE": "energy",
            "WHEAT": "grains", "SOYBEANOIL": "grains", "SOYBEANMEAL": "grains",
            "LIVECATTLE": "livestock", "LEANHOGS": "livestock"}


def configure():
    replay.SPREAD_PERCENT = SPREAD_PERCENT
    replay.CANDIDATES = list(SPREAD_PERCENT)
    replay.FEES = {pair: max(percent / 100 / 2, 0.0001) for pair, percent in SPREAD_PERCENT.items()}
    replay.CLUSTERS = CLUSTERS
    replay.SHORT_BLOCKED = set(SPREAD_PERCENT)


if __name__ == "__main__":
    configure()
    asyncio.run(replay.main())
