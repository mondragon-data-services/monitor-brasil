import math

import pandas as pd
import pytest

from src.transformation.metrics import (
    accumulated_12m,
    build_real_interest,
    detect_recession,
    filter_period,
    latest_with_delta,
    monthly_last,
    period_start,
    rate_per_100k,
    real_interest_fisher,
    yoy_change_pp,
)


def monthly(values, start="2024-01-01"):
    return pd.DataFrame({"date": pd.date_range(start, periods=len(values), freq="MS"), "value": values})


def test_accumulated_12m_is_compound_not_simple_sum():
    out = accumulated_12m(monthly([1.0] * 12))
    assert out["value"].iloc[:11].isna().all()
    assert out["value"].iloc[-1] == pytest.approx((1.01**12 - 1) * 100)
    assert out["value"].iloc[-1] != pytest.approx(12.0)


def test_fisher_real_interest():
    assert real_interest_fisher(10.0, 5.0) == pytest.approx((1.10 / 1.05 - 1) * 100)
    assert real_interest_fisher(5.0, 5.0) == pytest.approx(0.0)


def test_rate_per_100k():
    assert rate_per_100k(500, 1_000_000) == pytest.approx(50.0)


def test_yoy_change_pp():
    out = yoy_change_pp(monthly(list(range(1, 25))), 12)
    assert out.dropna().iloc[0] == 12


@pytest.mark.parametrize(
    "values,expected",
    [
        ([0.5, -0.2, -0.4], "Alerta de Recessão Técnica"),
        ([0.9, 0.5, -0.1], "Desaceleração"),
        ([0.2, 1.1, 0.5], "Expansão"),
        ([1.0, 0.1], "Desaceleração"),
        ([0.4], "Sem dados"),
    ],
)
def test_detect_recession(values, expected):
    assert detect_recession(values) == expected


def test_monthly_last_and_real_interest_merge():
    selic = pd.DataFrame({"date": pd.to_datetime(["2025-01-02", "2025-01-20", "2025-02-03"]), "value": [13.0, 13.25, 13.5]})
    assert monthly_last(selic)["value"].tolist() == [13.25, 13.5]
    ipca = monthly([0.5] * 14, "2024-01-01")
    out = build_real_interest(selic, ipca)
    assert {"selic", "ipca_12m", "juro_real"} <= set(out.columns)
    assert len(out) == 2 and out["juro_real"].notna().all()


def test_period_filters():
    today = pd.Timestamp("2026-10-26")
    assert period_start("2y", today) == pd.Timestamp("2024-10-26")
    assert period_start(None) is None
    df = monthly([1, 2, 3], "2023-01-01")
    assert len(filter_period(df, "2023-02-01")) == 2


def test_latest_with_delta():
    d = latest_with_delta(monthly(list(range(13))), 12)
    assert d.latest == 12 and d.change == 12
    assert latest_with_delta(monthly([]), 12) is None
    assert math.isnan(latest_with_delta(monthly([1.0, 2.0]), 12).change)
