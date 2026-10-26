"""Defesas determinísticas (sem LLM) contra injeção de prompt, abuso e vazamento. Rodam ANTES e DEPOIS do modelo."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MAX_INPUT_CHARS = 400
MAX_OUTPUT_CHARS = 2400

REFUSAL = ("Sou o Agente Monitora Brasil e só respondo sobre os dados e gráficos deste painel: inflação, juros, dívida, "
           "emprego, renda, PIB, segurança pública e educação. Tente, por exemplo: “Como está a segurança pública?” "
           "ou “Como está o endividamento?”.")
INJECTION_REFUSAL = ("Não posso atender a esse tipo de pedido. Posso responder perguntas sobre os indicadores e gráficos do "
                     "painel — por exemplo: “Como está o PIB?”.")

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­‪‫‬‭‮⁦⁧⁨⁩"))
_DELIMS = re.compile(r"</?\s*(pergunta_do_usuario|system|assistant|developer|user|tool|instructions?)\s*>", re.I)

# Padrões em PT/EN, aplicados ao texto minúsculo e sem acentos.
_INJECTION = [
    r"ignor\w*\s+(\w+\s+){0,3}(instruc|regra|prompt|anterior|acima|comando|diretriz)",
    r"(esquec|desconsider|descart|sobrescrev|substitu)\w*\s+(\w+\s+){0,3}(instruc|regra|prompt|anterior|acima|comando|diretriz)",
    r"ignore\s+(\w+\s+){0,3}(instruction|rule|prompt|previous|above|system)",
    r"(disregard|forget|override|bypass)\s+(\w+\s+){0,3}(instruction|rule|prompt|previous|above|system|guardrail|safety)",
    r"(system|developer|sistema|desenvolvedor)\s*(prompt|message|mensagem|instruc)",
    r"prompt\s+(do\s+)?(sistema|inicial|oculto|interno)",
    r"(revel|mostr|exib|imprim|repit|copi|traduz|resum|vaz)\w*\s+(\w+\s+){0,4}(instruc|prompt|regras|configurac|sua\s+chave|api)",
    r"(reveal|show|print|repeat|leak|output|translate)\s+(\w+\s+){0,4}(instruction|prompt|rules|configuration|api\s*key|secret)",
    r"(suas|tuas|seu|teu)\s+(instruc|regras|prompt|configurac)",
    r"(api\s*key|chave\s+(da\s+|de\s+)?(api|openai|secreta)|openai\s*key|secrets?\.toml|st\.secrets|variavel\s+de\s+ambiente|token\s+de\s+acesso|\bsk-[a-z0-9]{6,})",
    r"(voce\s+agora\s+e|a\s+partir\s+de\s+agora\s+voce|you\s+are\s+now|from\s+now\s+on|aja\s+como|atue\s+como|finja\s+(ser|que)|pretend\s+(to\s+be|you)|act\s+as|roleplay|role-play|interprete\s+o\s+papel)",
    r"(jailbreak|modo\s+(dan|desenvolvedor|deus|sem\s+restric)|developer\s+mode|\bdan\b|do\s+anything\s+now|sem\s+(filtros?|restric|limites?|regras))",
    r"(^|\n)\s*(system|assistant|developer|sistema)\s*:",
    r"(<\s*/?\s*(system|assistant|developer|instructions?)\s*>|\[\s*/?\s*inst\s*\]|###\s*(system|instruction)|```\s*(system|prompt))",
    r"(base64|rot13|hexadecimal)\s*(decod|encod|:)|decodifique|decode\s+this",
    r"(execut|rod|run|eval)\w*\s+(\w+\s+){0,3}(codigo|comando|script|python|shell|sql|bash|code|command)",
    r"(acesse|abra|visite|navegue|baixe|download|fetch|curl|wget)\s+\w*\s*(http|www\.|\w+\.\w{2,}/)",
]
_INJECTION_RE = [re.compile(p) for p in _INJECTION]


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    text: str = ""
    reason: str = ""
    message: str = ""


def _fold(text: str) -> str:
    """minúsculas + sem acentos, para casar padrões mesmo com 'ignóre' ou 'IGNORE'."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def clean_question(text: str) -> str:
    """Normaliza (NFKC), remove caracteres de controle/invisíveis e delimitadores falsos, e colapsa espaços."""
    t = unicodedata.normalize("NFKC", str(text)).translate(_ZERO_WIDTH)
    t = "".join(ch for ch in t if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    t = _DELIMS.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def check_question(raw: str | None) -> GuardResult:
    """Valida a pergunta SEM chamar o modelo. ``ok=False`` traz a mensagem amigável a exibir."""
    if raw is None or not str(raw).strip():
        return GuardResult(False, reason="vazia", message="Digite uma pergunta sobre os dados do painel.")
    if len(str(raw)) > MAX_INPUT_CHARS * 2:  # nem vale limpar textos enormes
        return GuardResult(False, reason="longa", message=f"Pergunta longa demais. Use até {MAX_INPUT_CHARS} caracteres.")
    if _DELIMS.search(unicodedata.normalize("NFKC", str(raw)).translate(_ZERO_WIDTH)):  # tags falsas: detectar ANTES de limpar
        return GuardResult(False, text=clean_question(raw), reason="injecao", message=INJECTION_REFUSAL)
    text = clean_question(raw)
    if not text:
        return GuardResult(False, reason="vazia", message="Digite uma pergunta sobre os dados do painel.")
    if len(text) > MAX_INPUT_CHARS:
        return GuardResult(False, reason="longa", message=f"Pergunta longa demais. Use até {MAX_INPUT_CHARS} caracteres.")
    folded = _fold(text)
    if any(rx.search(folded) for rx in _INJECTION_RE):
        return GuardResult(False, text=text, reason="injecao", message=INJECTION_REFUSAL)
    return GuardResult(True, text=text)


_TAGS = re.compile(r"<[^>]{0,200}>")
_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]*)\)")
_URL = re.compile(r"(https?://|www\.)\S+", re.I)
_SECRET = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")
_FENCE = re.compile(r"```.*?```", re.S)


def sanitize_answer(text: str, canary: str) -> str | None:
    """Limpa a resposta do modelo. Devolve ``None`` se houver sinal de vazamento (canário, chave) — aí vale a recusa."""
    if not text or not text.strip():
        return None
    if canary and canary in text:
        return None
    if _SECRET.search(text):
        return None
    out = _FENCE.sub("", text)
    out = _IMG.sub("", out)
    out = _LINK.sub(r"\1", out)
    out = _URL.sub("", out)
    out = _TAGS.sub("", out)
    out = unicodedata.normalize("NFKC", out).translate(_ZERO_WIDTH)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    if len(out) > MAX_OUTPUT_CHARS:
        out = out[:MAX_OUTPUT_CHARS].rsplit(" ", 1)[0] + "…"
    return out or None
