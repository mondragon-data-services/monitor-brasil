from datetime import date

import pandas as pd
import pytest

from src.contracts import validate_series
from src.ingestion.bcb_client import build_url, date_windows, parse_sgs_payload
from src.ingestion.cache import with_disk_fallback
from src.ingestion.http_client import IngestionError
from src.ingestion.ibge_client import parse_sidra_payload, sidra_period_to_date
from src.ingestion.ipea_client import parse_ipea_payload
from src.ingestion.sinesp_loader import (
    get_security_education,
    process_upload,
    sinesp_to_indicators,
    standardize_sinesp,
)


def test_windows_respect_ten_year_limit():
    wins = date_windows(date(2016, 1, 1), date(2026, 10, 26))
    assert len(wins) == 2
    assert wins[0] == (date(2016, 1, 1), date(2025, 12, 31))
    assert wins[1][0] == date(2026, 1, 1) and wins[-1][1] == date(2026, 10, 26)
    for s, e in wins:
        assert (e - s).days < 3660


def test_bcb_url_format():
    url = build_url(433, date(2020, 1, 1), date(2025, 12, 31))
    assert url.endswith("?formato=json&dataInicial=01/01/2020&dataFinal=31/12/2025")


def test_parse_sgs_payload_drops_bad_values():
    df = parse_sgs_payload([{"data": "01/01/2025", "valor": "0.16"}, {"data": "01/02/2025", "valor": ""}])
    assert len(df) == 1 and df["value"].iloc[0] == 0.16
    with pytest.raises(IngestionError):
        parse_sgs_payload({"erro": "x"})


SIDRA = [
    {"D3C": "Trimestre Móvel (Código)", "D3N": "Trimestre Móvel", "V": "Valor"},  # cabeçalho
    {"D3C": "202606", "D3N": "abr-mai-jun 2026", "V": "5.8"},
    {"D3C": "202607", "D3N": "mai-jun-jul 2026", "V": "..."},
    {"D3C": "202608", "D3N": "jun-jul-ago 2026", "V": "5.3"},
]


def test_sidra_skips_header_and_coerces_nulls():
    df = parse_sidra_payload(SIDRA, "movel")
    assert df["value"].tolist() == [5.8, 5.3]
    assert df["date"].iloc[-1] == pd.Timestamp("2026-08-01")


def test_sidra_quarter_code():
    assert sidra_period_to_date("202602", "trimestre") == pd.Timestamp("2026-06-01")


def test_ipea_payload():
    df = parse_ipea_payload({"value": [{"VALDATA": "2026-09-01T00:00:00-03:00", "VALVALOR": 7695.82}]})
    assert df["value"].iloc[0] == 7695.82
    with pytest.raises(IngestionError):
        parse_ipea_payload({"value": []})


def test_validate_series_contract():
    with pytest.raises(ValueError):
        validate_series(pd.DataFrame({"date": ["2025-01-01"]}))
    out = validate_series(pd.DataFrame({"date": ["2025-02-01", "2025-01-01", "2025-01-01"], "value": [2, 1, 1]}))
    assert out["value"].tolist() == [1, 2]


def test_disk_fallback(tmp_path):
    good = pd.DataFrame({"date": pd.to_datetime(["2025-01-01"]), "value": [1.0]})
    assert with_disk_fallback("s", lambda: good, tmp_path).mode == "live"

    def boom():
        raise IngestionError("api fora")

    res = with_disk_fallback("s", boom, tmp_path)
    assert res.mode == "cache" and res.ok and res.synced_at is not None
    assert with_disk_fallback("outra", boom, tmp_path).mode == "missing"


def test_consolidated_fallback_is_created_and_valid(tmp_path):
    target = tmp_path / "consolidado.parquet"
    res = get_security_education(sinesp_path=tmp_path / "s.parquet", consolidated_path=target, raw_dir=tmp_path / "raw")
    assert target.exists() and res.mode == "fallback"
    assert {"mvi_taxa", "feminicidio_total", "ideb_ensino_medio", "abandono_ensino_medio"} <= set(res.df["indicador"])
    assert (res.df["qualidade"] == "aproximado").all()


def sample_sinesp() -> pd.DataFrame:
    return pd.DataFrame({
        "UF": ["SP", "SP", "BA", "BA"],
        "Município": ["a", "b", "c", "d"],
        "Evento": ["Homicídio doloso", "Feminicídio", "Latrocínio", "Estupro de vulnerável"],
        "Mês/Ano": ["01/2024", "02/2024", "03/2024", "04/2024"],
        "Total Vítimas": [100, 5, 10, 40],
    })


def test_standardize_and_indicators():
    std = standardize_sinesp(sample_sinesp())
    assert list(std.columns) == ["uf", "municipio", "evento", "mes_ano", "total_vitimas"]
    ind = sinesp_to_indicators(std)
    br_mvi = ind[(ind.indicador == "mvi_total") & (ind.uf == "BR")]["valor"].iloc[0]
    assert br_mvi == 110
    assert ind[(ind.indicador == "mvi_taxa") & (ind.uf == "SP")]["valor"].iloc[0] == pytest.approx(100 / 44_411_238 * 1e5)
    assert (ind["qualidade"] == "oficial").all()


def test_upload_overrides_consolidated(tmp_path):
    csv = tmp_path / "vde.csv"
    sample_sinesp().to_csv(csv, index=False)
    out = tmp_path / "sinesp_vde.parquet"
    process_upload(csv, csv.name, out)
    res = get_security_education(sinesp_path=out, consolidated_path=tmp_path / "c.parquet", raw_dir=tmp_path / "raw")
    assert res.mode == "cache"
    assert (res.df[res.df.indicador == "mvi_taxa"]["qualidade"] == "oficial").all()
    assert "ideb_anos_iniciais" in set(res.df["indicador"])  # o restante vem do consolidado


def test_standardize_rejects_bad_sheet():
    with pytest.raises(ValueError):
        standardize_sinesp(pd.DataFrame({"x": [1]}))
