# Screener de value investing

Screener fundamental de acciones inspirado en la *Magic Formula* de Joel
Greenblatt, con controles adicionales de calidad de balance y comparabilidad
geográfica. Construye un universo de partida vía Yahoo Finance, calcula un
conjunto de métricas de valor y calidad, aplica una rúbrica de filtros
explícita y produce una lista de candidatas rankeadas — cada una con su
motivo de descarte auditable si no pasa.

> ⚠️ **Esto no es asesoramiento financiero.** Es un proyecto educativo de
> análisis de datos. Los resultados no son una recomendación de compra o
> venta, no sustituyen la revisión de las cuentas publicadas por cada
> empresa, y no incorporan ningún juicio cualitativo sobre el negocio, su
> gestión o su entorno competitivo. Cualquier decisión de inversión es
> responsabilidad exclusiva de quien la toma. Este aviso se imprime también
> en cada ejecución del programa.

## Qué hace

1. **`universos_yfinance.py`** construye el universo de partida: lanza una
   consulta al screener de Yahoo Finance por cada combinación de (región ×
   sector), en vez de una única consulta multi-región. Esto evita el sesgo
   observado al paginar consultas grandes, donde el orden global por
   capitalización no se respeta y un mercado (p. ej. Japón) puede acabar
   sobrerrepresentado frente a otro con mucho más peso real (p. ej. EE.UU.).
   También cataloga ETF y fondos por categoría temática (ver más abajo), para
   descubrir tickers sin buscarlos a mano.
2. **`screener_value.py`** descarga los fundamentales TTM y de balance de
   cada ticker, calcula las métricas, aplica los filtros de `UMBRALES` y
   genera un ranking tipo Magic Formula sobre las candidatas que los superan
   todos.
3. **`test_screener.py`** cubre el cálculo de métricas con datos sintéticos,
   sin red — incluye regresiones explícitas de los dos bugs reales
   corregidos durante el desarrollo (ver más abajo). `test_journal.py`,
   `test_seguimiento.py` y `test_prompt_llm.py` hacen lo mismo para sus
   módulos respectivos; los cuatro corren sin red y `pytest` los
   autodescubre juntos.
4. **`dashboard.py`** es un panel interactivo en Streamlit sobre el mismo
   motor: sliders para ajustar `UMBRALES` en vivo, tabla filtrable con los
   motivos de descarte y gráficos de composición del universo por región y
   sector.
5. **`journal.py`** + **`ejecutar_semanal.py`** + `.github/workflows/screener_semanal.yml`
   automatizan una ejecución semanal sobre `universo.txt` y acumulan el
   resultado en `journal_candidatos.csv` -un histórico, no una foto- con
   timestamp de cuándo se calculó cada fila (ver más abajo).
6. **`seguimiento.py`** + **`ejecutar_seguimiento.py`** miden, cada semana,
   el retorno TWR y el drawdown máximo real de cada candidata desde que
   apareció por primera vez, sobre precio de cierre ajustado y sin sesgo
   retrospectivo, acumulando el resultado en `seguimiento_candidatas.csv`.
7. **`prompt_llm.py`** convierte las candidatas actuales en un prompt listo
   para copiar y pegar en cualquier LLM, con instrucciones explícitas para
   que resuma datos sin inventar una tesis de inversión (ver más abajo).

## Uso

```bash
pip install -r requirements.txt

# 1. Construir un universo de tickers (opcional; también puedes usar tu propia lista)
python universos_yfinance.py usa --salida tickers_usa.txt

# 2. Ejecutar el screener sobre esa lista
python screener_value.py tickers_usa.txt
```

Sin argumentos, `screener_value.py` corre sobre una lista pequeña de
ejemplo. El resultado se guarda en `candidatos.csv`: una fila por ticker
evaluado, con todas las métricas, si pasó (`pasa`) y por qué no si no lo hizo
(`motivos_descarte`).

```bash
python -m pytest -v
```

### Dashboard interactivo

```bash
streamlit run dashboard.py
```

Elige un universo en la barra lateral (lista manual de tickers o uno de los
predefinidos en `universos_yfinance`) y pulsa **Descargar y calcular**. Esa es
la única operación que va a red — queda cacheada por lista de tickers durante
24h. A partir de ahí, cada slider de `UMBRALES` recalcula filtros y ranking al
instante sobre los datos ya descargados, sin volver a consultar Yahoo
Finance.

En la misma barra lateral, el desplegable **"🔎 Descubrir tickers por
categoría"** deja buscar acciones (por región + sector), ETF o fondos (por
categoría temática) directamente contra Yahoo Finance y añadirlos con un
clic a la lista manual, sin salir del dashboard (ver "Catálogo de universos
por tipo de activo" más abajo para la lógica que hay detrás). Y bajo la
tabla de resultados, la sección **"🤖 Prompt para interpretar con tu LLM"**
genera un prompt listo para copiar y pegar en Claude, ChatGPT, Gemini o
cualquier otro asistente, a partir de las candidatas que estés viendo en
ese momento (ver Fase 5 más abajo).

### Catálogo de universos por tipo de activo

Además de acciones, `universos_yfinance.py` descubre **ETF** y **fondos de
inversión** por categoría temática, para añadir tickers a la lista de
seguimiento sin buscarlos a mano:

```bash
# ETF de tecnología en EE.UU.
python universos_yfinance.py usa --tipo etf --categoria Technology --salida etf_tech.csv

# ETF de materias primas
python universos_yfinance.py usa --tipo etf --categoria "Materias Primas" --salida etf_commodities.csv

# Fondos de inversión (best-effort, ver limitación abajo)
python universos_yfinance.py --tipo fondo --categoria "Large Growth" --salida fondos.csv
```

El resultado es un CSV con `ticker, tipo_activo, categoria, region` — no una
lista plana como la de acciones, para poder organizar el universo por tipo y
categoría en vez de solo acumular tickers.

> ⚠️ **Este catálogo es solo para descubrir tickers, no para evaluarlos.**
> `screener_value.py` no se ejecuta sobre ETF ni fondos: PER, EV/EBIT, ROIC...
> son métricas de una empresa, no de un vehículo de inversión que posee
> muchas. Si un ticker de este tipo acaba en el screener de todos modos, la
> regla de "un dato ausente nunca pasa un filtro" ya lo descarta sin código
> especial -pero no tiene sentido interpretar ese descarte como una señal
> value.

**Limitación real, no un hueco pendiente:** Yahoo/Morningstar clasifica los
ETF por `categoryname`, una taxonomía de **estilo e índice** (Large Blend,
Foreign Small/Mid Growth...), no por sector GICS como las acciones. Solo 4-5
de los 9 sectores de `SECTORES_NO_FINANCIEROS` tienen un equivalente ETF
razonable (Technology, Healthcare→Health, Energy, Utilities, y Basic
Materials de forma parcial); **Industrials, Consumer Cyclical, Consumer
Defensive y Communication Services no tienen categoría ETF equivalente** en
esta taxonomía. `--categoria "Materias Primas"` e `--categoria Inmobiliario`
sí funcionan porque son categorías ETF bien pobladas sin equivalente del
lado de acciones. Para fondos de inversión (`--tipo fondo`) la limitación es
mayor: yfinance no valida los valores de `categoryname` en ese caso, así que
hay que pasar categorías Morningstar reales a mano (p. ej. `"Large Growth"`,
`"High Yield Bond"`) y el resultado es best-effort, sin la garantía de las
categorías de ETF ya verificadas.

### Ejecución semanal automatizada e histórico (Fase 3)

`.github/workflows/screener_semanal.yml` ejecuta el screener cada lunes a
las 07:00 UTC (y también se puede lanzar a mano desde la pestaña "Actions"
de GitHub, con `workflow_dispatch`). El flujo es:

1. Corre toda la suite de tests (`pytest` autodescubre cualquier
   `test_*.py`) — si el motor está roto, no se genera ni se commitea nada.
2. Ejecuta `python ejecutar_semanal.py universo.txt journal_candidatos.csv`.
3. Si `journal_candidatos.csv` cambió, lo commitea y lo pushea de vuelta al
   repositorio.

**`universo.txt` es una lista fija y versionada, deliberadamente NO
reconstruida desde Yahoo en cada ejecución.** La Fase 4 (seguimiento
longitudinal) necesita comparar la evolución de la MISMA cesta de
candidatas a lo largo de las semanas; si el universo se recalculase cada
vez con `universos_yfinance.py`, cada ejecución compararía cosas distintas
y el histórico dejaría de ser interpretable. Amplía `universo.txt` a mano
—las herramientas de descubrimiento de la sección anterior sirven
precisamente para decidir qué añadir, no para automatizarlo sin criterio.

**`journal_candidatos.csv` se AÑADE, nunca se sobrescribe** (a diferencia
de `candidatos.csv`, que es una foto de la última ejecución y por eso está
en `.gitignore`). Cada fila lleva, además de todas las métricas y el motivo
de descarte, dos columnas nuevas:

- `fecha_ejecucion`: timestamp UTC ISO 8601 de cuándo se calculó esa fila
  -no la fecha de las cuentas de la empresa, sino de la propia ejecución.
- `semana_iso`: semana natural (p. ej. `2026-W35`), para agrupar sin
  depender del día exacto en que corrió el workflow.

```bash
# Ejecutarlo tú mismo, igual que lo hace la Action:
python ejecutar_semanal.py universo.txt journal_candidatos.csv
```

Para que la Action funcione en tu propio fork necesitas: el repositorio en
GitHub, y el permiso de escritura ya declarado en el workflow
(`permissions: contents: write`) — no requiere secretos adicionales, el
`GITHUB_TOKEN` por defecto basta para commitear de vuelta al mismo repo.

### Seguimiento longitudinal del rendimiento real (Fase 4)

Las tres fases anteriores contestan "¿qué candidatas salieron esta semana y
con qué métricas?". Esta contesta la pregunta que de verdad importa:
**¿cómo les fue de verdad después?** Cada ejecución semanal de la Action
también corre `ejecutar_seguimiento.py`, que:

1. Lee `journal_candidatos.csv` y se queda con la **primera** vez que cada
   ticker apareció como candidata (`pasa=True`). Si vuelve a aparecer en
   semanas posteriores sin haber dejado de serlo, no abre una entrada
   nueva -sigue siendo la misma señal original, con la misma fecha de
   entrada. Un ticker que deja de ser candidata y vuelve a serlo meses
   después sí generaría una señal nueva, porque el contexto fundamental ya
   cambió.
2. Descarga el **precio de cierre ajustado** (splits + dividendos,
   `auto_adjust=True` de yfinance) desde esa fecha de entrada hasta hoy.
3. Calcula el retorno **encadenando los retornos diarios** (TWR: `∏(1+r_t)
   - 1`) y el **drawdown máximo** desde el pico acumulado de esa misma
   serie -nunca sobre "valor de la posición" con flujos, la misma
   disciplina que un backtest de cartera con aportaciones periódicas,
   aunque aquí no haya flujos que mezclar al ser una única entrada.
4. Añade el resultado a `seguimiento_candidatas.csv` -otro histórico que
   se **acumula**, nunca se sobrescribe, con su propio timestamp
   (`fecha_calculo`) para poder ver más adelante cómo evolucionó el
   retorno semana a semana, no solo su valor actual.

**Disciplina contra el sesgo retrospectivo (look-ahead bias):**

- La decisión de qué es candidata **nunca se recalcula** con los
  fundamentales de hoy: `journal_candidatos.csv` ya la dejó congelada en
  el momento en que se emitió. Este módulo solo lee esa fecha, no la
  cuestiona.
- El precio de entrada es el primer cierre ajustado disponible **en o
  después** de esa fecha, nunca uno anterior -usar un precio de antes de
  que la señal existiera sería fabricar una entrada más barata de la que
  realmente se podría haber conseguido.
- Un ticker recién detectado (como en la primera ejecución de este
  proyecto) no tiene todavía suficiente histórico de precio para calcular
  un retorno: la fila sale con `NaN` en vez de con un cero o un valor
  inventado, siguiendo la misma regla de "un dato ausente nunca se rellena
  por omisión" del resto del proyecto.

```bash
# Ejecutarlo tú mismo:
python ejecutar_seguimiento.py journal_candidatos.csv seguimiento_candidatas.csv
```

### Prompt de interpretación para tu LLM (Fase 5)

El roadmap original planteaba integrar la API de Claude para una
interpretación cualitativa. En vez de atarse a un proveedor concreto -que
exigiría una API key, facturación aparte (ninguna suscripción de consumo
tipo ChatGPT Plus o Gemini Pro incluye créditos de API) y mantenimiento
frente a cambios de precios/SDK-, `prompt_llm.py` genera un **prompt listo
para copiar y pegar** en el asistente que prefieras: Claude, ChatGPT,
Gemini o cualquier otro. Coste $0, sin API key, sin dependencia de
proveedor.

En el dashboard, justo debajo de la tabla de resultados, aparece un cuadro
con el prompt ya formateado a partir de las candidatas que superan los
umbrales actuales -se regenera solo si mueves un slider, sin volver a
consultar Yahoo Finance. El prompt incluye instrucciones explícitas para
que el LLM **resuma las métricas ya calculadas, nunca invente una tesis de
inversión ni recomiende comprar o vender**, y reproduce el mismo
disclaimer que el resto de la herramienta.

## Metodología

### Métricas

| Métrica | Cálculo | Qué mide |
|---|---|---|
| **PER** | market cap / net income | Precio sobre beneficio. `NaN` si hay pérdidas (el PER de una empresa con pérdidas no es comparable). |
| **EV/EBIT** | (market cap + deuda − caja) / EBIT | Precio del negocio operativo, indistinto de cómo esté financiado. |
| **FCF yield** | free cash flow / market cap | Caja generada frente a lo que cuesta la empresa en bolsa. |
| **ROIC** | EBIT × (1 − tasa fiscal) / capital invertido medio | Retorno sobre el capital que el negocio realmente emplea. |
| **Margen operativo** | EBIT / ingresos | Calidad del negocio antes de estructura de capital. |
| **Deuda neta/EBITDA** | (deuda − caja) / EBITDA | Apalancamiento frente a la caja que genera el negocio. |
| **Cobertura de intereses** | EBIT / gasto en intereses | Margen de seguridad frente a la deuda. |
| **CAGR de ingresos** | crecimiento anualizado sobre el histórico disponible | Filtra negocios en contracción estructural. |

### Decisiones de diseño no obvias

- **Un dato ausente nunca pasa un filtro.** Si una métrica es `NaN`, la fila
  se descarta explícitamente con el motivo `"<métrica>: sin dato"` — nunca se
  aprueba por omisión. Alternativas como imputar un valor "neutro" o ignorar
  el filtro esconderían justamente los casos en los que Yahoo Finance no
  tiene datos fiables, que es una señal en sí misma.

- **El capital invertido para ROIC es deuda + equity, sin restar la caja.**
  Restarla (como en la primera versión de este proyecto) infla
  artificialmente el ROIC de empresas con caja neta — y esa caja ya se
  refleja en el EV/EBIT, así que restarla también en ROIC puntuaba el mismo
  hecho de balance dos veces. Con la fórmula actual, dos empresas
  operativamente idénticas pero con distinta caja tienen el mismo ROIC; la
  caja ociosa se ve en `caja_neta_pct_mcap`, como diagnóstico aparte, no
  como un boost a la rentabilidad. Ver `test_screener.py::TestSesgoCajaNeta`.

- **La mediana de PER para el filtro relativo se calcula por (sector ×
  región comparable)**, con fallback a la mediana sectorial global si la
  región tiene menos de `min_empresas_sector` comparables. Calcularla sobre
  todo el universo global la contaminaba: con un universo con mucho peso
  japonés, la "mediana del sector" terminaba siendo en la práctica la
  mediana japonesa, y ninguna empresa de otro mercado con múltiplos
  estructuralmente distintos podía pasar el filtro relativo. Ver
  `test_screener.py::TestMedianaRegional`.

- **`roic_fiable`** marca (no filtra) las filas donde el ROIC supera
  `ROIC_MAXIMO_FIABLE` (100%). Ocurre en negocios *asset-light* con fondos
  propios casi nulos: un ROIC del 400% no significa "cuatro veces mejor",
  significa que la base de capital es demasiado pequeña para que el ratio
  sea comparable. Se marca para revisión manual en vez de descartarse,
  porque el negocio en sí puede ser perfectamente bueno.

- **Deduplicado de listings duales.** La misma empresa cotizando en dos
  bolsas (p. ej. `GSK.L` y `GSKL.XC`) se colapsa en una sola fila —se
  conserva la de mayor capitalización en EUR—, para no inflar el universo
  ni duplicar candidatas en el ranking.

- **Consistencia de divisa.** Si la divisa de cotización y la divisa de
  las cuentas (`financialCurrency`) no coinciden, todas las ratios que
  mezclan cotización y balance (PER, EV/EBIT, FCF yield) se anulan a
  `NaN` para esa fila, y se añade el motivo de descarte explícito. Mezclar
  divisas sin convertir produce ratios sin sentido que parecen válidos.

- **Actualización diaria/semanal, no tiempo real.** Las métricas
  fundamentales no cambian intradía; no hay ni se necesita streaming ni
  websockets.

### Ranking

Las candidatas que superan **todos** los filtros de `UMBRALES` se rankean
sumando dos posiciones (estilo Magic Formula): el rank por ROIC descendente
y el rank por *earnings yield* (1 / EV·EBIT) descendente. Puntuación más baja
= mejor combinación de calidad y precio. Las empresas descartadas no entran
en el ranking, pero conservan sus métricas y motivos de descarte en el CSV.

## Limitaciones conocidas de yfinance

- **Cobertura desigual fuera de EE.UU.** Para valores europeos y asiáticos
  es habitual que falten `Free Cash Flow`, `EBIT`/`Operating Income`, o el
  agregado TTM completo. Este proyecto no rellena esos huecos: los descarta
  explícitamente (ver "un dato ausente nunca pasa un filtro").
- **TTM ausente en algunos mercados.** Cuando Yahoo no publica el agregado
  TTM para un ticker, se usa de forma consistente el último ejercicio anual
  para *todas* las magnitudes de resultados de esa empresa, para no mezclar
  periodos de distinta duración dentro de la misma fila.
- **`financialCurrency` no siempre está informado**, lo que hace que el
  chequeo de consistencia de divisa sea conservador: ante la duda, descarta
  en vez de asumir que coinciden.
- **El paginado del screener de Yahoo no es fiable a gran escala.** Ver la
  cabecera de `universos_yfinance.py` para el sesgo geográfico real
  observado y por qué se resolvió con una consulta por (región × sector).
- **Datos de un único proveedor, sin contraste.** No hay verificación
  cruzada contra otra fuente; un error de Yahoo se propaga tal cual.

## Roadmap

- [x] Fase 1 — limpieza, tests reproducibles y documentación (este README).
- [x] Fase 2 — dashboard interactivo en Streamlit (`dashboard.py`): sliders
      sobre `UMBRALES`, tabla filtrable con motivos de descarte, composición
      del universo por región/sector, y descubrimiento de ETF/fondos por
      categoría conectado al mismo panel.
- [x] Fase 3 — ejecución semanal automatizada con GitHub Actions
      (`.github/workflows/screener_semanal.yml`) y un histórico acumulado
      (`journal_candidatos.csv`) de qué candidatas salieron cada semana y
      con qué métricas, con timestamp de cuándo se calculó cada fila.
- [x] Fase 4 — seguimiento longitudinal (`seguimiento.py`,
      `ejecutar_seguimiento.py`) del rendimiento real de las candidatas
      pasadas: retorno TWR y drawdown máximo sobre precio de cierre
      ajustado desde su primera aparición, sin look-ahead bias, acumulado
      semana a semana en `seguimiento_candidatas.csv`.
- [x] Fase 5 — interpretación cualitativa de las candidatas: en vez de
      integrar una API concreta, `prompt_llm.py` genera un prompt listo
      para copiar y pegar en el LLM que prefieras (Claude, ChatGPT, Gemini
      u otro), a coste $0 y sin atarse a ningún proveedor, con
      instrucciones explícitas para resumir datos sin inventar una tesis
      de inversión.
