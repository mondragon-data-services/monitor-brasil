"""TabNet/DATASUS (Ministério da Saúde): leitos hospitalares por estado (CNES) e cobertura vacinal por estado (SI-PNI).

O TabNet não tem API: cada consulta é um POST de formulário. O fluxo é: ler o formulário (``GET``), montar o POST com as
opções desejadas (valores EXATOS lidos do próprio formulário) e interpretar a resposta.

* **CNES — leitos** (``cnes/cnv/leiintbr.def``, POST em ``tabcgi.exe``): a resposta traz um bloco ``<PRE>`` em CSV
  (``;``), com entidades HTML nos nomes; a última linha é o total do Brasil. Há dado até o mês corrente.
* **SI-PNI — cobertura vacinal** (``bd_pni/cpnibr.def``, POST em ``webtabx.exe``): a resposta traz o link de um CSV com o
  resultado (decimal com vírgula). **Os dados vão só até 2022** (última atualização maio/2023); a partir de 2023 o Ministério
  publica apenas num painel Qlik sem API. A linha/coluna ``Total`` não deve ser usada (soma de percentuais).
"""
from __future__ import annotations

import csv
import html
import io
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote_from_bytes

import pandas as pd
import requests

from src.config import UF_BY_IBGE_CODE
from src.ingestion.cache import FetchResult, with_freshness
from src.ingestion.http_client import IngestionError

log = logging.getLogger(__name__)
TABNET = "http://tabnet.datasus.gov.br/cgi/"
LEITOS_DEF = "cnes/cnv/leiintbr.def"
PNI_DEF = "bd_pni/cpnibr.def"
ALL_CATEGORIES = "TODAS_AS_CATEGORIAS__"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
           "Accept-Language": "pt-BR,pt;q=0.9"}
PNI_VACCINES: dict[str, str] = {"triplice_viral": "Tríplice Viral D1", "polio": "Poliomielite", "penta": "Penta"}
LEITOS_COLUMNS = ["date", "uf", "existentes", "sus"]
PNI_COLUMNS = ["date", "uf", "vacina", "value"]


class _Form(HTMLParser):
    """Extrai ``<select>`` (com opções) e ``<input>`` de uma página do TabNet."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.selects: dict[str, list[dict]] = {}
        self.inputs: list[dict] = []
        self._cur: str | None = None
        self._opt: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): v for k, v in attrs}
        if tag == "select":
            self._cur = a.get("name")
            self.selects.setdefault(self._cur, [])
        elif tag == "option" and self._cur is not None:
            self._opt = {"value": a.get("value"), "text": ""}
            self.selects[self._cur].append(self._opt)
        elif tag == "input":
            self.inputs.append(a)

    def handle_data(self, data):
        if self._opt is not None:
            self._opt["text"] += data

    def handle_endtag(self, tag):
        if tag == "select":
            self._cur = self._opt = None


def parse_form(page: str) -> _Form:
    parser = _Form()
    parser.feed(page)
    return parser


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def encode_form(pairs: list[tuple[str, str]]) -> bytes:
    """Corpo ``application/x-www-form-urlencoded`` em latin-1 (o TabNet não entende UTF-8)."""
    return "&".join(f"{quote_from_bytes(k.encode('latin-1'))}={quote_from_bytes(v.encode('latin-1'))}" for k, v in pairs).encode()


def _get_page(session: requests.Session, url: str) -> str:
    try:
        resp = session.get(url, headers=HEADERS, timeout=(10, 120))
    except requests.RequestException as exc:
        raise IngestionError(f"falha de rede no TabNet: {exc}") from exc
    if resp.status_code >= 400:
        raise IngestionError(f"HTTP {resp.status_code} no TabNet")
    return resp.content.decode("latin-1")


def _post(session: requests.Session, endpoint: str, form_url: str, pairs: list[tuple[str, str]]) -> str:
    try:
        resp = session.post(TABNET + endpoint, data=encode_form(pairs), timeout=(10, 180),
                            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded", "Referer": form_url})
    except requests.RequestException as exc:
        raise IngestionError(f"falha de rede no TabNet: {exc}") from exc
    if resp.status_code >= 400:
        raise IngestionError(f"HTTP {resp.status_code} no POST do TabNet")
    return resp.content.decode("latin-1")


def _uf_of(label: str) -> str | None:
    """``"11 Rondônia"`` -> ``RO``; ``"Total"`` -> ``BR``; linhas desconhecidas -> ``None``."""
    label = html.unescape(label).strip().strip('"')
    if label.lower().startswith("total"):
        return "BR"
    m = re.match(r"^(\d{2})\b", label)
    return UF_BY_IBGE_CODE.get(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# CNES — leitos hospitalares
# --------------------------------------------------------------------------- #
def parse_leitos_pre(body: str) -> pd.DataFrame:
    """Bloco ``<PRE>`` CSV do TabNet -> DataFrame largo ``uf, date, value`` (período no formato ``2025/Dez`` / ``2026/Set``)."""
    pre = re.search(r"<PRE>(.*?)</PRE>", body, re.S | re.I)
    if not pre:
        raise IngestionError("resposta do CNES sem tabela (<PRE>)")
    rows = list(csv.reader(io.StringIO(html.unescape(pre.group(1)).strip()), delimiter=";"))
    if len(rows) < 3:
        raise IngestionError("tabela do CNES vazia")
    months = {"Jan": 1, "Fev": 2, "Mar": 3, "Abr": 4, "Mai": 5, "Jun": 6, "Jul": 7, "Ago": 8, "Set": 9, "Out": 10, "Nov": 11, "Dez": 12}
    dates = []
    for h in rows[0][1:]:
        m = re.match(r"^(\d{4})/(\w{3})", h.strip())
        dates.append(pd.Timestamp(int(m.group(1)), months.get(m.group(2).capitalize(), 12), 1) if m else None)
    out = []
    for row in rows[1:]:
        uf = _uf_of(row[0]) if row else None
        if uf is None:
            continue
        for d, v in zip(dates, row[1:], strict=False):
            if d is not None and v.strip() not in ("", "-"):
                out.append({"uf": uf, "date": d, "value": float(v.replace(".", "").replace(",", "."))})
    if not out:
        raise IngestionError("tabela do CNES sem valores")
    return pd.DataFrame(out)


def fetch_leitos(session: requests.Session | None = None, first_year: int = 2011) -> pd.DataFrame:
    """Leitos existentes e SUS por UF: um valor por dezembro desde ``first_year`` + o mês mais recente."""
    sess = session or requests.Session()
    form_url = TABNET + "deftohtm.exe?" + LEITOS_DEF
    form = parse_form(_get_page(sess, form_url))
    period_col = next((o["value"] for o in form.selects.get("Coluna", []) if o["value"].startswith("Ano")), None)
    files = [o["value"] for o in form.selects.get("Arquivos", [])]
    if period_col is None or not files:
        raise IngestionError("formulário do CNES mudou (colunas/arquivos não encontrados)")
    latest = files[0]
    chosen = [f for f in files if f == latest or (f[6:8] == "12" and 2000 + int(f[4:6]) >= first_year)]
    frames = {}
    for key, increment in (("existentes", "Quantidade_existente"), ("sus", "Quantidade_SUS")):
        pairs = [("Linha", "Unidade_da_Federação"), ("Coluna", period_col), ("Incremento", increment)] + [("Arquivos", f) for f in chosen]
        pairs += [(name, ALL_CATEGORIES) for name in form.selects if name and name.startswith("S")]
        pairs += [("formato", "prn"), ("mostre", "Mostra")]
        frames[key] = parse_leitos_pre(_post(sess, "tabcgi.exe?" + LEITOS_DEF, form_url, pairs)).rename(columns={"value": key})
    merged = frames["existentes"].merge(frames["sus"], on=["uf", "date"], how="outer").sort_values(["uf", "date"])
    return merged[LEITOS_COLUMNS].reset_index(drop=True)


def get_leitos(base: Path | None = None, session: requests.Session | None = None) -> FetchResult:
    return with_freshness("tabnet_leitos", lambda: fetch_leitos(session), 7, base)  # o CNES atualiza todo mês


# --------------------------------------------------------------------------- #
# SI-PNI — cobertura vacinal por UF e ano
# --------------------------------------------------------------------------- #
def parse_pni_csv(text: str, vaccine_key: str) -> pd.DataFrame:
    """CSV do TabNet (``;``, decimal com vírgula; linha ``Unidade da Federação`` + colunas de ano) -> ``date, uf, vacina, value``."""
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    if len(rows) < 2:
        raise IngestionError("CSV de cobertura vacinal vazio")
    years = [(i, h.strip()) for i, h in enumerate(rows[0]) if re.fullmatch(r"\d{4}", h.strip())]
    out = []
    for row in rows[1:]:
        uf = _uf_of(row[0]) if row else None
        if uf is None or uf == "BR":  # a linha Total soma percentuais: não usar
            continue
        for i, y in years:
            raw = row[i].strip() if i < len(row) else ""
            if raw in ("", "-"):
                continue
            value = float(raw.replace(".", "").replace(",", "."))
            if value > 0:  # 0 = ano sem dado/vacina ainda não existia
                out.append({"date": pd.Timestamp(int(y), 1, 1), "uf": uf, "vacina": vaccine_key, "value": value})
    if not out:
        raise IngestionError("CSV de cobertura vacinal sem valores")
    return pd.DataFrame(out)


def fetch_pni(session: requests.Session | None = None) -> pd.DataFrame:
    sess = session or requests.Session()
    form_url = TABNET + "dhdat.exe?" + PNI_DEF
    form = parse_form(_get_page(sess, form_url))
    line = next((o["value"] for o in form.selects.get("Linha", []) if _norm(o["text"]) == "Unidade da Federação"), None)
    column = next((o["value"] for o in form.selects.get("Coluna", []) if _norm(o["text"]) == "Ano"), None)
    years = [o["value"] for o in form.selects.get("PAno", [])]
    hidden = {i.get("name"): i.get("value") for i in form.inputs if (i.get("type") or "").lower() == "hidden"}
    if not (line and column and years):
        raise IngestionError("formulário do SI-PNI mudou")
    frames = []
    for key, label in PNI_VACCINES.items():
        increment = next((o["value"] for o in form.selects.get("Incremento", []) if _norm(o["text"]) == label), None)
        if increment is None:
            raise IngestionError(f"vacina {label!r} não está no formulário do SI-PNI")
        pairs = [("Linha", line), ("Coluna", column), ("Incremento", increment)] + [("PAno", y) for y in years]
        pairs += [(name, opts[0]["value"] if opts else ALL_CATEGORIES) for name, opts in form.selects.items() if name and name.startswith("S")]
        pairs += [("nomedef", hidden.get("nomedef") or PNI_DEF), ("formato", "prn"), ("mostre", "Mostra")]
        page = _post(sess, "webtabx.exe?" + PNI_DEF, form_url, pairs)
        link = re.search(r"HREF=(csv/[^>\s]+\.csv)", page, re.I)
        if not link:
            raise IngestionError("resposta do SI-PNI sem link de CSV")
        csv_text = _get_page(sess, TABNET + link.group(1))
        frames.append(parse_pni_csv(csv_text, key))
    return pd.concat(frames, ignore_index=True).sort_values(["vacina", "uf", "date"]).reset_index(drop=True)


def get_pni(base: Path | None = None, session: requests.Session | None = None) -> FetchResult:
    return with_freshness("tabnet_pni", lambda: fetch_pni(session), 365, base)  # série encerrada em 2022: quase estática
