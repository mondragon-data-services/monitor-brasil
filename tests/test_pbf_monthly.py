"""Bolsa Família mensal por UF: ETL do Portal da Transparência, gráficos e ferramenta do agente."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from src.agent.context import CATALOG, build_context
from src.agent.tools import run_tool
from src.ingestion import bolsa_familia_download as pbf
from src.ingestion.bolsa_familia_download import (
    aggregate_payments,
    combine,
    get_bolsa_familia_mensal,
    month_path,
    recent_months,
    sync_bolsa_familia,
)
from src.ingestion.cache import FetchResult
from src.ingestion.http_client import IngestionError
from src.visualization import charts


def text(fig) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fig.layout.title.text.replace("<br>", " "))).strip()


def chunk(rows: list[tuple[str, str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["MÊS COMPETÊNCIA", "UF", "NIS FAVORECIDO", "VALOR PARCELA"])


# ---------------------------------------------------------------- meses ------------------------------------------
def test_recent_months_goes_back_and_stops_at_program_start():
    assert recent_months(3, date(2026, 10, 28)) == ["202608", "202609", "202610"]
    assert recent_months(3, date(2026, 1, 5)) == ["202511", "202512", "202601"]                     # vira o ano
    assert recent_months(100, date(2023, 5, 1)) == ["202303", "202304", "202305"]                   # nunca antes de mar/2023


# ---------------------------------------------------------------- agregação --------------------------------------
def test_aggregate_payments_counts_distinct_families_and_sums_values():
    c1 = chunk([("202608", "BA", "111", "600,00"), ("202608", "BA", "111", "150,50"), ("202608", "BA", "222", "1.200,00")])   # 111 recebeu 2 parcelas
    c2 = chunk([("202608", "SP", "333", "375,00"), ("202608", "BA", "444", "600,00"), ("202608", "SP", "222", "600,00")])      # 222 aparece em 2 UFs
    out = aggregate_payments([c1, c2])
    ba = out[out["uf"] == "BA"].iloc[0]
    assert (ba["familias"], ba["pagamentos"]) == (3, 4) and ba["valor"] == pytest.approx(600 + 150.5 + 1200 + 600)
    assert out[out["uf"] == "SP"].iloc[0]["familias"] == 2
    br = out[out["uf"] == "BR"].iloc[0]
    assert br["familias"] == 4 and br["pagamentos"] == 6 and br["valor"] == pytest.approx(ba["valor"] + 975)    # BR = NIS únicos no país todo
    assert out["date"].eq(pd.Timestamp("2026-08-01")).all() and list(out.columns) == ["date", "uf", "familias", "pagamentos", "valor"]


def test_aggregate_payments_rejects_bad_layouts():
    with pytest.raises(IngestionError, match="colunas"):
        aggregate_payments([pd.DataFrame({"x": ["1"]})])
    with pytest.raises(IngestionError, match="inesperado"):
        aggregate_payments([chunk([("202608", "BA", "1", "1,00"), ("202607", "BA", "2", "1,00")])])        # duas competências no mesmo arquivo
    with pytest.raises(IngestionError):
        aggregate_payments([])


# ---------------------------------------------------------------- ETL (sem rede) ---------------------------------
def fake_month(ym: str) -> pd.DataFrame:
    d = pd.Timestamp(int(ym[:4]), int(ym[4:]), 1)
    return pd.DataFrame([{"date": d, "uf": "BR", "familias": 18_000_000, "pagamentos": 18_050_000, "valor": 12.0e9},
                         {"date": d, "uf": "BA", "familias": 2_300_000, "pagamentos": 2_310_000, "valor": 1.5e9}])


def test_sync_downloads_new_months_skips_cached_and_reports_unpublished(tmp_path, monkeypatch):
    downloaded: list[str] = []

    def fake_download(ym, dest, session=None):
        if ym == "202609":
            raise IngestionError("competência 202609 ainda não publicada (HTTP 403)")
        downloaded.append(ym)
        Path(dest).write_bytes(b"PK")
        return Path(dest)

    monkeypatch.setattr(pbf, "download_month", fake_download)
    monkeypatch.setattr(pbf, "aggregate_zip", lambda path: fake_month(Path(path).stem))
    first = sync_bolsa_familia(["202607", "202608", "202609"], base=tmp_path)
    assert first == [("202607", "baixado"), ("202608", "baixado"), ("202609", "indisponível")]
    again = sync_bolsa_familia(["202607", "202608"], base=tmp_path)
    assert again == [("202607", "cache"), ("202608", "cache")] and downloaded == ["202607", "202608"]      # não baixou de novo
    forced = sync_bolsa_familia(["202607"], base=tmp_path, force=True)
    assert forced == [("202607", "baixado")] and downloaded == ["202607", "202608", "202607"]
    res = get_bolsa_familia_mensal(tmp_path)
    assert res.mode == "cache" and res.detail.startswith("cache válido") and sorted(res.df["date"].dt.strftime("%Y%m").unique()) == ["202607", "202608"]


def test_one_corrupt_month_does_not_stop_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(pbf, "download_month", lambda ym, dest, session=None: Path(dest))

    def agg(path):
        if Path(path).stem == "202606":
            raise ValueError("zip corrompido")
        return fake_month(Path(path).stem)

    monkeypatch.setattr(pbf, "aggregate_zip", agg)
    report = dict(sync_bolsa_familia(["202606", "202607"], base=tmp_path))
    assert report == {"202606": "erro", "202607": "baixado"} and month_path("202607", tmp_path).exists() and not month_path("202606", tmp_path).exists()


def test_get_mensal_is_missing_before_the_etl_runs(tmp_path):
    res = get_bolsa_familia_mensal(tmp_path)
    assert res.mode == "missing" and not res.ok and "--bolsa-familia" in res.detail
    assert combine(tmp_path).empty


# ---------------------------------------------------------------- gráficos --------------------------------------
def monthly_df() -> pd.DataFrame:
    rows = []
    for d in pd.date_range("2025-08-01", "2026-08-01", freq="MS"):
        rows += [{"date": d, "uf": "BR", "familias": 20_000_000 if d.year == 2025 else 18_778_353, "pagamentos": 1, "valor": 12.763e9 * (0.97 if d.year == 2025 else 1.0)},
                 {"date": d, "uf": "BA", "familias": 2_336_477, "pagamentos": 1, "valor": 1_558_392_484.0},
                 {"date": d, "uf": "SC", "familias": 200_000, "pagamentos": 1, "valor": 120_000_000.0}]
    return pd.DataFrame(rows)


def test_monthly_charts():
    m = monthly_df()
    t = text(charts.pbf_monthly_chart(m))
    assert "Bolsa Família paga a 18,8 milhões de famílias em 08/2026 (-6,1% em 12 meses): R$ 679,67 por família" in t
    ba = text(charts.pbf_monthly_chart(m, "BA"))
    assert ba.startswith("Em BA, 2.336.477 famílias recebem o Bolsa Família (08/2026), com R$ 666,98 por família, contra R$ 679,67 no Brasil")
    r = text(charts.pbf_avg_uf_chart(m, "BA"))
    assert r.startswith("Benefício médio do Bolsa Família vai de R$ 600,00 (SC) a R$ 666,98 (BA) por família em 08/2026")
    assert "Dados indisponíveis" in text(charts.pbf_monthly_chart(pd.DataFrame()))
    assert "Dados indisponíveis" in text(charts.pbf_monthly_chart(m, "XX"))


# ---------------------------------------------------------------- agente ----------------------------------------
@pytest.fixture(scope="module")
def pctx():
    now = datetime(2026, 10, 28, 9, 0)
    fam = pd.concat([pd.DataFrame([{"date": pd.Timestamp("2025-01-01"), "uf": u, "value": v} for u, v in {"BR": 18_618_236.0, "BA": 2_301_351.0}.items()])])
    return build_context({"pbf_mensal": FetchResult("m", monthly_df(), "cache", now), "ipea_pbf_familias": FetchResult("f", fam, "live", now)})


def test_catalog_and_series_for_monthly_bolsa_familia(pctx):
    assert "bolsa_familia_familias_mensal" in CATALOG and "bolsa_familia_valor_medio_familia" in CATALOG
    f = json.loads(run_tool(pctx, "get_indicator_snapshot", '{"indicator":"bolsa_familia_familias_mensal"}'))
    assert f["ultimo"]["valor"] == pytest.approx(18.78) and f["ha_12_meses"]["valor"] == pytest.approx(20.0)
    v = json.loads(run_tool(pctx, "get_indicator_snapshot", '{"indicator":"bolsa_familia_valor_medio_familia"}'))
    assert v["ultimo"]["valor"] == pytest.approx(679.6, abs=0.1)


def test_bolsa_familia_tool_includes_monthly_block_by_state(pctx):
    ba = json.loads(run_tool(pctx, "get_bolsa_familia_by_uf", '{"uf":"BA"}'))
    m = ba["mensal"]
    assert m["mes_mais_recente"] == "2026-08" and m["familias"] == 2_336_477 and m["valor_medio_por_familia_reais"] == 667
    assert len(m["serie_mensal"]) == 12 and ba["uf"] == "BA" and ba["familias"] == 2_301_351              # anual (dezembro) continua lá
    rank = json.loads(run_tool(pctx, "get_bolsa_familia_by_uf", '{"uf":null}'))["mensal"]
    assert rank["ranking_valor_medio_por_familia"][0] == {"uf": "BA", "valor_medio": 667} and rank["brasil_familias"] == 18_778_353
    # sem o ETL: a ferramenta continua funcionando só com os dados anuais
    plain = json.loads(run_tool(build_context({"ipea_pbf_familias": FetchResult("f", pd.DataFrame([{"date": pd.Timestamp("2025-01-01"), "uf": "BR", "value": 1.0e7}]), "live", datetime.now())}),
                                "get_bolsa_familia_by_uf", '{"uf":null}'))
    assert "mensal" not in plain


def test_prompt_and_limits_updated_for_monthly_data():
    from src.agent.prompts import build_instructions
    from src.agent.tools import NOT_AVAILABLE
    assert "benefício médio por estado" not in build_instructions("x")
    assert "não há base nacional aberta" in NOT_AVAILABLE and "24 meses" in NOT_AVAILABLE
