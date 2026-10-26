"""Agente Monitora Brasil: orquestra guardas → limites → modelo (Responses API + function calling) → sanitização.

Fluxo de uma pergunta:
1. ``check_question``  — limpa e bloqueia injeção/abuso sem gastar nada.
2. ``RateLimiter.acquire`` — reserva a pergunta (sessão, IP, global, intervalo).
3. Loop do modelo — até ``MAX_ROUNDS`` rodadas e ``MAX_TOOL_CALLS`` chamadas; ferramentas somente-leitura.
4. Exige ao menos uma consulta de dados: resposta "solta" (sem ferramenta) vira recusa padrão.
5. ``sanitize_answer`` — remove links/HTML/código e descarta vazamentos (canário, chave).
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any

from src.agent.context import DataContext
from src.agent.guards import INJECTION_REFUSAL, REFUSAL, check_question, sanitize_answer
from src.agent.limits import RateLimiter
from src.agent.prompts import build_instructions, wrap_question
from src.agent.tools import TOOL_SCHEMAS, run_tool

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5-mini"
MAX_ROUNDS = 5
MAX_TOOL_CALLS = 8
REQUEST_TIMEOUT_S = 45.0
GENERIC_ERROR = "Não consegui consultar o agente agora. Tente novamente em instantes."


@dataclass
class AgentReply:
    status: str                      # ok | recusa | injecao | limite | invalida | erro | desativado
    text: str
    remaining: int = 0
    tools_used: list[str] = field(default_factory=list)
    tokens: int = 0

    @property
    def consumed(self) -> bool:
        return self.status in ("ok", "recusa", "injecao", "erro")


def model_kwargs(model: str) -> dict[str, Any]:
    """Parâmetros por família: modelos de raciocínio não aceitam temperature."""
    if model.startswith(("gpt-5", "o1", "o3", "o4")):
        kw: dict[str, Any] = {"reasoning": {"effort": "low"}, "max_output_tokens": 2500}
        if model.startswith("gpt-5"):
            kw["text"] = {"verbosity": "low"}
        return kw
    return {"temperature": 0.2, "max_output_tokens": 900}


class MonitoraBrasilAgent:
    def __init__(self, client: Any, limiter: RateLimiter, model: str = DEFAULT_MODEL):
        self.client, self.limiter, self.model = client, limiter, model
        self._canary = "MB-" + secrets.token_hex(6)  # sentinela de vazamento do prompt

    # -------------------------------------------------------------------------------------------------------
    def ask(self, question: str | None, ctx: DataContext, session_id: str, client_id: str | None = None) -> AgentReply:
        guard = check_question(question)
        if not guard.ok and guard.reason in ("vazia", "longa"):  # erros de digitação não gastam pergunta
            return AgentReply("invalida", guard.message, self.limiter.remaining(session_id))

        decision = self.limiter.acquire(session_id, client_id)
        if not decision.allowed:
            return AgentReply("limite", decision.message, self.limiter.remaining(session_id))

        tokens = 0
        try:
            if not guard.ok:  # injeção: recusa local, sem chamar a OpenAI (mas conta na cota)
                return AgentReply("injecao", INJECTION_REFUSAL, self.limiter.remaining(session_id))
            text, tools_used, tokens = self._run_model(guard.text, ctx, session_id)
            clean = sanitize_answer(text, self._canary) if tools_used else None
            if clean is None or text.strip() == REFUSAL:
                return AgentReply("recusa", REFUSAL, self.limiter.remaining(session_id), tools_used, tokens)
            return AgentReply("ok", clean, self.limiter.remaining(session_id), tools_used, tokens)
        except Exception as exc:  # nunca expor detalhes (podem conter cabeçalhos)
            log.warning("agente falhou: %s", type(exc).__name__)
            return AgentReply("erro", GENERIC_ERROR, self.limiter.remaining(session_id), tokens=tokens)
        finally:
            self.limiter.release(session_id, tokens)

    # -------------------------------------------------------------------------------------------------------
    def _run_model(self, question: str, ctx: DataContext, session_id: str) -> tuple[str, list[str], int]:
        instructions = build_instructions(self._canary)
        safety_id = hashlib.sha256(session_id.encode()).hexdigest()[:32]
        input_items: list[Any] = [{"role": "user", "content": wrap_question(question)}]
        previous_id: str | None = None
        tools_used: list[str] = []
        tokens = 0
        kw = model_kwargs(self.model)

        for _ in range(MAX_ROUNDS):
            resp = self.client.responses.create(
                model=self.model, instructions=instructions, input=input_items, previous_response_id=previous_id,
                tools=TOOL_SCHEMAS, tool_choice="auto", parallel_tool_calls=True, store=True,
                safety_identifier=safety_id, timeout=REQUEST_TIMEOUT_S, **kw,
            )
            tokens += int(getattr(getattr(resp, "usage", None), "total_tokens", 0) or 0)
            calls = [it for it in resp.output if getattr(it, "type", "") == "function_call"]
            if not calls:
                return (getattr(resp, "output_text", "") or "").strip(), tools_used, tokens
            if len(tools_used) + len(calls) > MAX_TOOL_CALLS:
                break
            input_items = []
            for call in calls:
                tools_used.append(call.name)
                input_items.append({"type": "function_call_output", "call_id": call.call_id,
                                    "output": run_tool(ctx, call.name, call.arguments)})
            previous_id = resp.id
        return "", tools_used, tokens
