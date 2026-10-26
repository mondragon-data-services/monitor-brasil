"""Limitador de uso em camadas para proteger os créditos da OpenAI (thread-safe, sem dependências externas).

Camadas (todas checadas ANTES de chamar o modelo; a reserva é atômica e conta mesmo se a chamada falhar):
1. Sessão: no máximo ``session`` perguntas (5).
2. Cliente (IP, só o hash com sal): ``client_daily`` perguntas por dia (padrão 5). É esta camada que impede burlar o limite
   com F5/nova aba: o contador acompanha o IP, não a sessão. Sem IP público detectável, só valem as demais camadas.
3. Global: ``global_daily`` perguntas por dia e ``tokens_daily`` tokens por dia para o app inteiro (disjuntor de custo).
4. Intervalo mínimo (``cooldown_s``) entre perguntas e uma única pergunta em andamento por sessão (anti-flood).

Limite honesto: os contadores vivem na memória do processo e zeram se o app reiniciar. A proteção definitiva de
dinheiro é o limite de gasto do projeto no painel da OpenAI (veja o README).
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SESSION_LIMIT = 5  # regra de negócio fixa: não é configurável para cima
_BRT = timezone(timedelta(hours=-3))


@dataclass(frozen=True)
class Limits:
    session: int = SESSION_LIMIT
    client_daily: int = SESSION_LIMIT
    global_daily: int = 200
    tokens_daily: int = 800_000
    cooldown_s: float = 8.0
    max_tracked: int = 5000


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""          # session | client | global | tokens | cooldown | busy
    message: str = ""
    retry_after: float = 0.0


class RateLimiter:
    def __init__(self, limits: Limits | None = None, clock: Callable[[], float] = time.time):
        self.limits = limits or Limits()
        self._clock = clock
        self._lock = threading.Lock()
        self._salt = secrets.token_hex(8)
        self._sessions: OrderedDict[str, dict] = OrderedDict()
        self._clients: OrderedDict[str, dict] = OrderedDict()
        self._day = self._today()
        self._global_count = 0
        self._tokens = 0

    # -- helpers ---------------------------------------------------------------------------------------------
    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock(), _BRT).strftime("%Y-%m-%d")

    def _roll_day(self) -> None:
        today = self._today()
        if today != self._day:
            self._day, self._global_count, self._tokens = today, 0, 0
            self._clients.clear()

    def hash_client(self, client_id: str | None) -> str | None:
        """Só o hash (com sal por processo) do IP fica em memória."""
        return hashlib.sha256(f"{self._salt}:{client_id}".encode()).hexdigest()[:16] if client_id else None

    @staticmethod
    def _trim(store: OrderedDict, limit: int) -> None:
        while len(store) > limit:
            store.popitem(last=False)

    # -- API -------------------------------------------------------------------------------------------------
    def remaining(self, session_id: str, client_id: str | None = None) -> int:
        """Perguntas que ainda podem ser feitas: o menor entre o saldo da sessão e o do IP (quando há IP)."""
        with self._lock:
            self._roll_day()
            left = self.limits.session - self._sessions.get(session_id, {}).get("used", 0)
            client = self.hash_client(client_id)
            if client and self.limits.client_daily:
                left = min(left, self.limits.client_daily - self._clients.get(client, {}).get("n", 0))
            return max(0, left)

    def acquire(self, session_id: str, client_id: str | None) -> Decision:
        """Reserva UMA pergunta (atômico). Se negada, nada é consumido."""
        now, lim = self._clock(), self.limits
        with self._lock:
            self._roll_day()
            sess = self._sessions.setdefault(session_id, {"used": 0, "last": 0.0, "busy": False})
            self._sessions.move_to_end(session_id)
            self._trim(self._sessions, lim.max_tracked)
            client = self.hash_client(client_id)

            if sess["busy"]:
                return Decision(False, "busy", "Aguarde a resposta anterior antes de perguntar de novo.", 2.0)
            if sess["used"] >= lim.session:
                return Decision(False, "session", f"Você usou as {lim.session} perguntas desta sessão. Obrigado por usar o agente!")
            last = max(sess["last"], self._clients.get(client, {}).get("last", 0.0) if client else 0.0)
            wait = last + lim.cooldown_s - now
            if wait > 0:
                return Decision(False, "cooldown", f"Aguarde {int(wait) + 1} s para a próxima pergunta.", wait)
            if client and lim.client_daily and self._clients.get(client, {}).get("n", 0) >= lim.client_daily:
                return Decision(False, "client", f"Você já usou as {lim.client_daily} perguntas diárias deste acesso. "
                                                 "Volte amanhã — os gráficos continuam disponíveis!")
            if self._global_count >= lim.global_daily or self._tokens >= lim.tokens_daily:
                reason = "global" if self._global_count >= lim.global_daily else "tokens"
                return Decision(False, reason, "O agente atingiu o limite diário de uso do painel. Volte amanhã.")

            sess["used"] += 1
            sess["last"], sess["busy"] = now, True
            self._global_count += 1
            if client:
                rec = self._clients.setdefault(client, {"n": 0, "last": 0.0})
                rec["n"] += 1
                rec["last"] = now
                self._clients.move_to_end(client)
                self._trim(self._clients, lim.max_tracked)
            return Decision(True)

    def release(self, session_id: str, tokens: int = 0) -> None:
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id]["busy"] = False
            self._tokens += max(0, int(tokens))

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"perguntas_hoje": self._global_count, "tokens_hoje": self._tokens, "sessoes": len(self._sessions)}
