"""Aba "Agente Monitora Brasil" (Streamlit). Toda a lógica de segurança fica em ``agent.py``/``guards.py``/``limits.py``."""
from __future__ import annotations

import ipaddress
import logging
import os
import secrets
from typing import Any

import streamlit as st

from src.agent.agent import DEFAULT_MODEL, MonitoraBrasilAgent
from src.agent.context import build_context
from src.agent.guards import MAX_INPUT_CHARS
from src.agent.limits import SESSION_LIMIT, Limits, RateLimiter
from src.ingestion.cache import FetchResult

log = logging.getLogger(__name__)

SUGGESTIONS = ("Como está a segurança pública?", "Como está o endividamento?", "Como está o PIB?")


def _secret(name: str, default: Any = None) -> Any:
    """Lê de ``st.secrets`` (Streamlit Cloud / secrets.toml) com fallback para variável de ambiente. Nunca loga o valor."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:  # sem secrets.toml
        pass
    return os.environ.get(name, default)


def _int_secret(name: str, default: int) -> int:
    try:
        return max(0, int(_secret(name, default)))
    except (TypeError, ValueError):
        return default


def agent_enabled() -> bool:
    flag = str(_secret("AGENT_ENABLED", "true")).strip().lower()
    return bool(_secret("OPENAI_API_KEY")) and flag not in ("false", "0", "no", "off")


@st.cache_resource(show_spinner=False)
def get_limiter() -> RateLimiter:
    """Um único limitador por processo, compartilhado entre TODAS as sessões (é o que impede burlar recarregando a página)."""
    base = Limits()
    return RateLimiter(Limits(
        session=SESSION_LIMIT,  # fixo em 5: não é configurável
        client_daily=_int_secret("AGENT_CLIENT_DAILY", base.client_daily),
        global_daily=_int_secret("AGENT_GLOBAL_DAILY", base.global_daily),
        tokens_daily=_int_secret("AGENT_TOKENS_DAILY", base.tokens_daily),
        cooldown_s=float(_int_secret("AGENT_COOLDOWN_S", int(base.cooldown_s))),
    ))


@st.cache_resource(show_spinner=False)
def get_agent() -> MonitoraBrasilAgent | None:
    key = _secret("OPENAI_API_KEY")
    if not key:
        return None
    from openai import OpenAI  # import tardio: o app abre mesmo sem o pacote/chave

    client = OpenAI(api_key=str(key), timeout=45.0, max_retries=1)
    return MonitoraBrasilAgent(client, get_limiter(), str(_secret("OPENAI_MODEL", DEFAULT_MODEL)))


def _client_ip() -> str | None:
    """IP do visitante (só para o limite por cliente; guardado apenas como hash). Cabeçalho inválido é ignorado."""
    try:
        raw = st.context.headers.get("X-Forwarded-For") or st.context.ip_address
    except Exception:
        return None
    if not raw:
        return None
    candidate = str(raw).split(",")[0].strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _esc(text: str) -> str:
    return text.replace("$", "\\$")  # evita que "R$ 1 ... R$ 2" vire fórmula LaTeX


def render_agent_tab(data: dict[str, FetchResult]) -> None:
    st.subheader("🤖 Agente Monitora Brasil")
    st.caption("Pergunte sobre os **dados e gráficos deste painel**. As respostas são geradas por IA a partir dos números "
               "carregados aqui — sempre confira a fonte indicada. O agente não responde sobre outros assuntos.")

    if not agent_enabled():
        st.info("O agente está temporariamente indisponível. Os gráficos e dados do painel continuam funcionando normalmente.",
                icon="ℹ️")
        return
    agent, limiter = get_agent(), get_limiter()
    if agent is None:
        st.info("O agente está temporariamente indisponível.", icon="ℹ️")
        return

    st.session_state.setdefault("agent_sid", secrets.token_hex(16))
    st.session_state.setdefault("agent_msgs", [])
    sid = st.session_state["agent_sid"]
    remaining = limiter.remaining(sid)

    top = st.columns([3, 1])
    top[0].progress(remaining / SESSION_LIMIT, text=f"Perguntas restantes nesta sessão: **{remaining} de {SESSION_LIMIT}**")

    pending: str | None = st.session_state.pop("agent_pending", None)
    if remaining > 0 and not st.session_state["agent_msgs"]:
        cols = st.columns(len(SUGGESTIONS))
        for col, text in zip(cols, SUGGESTIONS, strict=True):
            if col.button(text, key=f"sug_{text}", width="stretch"):
                st.session_state["agent_pending"] = text
                st.rerun()

    for msg in st.session_state["agent_msgs"]:
        with st.chat_message(msg["role"], avatar="🧑" if msg["role"] == "user" else "🤖"):
            st.markdown(_esc(msg["text"]))
            if msg.get("note"):
                st.caption(msg["note"])

    typed = st.chat_input(f"Pergunte sobre os dados do painel (até {MAX_INPUT_CHARS} caracteres)",
                          max_chars=MAX_INPUT_CHARS, disabled=remaining == 0)
    question = pending or typed
    if question:
        st.session_state["agent_msgs"].append({"role": "user", "text": question[:MAX_INPUT_CHARS]})
        with st.spinner("Consultando os dados do painel…"):
            reply = agent.ask(question, build_context(data), sid, _client_ip())
        note = {"ok": "Resposta gerada por IA com base nos dados do painel.", "limite": "Limite de uso atingido.",
                "invalida": "", "injecao": "", "recusa": "", "erro": ""}.get(reply.status, "")
        st.session_state["agent_msgs"].append({"role": "assistant", "text": reply.text, "note": note})
        st.rerun()

    if remaining == 0:
        st.warning(f"Você usou as {SESSION_LIMIT} perguntas desta sessão. Os gráficos continuam disponíveis para explorar!", icon="🔒")
