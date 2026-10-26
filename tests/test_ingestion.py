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
    """Um ano completo (12 meses) com as colunas reais do Sinesp VDE (``total_vitima`` no singular)."""
    rows = []
    for m in range(1, 13):
        stamp = pd.Timestamp(2024, m, 1)
        rows += [
            ("SP", "a", "Homicídio doloso", stamp, 10), ("SP", "b", "Feminicídio", stamp, 1),
            ("BA", "c", "Roubo seguido de morte (latrocínio)", stamp, 1),
            ("BA", "d", "Estupro de vulnerável", stamp, 40), ("BA", "e", "Tentativa de homicídio", stamp, 99),
        ]
    return pd.DataFrame(rows, columns=["UF", "Município", "Evento", "data_referencia", "total_vitima"])


def test_standardize_and_indicators():
    std = standardize_sinesp(sample_sinesp())
    assert list(std.columns) == ["uf", "municipio", "evento", "mes_ano", "total_vitimas"]
    ind = sinesp_to_indicators(std)
    br_mvi = ind[(ind.indicador == "mvi_total") & (ind.uf == "BR")]["valor"].iloc[0]
    assert br_mvi == 12 * 11  # tentativa de homicídio não entra no MVI
    assert ind[(ind.indicador == "mvi_taxa") & (ind.uf == "SP")]["valor"].iloc[0] == pytest.approx(120 / 44_411_238 * 1e5)
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


def test_incomplete_year_is_dropped():
    df = sample_sinesp()
    df = df[df["data_referencia"].dt.month <= 6]
    assert sinesp_to_indicators(standardize_sinesp(df)).empty


def test_excel_serial_dates_and_year_only():
    df = pd.DataFrame({"uf": ["SP", "SP"], "evento": ["Homicídio doloso"] * 2, "mes_ano": [45292, 45323],
                       "total_vitimas": [1, 2]})
    std = standardize_sinesp(df)
    assert std["mes_ano"].tolist() == [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01")]
    assert standardize_sinesp(df.assign(mes_ano=[2023, 2023]))["mes_ano"].dt.year.tolist() == [2023, 2023]


# --- download do Sinesp VDE (sem rede: sessão falsa) ------------------------------------------------------------
import io  # noqa: E402

from src.ingestion.http_client import IngestionError as _IngErr  # noqa: E402
from src.ingestion.sinesp_download import aggregate_vde, download_year, sync_sinesp_vde  # noqa: E402


class _Resp:
    def __init__(self, status: int, body: bytes = b""):
        self.status_code, self._body = status, body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size: int):
        yield self._body


class _Session:
    def __init__(self, bodies: dict[int, tuple[int, bytes]]):
        self.bodies, self.headers_seen = bodies, []

    def get(self, url, headers=None, **kw):
        self.headers_seen.append(headers)
        year = int(url.rsplit("-", 1)[1].split(".")[0])
        return _Resp(*self.bodies.get(year, (404, b"")))


def _xlsx_bytes(year: int) -> bytes:
    df = sample_sinesp().assign(data_referencia=lambda d: d["data_referencia"].apply(lambda t: t.replace(year=year)))
    df = df.rename(columns={"UF": "uf", "Município": "municipio", "Evento": "evento"})
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


def test_download_rejects_404_and_non_xlsx(tmp_path):
    with pytest.raises(_IngErr, match="404"):
        download_year(2030, tmp_path / "x.xlsx", _Session({}))
    with pytest.raises(_IngErr, match="xlsx"):
        download_year(2024, tmp_path / "y.xlsx", _Session({2024: (200, b"<html>erro</html>")}))


def test_sync_sinesp_downloads_aggregates_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr("src.ingestion.sinesp_download.read_vde_xlsx", lambda p: pd.read_excel(p, engine="openpyxl"))
    sess = _Session({2023: (200, _xlsx_bytes(2023)), 2024: (200, _xlsx_bytes(2024))})
    rep = sync_sinesp_vde([2023, 2024, 2030], base=tmp_path, session=sess)
    assert [y.status for y in rep.years] == ["baixado", "baixado", "indisponível"]
    assert rep.combined_rows > 0 and (tmp_path / "sinesp_vde.parquet").exists()
    assert "Referer" in sess.headers_seen[0] and "Mozilla" in sess.headers_seen[0]["User-Agent"]
    assert "Tentativa de homicídio" not in set(pd.read_parquet(tmp_path / "sinesp_vde.parquet")["evento"])
    again = sync_sinesp_vde([2023, 2024], base=tmp_path, session=_Session({}))
    assert [y.status for y in again.years] == ["cache", "cache"]  # anos fechados não são rebaixados


def test_aggregate_requires_event_column():
    with pytest.raises(_IngErr):
        aggregate_vde(pd.DataFrame({"x": [1]}))


def test_iso_and_br_text_dates_parse_correctly():
    iso = standardize_sinesp(pd.DataFrame({"uf": ["SP"], "evento": ["Feminicídio"], "mes_ano": ["2024-02-01"], "total_vitimas": [1]}))
    assert iso["mes_ano"].iloc[0] == pd.Timestamp("2024-02-01")
    br = standardize_sinesp(pd.DataFrame({"uf": ["SP"], "evento": ["Feminicídio"], "mes_ano": ["03/2024"], "total_vitimas": [1]}))
    assert br["mes_ano"].iloc[0] == pd.Timestamp("2024-03-01")


def test_traffic_deaths_are_not_homicides():
    """Regressão: 'Morte no trânsito (exceto homicídio doloso)' continha o texto 'homicídio doloso'."""
    from src.ingestion.sinesp_loader import _event_class
    assert _event_class("Morte no trânsito ou em decorrência dele (exceto homicídio doloso)") is None
    assert _event_class("Tentativa de homicídio") is None and _event_class("Estupro") is None
    assert _event_class("Homicídio doloso") == "mvi_total"
    assert _event_class("Morte por intervenção de Agente do Estado") == "mvi_total"
    assert _event_class("Roubo seguido de morte (latrocínio)") == "mvi_total"
    assert _event_class("Morte de Agente do Estado") is None
