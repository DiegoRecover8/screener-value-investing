"""Mapeo best-effort de tickers de yfinance a símbolos de TradingView.

TradingView usa el formato "BOLSA:SIMBOLO" (p. ej. "BME:ITX"), distinto del
sufijo de país de yfinance ("ITX.MC"). No existe una conversión oficial
entre ambos formatos -este mapeo se verificó a mano, símbolo por símbolo,
contra el widget real de TradingView (https://s.tradingview.com/widgetembed/)
para los mercados que aparecen en `universo.txt`:

- BME:ITX (Madrid), XETR:SAP (Alemania), MIL:ENI (Italia), VIE:OMV (Austria)
  y OMXHEX:NOKIA (Helsinki) resuelven bien.
- EURONEXT:ASML (Amsterdam) y EURONEXT:SAN (París) también resuelven bien
  -"EURONEXT" cubre varios mercados del grupo Euronext (Amsterdam, París,
  Bruselas, Lisboa, Dublín), aunque no se han probado los cinco uno a uno.
- EURONEXT:RYA (Ryanair, Dublín) es un símbolo válido pero TradingView lo
  marca "solo disponible en TradingView" en el widget gratuito -algunos
  símbolos concretos requieren su plan de pago aunque el mercado en general
  funcione. Esto puede pasar con cualquier ticker, no solo los irlandeses.
- Los tickers sin sufijo (mercado estadounidense) se pasan tal cual:
  TradingView los resuelve sin necesitar prefijo de bolsa (verificado con
  "AAPL").

Cuando el símbolo no resuelve -mapeo incorrecto, ticker con formato
distinto entre proveedores (acciones preferentes, doble clase), o
simplemente no disponible en el plan gratuito-, el propio widget de
TradingView deja buscar el símbolo correcto a mano al hacer clic en el
nombre; no hay manera de detectar el fallo desde fuera del iframe.
"""

from __future__ import annotations

import html
import json
import re

import pandas as pd

SUFIJO_A_BOLSA = {
    ".MC": "BME",
    ".DE": "XETR",
    ".AS": "EURONEXT",
    ".PA": "EURONEXT",
    ".LS": "EURONEXT",
    ".BR": "EURONEXT",
    ".IR": "EURONEXT",
    ".MI": "MIL",
    ".VI": "VIE",
    ".HE": "OMXHEX",
}

PATRON_TICKER = re.compile(r"^[A-Z0-9.^=\-]{1,32}$")


def ticker_a_tradingview(ticker: str) -> str:
    """Mejor intento de símbolo TradingView a partir de un ticker de yfinance."""
    ticker = ticker.strip().upper()
    if not PATRON_TICKER.fullmatch(ticker):
        raise ValueError(f"ticker no válido para TradingView: {ticker!r}")
    for sufijo, bolsa in SUFIJO_A_BOLSA.items():
        if ticker.endswith(sufijo):
            base = ticker[: -len(sufijo)]
            return f"{bolsa}:{base}"
    return ticker


def tickers_candidatos_para_grafico(df: pd.DataFrame) -> list[str]:
    """Devuelve solo candidatas, ordenadas por ranking y ticker.

    El helper es puro para poder probar la selección sin arrancar Streamlit.
    Si no existe ``puntuacion`` conserva un orden alfabético determinista.
    """
    if df.empty or "ticker" not in df or "pasa" not in df:
        return []
    candidatas = df[df["pasa"].fillna(False).astype(bool)].copy()
    if candidatas.empty:
        return []
    orden = ["puntuacion", "ticker"] if "puntuacion" in candidatas else ["ticker"]
    candidatas = candidatas.sort_values(orden, na_position="last")
    return list(dict.fromkeys(candidatas["ticker"].astype(str)))


def html_widget_tradingview(
    ticker: str,
    *,
    locale: str = "en",
    theme: str = "light",
    altura: int = 680,
) -> str:
    """Genera el embed oficial del Advanced Chart sin interpolación insegura."""
    if locale not in {"en", "es"}:
        raise ValueError("locale debe ser 'en' o 'es'")
    if theme not in {"light", "dark"}:
        raise ValueError("theme debe ser 'light' o 'dark'")
    if altura < 320:
        raise ValueError("altura debe ser al menos 320 px")

    simbolo = ticker_a_tradingview(ticker)
    configuracion = {
        "autosize": True,
        "symbol": simbolo,
        "interval": "D",
        "timezone": "exchange",
        "theme": theme,
        "backgroundColor": "#ffffff" if theme == "light" else "#0e1117",
        "gridColor": "rgba(42, 55, 76, 0.08)",
        "style": "1",
        "locale": locale,
        "allow_symbol_change": True,
        "calendar": False,
        "withdateranges": True,
        "hide_side_toolbar": True,
        "hide_top_toolbar": False,
        "hide_legend": False,
        "hide_volume": False,
        "save_image": False,
        "support_host": "https://www.tradingview.com",
    }
    enlace = simbolo.replace(":", "-")
    etiqueta = html.escape(f"{ticker_a_tradingview(ticker)} chart")
    return f"""<!doctype html>
    <html lang="{locale}">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <style>
        html, body {{ width:100%; height:100%; margin:0; padding:0; overflow:hidden; }}
        .tradingview-widget-container {{ width:100%; height:100vh; min-width:0; }}
        .tradingview-widget-container__widget {{
          width:100%; height:calc(100vh - 32px); min-height:{altura - 32}px;
        }}
        .tradingview-widget-copyright {{
          height:32px; line-height:32px; text-align:center;
          font:13px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          color:#787b86;
        }}
        .tradingview-widget-copyright a {{ color:#2962ff; text-decoration:none; }}
      </style>
    </head>
    <body>
    <div class="tradingview-widget-container">
      <div class="tradingview-widget-container__widget"
           style="height:calc(100vh - 32px);width:100%"></div>
      <div class="tradingview-widget-copyright">
        <a href="https://www.tradingview.com/symbols/{html.escape(enlace)}/"
           rel="noopener nofollow" target="_blank">
          <span class="blue-text">{etiqueta}</span>
        </a> by TradingView
      </div>
      <script type="text/javascript"
              src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js"
              async>{json.dumps(configuracion, ensure_ascii=True)}</script>
    </div>
    </body>
    </html>
    """
