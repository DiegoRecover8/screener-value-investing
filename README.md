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
2. **`providers/` + `screener_value.py`** separan la fuente del motor. El
   adaptador `ProveedorYFinance` normaliza fundamentales TTM/anuales y de
   balance, registra su procedencia y valida fechas, periodos, divisas y
   campos esenciales antes de que el motor calcule las métricas. `yfinance`
   sigue siendo el proveedor predeterminado, pero ya no está acoplado a los
   filtros ni al ranking.
3. **Los archivos `test_*.py`** cubren con datos sintéticos y sin red el
   motor, el journal, las señales y su seguimiento, el prompt LLM, el mapeo
   de TradingView y la exportación del histórico. Incluyen regresiones
   explícitas de bugs reales corregidos durante el desarrollo, y `pytest`
   los autodescubre juntos.
4. **`dashboard.py`** es un panel interactivo en Streamlit sobre el mismo
   motor: sliders para ajustar `UMBRALES` en vivo, tabla filtrable con los
   motivos de descarte y gráficos de composición del universo por región y
   sector.
5. **`journal.py`** + **`ejecutar_semanal.py`** + `.github/workflows/screener_semanal.yml`
   automatizan una ejecución semanal sobre el universo oficial activo y acumulan el
   resultado en `journal_candidatos.csv` -un histórico, no una foto- con
   un `snapshot_id` por ejecución y su control de integridad separado en
   `ejecuciones_screener.csv` (ver más abajo).
6. **`seguimiento.py`** + **`ejecutar_seguimiento.py`** miden, cada semana,
   el retorno TWR y el drawdown máximo real desde la entrada de cada señal,
   incluidas las reentradas, sobre precio de cierre ajustado y sin sesgo
   retrospectivo, acumulando el resultado en `seguimiento_candidatas.csv`.
7. **`prompt_llm.py`** convierte las candidatas actuales en un prompt listo
   para copiar y pegar en cualquier LLM, con instrucciones explícitas para
   que resuma datos sin inventar una tesis de inversión (ver más abajo).
8. **`tradingview.py`** mapea tickers de yfinance a símbolos de TradingView
   (verificado a mano contra el widget real) para embeber el gráfico de
   precio de cualquier ticker evaluado, dentro del dashboard.
9. **`exportar_historico.py`** exporta journal + seguimiento a un JSON
   compacto, usado para refrescar la "Bitácora del Screener" -una página
   estática publicada como Claude Artifact (ver más abajo).
10. **`verificacion_candidatas.py`** contrasta solo las candidatas con una
    segunda fuente y guarda el diagnóstico en un CSV separado. La verificación
    funciona en modo sombra: no cambia `pasa`, el ranking, el journal ni la
    identidad del snapshot.

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
de GitHub, con `workflow_dispatch`). Una ejecución manual obliga a elegir
entre **prueba** y **oficial**. El flujo es:

1. Corre toda la suite de tests (`pytest` autodescubre cualquier
   `test_*.py`) — si el motor está roto, no se genera ni se commitea nada.
2. Ejecuta `python ejecutar_semanal.py --universo-activo journal_candidatos.csv`.
   Antes de escribir, exige una estructura coherente y al menos un 80% de
   descargas sin error. Además cuenta por separado datos `ok`, pendientes de
   `revisar` e `inutilizables`: una respuesta técnica ya no se confunde con
   una observación contable fiable.
3. Si la ejecución es válida, añade el snapshot al journal y su resumen a
   `ejecuciones_screener.csv`. Si no lo es, la Action falla sin contaminar
   ninguno de los dos históricos.
4. Las ejecuciones programadas y las manuales marcadas como oficiales
   actualizan seguimiento y `historico.json`; las pruebas manuales no.
5. Commitea todos los archivos que hayan cambiado.

El workflow usa el grupo de concurrencia `screener-historico` con
`cancel-in-progress: false`: una ejecución activa nunca se cancela a mitad de
la descarga y otra debe esperar antes de hacer checkout y escribir. Esto
evita que dos Actions partan del mismo CSV y compitan al hacer `git push`.
Además, limita cada job a 30 minutos para no dejar un runner bloqueado y
activa la caché de `pip` de `setup-python`; la instalación sigue verificando
`requirements.txt`, pero puede reutilizar descargas en ejecuciones posteriores.

Una ejecución manual permite indicar opcionalmente `universo_prueba`. Si se
escribe el ID de un universo registrado, por ejemplo `uv_2026q3_r02`, la
Action lo evalúa conservando ese ID y su hash en el control. Esta opción exige
`modo: prueba`: un ID explícito nunca puede declararse oficial ni cambia el
universo activo. Con el campo vacío, tanto las pruebas normales como las
ejecuciones oficiales siguen usando el universo `active`.

### Verificación selectiva de candidatas

El screener no vuelve a descargar las 645 empresas desde otra fuente. Solo
contrasta las que ya han superado todos los filtros (11 en el snapshot oficial
del 1 de septiembre de 2026). El primer adaptador secundario es
`ProveedorSecEdgar`, basado en la API pública Company Facts de la SEC. La API
no requiere clave, pero la SEC exige identificar el cliente con un
`User-Agent`; además su cobertura se limita a emisores registrados y a los
conceptos XBRL estandarizados.

Para habilitarla en GitHub:

1. Ve a **Settings → Secrets and variables → Actions → Secrets**.
2. Crea el *repository secret* `SEC_USER_AGENT` con un valor como
   `screener-value-investing contacto@tu-dominio.example`. Aunque la SEC
   necesita recibirlo en la cabecera HTTP, guardarlo como Secret evita que el
   correo de contacto quede visible en los logs públicos de GitHub Actions.
3. En una ejecución manual, marca **Contrastar candidatas con SEC EDGAR en
   modo sombra**. Las ejecuciones programadas la activarán automáticamente
   cuando la variable exista.

El resultado se acumula en `verificacion_candidatas.csv`, con una fila por
`snapshot_id`, ticker y componente contable. Compara ingresos, beneficio neto,
EBIT aproximado mediante resultado operativo, FCF derivado, deuda, caja,
fondos propios e intereses. Antes de calcular diferencias exige la misma
divisa, el mismo tipo de periodo y fechas separadas como máximo 45 días. Los
estados posibles son:

Yahoo conserva para este artefacto una vista anual capturada durante la misma
consulta que alimenta el screener. Así, aunque el ranking use TTM cuando está
disponible, la comparación con el ejercicio anual de SEC queda etiquetada como
`yfinance_anual` y enfrenta periodos homogéneos. Esa vista lateral no sustituye
ni modifica los valores TTM del snapshot oficial.

- `verificado`: diferencia relativa de hasta el 10 %.
- `advertencia`: diferencia superior al 10 % y de hasta el 25 %.
- `discrepancia_material`: diferencia superior al 25 %.
- `no_comparable`: periodo, fecha o divisa incompatibles.
- `sin_dato`: falta ese componente en una de las fuentes.
- `sin_cobertura`: el ticker no está registrado literalmente en SEC o la API
  no respondió.

Nunca se recortan sufijos de mercado para buscar cobertura. Por ejemplo,
`IAG.MC` no se transforma en `IAG`, porque ese símbolo podría pertenecer a
otra sociedad en EE. UU. Tampoco se construye una ratio con el numerador de
Yahoo y el denominador de SEC. Durante al menos 3-4 snapshots, estos estados
deben servir solo para aprender qué cobertura y discrepancias son habituales;
no deben convertirse en un filtro automático. La documentación oficial de la
fuente está en [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

### Universos oficiales versionados

La Action no reconstruye el universo desde Yahoo cada semana. Resuelve la
única versión `active` de `universos/manifest.json`, valida su recuento y su
SHA-256, y exige que `universo.txt` sea todavía un espejo exacto durante la
transición. La versión inicial `uv_2026q3_r01` contiene sin cambios los 205
tickers legacy.

Los CSV bajo `universos/oficiales/` son inmutables una vez activados. Para
añadir o retirar empresas se crea una versión `draft`, se compara y después se
activa; la anterior pasa a `retired`, pero nunca se borra. Los resultados
amplios de Yahoo pertenecen a `universos/descubrimiento/` y nunca se convierten
automáticamente en universo oficial.

```bash
# Comprobar manifest, hash, recuento y espejo legacy
python gestionar_universos.py validar

# Ver la versión que utilizará la próxima Action
python gestionar_universos.py mostrar-activo

# Comparar una versión registrada con un CSV o TXT de descubrimiento
python gestionar_universos.py comparar uv_2026q3_r01 ruta/nueva_lista.csv

# Activar una versión draft ya registrada y actualizar universo.txt
python gestionar_universos.py activar uv_2026q4_r01
```

El hash se calcula sobre la pertenencia canónica: tickers normalizados,
únicos y ordenados. Así, cambiar el orden no crea otro universo, pero añadir o
retirar un ticker sí. Una ejecución oficial exige `--universo-activo`; una
lista pasada directamente al CLI se identifica como `adhoc_<hash>` y solo
puede guardarse como prueba no oficial.

#### Universo amplio de descubrimiento

`generar_descubrimiento.py` construye una lista amplia sin tocar el manifest
ni el universo activo. El perfil inicial recorre 22 regiones desarrolladas y
los 9 sectores compatibles con el screener: solicita hasta 30 empresas por
sector en EE. UU., 15 en los mercados grandes, 10 en los medianos y 6 en los
menores. Su capacidad teórica es 2.070 resultados antes de deduplicar; no es
una ponderación de cartera, sino una forma de dar profundidad a los mercados
grandes sin dejar fuera los pequeños.

```bash
python generar_descubrimiento.py
```

Cada ejecución guarda:

- `disc_<timestamp>.csv`: tickers únicos con país, sector, posición y cuota
  del bucket que los descubrió.
- `disc_<timestamp>.json`: configuración, SHA-256 de la pertenencia, hash
  del catálogo completo (incluidos país, sector y rank), recuentos, cobertura,
  fallos y diferencias frente al universo oficial activo.
- `checkpoint_disc_<timestamp>.json`: estado local después de cada bucket.
  Se elimina al publicar un snapshot válido y no se versiona.

El generador reintenta cada bucket dos veces y exige al menos un 90 % de
buckets correctos. Si no alcanza ese umbral, no publica un snapshot parcial y
devuelve la ruta exacta para continuar sin repetir lo ya descargado:

```bash
python generar_descubrimiento.py \
  --reanudar universos/descubrimiento/checkpoint_disc_<timestamp>.json
```

Un snapshot de descubrimiento nunca se activa automáticamente. Primero se
revisa y compara; solo una selección deliberada puede convertirse después en
un CSV `draft` bajo `universos/oficiales/`.

#### Selección reproducible hacia un draft

`generar_universo_draft.py` transforma un snapshot concreto en una versión
oficial revisable, sin consultar Yahoo y sin activar el resultado. El perfil
versionado `balanced_rank_v1` aplica una cuota por bucket país×sector: 12 en
EE. UU.; 6 en Canadá, Reino Unido, Alemania, Francia, Japón y Australia; 4 en
Suiza, Países Bajos, España, Italia y Suecia; y 2 en los demás mercados del
perfil. Solo conserva una empresa del universo activo fuera de la cuota si
fue descubierta como máximo tres posiciones después. No recupera manualmente
empresas ausentes del snapshot.

El selector exige los 198 buckets correctos y un resultado de 400 a 800
empresas. Verifica tanto el hash de tickers como el hash del ranking completo;
por eso modificar país, sector, posición o cuota invalida la entrada. El orden
del CSV no cambia el resultado.

```bash
python generar_universo_draft.py \
  --snapshot universos/descubrimiento/disc_20260831T103225269745Z.csv \
  --id uv_2026q3_r02
```

La operación crea tres artefactos auditables:

- `universos/oficiales/uv_<...>.csv`, con la regla y el rank de cada miembro.
- `universos/selecciones/uv_<...>.json`, con los hashes de entrada, el perfil,
  altas, bajas, permanencias y cualquier retención por margen.
- Una entrada `status: draft` en `universos/manifest.json`.

`active_universe_id` y `universo.txt` permanecen intactos. La activación sigue
siendo una decisión manual posterior, después de revisar la auditoría y comparar:

```bash
python gestionar_universos.py comparar uv_2026q3_r01 uv_2026q3_r02
python gestionar_universos.py activar uv_2026q3_r02
```

#### Refinado previo de cotizaciones duales

El deduplicado del motor usa nombre normalizado, país y capitalización para
escoger un solo listing por empresa. Una ejecución oficial con cobertura del
100 % permite reutilizar ese resultado antes de la siguiente descarga.
`generar_refinado_draft.py` exige que el snapshot sea oficial, que su hash
coincida con el universo registrado y que los recuentos de control y journal
cierren exactamente.

Para cada listing descartado, el perfil `refine_observed_v1` toma el siguiente
rank disponible del mismo país×sector. Si ese bucket se agotó, conserva el país
y elige la reserva con mejor rank relativo entre los demás sectores. La versión
de origen permanece activa y el resultado se registra siempre como `draft`:

```bash
python generar_refinado_draft.py \
  --id uv_2026q3_r03 \
  --origen uv_2026q3_r02 \
  --snapshot-oficial snap_20260831T155950914633Z \
  --descubrimiento universos/descubrimiento/disc_20260831T103225269745Z.csv
```

El informe `universos/selecciones/uv_<...>.json` conserva la ejecución oficial,
los 119 listings conocidos que se excluyeron y cada sustitución con su regla.
Los tickers de reserva aún no observados podrían contener nuevos listings
duales; por eso el draft refinado debe pasar otra prueba controlada en Actions
antes de activarse y, si quedan duplicados, puede repetirse el mismo proceso.

**`journal_candidatos.csv` se AÑADE, nunca se sobrescribe** (a diferencia
de `candidatos.csv`, que es una foto de la última ejecución y por eso está
en `.gitignore`). Cada fila lleva, además de todas las métricas y el motivo
de descarte, tres columnas de auditoría:

- `fecha_ejecucion`: timestamp UTC ISO 8601 de cuándo se calculó esa fila
  -no la fecha de las cuentas de la empresa, sino de la propia ejecución.
- `semana_iso`: semana natural (p. ej. `2026-W35`), para agrupar sin
  depender del día exacto en que corrió el workflow.
- `snapshot_id`: identificador UTC con precisión de microsegundos compartido
  por todas las filas de una ejecución. Dos ejecuciones de la misma semana
  tienen IDs diferentes.

`ejecuciones_screener.csv` contiene una sola fila por snapshot válido con
su origen, si fue declarado oficial, su número de revisión, tickers
solicitados, descargas correctas y fallidas, listings deduplicados, empresas
evaluadas, candidatas y tasa de éxito. En GitHub también guarda `github_run_id`,
`github_run_attempt`, `github_run_url` y `github_sha`, para enlazar el snapshot
con el run exacto, distinguir sus reintentos y saber qué commit se ejecutó;
en ejecuciones locales esos campos quedan vacíos. `universe_id`,
`universe_sha256` y `universe_path` congelan además la lista exacta que se
evaluó. Las ejecuciones anteriores a este modelo dejan esos tres campos vacíos
porque no se reconstruyen metadatos retrospectivos sin garantía. Cada snapshot válido incrementa la
`revision` de su semana, sea prueba u oficial. Si hay varias revisiones
marcadas como oficiales, la de mayor número es la **oficial efectiva**; las
anteriores no se borran, pero dejan de alimentar señales, seguimiento y la
Bitácora. Cero candidatas no se considera un fallo: lo que bloquea el
histórico es una descarga poco fiable o una estructura incoherente.

Los snapshots anteriores a este control tienen `snapshot_id` en el journal,
pero no una fila retrospectiva de control, porque esos recuentos no pueden
reconstruirse con garantía. Se consideran snapshots oficiales *legacy* para
conservar el historial ya publicado.

```bash
# Ejecutarlo tú mismo, igual que lo hace la Action:
python ejecutar_semanal.py --universo-activo journal_candidatos.csv

# Prueba ad hoc (nunca puede marcarse oficial):
python ejecutar_semanal.py mi_lista.txt journal_prueba.csv control_prueba.csv
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

1. Lee `journal_candidatos.csv`, excluye las pruebas y revisiones oficiales
   sustituidas mediante `ejecuciones_screener.csv`, e identifica cada
   transición válida de
   `pasa=False` a `pasa=True`. Si el ticker sigue pasando en ejecuciones
   posteriores, conserva la señal original y su fecha de entrada; si deja
   de pasar y más adelante vuelve a hacerlo, abre una señal nueva. Una
   descarga fallida o la ausencia del ticker en un snapshot no cuentan como
   salida. Cada señal se identifica por `(ticker, fecha_entrada)`, sin
   cambiar el esquema del CSV existente.
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

### Bitácora del Screener (Artifact estático)

Complemento a la vista "Histórico" del dashboard: una página HTML
autocontenida publicada como Claude Artifact -privada por defecto, gratis,
sin necesitar GitHub Pages (que exige un plan de pago para repos privados
y, aun así, publica la página en abierto). Muestra las mismas tres cosas
que la vista "Histórico" (KPIs, candidatas de la última ejecución, journal
filtrable, rendimiento trackeado con gráfico de evolución), pero como una
página que se puede compartir sin tener Streamlit corriendo.

**No se actualiza sola** -un Artifact es una página publicada, no un
servidor; no puede leer el repo en directo. El refresco es en dos pasos:

1. La misma GitHub Action, inmediatamente después del seguimiento de un
   snapshot oficial, ejecuta `exportar_historico.py` y commitea
   `historico.json` junto con los CSV. El JSON contiene solo snapshots
   oficiales efectivos y los controles de ejecución.
2. Cuando quieras ver la Bitácora al día, pide "actualiza el Artifact del
   histórico": se lee `historico.json` y se republica en la misma URL. Este
   paso sigue siendo manual porque publicar un Artifact solo se puede hacer
   desde una sesión de Claude Code, no desde una Action.

La antigua rutina cloud de las 09:00 UTC ya no es necesaria y debe quedar
desactivada fuera del repositorio para que no compita con este workflow al
hacer `git push`.

```bash
# Regenerar historico.json a mano en cualquier momento:
python exportar_historico.py > historico.json
```

### Gráfico de TradingView

Tanto en "Analizar en vivo" como en "Histórico", un selector deja elegir
cualquier ticker evaluado y ver su gráfico de precio real, embebido con el
widget gratuito de TradingView (`tradingview.py`).

**Por qué vive en el dashboard de Streamlit y no en el Artifact de
histórico**: los Artifacts de Claude tienen una CSP estricta que bloquea
cualquier script externo salvo Google Fonts -TradingView no puede cargar
ahí bajo ninguna circunstancia. Streamlit, al ser una página normal servida
localmente, no tiene esa restricción.

yfinance y TradingView usan formatos de ticker distintos (`ITX.MC` frente a
`BME:ITX`) y no existe una conversión oficial entre ambos. El mapeo de
`tradingview.py` está **verificado a mano, símbolo por símbolo, contra el
widget real** para los mercados de `universo.txt` -no es una tabla
inventada. Limitaciones reales encontradas durante esa verificación:

- Los tickers sin sufijo (EE.UU.) se pasan tal cual: TradingView los
  resuelve sin necesitar el prefijo de bolsa.
- "EURONEXT" cubre Ámsterdam y París (verificado); se asume que también
  cubre Bruselas y Lisboa por ser el mismo grupo, pero no se ha probado
  símbolo a símbolo.
- Algunos símbolos válidos están disponibles en tradingview.com pero el
  widget gratuito los bloquea con "solo disponible en TradingView" -pasó
  con Ryanair (Dublín) aunque el mercado irlandés en general funciona. Esto
  depende de la licencia de datos de cada valor, no del mercado.
- Cuando el símbolo no resuelve, el propio widget deja buscarlo a mano
  haciendo clic en su nombre -no hay forma de detectar el fallo desde
  fuera del iframe para mostrar un aviso automático.

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

- **Procedencia y periodo por fila.** `candidatos.csv` y los nuevos snapshots
  del journal conservan `proveedor_datos`, `fecha_consulta_utc`,
  `tipo_periodo`, fechas de resultados/caja/balance, `calidad_datos` e
  `incidencias_datos`. Al migrar el journal, esas columnas quedan vacías en
  snapshots antiguos: no se inventa retrospectivamente información que no se
  guardó.

- **TTM solo si resultados y flujo de caja son TTM.** Si Yahoo ofrece TTM
  para uno pero no para el otro, ambos se toman del último anual. También se
  marcan estados con más de 550 días, fechas futuras o resultados y caja
  desalineados más de 45 días. Una fila `revisar`, `inutilizable` o `error`
  no puede convertirse en candidata aunque sus ratios aislados parezcan
  superar los umbrales.

- **Resumen de incidencias en Actions.** Al final de cada ejecución se
  muestra cuántas empresas tienen campos esenciales ausentes, divisas
  problemáticas, cuentas obsoletas, fechas ausentes/futuras, periodos
  desalineados, valores inválidos o errores de descarga. Una empresa cuenta
  una sola vez dentro de cada categoría, aunque acumule varias incidencias
  del mismo tipo; también se desglosan los campos ausentes más frecuentes.

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
- **Datos todavía sin contraste externo.** La arquitectura ya permite
  inyectar otro proveedor y detecta incoherencias internas de Yahoo, pero no
  decide cuál de dos cifras discrepantes es correcta. La siguiente capa
  prevista es contrastar solo las candidatas contra filings/SEC u otra fuente,
  sin rellenar campos silenciosamente.

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
      pasadas: retorno TWR y drawdown máximo sobre precio de cierre ajustado
      desde la entrada de cada señal, incluidas reentradas después de dejar de
      pasar los filtros, sin look-ahead bias y acumulado semana a semana en
      `seguimiento_candidatas.csv`.
- [x] Fase 5 — interpretación cualitativa de las candidatas: en vez de
      integrar una API concreta, `prompt_llm.py` genera un prompt listo
      para copiar y pegar en el LLM que prefieras (Claude, ChatGPT, Gemini
      u otro), a coste $0 y sin atarse a ningún proveedor, con
      instrucciones explícitas para resumir datos sin inventar una tesis
      de inversión.
- [x] Extra — Bitácora del Screener: página estática (Claude Artifact) con
      el mismo histórico que la vista "Histórico" del dashboard, refrescada
      con `exportar_historico.py` (exportación dentro de la misma GitHub
      Action oficial, publicación manual). Y gráfico de precio real de
      TradingView (`tradingview.py`) embebido en el dashboard -no en el
      Artifact, cuya CSP bloquea scripts externos-, con el mapeo de
      tickers verificado símbolo a símbolo contra el widget real.
