"""Aba "Agente Monitora Brasil" (Streamlit). Toda a lógica de segurança fica em ``agent.py``/``guards.py``/``limits.py``."""
from __future__ import annotations

import functools
import hashlib
import ipaddress
import logging
import os
import secrets
from pathlib import Path
from typing import Any

import streamlit as st

from src.agent.agent import DEFAULT_MODEL, MonitoraBrasilAgent
from src.agent.context import build_context
from src.agent.guards import MAX_INPUT_CHARS
from src.agent.limits import SESSION_LIMIT, Limits, RateLimiter
from src.ingestion.cache import FetchResult

log = logging.getLogger(__name__)

def _code_version() -> str:
    """Hash do código do agente: entra como argumento dos ``st.cache_resource`` para que um redeploy NUNCA reaproveite um
    objeto criado por uma versão antiga das classes (causava TypeError em produção ao mudar a assinatura do limitador)."""
    h = hashlib.sha256()
    for name in ("limits.py", "agent.py", "guards.py", "tools.py", "context.py", "prompts.py"):
        try:
            h.update((Path(__file__).with_name(name)).read_bytes())
        except OSError:
            h.update(name.encode())
    return h.hexdigest()[:12]


CODE_VERSION = _code_version()
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
def _build_limiter(version: str) -> RateLimiter:
    """Um único limitador por processo, compartilhado entre TODAS as sessões (é o que impede burlar recarregando a página)."""
    base = Limits()
    return RateLimiter(Limits(
        session=SESSION_LIMIT,  # fixo em 5: não é configurável
        client_daily=_int_secret("AGENT_CLIENT_DAILY", base.client_daily),
        global_daily=_int_secret("AGENT_GLOBAL_DAILY", base.global_daily),
        tokens_daily=_int_secret("AGENT_TOKENS_DAILY", base.tokens_daily),
        cooldown_s=float(_int_secret("AGENT_COOLDOWN_S", int(base.cooldown_s))),
    ))


def get_limiter() -> RateLimiter:
    return _build_limiter(CODE_VERSION)


def get_agent() -> MonitoraBrasilAgent | None:
    return _build_agent(CODE_VERSION)


@st.cache_resource(show_spinner=False)
def _build_agent(version: str) -> MonitoraBrasilAgent | None:
    key = _secret("OPENAI_API_KEY")
    if not key:
        return None
    from openai import OpenAI  # import tardio: o app abre mesmo sem o pacote/chave

    client = OpenAI(api_key=str(key), timeout=45.0, max_retries=1)
    return MonitoraBrasilAgent(client, get_limiter(), str(_secret("OPENAI_MODEL", DEFAULT_MODEL)))


def _client_ip() -> str | None:
    """IP PÚBLICO do visitante (só vira hash no limitador). Devolve ``None`` se não houver um confiável.

    Usa a entrada mais à DIREITA e pública de ``X-Forwarded-For`` (a que o proxy mais próximo do servidor acrescentou — o
    cliente só consegue acrescentar entradas à ESQUERDA, então forjar não muda o resultado), mais ``st.context.ip_address``.
    IPs privados/loopback (proxies internos, execução local) são ignorados: compartilhá-los bloquearia todo mundo junto.
    """
    try:
        headers = st.context.headers
        entries = [e.strip() for e in str(headers.get("X-Forwarded-For") or "").split(",") if e.strip()]
        direct = st.context.ip_address
    except Exception:
        return None
    candidates = [*entries, *([str(direct)] if direct else [])]
    for raw in reversed(candidates):
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if ip.is_global:
            return str(ip)
    return None


def _debug_line(limiter: RateLimiter, sid: str, ip: str | None) -> str:
    st_ = limiter.stats()
    return (f"IP público detectado: {'sim' if ip else 'NÃO (limite só por sessão)'} · perguntas restantes: "
            f"{limiter.remaining(sid, ip)} · perguntas hoje no app: {st_['perguntas_hoje']}")


def _guarded(fallback: Any = None, message: str | None = None):
    """Isola o agente do resto do painel: qualquer falha vira log + (opcional) aviso discreto, nunca erro de página.

    ``st.rerun`` levanta uma exceção de controle (BaseException) que passa por aqui sem ser capturada.
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                log.error("falha no agente (%s): %s: %s", fn.__name__, type(exc).__name__, exc)
                if message:
                    st.info(message, icon="ℹ️")
                return fallback
        return wrapper
    return deco


_UNAVAILABLE = "O agente está temporariamente indisponível. Os gráficos e dados do painel continuam funcionando normalmente."


def _esc(text: str) -> str:
    return text.replace("$", "\\$")  # evita que "R$ 1 ... R$ 2" vire fórmula LaTeX


def _sid() -> str:
    st.session_state.setdefault("agent_sid", secrets.token_hex(16))
    st.session_state.setdefault("agent_msgs", [])
    return st.session_state["agent_sid"]


def _render_chat(data: dict[str, FetchResult], where: str) -> None:
    """Chat compartilhado pela aba (``where="tab"``) e pela janela da barra lateral (``where="dlg"``).

    O histórico, o contador e os limites vivem em ``session_state``/``RateLimiter`` e são os mesmos nos dois lugares;
    só as chaves dos widgets mudam para não colidirem quando ambos existem na mesma execução.
    """
    in_dialog = where == "dlg"
    refresh = (lambda: st.rerun(scope="fragment")) if in_dialog else st.rerun
    if not agent_enabled():
        st.info("O agente está temporariamente indisponível. Os gráficos e dados do painel continuam funcionando normalmente.",
                icon="ℹ️")
        return
    agent, limiter = get_agent(), get_limiter()
    if agent is None:
        st.info("O agente está temporariamente indisponível.", icon="ℹ️")
        return

    sid, ip = _sid(), _client_ip()
    remaining = limiter.remaining(sid, ip)
    if str(_secret("AGENT_DEBUG", "false")).lower() in ("true", "1"):  # diagnóstico opcional, só com AGENT_DEBUG=true
        st.caption(_debug_line(limiter, sid, ip))
    st.progress(remaining / SESSION_LIMIT, text=f"Perguntas restantes nesta sessão: **{remaining} de {SESSION_LIMIT}**")

    pending: str | None = st.session_state.pop(f"agent_pending_{where}", None)
    if remaining > 0 and not st.session_state["agent_msgs"]:
        cols = st.columns(len(SUGGESTIONS))
        for col, text in zip(cols, SUGGESTIONS, strict=True):
            if col.button(text, key=f"sug_{where}_{text}", width="stretch"):
                st.session_state[f"agent_pending_{where}"] = text
                refresh()

    for msg in st.session_state["agent_msgs"]:
        with st.chat_message(msg["role"], avatar="🧑" if msg["role"] == "user" else "🤖"):
            st.markdown(_esc(msg["text"]))
            if msg.get("note"):
                st.caption(msg["note"])

    typed: str | None = None
    if in_dialog:  # chat_input não é fixado dentro de janelas: usa formulário
        with st.form("agent_form_dlg", clear_on_submit=True, border=False):
            box = st.text_input("Sua pergunta", max_chars=MAX_INPUT_CHARS, disabled=remaining == 0,
                                placeholder=f"Pergunte sobre os dados do painel (até {MAX_INPUT_CHARS} caracteres)",
                                label_visibility="collapsed")
            if st.form_submit_button("Perguntar", type="primary", disabled=remaining == 0):
                typed = box
    else:
        typed = st.chat_input(f"Pergunte sobre os dados do painel (até {MAX_INPUT_CHARS} caracteres)",
                              max_chars=MAX_INPUT_CHARS, disabled=remaining == 0)

    question = pending or typed
    if question:
        st.session_state["agent_msgs"].append({"role": "user", "text": question[:MAX_INPUT_CHARS]})
        with st.spinner("Consultando os dados do painel…"):
            reply = agent.ask(question, build_context(data), sid, ip)
        note = {"ok": "Resposta gerada por IA com base nos dados do painel.", "limite": "Limite de uso atingido."}.get(reply.status, "")
        st.session_state["agent_msgs"].append({"role": "assistant", "text": reply.text, "note": note})
        refresh()

    if remaining == 0:
        st.warning("Você usou todas as perguntas disponíveis para este acesso hoje. Os gráficos continuam disponíveis para explorar!", icon="🔒")


@_guarded(None, _UNAVAILABLE)
def render_agent_tab(data: dict[str, FetchResult]) -> None:
    st.subheader("🤖 Agente Monitora Brasil")
    st.caption("Pergunte sobre os **dados e gráficos deste painel**. As respostas são geradas por IA a partir dos números "
               "carregados aqui — sempre confira a fonte indicada. O agente não responde sobre outros assuntos.")
    _render_chat(data, "tab")


@st.dialog("🤖 Agente Monitora Brasil", width="large", on_dismiss="rerun")
def _agent_dialog(data: dict[str, FetchResult]) -> None:
    st.caption("Pergunte sobre os **dados e gráficos deste painel**. Respostas geradas por IA a partir dos números "
               "carregados aqui — confira sempre a fonte. Não respondo sobre outros assuntos.")
    _render_chat(data, "dlg")


SIDEBAR_CSS = """
<style>
  section[data-testid="stSidebar"] button[kind="primary"] {
    background: linear-gradient(135deg, #2B6CB0 0%, #1B9E77 100%); border: 0; color: #fff;
    font-weight: 700; font-size: 1.02rem; padding: .85rem .6rem; border-radius: 12px;
    box-shadow: 0 4px 14px rgba(43,108,176,.35); transition: transform .12s ease, box-shadow .12s ease;
  }
  section[data-testid="stSidebar"] button[kind="primary"]:hover { transform: translateY(-1px); box-shadow: 0 6px 18px rgba(43,108,176,.5); }
  .mb-agent-hint { color:#4A5568; font-size:.82rem; margin:.15rem 0 .9rem; }
</style>
"""


@_guarded(False)
def sidebar_agent_button() -> bool:
    """Botão em destaque no topo da barra lateral. Devolve ``True`` no clique (a janela é aberta por ``open_agent``)."""
    st.markdown(SIDEBAR_CSS, unsafe_allow_html=True)
    left = limiter_left() if agent_enabled() else None
    label = "🤖 Perguntar ao Agente de IA" + (f"  ·  {left}/{SESSION_LIMIT}" if left is not None else "")
    clicked = st.button(label, key="open_agent", type="primary", width="stretch",
                        help="Tire dúvidas sobre os dados e gráficos do painel (até 5 perguntas por sessão).")
    st.markdown("<div class='mb-agent-hint'>Ex.: “Como está a segurança pública?” · “Como está o PIB?”</div>",
                unsafe_allow_html=True)
    return clicked


def limiter_left() -> int:
    return get_limiter().remaining(_sid(), _client_ip())


@_guarded(None, _UNAVAILABLE)
def open_agent(data: dict[str, FetchResult]) -> None:
    _agent_dialog(data)
