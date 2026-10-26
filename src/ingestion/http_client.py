"""Sessão HTTP resiliente: Retry com backoff exponencial, timeout e User-Agent customizado."""
from __future__ import annotations

import logging
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import HTTP_HEADERS, HTTP_TIMEOUT

log = logging.getLogger(__name__)


class IngestionError(RuntimeError):
    """Falha ao obter ou interpretar dados de uma fonte externa."""


def build_session(total_retries: int = 4, backoff_factor: float = 1.0) -> requests.Session:
    """Cria uma ``requests.Session`` com retry/backoff exponencial para erros transitórios."""
    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        status=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HTTP_HEADERS)
    return session


def get_json(
    url: str,
    session: requests.Session | None = None,
    *,
    json_attempts: int = 3,
    sleep: float = 1.5,
) -> Any:
    """GET que devolve JSON.

    Algumas APIs governamentais (BCB) respondem ``HTTP 200`` com uma página HTML/XML de
    "Requisição inválida" em instabilidades. Isso é tratado aqui como erro transitório e
    repetido com backoff, além do retry HTTP da sessão.
    """
    sess = session or build_session()
    last_error: Exception | None = None
    for attempt in range(1, json_attempts + 1):
        try:
            resp = sess.get(url, timeout=HTTP_TIMEOUT)
            if resp.status_code >= 400:
                raise IngestionError(f"HTTP {resp.status_code} em {url}")
            return resp.json()
        except (requests.RequestException, ValueError, IngestionError) as exc:
            last_error = exc
            log.warning("tentativa %d/%d falhou (%s): %s", attempt, json_attempts, url, exc)
            if attempt < json_attempts:
                time.sleep(sleep * 2 ** (attempt - 1))
    raise IngestionError(f"falha ao obter JSON de {url}: {last_error}") from last_error
