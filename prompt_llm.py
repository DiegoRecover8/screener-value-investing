"""Generador de prompt copy-paste para interpretar candidatas con un LLM.

Fase 5 (ligera): en vez de integrar la API de un proveedor concreto -que
exigiría una API key, factura aparte y ataduras a un proveedor-, este módulo
solo da FORMATO a las métricas que el screener ya calculó, en un texto listo
para pegar en Claude, ChatGPT, Gemini o cualquier otro asistente. Coste $0,
sin API key, sin dependencia de proveedor.

No decide nada por su cuenta: es puro formateo de texto sobre datos ya
calculados por `screener_value.py`, sin descargar ni recalcular nada, así
que es trivialmente testeable sin red.
"""

from __future__ import annotations

import pandas as pd

from screener_value import DISCLAIMER

INSTRUCCIONES = f"""\
Eres un analista que interpreta la salida de un screener cuantitativo de \
value investing (estilo Magic Formula). Las métricas de cada empresa de \
abajo YA están calculadas -no las recalcules ni las cuestiones.

Tu tarea:
- Resume en lenguaje natural qué destaca de cada empresa según ESTAS \
métricas (p. ej. "PER bajo frente a su sector", "ROIC alto", "poca deuda").
- Si una fila marca `roic_fiable: no` o una caja neta alta sobre su \
capitalización, señálalo explícitamente como una nota a revisar, no lo \
ignores ni lo suavices.

Lo que NO debes hacer bajo ningún concepto:
- NO recomiendes comprar, vender o mantener ninguna de estas acciones.
- NO inventes una tesis de inversión, catalizadores, previsiones de \
crecimiento, opiniones sobre el sector o cualquier dato que no esté \
explícitamente en esta lista.
- NO dictamines si es "buena" o "mala" inversión: solo describe lo que \
dicen los números.

{DISCLAIMER}
"""


def _fmt(valor, patron="{:.2f}") -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return "N/D"
    try:
        return patron.format(valor)
    except (TypeError, ValueError):
        return str(valor)


def _bloque_candidata(indice: int, fila: pd.Series) -> str:
    fiable = fila.get("roic_fiable", True)
    fiable_txt = "sí" if bool(fiable) else "no"
    market_cap_eur = fila.get("market_cap_eur")
    cap_b_eur = market_cap_eur / 1e9 if pd.notna(market_cap_eur) else float("nan")
    return (
        f"{indice}. {fila.get('ticker', 'N/D')} — {fila.get('nombre', 'N/D')} "
        f"({fila.get('sector', 'N/D')}, {fila.get('region', 'N/D')})\n"
        f"   PER: {_fmt(fila.get('per'))} | EV/EBIT: {_fmt(fila.get('ev_ebit'))} | "
        f"FCF yield: {_fmt(fila.get('fcf_yield'), '{:.1%}')} | "
        f"ROIC: {_fmt(fila.get('roic'), '{:.1%}')} (fiable: {fiable_txt})\n"
        f"   Deuda neta/EBITDA: {_fmt(fila.get('deuda_ebitda'))} | "
        f"Cobertura intereses: {_fmt(fila.get('cobertura_int'))} | "
        f"CAGR ingresos: {_fmt(fila.get('cagr_ingresos'), '{:.1%}')}\n"
        f"   Caja neta % cap.: {_fmt(fila.get('caja_neta_pct_mcap'), '{:.1%}')} | "
        f"Capitalización: {_fmt(cap_b_eur)} B EUR | "
        f"Puntuación ranking: {_fmt(fila.get('puntuacion'))}"
    )


def generar_prompt_interpretacion(candidatas: pd.DataFrame) -> str | None:
    """Prompt listo para copiar y pegar sobre las candidatas que pasan el filtro.

    Devuelve `None` si `candidatas` está vacío -no tiene sentido generar un
    prompt sin datos que interpretar; quien llama debe manejar ese caso.
    """
    if candidatas is None or candidatas.empty:
        return None

    bloques = [
        _bloque_candidata(i, fila)
        for i, (_, fila) in enumerate(candidatas.iterrows(), start=1)
    ]

    return (
        f"{INSTRUCCIONES}\n"
        f"CANDIDATAS ({len(candidatas)}):\n\n" + "\n\n".join(bloques)
    )
