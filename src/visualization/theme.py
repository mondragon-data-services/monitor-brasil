"""Paleta semântica, template Plotly e anotações históricas (Tufte / Knaflic)."""
from __future__ import annotations

import textwrap

import pandas as pd
import plotly.graph_objects as go

from src.config import (
    HISTORICAL_EVENTS,
    INFLATION_TARGET,
    INFLATION_TOLERANCE_LOWER,
    INFLATION_TOLERANCE_UPPER,
    PALETTE,
)

FONT_FAMILY = "Inter, 'Segoe UI', system-ui, -apple-system, sans-serif"


def br(value: float, decimals: int = 1, signed: bool = False) -> str:
    """Formata número no padrão brasileiro (``1.234,5``)."""
    if value is None or pd.isna(value):
        return "—"
    text = f"{value:{'+' if signed else ''},.{decimals}f}"
    return text.replace(",", "§").replace(".", ",").replace("§", ".")


def br_pct(value: float, decimals: int = 1, signed: bool = False) -> str:
    return f"{br(value, decimals, signed)}%"


def br_pp(value: float, decimals: int = 1) -> str:
    return f"{br(value, decimals, signed=True)} p.p."


def br_money(value: float) -> str:
    return f"R$ {br(value, 2)}"


def base_layout(fig: go.Figure, title: str, subtitle: str = "", height: int = 420, wrap: int = 110) -> go.Figure:
    """Layout limpo: título ativo, legenda horizontal no topo, sem grade vertical e separadores pt-BR."""
    lines = textwrap.wrap(title, wrap) or [title]
    sub_lines = textwrap.wrap(subtitle, wrap + 10) if subtitle else []
    subtitle = "<br>".join(sub_lines)
    heading = f"<b>{'<br>'.join(lines)}</b>" + (f"<br><span style='font-size:13px;font-weight:400;color:{PALETTE.muted}'>{subtitle}</span>" if subtitle else "")
    fig.update_layout(
        title=dict(text=heading, x=0, xanchor="left", y=0.97, yanchor="top",
                   font=dict(size=17, color=PALETTE.text, family=FONT_FAMILY)),
        template="simple_white",
        height=height,
        separators=",.",  # tooltips e eixos: 12,3 e 1.234,56
        font=dict(family=FONT_FAMILY, size=13, color=PALETTE.text),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=10, r=10, t=70 + 24 * len(lines) + 18 * len(sub_lines), b=40),
        hovermode="x unified",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(showgrid=False, showline=True, linecolor=PALETTE.grid, ticks="outside", tickcolor=PALETTE.grid)
    fig.update_yaxes(showgrid=True, gridcolor=PALETTE.grid, zeroline=False, showline=False)
    return fig


def empty_figure(message: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False, font=dict(size=15, color=PALETTE.muted),
                       xref="paper", yref="paper", x=0.5, y=0.5)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return base_layout(fig, "Dados indisponíveis no momento", "Tente 'Forçar atualização' na barra lateral.", height=300)


def add_inflation_band(fig: go.Figure) -> go.Figure:
    """Faixa de tolerância da meta (1,5%–4,5%) e linha do centro da meta (3,0%)."""
    fig.add_hrect(y0=INFLATION_TOLERANCE_LOWER, y1=INFLATION_TOLERANCE_UPPER, fillcolor=PALETTE.band,
                  line_width=0, layer="below")
    fig.add_hline(y=INFLATION_TARGET, line_dash="dash", line_color=PALETTE.institutional, line_width=1.2,
                  annotation_text="Centro da meta 3,0%", annotation_position="bottom right",
                  annotation_font=dict(size=11, color=PALETTE.institutional))
    fig.add_hline(y=INFLATION_TOLERANCE_UPPER, line_dash="dot", line_color=PALETTE.neutral, line_width=1,
                  annotation_text="Teto 4,5%", annotation_position="top right",
                  annotation_font=dict(size=11, color=PALETTE.neutral))
    return fig


def add_events(fig: go.Figure, x_min: pd.Timestamp, x_max: pd.Timestamp) -> go.Figure:
    """Sombreia eventos históricos que caem dentro do intervalo exibido."""
    for ev in HISTORICAL_EVENTS:
        start, end = pd.Timestamp(ev["start"]), pd.Timestamp(ev["end"])
        if end < x_min or start > x_max:
            continue
        fig.add_vrect(x0=start, x1=end, fillcolor=PALETTE.neutral, opacity=0.10, line_width=0, layer="below",
                      annotation_text=ev["label"], annotation_position="top left",
                      annotation_font=dict(size=10, color=PALETTE.neutral))
    return fig
