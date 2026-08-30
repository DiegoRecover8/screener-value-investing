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


def ticker_a_tradingview(ticker: str) -> str:
    """Mejor intento de símbolo TradingView a partir de un ticker de yfinance."""
    ticker = ticker.strip().upper()
    for sufijo, bolsa in SUFIJO_A_BOLSA.items():
        if ticker.endswith(sufijo):
            base = ticker[: -len(sufijo)]
            return f"{bolsa}:{base}"
    return ticker
