"""Construcción de universos iniciales mediante el screener de Yahoo Finance.

PROBLEMA QUE RESUELVE ESTE MÓDULO (corregido tras la ejecución de jul-2026):
    Una única consulta multi-región con `sortField="intradaymarketcap"` NO
    devuelve las N mayores empresas del conjunto. Yahoo pagina de forma que el
    orden global no se respeta, y el resultado quedó sesgado: sobre 1.000
    tickers "de mercados desarrollados" salieron 584 japoneses y 92
    estadounidenses, cuando EE.UU. es ~70% del MSCI World por capitalización.

SOLUCIÓN:
    Lanzar una consulta INDEPENDIENTE por cada combinación (región, sector) y
    fusionar. Cada bucket recibe su propia cuota, así que dentro de cada uno el
    orden por capitalización sí es fiable y ninguna región desplaza a las
    demás. Como efecto secundario multiplica el techo real de resultados,
    porque el límite de Yahoo aplica por consulta, no por ejecución.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yfinance as yf
from yfinance import EquityQuery, ETFQuery, FundQuery


UNIVERSOS_YAHOO = {
    "usa": ["us"],
    "espana": ["es"],
    "eurozona": ["es", "pt", "fr", "de", "it", "nl", "be", "at", "ie", "fi"],
    "nordicos": ["dk", "se", "no", "fi"],
    "europa_desarrollada": [
        "gb", "ch", "de", "fr", "nl", "be", "at", "ie", "es", "pt",
        "it", "dk", "se", "no", "fi",
    ],
    # Aproximación geográfica, no una réplica del MSCI World.
    "desarrollados_aproximado": [
        "us", "ca", "gb", "ch", "de", "fr", "nl", "be", "at", "ie",
        "es", "pt", "it", "dk", "se", "no", "fi", "jp", "au", "nz",
        "sg", "hk",
    ],
}

SECTORES_NO_FINANCIEROS = [
    "Technology",
    "Industrials",
    "Healthcare",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Communication Services",
    "Energy",
    "Basic Materials",
    "Utilities",
]

# Registro de tipos de activo soportados por el catálogo. Cada tipo usa una
# clase de query distinta de yfinance y un campo de categoría distinto:
# las acciones se clasifican por "sector" (GICS), los ETF y los fondos por
# "categoryname" (taxonomía Morningstar, que NO es un mapeo 1:1 con GICS,
# ver CATEGORIAS_TEMATICAS_ETF más abajo). Los fondos, además, no tienen
# campo de región en yfinance -solo "exchange"- ni de volumen diario
# (cotizan una vez al día a NAV, no de forma continua), así que no admiten
# ni la matriz región x categoría ni el filtro de liquidez de acciones/ETF.
#
# `campo_orden` también difiere por tipo: Yahoo devuelve HTTP 400 si se pide
# ordenar ETF o fondos por "intradaymarketcap" -ese campo es solo válido
# para EquityQuery-. Verificado con una llamada real: "fundnetassets"
# funciona para ambos.
TIPOS_ACTIVO = {
    "accion": {
        "query_cls": EquityQuery,
        "campo_categoria": "sector",
        "soporta_region": True,
        "soporta_volumen": True,
        "campo_orden": "intradaymarketcap",
    },
    "etf": {
        "query_cls": ETFQuery,
        "campo_categoria": "categoryname",
        "soporta_region": True,
        "soporta_volumen": True,
        "campo_orden": "fundnetassets",
    },
    "fondo": {
        "query_cls": FundQuery,
        "campo_categoria": "categoryname",
        "soporta_region": False,
        "soporta_volumen": False,
        "campo_orden": "fundnetassets",
    },
}

# Categorías temáticas para ETF, mapeadas a valores REALES de
# `categoryname` (verificados contra yfinance.const.ETF_SCREENER_EQ_MAP,
# taxonomía Morningstar). Se reutilizan las mismas etiquetas que
# SECTORES_NO_FINANCIEROS donde existe un equivalente real -no todas lo
# tienen-, y se añaden dos categorías sin equivalente en el lado de
# acciones pero bien pobladas en ETF.
#
# LIMITACIÓN CONOCIDA: Morningstar clasifica la mayoría de ETF por
# estilo/capitalización (Large Blend, Small Value...), no por sector GICS.
# "Industrials", "Consumer Cyclical", "Consumer Defensive" y
# "Communication Services" NO TIENEN categoría ETF equivalente en esta
# taxonomía -por eso no aparecen aquí-, y "Basic Materials" solo tiene un
# equivalente parcial. Esto es una limitación real de los datos de Yahoo,
# no un hueco pendiente de rellenar.
CATEGORIAS_TEMATICAS_ETF = {
    "Technology": ["Technology"],
    "Healthcare": ["Health"],
    "Energy": ["Equity Energy", "Energy Limited Partnership"],
    "Utilities": ["Utilities"],
    "Basic Materials": ["Equity Precious Metals", "Natural Resources"],
    "Materias Primas": ["Commodities Broad Basket", "Commodities Agriculture"],
    "Inmobiliario": ["Real Estate", "Global Real Estate"],
}

PAGINA_MAXIMA = 250  # límite duro de Yahoo por respuesta


def construir_consulta_activo(
    tipo_activo: str,
    regiones: list[str] | None,
    categorias: list[str],
    precio_minimo: float = 2.0,
    volumen_medio_minimo: int = 100_000,
):
    """Consulta amplia para el tipo de activo indicado (acción, ETF o fondo).

    Generaliza `construir_consulta`: usa el registro TIPOS_ACTIVO para elegir
    la clase de query de yfinance y el campo de categoría ("sector" para
    acciones, "categoryname" para ETF/fondos), y omite la cláusula de región
    si el tipo de activo no la soporta (los fondos no tienen ese campo).
    """
    if tipo_activo not in TIPOS_ACTIVO:
        disponibles = ", ".join(sorted(TIPOS_ACTIVO))
        raise ValueError(f"Tipo de activo desconocido: {tipo_activo}. Disponibles: {disponibles}")
    if not categorias:
        raise ValueError("Debe indicarse al menos una categoría")
    config = TIPOS_ACTIVO[tipo_activo]
    query_cls = config["query_cls"]

    clausulas = []
    if config["soporta_region"]:
        if not regiones:
            raise ValueError(f"El tipo de activo '{tipo_activo}' requiere al menos una región")
        clausulas.append(query_cls("is-in", ["region", *regiones]))
    clausulas.append(query_cls("is-in", [config["campo_categoria"], *categorias]))
    clausulas.append(query_cls("gte", ["intradayprice", precio_minimo]))
    if config["soporta_volumen"]:
        clausulas.append(query_cls("gte", ["avgdailyvol3m", volumen_medio_minimo]))
    return query_cls("and", clausulas)


def construir_consulta(
    regiones: list[str],
    precio_minimo: float = 2.0,
    volumen_medio_minimo: int = 100_000,
    sectores: list[str] | None = None,
) -> EquityQuery:
    """Consulta amplia de acciones, previa a los filtros fundamentales del screener.

    No filtra por PER ni por múltiplos para no sesgar las medianas sectoriales.
    Financial Services y Real Estate se excluyen porque requieren métricas
    sectoriales distintas.
    """
    if not regiones:
        raise ValueError("Debe indicarse al menos una región")
    return construir_consulta_activo(
        "accion", regiones, sectores or SECTORES_NO_FINANCIEROS,
        precio_minimo=precio_minimo, volumen_medio_minimo=volumen_medio_minimo,
    )


def _consultar_bucket(
    consulta: EquityQuery | ETFQuery | FundQuery,
    max_resultados: int,
    vistos: set[str],
    campo_orden: str = "intradaymarketcap",
) -> list[str]:
    """Pagina una única consulta y devuelve los tickers nuevos.

    `campo_orden` debe coincidir con el tipo de query: Yahoo devuelve
    HTTP 400 si se pide ordenar un ETFQuery/FundQuery por
    "intradaymarketcap" (ver TIPOS_ACTIVO["etf"/"fondo"]["campo_orden"]).
    """
    nuevos: list[str] = []
    for offset in range(0, max_resultados, PAGINA_MAXIMA):
        tamano = min(PAGINA_MAXIMA, max_resultados - offset)
        respuesta = yf.screen(
            consulta,
            offset=offset,
            size=tamano,
            sortField=campo_orden,
            sortAsc=False,
        )
        cotizaciones = respuesta.get("quotes", []) if isinstance(respuesta, dict) else []
        agregados = 0
        for cotizacion in cotizaciones:
            ticker = str(cotizacion.get("symbol") or "").strip().upper()
            if ticker and ticker not in vistos:
                vistos.add(ticker)
                nuevos.append(ticker)
                agregados += 1
        # Página incompleta = no hay más resultados. Cero altas = el servidor
        # repite símbolos, así que seguir paginando no aporta nada.
        if len(cotizaciones) < tamano or agregados == 0:
            break
    return nuevos


def obtener_tickers_universo(
    nombre: str,
    max_por_bucket: int = 250,
    precio_minimo: float = 2.0,
    volumen_medio_minimo: int = 100_000,
    sectores: list[str] | None = None,
    por_sector: bool = True,
    verbose: bool = False,
) -> list[str]:
    """Devuelve tickers únicos del universo elegido, sin sesgo por región.

    Lanza una consulta por cada (región, sector) en lugar de una sola global.
    `max_por_bucket` es la cuota de CADA consulta, no el total: con 22 regiones
    y 9 sectores, un valor de 250 permite hasta 49.500 resultados teóricos.
    Ajústalo a la baja si solo quieres las mayores de cada nicho.

    Con `por_sector=False` se consulta una vez por región: más rápido, pero
    cada región queda limitada al techo de una sola consulta.
    """
    if nombre not in UNIVERSOS_YAHOO:
        disponibles = ", ".join(sorted(UNIVERSOS_YAHOO))
        raise ValueError(f"Universo desconocido: {nombre}. Disponibles: {disponibles}")
    if max_por_bucket <= 0:
        return []

    regiones = UNIVERSOS_YAHOO[nombre]
    lista_sectores = list(sectores or SECTORES_NO_FINANCIEROS)
    buckets = (
        [([r], [s]) for r in regiones for s in lista_sectores]
        if por_sector
        else [([r], lista_sectores) for r in regiones]
    )

    vistos: set[str] = set()
    tickers: list[str] = []
    for region_bucket, sector_bucket in buckets:
        consulta = construir_consulta(
            region_bucket,
            precio_minimo=precio_minimo,
            volumen_medio_minimo=volumen_medio_minimo,
            sectores=sector_bucket,
        )
        try:
            nuevos = _consultar_bucket(consulta, max_por_bucket, vistos)
        except Exception as exc:  # una región/sector caído no aborta el resto
            if verbose:
                print(f"  ERR {region_bucket}/{sector_bucket}: {exc}")
            continue
        tickers.extend(nuevos)
        if verbose:
            etiqueta = f"{region_bucket[0]}/{sector_bucket[0] if por_sector else 'todos'}"
            print(f"  {etiqueta:>36s}: +{len(nuevos):4d}  (acumulado {len(tickers)})")
    return tickers


COLUMNAS_CATALOGO = ["ticker", "tipo_activo", "categoria", "region"]


def catalogar_tickers(
    tipo_activo: str,
    regiones_o_universo: str | list[str] | None = None,
    categorias: list[str] | None = None,
    max_por_bucket: int = 25,
    precio_minimo: float = 2.0,
    volumen_medio_minimo: int = 100_000,
    verbose: bool = False,
) -> pd.DataFrame:
    """Descubre tickers y los etiqueta por tipo de activo, categoría y región.

    A diferencia de `obtener_tickers_universo` (lista plana, solo acciones),
    cubre también ETF y fondos, y conserva de qué bucket (región, categoría)
    salió cada ticker -la pieza que permite organizar el universo por tipo de
    activo y categoría, en vez de solo acumular tickers sueltos. Es un
    catálogo para DESCUBRIR tickers, no un sustituto de `screener_value.py`:
    PER, ROIC, EV/EBIT... son métricas de una empresa, no de un ETF o un
    fondo, así que estos tickers no deben evaluarse con ese motor.

    Igual que en `obtener_tickers_universo`, el dedup de tickers es global a
    toda la ejecución: un ticker que aparece en varios buckets se etiqueta
    con el primero en encontrarlo, no con todos los que aplicarían.

    Para "accion" y "etf" se recorre una matriz región x categoría, y
    `regiones_o_universo` puede ser un nombre de UNIVERSOS_YAHOO o una lista
    de códigos de región de Yahoo. Para "fondo" no hay campo de región en
    yfinance -solo "exchange"-, así que solo se recorre por categoría y
    `regiones_o_universo` se ignora.

    Las categorías por defecto son SECTORES_NO_FINANCIEROS para acciones y
    CATEGORIAS_TEMATICAS_ETF para ETF (ambas verificadas). Los fondos no
    tienen una lista curada -yfinance no valida "categoryname" para
    FundQuery-, así que hay que pasar `categorias` explícitamente con
    valores reales de Morningstar (p. ej. ["Large Growth", "High Yield
    Bond"]); es un descubrimiento best-effort, sin la misma garantía que ETF.
    """
    if tipo_activo not in TIPOS_ACTIVO:
        disponibles = ", ".join(sorted(TIPOS_ACTIVO))
        raise ValueError(f"Tipo de activo desconocido: {tipo_activo}. Disponibles: {disponibles}")
    if max_por_bucket <= 0:
        return pd.DataFrame(columns=COLUMNAS_CATALOGO)
    config = TIPOS_ACTIVO[tipo_activo]

    categorias_por_defecto = {
        "accion": {s: [s] for s in SECTORES_NO_FINANCIEROS},
        "etf": CATEGORIAS_TEMATICAS_ETF,
        "fondo": {},
    }[tipo_activo]
    mapa_categorias = (
        {c: categorias_por_defecto.get(c, [c]) for c in categorias}
        if categorias
        else categorias_por_defecto
    )
    if not mapa_categorias:
        raise ValueError(
            f"El tipo de activo '{tipo_activo}' no tiene categorías por defecto; "
            "indica `categorias` explícitamente."
        )

    if config["soporta_region"]:
        if isinstance(regiones_o_universo, str):
            if regiones_o_universo not in UNIVERSOS_YAHOO:
                disponibles = ", ".join(sorted(UNIVERSOS_YAHOO))
                raise ValueError(f"Universo desconocido: {regiones_o_universo}. Disponibles: {disponibles}")
            regiones = UNIVERSOS_YAHOO[regiones_o_universo]
        else:
            regiones = regiones_o_universo
        if not regiones:
            raise ValueError(f"El tipo de activo '{tipo_activo}' requiere `regiones_o_universo`")
    else:
        regiones = [None]  # sin campo de región: un único bucket "global"

    vistos: set[str] = set()
    filas: list[dict] = []
    for region in regiones:
        for etiqueta_categoria, valores_categoria in mapa_categorias.items():
            consulta = construir_consulta_activo(
                tipo_activo,
                [region] if region else None,
                valores_categoria,
                precio_minimo=precio_minimo,
                volumen_medio_minimo=volumen_medio_minimo,
            )
            try:
                nuevos = _consultar_bucket(
                    consulta, max_por_bucket, vistos, campo_orden=config["campo_orden"],
                )
            except Exception as exc:  # una región/categoría caída no aborta el resto
                if verbose:
                    print(f"  ERR {tipo_activo}/{region}/{etiqueta_categoria}: {exc}")
                continue
            for ticker in nuevos:
                filas.append({
                    "ticker": ticker,
                    "tipo_activo": tipo_activo,
                    "categoria": etiqueta_categoria,
                    "region": region or "",
                })
            if verbose:
                etiqueta = f"{tipo_activo}/{region or 'global'}/{etiqueta_categoria}"
                print(f"  {etiqueta:>48s}: +{len(nuevos):4d}  (acumulado {len(filas)})")
    return pd.DataFrame(filas, columns=COLUMNAS_CATALOGO)


def guardar_tickers(tickers: list[str], ruta: str | Path) -> Path:
    destino = Path(ruta)
    destino.write_text("\n".join(tickers) + ("\n" if tickers else ""), encoding="utf-8")
    return destino


def main() -> None:
    parser = argparse.ArgumentParser(description="Construye universos para screener_value.py")
    parser.add_argument("universo", nargs="?", choices=sorted(UNIVERSOS_YAHOO), default=None,
                        help="requerido para --tipo accion/etf; se ignora para --tipo fondo")
    parser.add_argument("--tipo", choices=sorted(TIPOS_ACTIVO), default="accion", dest="tipo_activo",
                        help="tipo de activo a descubrir (accion conserva el comportamiento original)")
    parser.add_argument("--categoria", action="append", dest="categorias", default=None,
                        help="categoría a incluir (repetible); por defecto usa las categorías "
                             "estándar del tipo de activo. Para --tipo fondo es obligatorio.")
    parser.add_argument("--max-por-bucket", type=int, default=250, dest="max_por_bucket",
                        help="cuota por cada consulta (región x categoría), no total")
    parser.add_argument("--sin-desglose-sectorial", action="store_true",
                        help="acciones: una consulta por región en vez de por región y sector")
    parser.add_argument("--salida", default=None)
    args = parser.parse_args()

    if args.tipo_activo == "accion":
        if not args.universo:
            parser.error("el universo es obligatorio para --tipo accion")
        tickers = obtener_tickers_universo(
            args.universo,
            max_por_bucket=args.max_por_bucket,
            sectores=args.categorias,
            por_sector=not args.sin_desglose_sectorial,
            verbose=True,
        )
        print(f"\n{len(tickers)} tickers únicos obtenidos para {args.universo}")
        if args.salida:
            destino = guardar_tickers(tickers, args.salida)
            print(f"Lista guardada en {destino}")
        else:
            print("\n".join(tickers))
        return

    # ETF y fondos: catálogo tageado por categoría, no una lista plana.
    config = TIPOS_ACTIVO[args.tipo_activo]
    if config["soporta_region"] and not args.universo:
        parser.error(f"el universo es obligatorio para --tipo {args.tipo_activo}")
    catalogo = catalogar_tickers(
        args.tipo_activo,
        regiones_o_universo=args.universo if config["soporta_region"] else None,
        categorias=args.categorias,
        max_por_bucket=args.max_por_bucket,
        verbose=True,
    )
    print(f"\n{len(catalogo)} tickers catalogados ({args.tipo_activo})")
    if args.salida:
        catalogo.to_csv(args.salida, index=False)
        print(f"Catálogo guardado en {args.salida}")
    else:
        print(catalogo.to_string(index=False))


if __name__ == "__main__":
    main()
