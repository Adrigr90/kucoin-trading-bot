# ESPECIFICACIÓN VIGENTE: FASE 0 (CONSOLIDADA)

**Proyecto:** `kucoin-trading-bot` · **Consolidada el:** 5 de noviembre de 2026 · **Fuente de verdad:** `01_Documento_Maestro.md` (DM v1.0) · **Versión 1.3** (7 de noviembre de 2026: hechos comprobados en la etapa E1 de la Fase 2) · Versión 1.2 (6 de noviembre de 2026: ronda 4 de revisión, antes de la Fase 2) · Versión 1.1 (revisión de la sección 9, antes de la Fase 1) · Versión 1.0 congelada el 6 de noviembre de 2026 (cierre de la Fase 0)

**Regla de congelación:** desde la versión 1.0, cualquier cambio en este documento necesita una entrada en la sección 18 y la aprobación del propietario. Las rondas 4-7 se hacen justo antes de cada módulo (D35) y sus correcciones siguen esta misma regla.

Este documento **sustituye, para leer y auditar**, al Informe V4 y a los anexos V4.1-V4.10, que quedan como archivo histórico. **Solo contiene las reglas vigentes**: lo sustituido se ha eliminado.

**Regla de consolidación:** el revisor independiente revisó los hallazgos H01-H18 de la auditoría del V4.10 en la ronda 1 (5 de noviembre de 2026). **Los confirmados están aplicados en este documento** y marcados con [Hxx]. Eso los deja **especificados, no validados**. El propietario aceptó H09 el 5 de noviembre de 2026, y H16 no exige cambios. Detalle en `04_Pendientes.md` y en la sección 18.

**Estado global:**

| Estado | Situación |
|---|---|
| Fase 0 | **Cerrada** el 6 de noviembre de 2026 (D35). Fase 1 autorizada |
| Especificado | Sí, con pendientes |
| Verificado documentalmente | Parcial |
| Validado mediante pruebas | **Fase 1** (6 de noviembre de 2026): H07, B18, B20, B25, B26, RC14, RC15 y la parte de PostgreSQL y roles de la sección 9. Informe en `docs/informes/Informe_Fase1.md` del repositorio. **Fase 2**, etapas E1 y E2 (7 y 9 de noviembre de 2026): criterios F2-A1 a F2-A8, salvo los bytes por día de F2-A8, que se miden en E5 (`docs/informes/Trazabilidad_aceptacion.md`) |
| Implementado | **Fase 1** integrada en `main` (pull requests #1 y #2, 6 de noviembre de 2026). **Fase 2:** etapas E1 (pull request #3, 7 de noviembre de 2026) y E2 (pull request #4, 9 de noviembre de 2026) integradas en `main` |
| Decisiones D01-D35 aprobadas | **10**: D17, D33 y D35, el 5 de noviembre de 2026; D18 y D03, el 6 de noviembre; D04, D01, D02, D11 y D25, el 7 de noviembre (ver `03_Decisiones.md`) |

**Etiquetas:** [H] hecho oficial verificado (fuente en la sección 16) · [I] inferencia técnica · [R] recomendación · [P] pendiente · [Cxx] corrección de origen · [Dxx] decisión del propietario (pendiente salvo que se indique «aprobada») · [Hxx] hallazgo abierto.

---

## 0. Alcance y principios

**Alcance operativo (DM):**
- Solo análisis y paper trading.
- **Ninguna clave de KuCoin**, ni siquiera de lectura: todos los datos necesarios son públicos [H].
- Sin órdenes reales, sin permisos de trading ni de retirada.
- Simulador propio.
- Infraestructura a 0 €; IA como máximo 25 €/mes.

**Alcance contable** [D33, aprobada el 5 de noviembre de 2026]:
- **Incluido:** Spot con pares cotizados en USDT, y Futures **lineales** con margen y liquidación en USDT (`isInverse = false` [H]).
- **Excluido:** contratos inversos, liquidación en otra moneda, pares no USDT, descuento de comisiones en KCS y cualquier otro instrumento fuera de este alcance.

**Principios transversales:**
1. **Ante un fallo, se bloquea** (fail-closed): si falta un dato, un servicio o una validación, se bloquean las entradas. **Las salidas reduce-only están siempre permitidas.**
2. **Registros críticos solo de inserción** (ledger, decisiones, auditoría). Las correcciones se hacen con asientos nuevos.
3. **La misma lógica en backtest y en paper trading.** Solo cambian el reloj y la capa transaccional.
4. **Nunca se inventan** precios, ejecuciones, secuencias ni ausencias de operaciones.
5. **Ningún redondeo es implícito.** Todo redondeo sigue una regla y queda registrado.
6. **Las fronteras de seguridad las imponen el sistema operativo y PostgreSQL**, no clases de Python.

## 1. Arquitectura

### 1.1 Procesos (un usuario del sistema y un rol SQL por proceso)

| Proceso | Responsabilidad | Acceso a red |
|---|---|---|
| `svc-ingest` | Datos de KuCoin, calidad, universo dinámico, anomalías | Solo KuCoin |
| `svc-news` | Noticias, verificación, IA, presupuesto de IA | Fuentes de noticias y Anthropic |
| `svc-strategy` | Indicadores, 5 familias, combinador, régimen | **Ninguno** |
| `svc-risk` | Autorización atómica, estados de riesgo | **Ninguno** |
| `svc-broker` | Simulador Spot y Futures, ledger, clasificador de resultados | **Ninguno** |
| `svc-watchdog` | Invariantes, salud, parada de emergencia. **Funciona aunque PostgreSQL esté caído** | Solo Telegram |
| `svc-telegram` | Notificaciones, consultas, solicitud de pausa | Solo Telegram |
| `svc-web` | Panel en `127.0.0.1`, acceso por túnel SSH (D06) | Ninguno |

**Temporizadores:** copias de seguridad, prueba de restauración, sellado diario, validación del Parquet, aprendizaje (solo propuestas), informes y barrido de reservas.

### 1.2 Comunicación

- **Base de datos:** PostgreSQL 18 por socket Unix (sin TCP), con autenticación `peer` (sin contraseñas).
- **Eventos persistentes** (C01): patrón outbox. `NOTIFY` solo sirve como aviso; además, cada consumidor consulta la cola cada 2 s [H: `NOTIFY` se pierde si nadie escucha].
- **Cadena principal:** `candle_closed` → svc-strategy → `intent_created` → svc-risk (autorización atómica) → `order_approved` → svc-broker (ejecución, posición y ledger) → `fill_recorded` / `position_closed` → Telegram e informes.
- **Trazabilidad:** un mismo `correlation_id` de la señal al cierre. La vista `trade_journal` reconstruye cada operación.

### 1.3 Esquemas y tablas principales

| Esquema | Tablas principales |
|---|---|
| `market` | `instruments`, `risk_tiers`, `candles` (last, mark, index; con estado de vela), `ws_candles`, `exhausted_index_rebuilds`, `depth_metrics`, `depth_snapshots`, `depth_slot_status`, `depth_late_books`, `funding_rates`, `funding_symbol_map`, `data_quality_minute`, `minute_status`, `trading_seen_late`, `late_ws_candles`, `late_ws_repeats`, `data_gaps`, `universe_snapshots`, `correlation_snapshots` |
| `trading` | `paper_accounts`, `signals`, `intents`, `risk_decisions`, `risk_reservations`, `close_reservations`, `approved_orders`, `paper_orders`, `paper_fills`, `paper_positions`, `outcome_criteria`, `equity_snapshots`, `risk_state` |
| `ledger` | `accounts` (cada una con su unidad), `journal`, `postings` (etiquetadas con `position_id`), plantillas |
| `news` | `sources`, `items`, `events`, `verification_log`, `sentiment` |
| `ai` | `budget_months`, `reservations`, `calls`, `price_table` (versionada) |
| `ops` | `events`, `event_deliveries`, `halt_flags`, `heartbeats`, `admin_requests`, `audit_log` (encadenado por hash), `manifests`, `seals`, `system_events`, `telegram_outbox` |
| `config` | `risk_limits` (solo escribible por `kbot_owner`), `strategy_versions` (con aprobación), `proposals`, `tolerance_versions` |

**Unicidades clave:**
- señal por (estrategia, versión, instrumento, temporalidad, vela, dirección);
- `intents.dedupe`;
- `approved_orders.intent_id`;
- `paper_orders.approved_order_id`;
- `events.dedupe_key`;
- funding por (posición, $T_k$);
- idempotencia por (cuenta, tipo, `dedupe_key`).

## 2. Tecnología

| Área | Elección |
|---|---|
| Sistema operativo | Ubuntu Server 24.04 LTS ARM64 (Oracle A1) |
| Base de datos | PostgreSQL 18 [H: 18.6; soporte hasta el 14 de noviembre de 2030]. Particionado nativo y ventana caliente de 90 días. **Sin TimescaleDB al inicio** [D17, aprobada el 5 de noviembre de 2026] [H: hay paquetes ARM64 si se necesitan] |
| Histórico | Parquet (zstd), inmutable, consultado con Polars o DuckDB |
| Lenguaje | Python 3.12, `uv`, `httpx`, `websockets`, `json` de la biblioteca estándar con `Decimal` (no `orjson`, que lee los decimales como `float` y rompería la regla 5 de §0), `pydantic` v2, `psycopg 3`, `SQLAlchemy 2`, `Alembic`, `numpy`, `polars`, `structlog`, `anthropic`, `python-telegram-bot`, `fastapi`, `argon2-cffi`, `pyotp`, `feedparser`, `trafilatura`, `rapidfuzz` |
| Indicadores | Implementación propia, validada contra TA-Lib en las pruebas [P: paquetes para aarch64] |
| Cliente de KuCoin | Propio y **sin el SDK oficial**, que incluye órdenes. **REST: solo GET** y con lista blanca de rutas. **WebSocket: su propia lista blanca de mensajes** (3.1) |
| Procesos | systemd (`User=`, `IPAddressDeny=`, `ProtectSystem=strict`, `LoadCredential=`, `MemoryMax=`) |
| Backtests | En el PC del propietario, con WSL2 [D18, aprobada el 6 de noviembre de 2026]. El resto del trabajo del propietario es en Windows |

## 3. Datos de mercado de KuCoin

### 3.1 Acceso y límites

- **Pool público:** 2.000 de peso cada 30 s por IP [H]. Se usa como máximo el 50 %, con una **ventana deslizante de 30 s** y lectura de las cabeceras `gw-ratelimit-*` [H]. No se usa un token bucket: lleno al empezar y con su recarga, permitiría hasta el doble del 50 % dentro de una misma ventana. **Un solo presupuesto por máquina**, compartido por todos los clientes REST de un único proceso: el primero que lo pide toma un bloqueo exclusivo del sistema operativo y cualquier otro proceso recibe un error en lugar de un segundo presupuesto. Así las peticiones en vuelo siempre están contadas. En el servidor, el archivo de bloqueo (`KBOT_REST_LOCK`) debe estar en una ruta común a todos los servicios, no en un `/tmp` privado (E4). Las cabeceras del servidor quedan como segunda barrera frente al tráfico de otras máquinas tras la misma IP, que solo pueden detectar después de cada respuesta. Los pesos de todas las rutas de la lista blanca se midieron con estas cabeceras el 6 de noviembre de 2026 [H, `docs/kucoin/matriz_v1_v2.md`].
- **Error 429000 [H], con dos causas que se tratan por separado:**
  - **cuota agotada:** las cabeceras indican cuándo se puede volver a pedir; se paran todas las peticiones hasta `gw-ratelimit-reset`;
  - **sobrecarga temporal del servidor:** la respuesta llega sin la información de límite en las cabeceras y KuCoin recomienda reintentar más tarde; se paran todas las peticiones y se espera con backoff exponencial y aleatorio, de 5 s a 5 min [R].
  - En ningún caso hay reintentos rápidos. El 429001 (límite del sistema, en la tabla de errores de UTA V2 [H]) se trata como la sobrecarga.
- **WebSocket UTA público:**
  - sin token [H], en `wss://x-push-spot.kucoin.com` y `wss://x-push-futures.kucoin.com` [H];
  - ping según `pingInterval` (18.000 ms el 6 de noviembre de 2026) [H]. La documentación da dos formatos de ping (`"type": "ping"` y `"op": "ping"`); **`{"id", "type": "ping"}` recibe su pong** [H, prueba de contrato de E1]. Falta comprobar que la conexión aguanta 24 h con ese formato [P: B31, en E5];
  - la conexión se corta a las 24 h [H]; el cliente la reabre por su cuenta a las 23,5 h y registra el hueco;
  - sin pong en unos 3 s, la conexión se da por rota [H];
  - como máximo 300 mensajes del cliente cada 10 s, contando suscripciones, cancelaciones y pings [H].
- **Lista blanca de mensajes del WebSocket:** solo la conexión a los dos endpoints públicos, la suscripción y la cancelación de los canales públicos de mercado de la lista (fijada con la matriz V1/V2 de 3.2) y el ping. Se rechazan, antes de enviarlos, los mensajes de autenticación (`op: auth`), los canales privados, los de órdenes y cualquier canal fuera de la lista.
- **Diseño del WebSocket:**
  - ≤ 100 topics por conexión (la documentación da cifras contradictorias: 200, 400 o 600 [H]);
  - ≤ 5 conexiones;
  - ≤ 1 conexión nueva cada 5 s ([H] límite: 300 cada 5 min por IP en Spot público).
- **Profundidad:** nivel 5 para todo el universo; nivel 50 solo para los candidatos de scalping [D03].
  - **Fuente:** canal `obu` de UTA con profundidad 5 o 50. Cada mensaje es un **snapshot completo** que se puede usar directamente, sin mantener un libro local. Se publica como mucho cada 100 ms, y **no se publica nada si el libro no cambia** [H]. No se usa el canal incremental.
  - **No se guardan los mensajes originales.** Se trabaja con una rejilla de intervalos de muestreo de **Δ = 10 s** [R].
  - **En cada intervalo y canal:** un estado terminal (3.5) y una fila de métricas calculadas con el último snapshot válido, con la antigüedad de ese snapshot. De nivel 5 para todo el universo y de nivel 50 para los candidatos.
  - **Snapshot completo guardado:** el último libro válido al final de cada minuto, de nivel 5 para todo el universo y de nivel 50 para los candidatos [R].
  - **Candidatos de nivel 50:** 10 pares de Spot y 10 de Futures, los más líquidos del universo [aprobado por el propietario con el alcance de la F2, P3].
  - **Estimación** [I], con los supuestos del Informe V4 (180 B por métrica, 32·N + 60 B por snapshot de N niveles en total y 150 B por vela de last, mark o index), 40 B por estado de intervalo, un universo de 20 + 20 pares y 20 candidatos: unos **259 MB al día** (cada candidato añade ≈ 6,6 MB). Base de datos con 90 días calientes: **≈ 28 GB**, con un 20 % de margen. Parquet: **≈ 95 GB al año sin comprimir** y ≈ 12-32 GB comprimido (÷3 a ÷8, compresión sin medir). Con la compresión más baja supuesta cabe en el volumen de datos de 110 GB (10.3): ≈ 60 GB el primer año y ≈ 92 GB el segundo. **Sin compresión no cabría** (≈ 123 GB el primer año), así que la medición de la F2 es obligatoria.
  - Guardar el nivel 50 cada 10 s en lugar de cada minuto llevaría el volumen a ≈ 730 MB al día: **no cabe en el disco gratuito y no se adopta**.
  - **Se mide en la F2:** bytes por instrumento y día de cada canal, proyectados a un año. Si la proyección no cabe en el volumen aprobado, se reducen las frecuencias o el universo, con decisión del propietario. **Nunca se superan los 200 GB ni se contrata nada.**

### 3.2 Endpoints

**Versión de UTA: D04 aprobada el 7 de noviembre de 2026 [H08]: UTA V2 más Classic.** Existen V1 y V2, con **formatos de respuesta distintos**; el bot usa solo V2 y las rutas V1 no están en la lista blanca. No se suponen formatos. **La matriz V1/V2 y las pruebas de contrato con respuestas reales están hechas** (etapa E1, 6 de noviembre de 2026: `docs/kucoin/matriz_v1_v2.md` y `tests/contract/` del repositorio). Hechos comprobados [H]: las velas REST son `[apertura en s, open, high, low, close, volumen, turnover]` (coinciden con la vela etiquetada del WebSocket del mismo minuto); `klineType` solo admite `TRADE` (MARK e INDEX se rechazan con el código 148035), así que **la API UTA no da velas de mark ni de index**; con un símbolo, `productType` no cambia la respuesta del funding V2; V2 envía todos los decimales como texto y V1 mezcla números JSON y texto. Observación [I]: en los minutos grabados, el volumen y el turnover de la vela son exactamente el doble de la suma de las operaciones del canal `trade` (B8 sigue abierta).
- **Antes de implementar la ingesta (F2)** se elabora una **matriz de revisión V1/V2** de **cada** endpoint usado, con: endpoint, versión, parámetros, estructura de la respuesta, unidades, campos obligatorios y comportamiento ante respuestas vacías y errores.
- **Pruebas de contrato independientes** para cada versión que se use.
- **No se da por verificado** ningún endpoint ni formato que no esté descrito en esta especificación.

| Uso | Endpoint |
|---|---|
| Instrumentos | UTA `GET /api/ua/v1/market/instrument` (peso 4). Campos [H]: `baseOrderStep`, `minBaseOrderSize`, `minFunds`, `tickSize`, `feeCurrency`, `tradingStatus` |
| Ticker | UTA `/api/ua/v1/market/ticker` (peso 15) |
| Velas V1 | `GET /api/ua/v1/market/kline` |
| Velas V2 | `GET /api/ua/v2/market/kline`, con `klineType` y respuesta `{tradeType, symbol, list}` [H] |
| Límites de velas (V2) [H] | Spot: 1.500 por petición y sin límite de antigüedad. Futures: 200 por petición e intervalos < 8 h **solo desde el 1 de enero de 2025**. **No hay vela si no hubo operaciones** en Futures; **en Spot, KuCoin publica una vela plana de volumen 0** (open = high = low = close, igual al cierre anterior) [H, medido el 8 nov 2026: BNB-USDT, 42 de 180 minutos; TRXUSDTM, 10 minutos sin vela y ninguna de volumen 0]. Las velas "pueden ser incompletas" |
| Funding actual V1 | `GET /api/ua/v1/market/funding-rate`. `data` es un **objeto** y `symbol` = `.XBTUSDTMFPI8H` [H] |
| Funding actual V2 | `GET /api/ua/v2/market/funding-rate`. `data` es una **lista** y `symbol` = `XBTUSDTM`. Peso 2, público [H] |
| Campos del funding [H] | `nextFundingRate`, `fundingTime`, `fundingRateCap`, `fundingRateFloor`, `currentGranularity`, `newGranularity`, `newGranularityStartTime` |
| Histórico de funding | Público [H] |
| Metadatos de Futures (Classic) | `GET /api/v1/contracts/active`: `multiplier`, `lotSize`, `maxOrderQty`, `maintainMargin`, `maxRiskLimit`, comisiones, `fundingRateGranularity`, `isInverse` [H] |
| Tramos de riesgo (Classic) | `GET /api/v1/contracts/risk-limit/{symbol}` (peso 5), público [H, respuesta real grabada en E1] |
| Mark price actual (Classic) | `GET /api/v1/mark-price/{symbol}/current` (peso 3): solo el valor actual [H, E1] |
| Índice (Classic) | `GET /api/v1/index/query` (peso 2): histórico paginado del índice del contrato (`indexSymbol`, por ejemplo `.KXBTUSDT`) [H, E1] |
| Operaciones recientes | UTA Get Trades: **solo las 100 últimas**, con `sequence`, `tradeId` y `ts` [H] |

### 3.3 Universo, libro de órdenes y anomalías

- **Universo dinámico:** liquidez, volumen, spread, volatilidad y disponibilidad de datos. Objetivo inicial de 20 pares Spot y 20 de Futures [D12].
- **Libro de órdenes VÁLIDO:**
  - en los canales incrementales, sincronizado desde un snapshot y con la secuencia continua; si se pierde algo, pasa a **INVÁLIDO** hasta resincronizar;
  - en los canales de snapshot, debe ser reciente y con marcas de tiempo que avanzan. Como KuCoin no publica nada si el libro no cambia [H], **el silencio con la conexión sana** (pings respondidos a tiempo) **no invalida el libro**: el último snapshot sigue vigente y se registra su antigüedad. Es inconsistente un snapshot cuya secuencia (`C`) o cuya hora de publicación (`P`) retrocede respecto al anterior de la suscripción, un libro tardío (publicado en la suscripción en curso en un intervalo ya cerrado, 3.5), uno con el libro cruzado (mejor compra ≥ mejor venta) o con tamaños no positivos; el libro vuelve a ser válido con el siguiente snapshot correcto [tercera y cuarta revisión de E2]. Un snapshot publicado antes de la suscripción en curso no es tardío: es el libro que KuCoin tenía entonces. Si la secuencia es igual a la anterior, es un duplicado y se ignora. En los snapshots, los campos `O` y `C` son las secuencias inicial y final [H]; el significado del campo de tiempo `M` no está documentado [P: B1].
- **Las métricas de profundidad solo se calculan con un libro válido.**
- **Anomalías** (bloquean las señales del instrumento):
  - high < low;
  - saltos mayores de k·ATR;
  - el ticker y la vela no coinciden;
  - desviación del reloj.

### 3.4 Calidad del flujo y de las velas

**Estado del flujo por canal y minuto** (C60):

| Estado | Condición | Qué permite afirmar |
|---|---|---|
| **S1** | Conexión continua, ping a tiempo, canales periódicos dentro de su frecuencia y sin saltos de secuencia | Sin operaciones recibidas y sin vela oficial: solo **`sin_operaciones_probable`**, nunca una certeza [P: continuidad del WebSocket no verificada] |
| **S2** | Reconexión, silencio anómalo o salto | Nada sobre ausencia de operaciones |
| **S3** | Sin datos y sin REST suficiente | Nada |

**Dimensiones de consistencia de la vela:**
- **N1-P:** precios, con tolerancia de 1 tick.
- **N1-V:** volumen y turnover, con tolerancias **separadas** y unidades normalizadas.
- **N1-T:** presencia y marca de tiempo.

| Indicador o estrategia | Dimensiones que necesita |
|---|---|
| RSI, MACD, EMA/SMA, Bollinger, ATR, ADX, soportes y resistencias | P + T |
| VWAP, volumen relativo, breakout con volumen | P + V + T |
| Volumen y flujo de mercado; scalping | P + V + T + **profundidad válida** |

**Evidencia de la vela** (C72): `vela_cruzada_consistente` (REST y WebSocket comparados) o `vela_rest_suficiente` (solo REST, con el flujo en S2 o S3). Se guardan por separado. Si se deben excluir de la serie consistente los resultados basados solo en REST lo decide **D29**.

**Estado de la vela** (C77, una dimensión distinta de la evidencia):

| Estado | Significado |
|---|---|
| `abierta` | Intervalo en curso |
| `pendiente_estabilizacion` | Ha terminado, pero no ha pasado el margen de publicación |
| `cerrada_rest` | Cerrada con datos REST |
| `cerrada_cruzada` | Cerrada y comparada entre fuentes |
| `discrepante` | La comparación falló |
| `ausencia_sin_trades_probable` | Flujo en S1 todo el minuto, sin operaciones recibidas y sin vela oficial. **Es una inferencia, no una certeza** [H12] |
| `ausencia_no_verificable` | No se puede saber por qué falta |

- **Nunca se fabrican velas.**
- **Una vela es cerrada** si su intervalo ha terminado, el reloj está sincronizado, ha pasado el margen de estabilización, hay evidencia y se han validado sus campos.
- **Alcance de esta regla [H13]:** solo se aplica a la **generación de señales** y a los indicadores.
  - **Los stops, las liquidaciones y el funding se evalúan con el flujo** (3.6 y sección 7) y **nunca esperan** a que la vela se cierre.
  - La latencia añadida a las señales se mide frente a un límite declarado.
  - El consumo de REST para cerrar velas (estimación [I]: unos 60 de peso cada 30 s con 40 instrumentos) **se medirá en la F2** con las cabeceras `gw-ratelimit-*`.

**Tolerancias de volumen y turnover** (procedimiento [H06, H17]; los valores van a D26; falta validarlo con datos):
1. **Por tramos:** la diferencia $|V_s - V_r|$ se calibra **por separado en cada tramo de volumen** del instrumento (por ejemplo, cuantiles de $\max(V_s, V_r)$). La tolerancia de un minuto es la de su tramo, nunca una tolerancia global. Lo mismo para el turnover, con tramos y valores propios.
2. **Prueba de sesgo con signo:** en cada tramo, el intervalo de confianza de la media de $V_r - V_s$ debe contener 0. Si no lo contiene, el flujo pierde operaciones de forma sistemática: el instrumento se marca como `calidad_volumen_insuficiente` y **no se calibra una tolerancia que absorba ese sesgo**.
3. **Tope máximo** de tolerancia por tramo, en proporción al volumen mediano del tramo. Si la calibración lo supera, se marca `calidad_*_insuficiente` y el instrumento no se usa en las estrategias de volumen. **Nunca se amplía la tolerancia.**
4. **Validación con muestras independientes:** se calibra con los primeros días de la ventana (por ejemplo, 10 de 14) y se valida con los restantes. En la parte de validación, la cobertura debe alcanzar el nivel nominal.
5. **Detección de pérdidas inyectadas:** en la validación se eliminan operaciones a propósito en volúmenes bajos, medios y altos. La tasa de falsas consistencias debe quedar por debajo del máximo fijado (D26).
6. **Casos con cero [H17]:**
   - ambos volúmenes 0: consistente;
   - solo uno es 0: solo se compara la diferencia absoluta con la tolerancia del tramo más bajo, sin término relativo;
   - ambos positivos: regla normal del tramo.
7. **Fuente REST:** KuCoin advierte de que sus velas pueden ser incompletas [H], así que la comparación demuestra **consistencia, no integridad**.
8. Calibración **mensual y versionada**. Cada resultado guarda la versión de tolerancias que se usó.

- **Unidad del volumen de Futures:** se guarda la original. Si su semántica no está documentada, no se usa [P].

### 3.5 Completitud

- **Convención de tiempo:** UTC, microsegundos e intervalos $[inicio, fin)$.
- **Elegibilidad** $\mathcal{E}_i$: el instrumento está en el universo **y** cotizando (`tradingStatus` [H]).
- **Categorías de minuto:** `completo_elegible` (cuenta en el denominador), `parcial_elegible` (se informa aparte) y `no_elegible`.
- **Estado terminal de cada minuto** (`minute_status`): `datos`, `hueco`, `sin_operaciones_probable` o `sin_explicar`. El día es **completo** solo si $M_{sin\_explicar} = 0$.
- **Correspondencia entre clasificaciones** [H12]. El estado terminal del minuto se asigna en el proceso de cierre del día, cuando ya ha pasado el margen de estabilización:

| Estado de la vela (3.4) | Estado del flujo | `minute_status` (terminal) |
|---|---|---|
| `cerrada_cruzada` o `cerrada_rest` | Cualquiera | `datos` |
| `discrepante` | S1 | `datos`, con la discrepancia registrada (F2-V parcial o con fallo) |
| `ausencia_sin_trades_probable` | **S1 todo el minuto** y sin vela oficial | `sin_operaciones_probable` |
| `ausencia_no_verificable` | S2 o S3 | `hueco` si consta en `data_gaps`; si no, `sin_explicar` |
| `abierta` o `pendiente_estabilizacion` | — | Todavía no es terminal; si sigue así al cerrar el día, `sin_explicar` |
- **Profundidad por intervalo de muestreo** (Δ = 10 s, 3.1): `valida`, `libro_inconsistente`, `no_recibida`, `desconectado` o `fuera_de_plazo`. Los duplicados no crean intervalos. **Una desconexión nunca sale del denominador.** Cada intervalo recibe el primero de estos estados que se cumpla, en este orden:
  - `desconectado`: hubo una desconexión durante el intervalo;
  - `libro_inconsistente`: el snapshot vigente es inconsistente (3.3);
  - `fuera_de_plazo`: un snapshot publicado en el intervalo llegó después de su plazo, **haya o no un snapshot vigente anterior** [auditoría de E2, hallazgo E2-03]. La latencia normal de la red cabe en el margen y nunca lo provoca [H, prueba en vivo del 8 nov 2026: con la definición estricta, casi todos los intervalos salían fuera de plazo];
  - `no_recibida`: conexión sana, pero ningún snapshot desde la última suscripción, ni a tiempo ni tarde;
  - `valida`: la conexión estuvo sana todo el intervalo y hay un snapshot vigente y consistente: el último publicado por KuCoin (hora `P`) antes del fin del intervalo y recibido antes de su plazo (fin más el margen de retraso L, provisional 2 s, D26); sin cambios no hay mensajes (3.3).
- **Evidencia tardía** [cuarta revisión de E2]. Un intervalo o un minuto se cierra cuando la conexión ya ha dado una prueba de vida posterior a su plazo (3.8), pero eso no demuestra que haya llegado todo lo publicado en él. Lo que llega después y contradice un resultado ya escrito se guarda, y ese resultado cuenta como el peor caso (3.6) sin que su fila cambie:
  - **Libro tardío:** publicado en la suscripción en curso, en un intervalo ya cerrado. Queda en `depth_late_books`, y cada intervalo de su canal desde el suyo hasta el último cerrado cuando llegó, si se escribió `valida` o `no_recibida`, cuenta como `fuera_de_plazo`, con sus métricas y el snapshot de su minuto. Los demás estados ya dicen que el intervalo no estuvo sano o que su libro llegó tarde, y se mantienen.
  - **Trades y velas del WebSocket de un minuto ya cerrado** [cuarta a sexta revisión de E2]: el grabador los entrega todos a la base de datos, que guarda solo los que cambian lo que el minuto puede decidirse, juzgándolo con lo que tiene guardado. Así un reinicio entre el cierre del minuto y su llegada no cambia nada. Solo en minutos con el flujo `trade` en S1, porque con S2 o S3 el WebSocket no decide nada:
    - **Un trade** (por su hora de ejecución `M`) va a `trading_seen_late` si no llegó a tiempo ningún trade del minuto y el WebSocket no había visto operaciones en él: ninguna vela suya con volumen, a tiempo o tardía, y ningún trade tardío ya guardado. Una vela de volumen 0 no es una operación. Descarta la ausencia probable de operaciones y contradice una vela REST de volumen 0.
    - **Una vela** va a `late_ws_candles` si es más reciente (`P`) que todas las vistas del minuto y distinta de la última. Si es más reciente y la misma, como la vela de cierre que KuCoin Futures reenvía con la primera operación del minuto siguiente [H, grabación de E1], solo se guarda su hora, en `late_ws_repeats`: no cambia ninguna vela, pero una vela publicada antes que ella nunca pasa por la última, tampoco tras un reinicio. Si no es más reciente, porque es anterior o tiene la misma `P` que una ya vista, solo se guarda cuando su volumen es la primera señal de operaciones del minuto, y nunca pasa por la última (`latest = false`) [séptima y octava revisión de E2]. La vela del WebSocket de un minuto, con la que se comparan los precios, es la última por `P` de `ws_candles` y de las velas tardías guardadas como última (`latest = true`). Cada una de estas es más reciente que todas las anteriores, así que nunca hay empate y la última es siempre una. Una vela guardada tarde como última y otra distinta que solo muestra operaciones pueden tener la misma `P`, y se guardan las dos.
    - **Haber visto operaciones no se olvida y va aparte de cuál es la última vela** [sexta y séptima revisión de E2]. Cualquier vela del minuto con volumen lo deja visto: a tiempo, aunque otra posterior de volumen 0 la sustituya o llegue después siendo más antigua (columna `traded` de `ws_candles`), o tarde, aunque sea anterior a la última. También un trade tardío. Sigue visto aunque la última vela tenga volumen 0.
    - **Lo que cuenta el minuto:** si aún espera su vela REST, se decide con ellos, pero solo cuando la base ya los tiene. Así una decisión nunca descansa en algo que un reinicio no encontraría [sexta revisión de E2]. Si ya se decidió, cuenta como el peor entre lo escrito y lo que sale de decidirlo de nuevo con todo lo guardado: una discrepancia de cualquiera de los dos se mantiene, y una ausencia probable que el WebSocket contradice es `sin_explicar`, todavía pendiente. El cierre del día también los tiene en cuenta.
  - **Valor de `mark-price` tardío:** no cambia ninguna vela escrita. Las de un minuto S1 salen de sus 60 valores válidos, que lo siguen siendo, y un minuto S2 o S3 no tiene velas propias.
  - Quien lea estos datos aplica estas reglas (`slot_states` y `minute_statuses` de `kbot.market.store`), nunca las tablas de estados solas.

### 3.6 Orden temporal (C43)

- **El orden de llegada nunca sirve como prueba.** La marca de agua $W$ (el mínimo por canal menos el retraso $L$) **ordena de forma provisional, pero no demuestra que no falte nada**.
- **Canales periódicos** (mark): dan intervalos, no instantes. Si se quedan en silencio, la marca de agua se congela.
- **Canales por sucesos** (operaciones): la marca de agua avanza por tiempo y siempre es provisional.
- **El orden entre sucesos que compiten** (stop por last frente a liquidación por mark) **solo es demostrable** si:
  1. la semántica de `ts` está documentada y medida;
  2. hay secuencia continua o un retraso máximo documentado;
  3. los intervalos, ampliados en $\pm\delta$, no se solapan;
  4. ningún canal estaba degradado.
- **Hoy no se cumplen 1 y 2**, así que esos casos son `acotada`.
- **Sin sucesos que compitan, el orden no importa.**
- **Mensajes tardíos:** si cambian un resultado, se aplica el peor caso con asientos correctivos y se abre un incidente. En los datos de mercado, el incidente es la fila de evidencia tardía de 3.5 [cuarta revisión de E2]. Las regresiones de marca de tiempo o los huecos de secuencia ponen el canal en DEGRADADO.
- Los parámetros $L$, $\delta$, $H$, $k$ y $S$ se calibran con mediciones [D26].

### 3.7 Qué se puede recuperar tras una desconexión

| Fuente | ¿Recuperable? |
|---|---|
| Mensajes WebSocket pasados | **No** (no hay reenvío documentado) |
| Velas de 1m de last | Sí, con los límites de 3.2 |
| Velas de 1m de mark | **No:** la API UTA no da velas de mark [H, E1]. Se construyen con el canal `mark-price` (un valor por segundo); los minutos perdidos quedan como hueco y **nunca se inventan** [decisión del propietario, 7 nov 2026] |
| Velas de 1m de index | **En parte:** se construyen con el mismo canal; los minutos perdidos se reconstruyen con el histórico del índice de Classic (`/api/v1/index/query`, un valor por segundo [H, E1]) [decisión del propietario, 7 nov 2026]. **Solo durante unos 100 días:** el 9 de noviembre de 2026, el valor más antiguo de `.KXBTUSDT` y de `.KETHUSDT` era del 31 de julio de 2026 a las 16:00 UTC [H, E3: B3]. Probablemente KuCoin lo borra por meses enteros, en hora de Pekín [I] |
| Operaciones recientes | Solo las 100 últimas |
| Mark a resolución de segundos | **No** |
| Funding histórico | Sí |
| Libro de órdenes histórico | **No** [H] |
| Velas de pares deslistados | **Solo durante unas semanas:** BLAST/USDT tenía todo su histórico 24 días después de su retirada; OXT/USDT, REN/USDT y HYVE/USDT, retirados hace entre 87 y 315 días, ya no existen en la API (código 138004) [H, E3: B13]. Detalle en `docs/kucoin/historico.md` del repositorio |

### 3.8 Esquema `market` de la Fase 2 (etapa E2) [revisado y confirmado; migración `0002`]

**Reglas comunes:**
- **Solo inserciones.** `kbot_ingest` tiene `SELECT` e `INSERT` en estas tablas y nada más (§8 y §9). Un estado que cambia se guarda como una fila nueva o se escribe una sola vez cuando ya es terminal; lo provisional (vela `abierta` o `pendiente_estabilizacion`, intervalo en curso) vive en memoria. Las reinserciones usan `ON CONFLICT DO NOTHING`, que solo necesita `INSERT`.
- **Decimales exactos** (`numeric`) con el texto que envió KuCoin; las cantidades en su unidad original (contratos en Futures) y el multiplicador al lado (3.4).
- **Tiempos en UTC** (`timestamptz`, µs). Cada fila guarda la hora del exchange cuando existe y la hora de recepción.
- **La coherencia entre filas la impone PostgreSQL, no solo Python** (§0, regla 6): claves foráneas compuestas que incluyen el estado y restricciones `CHECK` con la tabla de 3.5. Así es imposible guardar métricas de un intervalo que no es `valida`, una vela cuya evidencia contradice el flujo de su minuto o un estado de minuto que contradice su vela. Un `CHECK` que da NULL se acepta y una clave foránea con una columna nula no se comprueba, así que **cada columna que una combinación necesita se exige con `IS NOT NULL`** [tercera revisión de E2].
- **Orden de cierre de un minuto:** primero `data_quality_minute` (estado terminal del flujo de cada canal), después `candles` (que la referencia) y por último `minute_status` (que referencia las dos y, si es `hueco`, su fila de `data_gaps`). Repetir el cierre, por ejemplo tras un reinicio, no cambia el primer resultado.
- **Sin índices adicionales ni particionado** en E2: bastan las claves primarias y únicas.
- **Las migraciones son inmutables desde que entran en `main`:** antes se pueden corregir en su rama; después, cualquier cambio va en una migración nueva. La `0002` se corrigió en las ramas de E2 antes de integrarse (la última vez, tras la auditoría de E2, con `ws_candles` y `exhausted_index_rebuilds`); no existe ninguna base persistente con una versión anterior (la CI crea una base nueva en cada ejecución y el servidor de Oracle aún no existe). El particionado llega con E3, en una revisión explícita de la lista blanca de SQL.
- **`funding_symbol_map` no se crea:** con D04 (V2) el funding usa el símbolo del contrato y la correspondencia de V1 (B6) ya no hace falta.

| Tabla | Una fila por | Columnas principales (además de las horas) | Clave primaria |
|---|---|---|---|
| `instruments` | Instrumento y cambio observado (se inserta solo si cambia) | mercado, símbolo, base, cotización, liquidación, `tick_size`, paso de cantidad, multiplicador (1 en Spot), mínimos, estado de cotización y si cotiza, respuesta original | (mercado, símbolo, observado) |
| `risk_tiers` | Tramo y observación con cambios (solo Futures) | mercado, símbolo, nivel, límites mínimo y máximo, apalancamiento máximo, margen inicial y de mantenimiento | (mercado, símbolo, observado, nivel) |
| `candles` | Serie, instrumento y minuto **terminal** | serie (`last`, `mark`, `index`), OHLC; volumen y turnover solo en `last`; estado (`cerrada_rest`, `cerrada_cruzada`, `discrepante` o `cerrada_propia`); evidencia (`vela_cruzada_consistente`, `vela_cruzada_discrepante`, `vela_rest_suficiente`, `construida_flujo` o `reconstruida_classic`); muestras (60 en las propias); canal y estado del flujo de su minuto | (serie, mercado, símbolo, apertura) |
| `ws_candles` | Instrumento y minuto cerrado de `last` con vela etiquetada del WebSocket | OHLC, volumen y turnover de la última vela del WebSocket del minuto (la de `P` más reciente) y su `P`; `traded`, si alguna vela del minuto recibida a tiempo tuvo volumen, esta u otra (séptima revisión de E2); apunta a la fila de flujo `trade` de su minuto. Es la evidencia de `cerrada_cruzada` y `discrepante` y lo que permite decidir tras un reinicio (auditoría E2-01) | (mercado, símbolo, apertura) |
| `exhausted_index_rebuilds` | Minuto de index que no se pudo rehacer | intentos; estado del flujo `mark-price` del minuto (solo S2 o S3, porque con S1 la vela es propia). Un minuto de `mark-price` que no es S1, sin vela de index y sin fila aquí, sigue pendiente (auditoría E2-02) | (mercado, símbolo, minuto) |
| `depth_slot_status` | Canal (nivel 5 o 50), instrumento e intervalo de 10 s | estado de 3.5, secuencia `C` del snapshot vigente, antigüedad del snapshot al cierre del intervalo | (mercado, símbolo, nivel, inicio del intervalo): **exactamente un estado terminal** |
| `depth_metrics` | Intervalo con estado `valida` | mejor compra y venta, medio y spread; por lado: niveles, cantidad y nocional visibles, nocional dentro de ±0,1 % y ±0,5 % del medio y precio del nivel más profundo visto; secuencia y antigüedad. **Solo valores exactos:** el spread relativo, el desequilibrio y el alcance del libro son cocientes y se calculan al leer, así que la ingesta no redondea nada (§0, regla 5) | (mercado, símbolo, nivel, inicio del intervalo) |
| `depth_snapshots` | Canal, instrumento y minuto | último libro válido del minuto, como listas de [precio, cantidad] en texto; intervalo `valida` del que procede; secuencia; horas (`M` nulo si no llega, nunca inventado) | (mercado, símbolo, nivel, minuto) |
| `depth_late_books` | Libro tardío (3.5) | intervalo de su `P` y último intervalo cerrado cuando llegó (los dos ya escritos), secuencia `C`, `P` y hora de recepción. Los intervalos `valida` o `no_recibida` entre los dos cuentan como `fuera_de_plazo` [cuarta revisión de E2] | (mercado, símbolo, nivel, intervalo, secuencia) |
| `trading_seen_late` | Minuto de `last` con flujo S1 en el que el WebSocket no había visto operaciones y llegó tarde un trade suyo (3.5) | hora de recepción; apunta al flujo `trade` S1 de su minuto. Descarta la ausencia probable de operaciones [cuarta a sexta revisión de E2] | (mercado, símbolo, minuto) |
| `late_ws_candles` | Vela del WebSocket que llegó con su minuto cerrado y cambia algo (3.5): más reciente que todas las vistas y distinta de la última (`latest = true`), o no más reciente, también con la misma `P`, pero con el primer volumen visto del minuto (`latest = false`) | OHLC, volumen y turnover, su `P`, la hora de recepción y `latest`; apunta al flujo `trade` S1 de su minuto [quinta a octava revisión de E2] | (mercado, símbolo, apertura, `P`, `latest`) |
| `late_ws_repeats` | La última vela del WebSocket de un minuto, reenviada igual y más reciente con el minuto ya cerrado (3.5) | su `P` y la hora de recepción, para que una vela anterior nunca pase por la última; apunta al flujo `trade` S1 de su minuto [sexta revisión de E2] | (mercado, símbolo, apertura, `P`) |
| `funding_rates` | Símbolo, hora de liquidación, tipo y observación | tipo `liquidado` (histórico, una sola fila por hora) o `previsto` (`nextFundingRate`, una por observación); tasa; granularidad | (mercado, símbolo, tipo, hora de liquidación, observado) |
| `data_quality_minute` | Canal, instrumento y minuto | estado del flujo S1, S2 o S3 (3.4); mensajes; saltos o retrocesos de secuencia; reconexiones | (mercado, símbolo, canal, minuto) |
| `minute_status` | Instrumento y minuto, al cerrar el día | estado terminal de 3.5, estado de la vela y del flujo usados, vela y hueco de los que procede | (mercado, símbolo, minuto) |
| `data_gaps` | Hueco cerrado | mercado, canal, símbolo, inicio y fin, causa (desconexión, rotación planificada, sin pong, suscripción rechazada, proceso detenido) | (mercado, canal, símbolo, inicio) |
| `universe_snapshots` | Evaluación del universo e instrumento | posición, si entra en el universo, si es candidato de nivel 50, volumen de 24 h en la moneda de cotización, mejor compra y venta (el spread relativo se calcula al leer), versión de los criterios | (evaluado, mercado, símbolo) |

**Velas propias de mark e index** (3.7): el canal `mark-price` es periódico, un valor por segundo. Un minuto da velas (`construida_flujo`, flujo S1) **solo si la conexión estuvo sana todo el minuto y llegó un valor válido** (decimal finito y positivo) **en cada uno de sus 60 segundos exactos**. Que el socket siga abierto no demuestra nada sobre los valores que no llegaron. Si no, el flujo es S2 (datos incompletos) o S3 (ninguno), el minuto de mark es un hueco y el de index espera su reconstrucción con los valores por segundo de Classic (`reconstruida_classic`), que también exige los 60 segundos con valores válidos; si no, es un hueco. Los valores inválidos o fuera del segundo exacto cuentan como anomalías. **Nunca se completa ni se interpola una vela.**

**Velas de last** [decisión del propietario, 8 nov 2026, provisional hasta D26]: la vela oficial es la de REST, pedida 15 s después del final del minuto. Con el flujo de operaciones en S1 se compara con la vela etiquetada del WebSocket del mismo minuto: si algún precio difiere en más de un tick, `discrepante` (`vela_cruzada_discrepante`); si no, `cerrada_cruzada`. El volumen y el turnover del WebSocket se guardan (`ws_volume`, `ws_turnover`) pero no deciden el estado hasta D26 (B8). **Vela REST de volumen 0 (Spot):** con S1 y sin operaciones en el WebSocket es `cerrada_cruzada` (las dos fuentes dicen que no hubo operaciones); si el WebSocket vio operaciones, `discrepante` (`volumen_rest_cero`, una contradicción, no una tolerancia). Con S2 o S3, `cerrada_rest`. **El WebSocket vio operaciones si mandó una vela del minuto o una operación del minuto** [tercera revisión de E2], antes de cerrarse el minuto o después (`trading_seen_late` y `late_ws_candles`, 3.5) [cuarta y quinta revisión de E2]: un trade sin vela del canal kline contradice también una vela REST de volumen 0 (`discrepante`), igual que con una vela del WebSocket de volumen 0. Cada trade cuenta en el minuto de su hora de ejecución `M`, como cada vela en el de su apertura `O`; uno mal formado, en el minuto en que llegó [cuarta revisión de E2]. Sin vela REST: `ausencia_sin_trades_probable` solo con S1 y sin vela ni trade del WebSocket; `ausencia_no_verificable` con S2 o S3; y `pendiente_estabilizacion` con S1 si el WebSocket vio operaciones (KuCoin avisa de que sus velas REST pueden ser incompletas; el bot espera y nunca construye la vela). **La vela del WebSocket de cada minuto se guarda con su flujo** (`ws_candles`), en la misma transacción, y la que llega después y la cambia, en `late_ws_candles` (3.5): si el proceso se reinicia entre el cierre del minuto y la respuesta REST, el minuto se decide con la misma evidencia y nunca sale `discrepante` por haberla perdido (auditoría E2-01). **Cierre del día** a las 00:00 UTC más el margen: cada minuto recibe su estado terminal solo con lo guardado en la base (flujo de los canales `trade` y `kline`, velas, operaciones vistas tarde y huecos), así que el resultado es el mismo tras un reinicio; los minutos sin fila de flujo, porque nada estaba grabando, son S3.

**Huecos:** una desconexión abre un hueco en memoria y se inserta en `data_gaps` al cerrarse. Si el proceso se detiene, al arrancar se inserta el hueco desde el último intervalo registrado (`proceso_detenido`). Todos los intervalos de profundidad afectados quedan `desconectado` (F2-A5).

**Universo provisional** (D12 sin aprobar): pares en USDT que cotizan, **sin monedas estables como moneda base** (USDC, USDE, FDUSD, DAI, TUSD, PYUSD) [decisión del propietario, 7 nov 2026], ordenados por volumen de 24 h en la moneda de cotización, los 20 primeros de Spot y de Futures; los 10 primeros de cada mercado son candidatos de nivel 50 (P3). Se reevalúa una vez al día y cada cambio queda en `universe_snapshots`.

**El grabador (`svc-ingest`, etapa E2)** [auditado el 8 nov 2026; segunda revisión: APROBABLE CON CORRECCIONES MENORES; tercera revisión, con un revisor nuevo que ejecutó las pruebas con PostgreSQL: APROBABLE CON CORRECCIONES, siete hallazgos MEDIOS; cuarta revisión: APROBABLE CON CORRECCIONES, seis de ellos resueltos, I-E2-04 parcial y un hallazgo nuevo, R4-E2-01; quinta revisión: APROBABLE CON CORRECCIONES, R4-E2-01 e I-E2-04 resueltos en profundidad y dos hallazgos MEDIOS en la evidencia tardía de los minutos; sexta revisión: APROBABLE CON CORRECCIONES, esos dos resueltos y tres hallazgos MEDIOS y uno BAJO más; séptima revisión: APROBABLE CON CORRECCIONES, tres de ellos resueltos y R6-E2-03 parcial; octava revisión: R6-E2-03 resuelto y un caso límite nuevo (N-E2-01, velas con la misma `P`), corregido; novena revisión: N-E2-01 resuelto, sin reaperturas ni defectos nuevos, y el revisor aprueba la corrección sin pedir otra ronda]:
- **Un núcleo sin reloj** (`kbot.ingest.core`) recibe los mensajes, los eventos de conexión y la hora, y devuelve lo que hay que guardar; una capa asíncrona (`kbot.ingest.service`) abre las dos conexiones públicas, llama al núcleo una vez por segundo y escribe en un hilo aparte, para que una escritura lenta nunca retrase los pings.
- **El universo cambia a medianoche UTC:** el de mañana se evalúa a las 23:59 con los datos del día y rige desde las 00:00 (`evaluated_at` es el momento desde el que rige). Un instrumento que sale deja de grabarse a las 00:00 y sus velas REST pendientes se deciden igual; uno que entra empieza a las 00:00; si cambia el conjunto, se renuevan las suscripciones (hueco `rotacion_planificada` de unos segundos). Tras un reinicio dentro del día se reutiliza el universo en vigor, y entre las 23:59 y la medianoche, la evaluación ya guardada para esa medianoche; no se evalúa dos veces. **Si el grabador estuvo parado o suspendido a medianoche y no evaluó a las 23:59**, al volver evalúa el universo del día ya empezado, lo guarda con la hora real de la evaluación y lo aplica en ese momento, renovando las suscripciones; nunca aplica a un día un universo evaluado para otro ni finge que evaluó a las 23:59 (auditoría E2-04). Si los listados de KuCoin llegan incompletos, no se guarda nada, sigue en vigor el universo actual y la evaluación se repite a los 30 s; pasada la medianoche, la repetición es una evaluación tardía como la anterior [segunda revisión de E2]. El cierre del día cubre todos los instrumentos que estuvieron en el universo en algún momento del día.
- **Velas REST:** cada minuto se pide un rango por instrumento. **Una petición cubre como mucho 100 minutos objetivo, del primero al último**, aunque entre ellos esperen pocos; la ventana pedida tiene un minuto más por cada lado, así que nunca pasa de 102 velas, por debajo de las 200 de Futures (3.2). Una respuesta sin la vela de un minuto `pendiente_estabilizacion` o `ausencia_no_verificable` cuenta como intento: el minuto se vuelve a pedir una vez por minuto, como mucho 5 veces. **Una petición fallida no es una respuesta y no gasta intentos** (error de red, error de KuCoin o una respuesta con alguna fila ilegible, que se descarta entera): se repite tras 1, 2, 4 y 8 minutos y después cada 15, durante 48 h desde el minuto; pasado ese plazo, el minuto se cierra tal como esté. Un error de red nunca detiene el grabador [segunda revisión de E2].
- **Cierre del día, por instrumento:** desde las 00:00:15 UTC, en cuanto el núcleo ha cerrado todos sus minutos de ese día y ninguno espera su vela REST. Lo que escribe el cierre es terminal y nunca cambia. Por eso espera a que el núcleo cierre cada minuto que graba, también los de un instrumento retirado y uno que aún espera su prueba de vida: el relleno S3 es solo para los minutos que nadie grababa, y antes podía adelantarse a un minuto aún abierto, que luego chocaba con él al escribirse [cuarta revisión de E2, R4-E2-01]. Y un minuto cuya vela aún se puede recuperar mantiene abierto el día de su instrumento: las respuestas sin vela terminan en unos minutos, y un minuto sin respuesta espera como mucho 48 h. Los demás instrumentos cierran a su hora [segunda revisión de E2].
- **Tras una parada** el grabador continúa donde dice la base de datos, como mucho un día atrás (los minutos más antiguos reciben su fila S3 al cerrar su día; el hueco registra toda la parada). Las velas REST de la parada se piden también (histórico recuperable, 3.7). **Al arrancar reanuda, solo desde la base, el trabajo pendiente de las últimas 48 h:** los minutos con flujo `trade` sin vela de last ni estado terminal, con su última vela del WebSocket (a tiempo o tardía), el tick en vigor al final del minuto y el trade tardío guardado, si lo hay, y los minutos de index que ni se rehicieron ni se dieron por perdidos (auditoría E2-01 y E2-02). Un minuto con su vela no se vuelve a pedir, y repetir el arranque no duplica nada. Lo que no está en la base no es un resultado: un minuto que se quedó sin vela tras 5 respuestas, si el día de su instrumento aún no se ha cerrado, se vuelve a pedir tras un arranque; una vez cerrado el día, nada cambia. **El arranque busca también en la base los días de los últimos 30 con minutos grabados sin estado terminal** y los cierra, con la vela que tengan si ya pasaron las 48 h; así ningún día queda abierto aunque la parada dure más de dos días [tercera revisión de E2].
- **Índice desde Classic:** una petición trae hasta 400 valores por segundo [H, 7 nov 2026], así que se rehacen hasta 6 minutos por petición; se piden 2 minutos después del minuto, como mucho 30 peticiones por minuto y 3 intentos. Si Classic respondió sin el minuto completo, el minuto se da por perdido y queda en `exhausted_index_rebuilds`, así que ningún reinicio lo vuelve a pedir; si Classic nunca respondió, sigue pendiente para el siguiente arranque.
- **Funding:** el previsto cada 10 minutos y el liquidado cada hora (últimas 24 h, sin duplicados). Instrumentos y tramos de riesgo, a diario y solo si cambian.
- **La salud de la conexión hay que demostrarla, no suponerla:** una desconexión se registra desde el último mensaje recibido (la última prueba de vida), no desde que se detecta; y un vigilante comprueba cada segundo que el proceso avanza: si pasa más de 5 s sin ejecutarse (máquina suspendida, CPU agotada, una llamada bloqueada), ese tiempo es un hueco `proceso_detenido` y sus intervalos `desconectado`. Lo destapó la primera prueba en vivo (8 nov 2026): el PC se suspendió 17 minutos y, sin el vigilante, ese tiempo habría pasado por conexión sana. La suspensión se registra desde la última prueba de vida de cada conexión que estaba sana; **una conexión que ya estaba caída sigue caída** hasta que su propio stream confirme la reconexión [tercera revisión de E2].
- **Nada se cierra sin prueba de vida posterior** [tercera revisión de E2]: con la conexión sana, un intervalo de profundidad o un minuto solo se cierra cuando llegó algún mensaje o un pong al menos el margen L después de su fin. Así una desconexión, fechada en la última prueba de vida, nunca cae sobre un resultado ya escrito como sano. Con la conexión caída se cierra hasta el momento actual. En una conexión tranquila la espera es como mucho de un ping (unos 18 s).
- **La prueba de vida no demuestra que haya llegado todo** [cuarta revisión de E2]: KuCoin no documenta que envíe en orden de publicación ni que un pong salga después de todo lo publicado antes. La completitud por hora de publicación es, por tanto, provisional: lo que llega con su intervalo ya cerrado y contradice un resultado escrito se guarda como evidencia tardía y rebaja lo que ese resultado cuenta, sin cambiar sus filas (3.5). Nunca se descarta en silencio.
- **Si KuCoin no da los listados al arrancar, el servicio falla** y systemd lo vuelve a arrancar más tarde (§0, regla 1). Un instrumento cuyos datos no cumplen su contrato no se graba y queda en el registro.
- **En un PC sin PostgreSQL** (`--sink file`) el grabador escribe en archivos JSON lo que guardaría, para comprobarlo contra KuCoin real.

### 3.9 Almacenamiento histórico (etapa E3) [diseño revisado el 9 nov 2026, con las decisiones del propietario; pendiente de implementar]

**Qué pide la etapa** (`07` §2, E3):
- 90 días calientes en PostgreSQL con particionado nativo [D17];
- lo antiguo en Parquet (zstd) inmutable, con SHA-256 de cada archivo (§2 y §10.1);
- tamaño y tiempo de consulta del Parquet medidos con datos reales (punto 66 de `04`).

**Revisión y decisiones.** El 9 de noviembre de 2026 se hicieron dos revisiones del diseño: la primera, con el modelo anterior del revisor, y la segunda, con el auditor actual. Las dos dieron **APROBABLE CON CORRECCIONES DE DISEÑO**: los hallazgos E3-D01 a E3-D04 y cinco condiciones más, todo incorporado abajo. **Decisiones del propietario:**
1. Las particiones las gestionan funciones `SECURITY DEFINER` acotadas, que usa un rol nuevo, `kbot_archive` (opción A).
2. Las tablas se rehacen con una migración nueva, la `0003`, sin tocar la `0002` (opción B).

**1. Particiones por día UTC**
- **Doce tablas particionadas**, por rango de su columna de tiempo:
  - por su minuto: `data_quality_minute`, `minute_status`, `candles`, `ws_candles`, `exhausted_index_rebuilds`, `trading_seen_late`, `late_ws_candles`, `late_ws_repeats` y `depth_snapshots`;
  - por `slot_start`: `depth_slot_status`, `depth_metrics` y `depth_late_books`.

  Sus claves primarias y únicas ya incluyen esa columna [H: PostgreSQL 18, §5.12.2.3].
- **Cinco tablas sin particionar:** `instruments`, `risk_tiers`, `funding_rates`, `universe_snapshots` y `data_gaps`. Su política de retención se define aparte, y no en E3. `data_gaps`, por ejemplo, solo se podrá limpiar si el hueco está cerrado, nada lo referencia en los días calientes y su información está preservada.
- **Por día y no por mes** [R]: así cada copia horaria lleva solo los días aún no excluidos (§10.1 y punto 4).
- **Rango preparado:** la función de preparar mantiene las particiones de **[hoy − 30, hoy + 14]**, el horizonte de recuperación del grabador (I-E2-06). Se ejecuta antes de arrancar el grabador y una vez al día. Nunca vuelve a crear un día ya retirado. Si quedan menos de 7 días futuros, salta una alerta.
- **Sin partición por defecto:** una fila sin partición falla y se ve.
- **Siempre en UTC:** los límites se calculan como `(día::timestamp AT TIME ZONE 'UTC')`, y las funciones fijan `SET TimeZone = 'UTC'`. Así no dependen de la zona horaria de la sesión: el revisor comprobó que la conversión `date → timestamptz` da límites distintos en UTC, Madrid y Nueva York.
- **Comprobación por catálogo:** cada partición se comprueba en el catálogo (padre, límites, propietario y permisos).
- **Bloqueos y escala:** crear una partición toma `ACCESS EXCLUSIVE` sobre la tabla padre [H], así que las funciones usan un `lock_timeout` corto y reintentan. Con unas 1.250 particiones activas se mide el tiempo de planificación, el de mantenimiento y el del DDL.

**2. Migración `0003`** (decisión 2)
- La `0002` no se toca. Desde ahora, la CI compara el SHA-256 de cada migración que ya está en `main` con una lista guardada, y falla si alguna cambia.
- La `0003`, en una sola transacción:
  1. `SET LOCAL lock_timeout`;
  2. `LOCK` de las doce tablas en un orden fijo;
  3. comprueba que todas están vacías: un `CHECK (false)` en cada una falla si tiene alguna fila;
  4. las borra sin `CASCADE`, en el orden que exigen las claves foráneas;
  5. las vuelve a crear particionadas, con las mismas columnas y restricciones.

  Ante datos o contención, aborta sin cambiar nada.
- Añade dos restricciones que la `0002` no tenía:
  - las columnas de minuto, alineadas al minuto UTC (`date_trunc('minute', x AT TIME ZONE 'UTC') = x AT TIME ZONE 'UTC'`);
  - en `depth_snapshots`, `slot_start - minute_start < '00:01:00'`.

  Con las dos, un snapshot nunca cruza de día; el límite superior solo no bastaba (segunda revisión).
- La CI aplica `0001 → 0002 → 0003` y comprueba que el esquema resultante es exactamente el aprobado.

**3. Sellado y archivo de un día D** (E3-D01, E3-D02 y condiciones 1 y 3)
- **Estados de solo inserción:** cada transición es una fila nueva en una tabla de estados del archivo, y el estado de un día es su última fila: `sellado` → `archivado_local` → `replicado_fuera` → `verificado_fuera` → `elegible_para_exclusion_backup` → `retirado`.
- **Sellado** [R: con él se coordinan las escrituras con el corte (condición 1)]:
  - Cuando el día está cerrado para todos sus instrumentos (`day_is_closed`) y han pasado al menos 72 h, la función de sellar añade a cada una de sus doce particiones `CHECK (false) NOT VALID`. Las filas existentes se quedan y no puede entrar ninguna nueva [I: se comprueba en la CI].
  - Antes de sellar, la evidencia tardía sí podía llegar después de las 72 h (`core.py`, segunda revisión). Desde el sellado, la base lo impide.
  - El grabador no escribe evidencia de un día sellado o retirado: la descarta y la cuenta como «evidencia de un día cerrado», sin fallar. Hoy se descarta en silencio, porque falta la fila de flujo.
- **Generación** (condición 3). Después de sellar, en una instantánea coherente (`REPEATABLE READ`), se escribe una generación del día:
  - **los doce archivos**, también los vacíos, en `market/<AAAA-MM-DD>/g<id>/<tabla>.parquet`;
  - **su manifiesto**: versión del esquema y del formato, filas, huella y SHA-256 de cada archivo;
  - **escritura segura:** cada archivo se escribe en un temporal, se sincroniza con el disco y se renombra;
  - **verificación:** se relee y se comprueban sus filas y su huella;
  - **publicación:** la generación solo se publica cuando sus doce archivos son durables y están verificados. Un intento que cae antes queda incompleto, con su identificador, y el siguiente lo descarta. Nada se sobrescribe, y una generación nueva lleva otro identificador.
- **Huella canónica:**
  - las filas, en el orden de su clave primaria;
  - cada valor, en una forma única:
    - `NULL`, con una marca propia;
    - `numeric`, como texto decimal normalizado (sin exponente, sin ceros finales y con `-0` como `0`);
    - las marcas de tiempo, en UTC ISO 8601 con microsegundos;
    - `jsonb` y las listas, en JSON canónico (JCS, RFC 8785);
  - cada fila, como un array JCS, y la huella, el SHA-256 de las huellas de las filas concatenadas;
  - la misma serialización en PostgreSQL y en el Parquet releído, que no depende de cómo los represente PyArrow.
- **Comprobación diaria:** las particiones selladas que siguen en la base conservan su sello, y su huella (no solo el recuento) coincide con la de su generación.

**4. Copias** (E3-D01 y condición 1)
- Una partición solo se excluye de la copia horaria si se cumplen tres condiciones:
  - su sello existe, comprobado en el catálogo dentro de la instantánea de la propia copia (`pg_dump --snapshot`) [H: documentación de `pg_dump` 18];
  - su generación está `verificado_fuera`;
  - el manifiesto de la copia fija esa generación exacta.

  Restaurar nunca busca «la última» generación.
- Se excluye con `--exclude-table-data` por partición, y nunca con `--exclude-table-data-and-children` [H].
- **En E3 no se excluye nada:** todas las copias llevan todas las particiones.

**5. Retirada a los 90 días** (E3-D03 y condición 2)
- **La evidencia no cruza de día** [R]. Un libro tardío cuyo rango cruza la medianoche se guarda en una fila por cada día al que afecta, y cada fila solo referencia intervalos de su día (cambia en la `0003`, en `write_late_books` y en `slot_states`). Así, retirar el día D nunca mejora los estados efectivos de D+1. El revisor lo reprodujo: con la evidencia de D presente, dos intervalos de D+1 contaban como `fuera_de_plazo`; sin ella, volvían a `valida`.
- **Condiciones para retirar el día D:**
  - tiene más de 90 días;
  - su generación es `elegible_para_exclusion_backup`;
  - desde E4, ha pasado una prueba integral de restauración.
- **Cómo se retira:** primero las particiones que referencian y después las referenciadas, con `DETACH` y luego `DROP`, sin `CASCADE` y con `lock_timeout`. Si algo falla, se detiene sin borrar más, y lo ya retirado queda anotado para reintentar.
- **En E3** la función de retirar existe y se prueba en la CI, pero **ningún rol de servicio puede ejecutarla**: ni `PUBLIC` ni los roles operativos, tampoco por herencia.
- **Lectura histórica:** la lectura de días ya retirados aplica las reglas de calidad (`minute_statuses`, `slot_states`) sobre el Parquet, no los estados crudos.

**6. Parquet** [R]
- **Tipos:**
  - `numeric` como `decimal128(38, 18)`. Si un valor no cabe sin perder dígitos, no se escribe y se avisa; nunca se redondea. Pruebas: negativos, ceros, más de 18 decimales y enteros por encima del máximo.
  - Las marcas de tiempo, en UTC con microsegundos.
  - Los niveles de los snapshots, como listas tipadas. La medición los compara con guardar el JSON tal cual.
- **Compresión:** zstd, con el nivel que dé la medición.
- **Bibliotecas:** `pyarrow` para escribir y verificar, y DuckDB para las consultas de la medición.

**7. Funciones y roles** (decisión 1, con las condiciones de las dos revisiones)
- **Tres funciones `SECURITY DEFINER` propiedad de `kbot_owner`:** `market.prepare_partitions(día)`, `market.seal_day(día)` y `market.retire_day(día)`.

  La de sellar se añade a las dos de la propuesta, dentro de la opción A [R: el propietario la confirma]. Las excepciones a §9 (propietario `kbot_owner` y SQL dinámico acotado) valen **solo para estas tres**.
- **Cada función:**
  - trabaja sobre una lista fija de las doce tablas, escrita dentro de la función;
  - recibe un argumento `date` validado para su operación, sin nombres que aporte quien llama;
  - fija `SET search_path = market, pg_temp` y `SET TimeZone = 'UTC'`, y escribe todos los objetos con su esquema;
  - solo ejecuta `format()` con `%I` y `%L` y una de las cuatro plantillas aprobadas: crear una partición, sellarla, separarla y borrarla. Nunca `CASCADE`;
  - toma un bloqueo consultivo (`pg_advisory_xact_lock`) contra dos ejecuciones simultáneas y usa `lock_timeout`;
  - se crea con su `REVOKE ALL ... FROM PUBLIC` en la misma transacción [H].
- **`EXECUTE`:** `kbot_archive`, solo en preparar y sellar. Retirar no lo tiene nadie en E3.
- **`kbot_archive`:** LOGIN, conexión `peer` desde su propio usuario del sistema y sin pertenecer a ningún rol. Tiene `SELECT` en `market` e `INSERT` solo en las tablas del archivo (estados, generaciones y manifiesto).
- **`sql_guard`** admite, en las migraciones:
  - `PARTITION BY RANGE` de una columna `timestamptz` en esas doce tablas, y particiones con límites constantes;
  - `CREATE FUNCTION` solo `SECURITY DEFINER` en PL/pgSQL, con esas dos opciones `SET`, su `REVOKE` obligatorio y `GRANT EXECUTE` solo según la matriz;
  - dentro de esas funciones, `EXECUTE` solo con las cuatro plantillas;
  - `LOCK`, `SET LOCAL lock_timeout`, `CHECK (false)` y `DROP TABLE` sin `CASCADE`, solo en la `0003` y solo sobre las doce tablas;
  - en un `CHECK`, solo `date_trunc` y `timezone` con argumentos constantes.

  La huella de las funciones va en `sql/security_definer_catalog.json`.

**8. Medición con datos reales** (punto 66)
- **Grabación:** un `--sink rows` escribe por tabla las filas que irían a PostgreSQL, construidas con las mismas funciones que `store`. Antes de usar sus medidas, una prueba de la CI demuestra que, para la misma entrada sintética, produce exactamente las filas que guarda `DatabaseSink`.
- **Conversión:** se graban unas horas en el PC con el universo completo, y una herramienta convierte las filas a Parquet con el formato de arriba.
- **Qué se mide:**
  - los bytes por tabla y día, sin comprimir y con zstd 3 y 9;
  - su proyección a un año frente a §3.1 y §10.3, con margen para índices, WAL, particiones y catálogo;
  - el tiempo de dos consultas tipo con DuckDB.
- La medición definitiva, con la base real, es la de E5.

**9. Pruebas de aceptación** (F2-A9 y la lista blanca, en la CI con PostgreSQL 18)
- **Particiones:**
  - existen por adelantado y cada fila va a la suya;
  - tras más de 14 días parado, el arranque prepara el rango de recuperación sin recrear días retirados;
  - llamar desde sesiones en UTC, Madrid y Nueva York crea las mismas particiones UTC.
- **Migraciones:**
  - la `0003` con un escritor concurrente, o con datos, aborta sin cambios;
  - el esquema `0001 → 0002 → 0003` es exactamente el aprobado;
  - los SHA-256 de las migraciones ya integradas no cambian.
- **Sello:**
  - una fila nueva en un día sellado la rechaza la base;
  - el grabador la descarta y la cuenta.
- **Archivo:**
  - una generación tiene doce archivos de una sola instantánea, con su SHA-256, y al releerla coinciden las filas y la huella;
  - una caída entre los archivos y la publicación se reintenta sin publicar antes de tiempo;
  - con dos archivadores a la vez, ni se duplica ni se interfiere;
  - archivar dos veces no sobrescribe nada, y cada copia restaura su propia generación.
- **Copias y retirada:**
  - con el archivo solo local, la partición ni se excluye ni se retira;
  - con el mismo recuento y otro contenido, la retirada se deniega;
  - una restauración que mezcla particiones incluidas y excluidas recupera todo desde PostgreSQL y el Parquet.
- **Bordes de día:**
  - con evidencia de D que afecta a D+1, D+1 conserva sus estados efectivos al retirar D, también tras restaurar;
  - una caída durante la separación no pierde filas y se reintenta.
- **Seguridad:**
  - una fecha fuera de rango o un intento de inyección se rechazan;
  - `kbot_ingest` no puede llamar a las funciones;
  - `kbot_archive` no puede hacer DDL directo ni retirar;
  - `sql_guard` rechaza cualquier otra plantilla;
  - la matriz de roles sigue siendo exacta (F2-A7).
- **Formato:** las pruebas de los decimales y la equivalencia de `--sink rows`.

**Condición expresa:** ningún proceso borra particiones de producción hasta superar una prueba integral de restauración (E4).

## 4. Eventos y recuperación

### 4.1 Eventos (C01)

- **Tablas:** `ops.events` (UUIDv7, `dedupe_key` única, `correlation_id`) y `event_deliveries` (estados `pending`, `processing`, `completed`, `failed`, `retryable`; intentos, lease y error).
- **Reclamación:** con `FOR UPDATE SKIP LOCKED` [H].
- **Idempotencia:** clave del productor, efecto y cierre en la misma transacción, y claves naturales únicas.
- **Reintentos:** backoff exponencial y leases que caducan. Tras 8 intentos va a la cola de fallidos con alerta; si es de riesgo, se bloquea el instrumento.

### 4.2 Arranque seguro y recuperación

**Todo servicio** arranca con backoff, genera un `run_id`, envía heartbeat y recupera las leases caducadas.

**`svc-risk` arranca en `ENTRY_BLOCKED`** hasta que la reconciliación confirma:
- el ledger cuadra (sección 5);
- todas las posiciones tienen stop;
- no hay reservas huérfanas;
- el hash de los límites coincide;
- los datos son recientes;
- el watchdog está vivo.

Las `halt_flags` **persisten tras un reinicio**.

| Fallo | Respuesta |
|---|---|
| svc-ingest | DEGRADED o STALE, entradas bloqueadas, posiciones `no_verificable`. Al volver: relleno por REST y recuperación (catch-up) |
| svc-news | El análisis matemático continúa; el estado pasa a `ANALISIS_FUNDAMENTAL_DEGRADADO`; las noticias no justifican entradas [D19] |
| svc-strategy | No hay señales. Los stops siguen funcionando en el bróker |
| svc-risk | Ninguna aprobación. El watchdog vigila los límites |
| svc-broker | La **marca de agua de supervisión** de los stops se detiene → alerta "stops no supervisados" y entradas bloqueadas. Al volver: recuperación cronológica de stops, liquidaciones y funding (C25) |
| svc-watchdog | Alerta por `OnFailure=`. **Riesgo bloquea las entradas sin heartbeat del watchdog de menos de 60 s** |
| svc-telegram | Outbox; el watchdog avisa de lo crítico directamente |
| svc-web | Sin efecto sobre el trading |
| PostgreSQL | Todo bloqueado. El watchdog lo detecta sin PostgreSQL y alerta directamente |
| Servidor | Orden de systemd, relleno, recuperación y reconciliación |

**Intervención manual:** según el runbook, con reanudación desde el panel con segundo factor y auditoría.

## 5. Contabilidad

### 5.1 Plan de cuentas (C33, C58, C76)

| Cuenta | Unidad | Clase |
|---|---|---|
| `cash_free` | USDT | Activo |
| `cash_reserved:{orden}` | USDT | Activo (efectivo bloqueado para una orden **no ejecutada**) |
| `futures_margin:{pos}` | USDT | Activo (margen aislado de los contratos **abiertos**) |
| `spot_cost:{A}` | USDT | Activo (coste del inventario) |
| `spot_qty:{A}` | A | Activo (cantidad) |
| `mercado:{A}` | A | Contrapartida externa |
| `fees_especie:{A}` | A | Memoria en unidades: cantidad consumida en comisiones |
| `dust:{A}` / `dust_cost:{A}` | A / USDT | Polvo no negociable, dentro de la **cartera residual** (5.5) [H03, H04] |
| `capital_inicial` | USDT | Patrimonio (10.000) |
| `realized_pnl`, `fees`, `funding`, `liquidation_loss` | USDT | **Resultado** |
| `deficit_simulado` | USDT | Pasivo |

**Reglas:**
1. Cada cuenta tiene una sola unidad; una clave foránea rechaza apuntes con otra unidad.
2. Cada asiento cuadra **por unidad**.
3. Las plantillas por tipo de evento las impone un trigger.
4. Todo apunte lleva la etiqueta `position_id`.
5. Los contratos de Futures **no son un activo del ledger**.
6. Coste medio ponderado [D24].

**Invariantes:**
- `spot_qty:A + dust:A + mercado:A + fees_especie:A = 0` [H04].
- `spot_cost` = 0 si y solo si la cantidad es 0.
- Las reservas terminadas valen 0.
- El margen de una posición cerrada vale 0.

### 5.2 Resultado y equity

- **$Res$ = resultado firmado** = saldo de `realized_pnl` + `fees` + `funding` + `liquidation_loss`, con **ganancias en positivo**. **No incluye reservas, activos ni pasivos** [H02]. Crear, modificar o liberar una reserva no cambia $Res$.
- **Identidad del ledger, válida en todo asiento:** $\Delta(\text{activos}) - \Delta(\text{pasivos}) = \Delta Res$ (C53, C58).
- **Equity de mercado (C70; versión vigente):**

$$E_{mercado} = 10.000 + Res + \sum_A(q_A P^{obs}_A - spot\_cost_A) + \sum_{p\in F}U_p$$

- **No se resta el déficit:** la pérdida que lo generó ya está en $Res$, y el déficit es solo su contrapartida en el pasivo [H01]. Cerrar una posición con déficit **no cambia la equity** más allá de las comisiones y el slippage reales (RC9).
- Forma directa equivalente: $cash\_free + \sum cash\_reserved + \sum_A q_A P^{obs}_A + \sum_p (M_{act,p}+U_p) - deficit\_simulado$.
- $q_A$ incluye **todo** el inventario: disponible, reservado para venta y polvo.
- **Equity prudente** = $E_{mercado} + Ajuste_{prudente}$, con $Ajuste \le 0$. El ajuste **nunca entra en el ledger**. **Todos los límites usan la equity prudente.**

| Antigüedad del precio | Spot | Futures | Efecto |
|---|---|---|---|
| ≤ $\tau_{fresco}$ | $q\cdot bid$ | $M_{act}+U$ (mark) | Normal |
| ≤ $\tau_{max}$ | $q\,P_{últ}(1-g)$ | $M_{act}+dNm(P_{últ}(1-dg)-\bar P)$ | Entradas bloqueadas en el instrumento |
| ≤ $\tau_{indet}$ | $q\,P_{últ}(1-g_{cont})$ | Igual, con $g_{cont}$ y **sin suelo en el margen** (lo negativo es una provisión de déficit potencial) | Bloqueo y alerta |
| > $\tau_{indet}$ | 0 | Largo: $M_{act}-Nm\bar P$. **Corto: `no_determinable`** | **No determinable → bloqueo global de entradas** |

- **Estados de la equity prudente:** `determinable`, `no_determinable` e `invalida` (inconsistencia contable). Los dos últimos bloquean las entradas; las salidas reduce-only siguen permitidas.
- Los parámetros son D22.

### 5.3 Futures lineales (C59, C66)

| Magnitud | Definición |
|---|---|
| $K^F_{ab}$ | Nocional de entrada de la parte abierta, en USDT (dato de memoria, no cuenta del ledger) |
| $\bar P$ | $K^F_{ab}/(N_{ab}m)$. Cambia con las ampliaciones, **no con las reducciones** |
| $U$ | $dN_{ab}m(P_{mark}-\bar P)$ |
| $M_{act}$ | Margen aportado − liberado ± funding. Es lo que va en `futures_margin` |
| Cierre de $N_c$ | $\pi = dN_cm(P_x-\bar P)$; $M_r = M_{act}\cdot N_c/N_{ab}$; **el cierre total libera el resto exacto** |

**El margen es garantía, no coste.**

**Invariantes:**
- Conservación del nocional: $K^F_{ab}+K^F_{baja}=K^F_{ej}$.
- Conservación del margen.
- Cada unidad está o abierta o cerrada, nunca en los dos estados.

### 5.4 Plantillas (solo patas en USDT; ΔA − ΔP = ΔRes)

| Plantilla | Asiento | Tipo |
|---|---|---|
| Reserva, liberación, ejecución en Spot o Futures | Entre cuentas de activo | Transferencia (ΔRes = 0) |
| Comisión $f$ | D `fees` / H `cash_reserved` o `cash_free` | Resultado |
| Funding pagado o cobrado | Contra `futures_margin` [P: verificar si KuCoin lo liquida contra el margen o el saldo disponible] | Resultado |
| Venta Spot (ingreso $I$, coste $c$) | D `cash_free` $I$ / H `spot_cost` $c$ / ± `realized_pnl` $(I-c)$ | Realización |
| Cierre Futures | D `cash_free` $(M_r+\pi)$ / H `futures_margin` $M_r$ / ± `realized_pnl` $\pi$ | Liberación más realización |
| **Cierre con déficit** ($\pi<-M_r$) | D `realized_pnl` $|\pi|$ / H `futures_margin` $M_r$ / H `cash_free` (lo que haya) / H `deficit_simulado` (el resto) | Incidente crítico y **parada general**, reanudación manual [D32] |
| Liquidación | D `liquidation_loss` $M_{rest}$ / H `futures_margin` $M_{rest}$ (sin `realized_pnl`) | Resultado |
| Devolución de un resto tras liquidar | **Desactivada** [P] | — |

### 5.5 Comisiones (C58, C65, C71)

- **La moneda de la comisión sale de `feeCurrency`** [H]. Se registran el importe original, la moneda, el tipo usado, su fuente y su marca de tiempo.
- **Valoración al precio de esa ejecución** (en moneda base) o al precio medio de su par con USDT. **No se recalcula** [H11].
- **Si no se puede valorar la comisión, la operación no se ejecuta** (falla F3): no hay ejecución ni resultado [H05].
- **Compra con comisión en moneda base:** se recibe $q - f_b$; `spot_cost` = $(q-f_b)P$; `fees` = $f_bP$.
- **Venta con comisión en moneda base** (modelo simulado [P]):
  - salen $q+f_b$ unidades, con $f_b = rq$ sobre la cantidad bruta;
  - se da de baja el coste $(q+f_b)\bar P$;
  - `realized_pnl` = $(q+f_b)(P-\bar P)$;
  - `fees` = $f_bP$.
- **Venta total con comisión en moneda base:**
  - $q^* = q_{ab}/(1+r)$, redondeado hacia abajo al paso;
  - $f_b = rq$, redondeado hacia arriba [P];
  - si $q+f_b>q_{ab}$, se resta un paso a $q$;
  - residuo ≥ 0.
### 5.5.1 Polvo y cartera residual [H03, H04, H10, H18]

**Qué es:** el residuo de una venta total menor que `minBaseOrderSize` [H]. No se puede negociar.

**Traspaso a valor razonable** (en la misma transacción que cierra la posición $p$, residuo $r$, al bid observado):

| Unidad | Debe | Haber |
|---|---|---|
| USDT | `dust_cost:A` $r\cdot bid$ | `spot_cost:A` $r\cdot\bar P$; ± `realized_pnl` (etiqueta $p$) $r(bid-\bar P)$ |
| A | `dust:A` $r$ | `spot_qty:A` $r$ |

- **Para la posición,** el residuo se realiza al bid. La posición queda **cerrada** ($V_p = 0$) y se cumple $\Delta cash\_free_p + r\cdot bid = Res_p$.
- **Para la cuenta,** el polvo pasa a la **cartera residual**, que no pertenece a ninguna estrategia. Su coste es el valor de mercado del traspaso y conserva las posiciones de origen (trazabilidad).
- **Su PnL diario:** $PnL_{res,d} = \Delta Res_{res,d} + \Delta V_{res,d}$, con $V_{res} = q_{dust}\cdot bid - dust\_cost$. No tiene coste estimado de cierre porque no se puede vender.
- **Identidad diaria completa:** $\sum_p PnL_{p,d} + PnL_{res,d} = \Delta E^{neta}_d$.
- **Sin precio fresco,** se valora de forma prudente (5.2).
- **Riesgo [H18]:** su valor completo ($\sum q_{dust}\cdot P^{val}$) cuenta en $R_{total}$, sin compensaciones.
- **Nunca se convierte en una posición [H10]:** no se puede ampliar ni usar para abrir. **La única salida es una venta reduce-only** (mediante el PBP, sección 8), cuando la cantidad acumulada alcance el mínimo negociable. Su resultado se calcula frente a `dust_cost`.
- **Separación:** si una estrategia compra después el mismo activo, el polvo **no se mezcla** con la posición nueva.
- **Stop obligatorio [H09, aceptada por el propietario el 5 de noviembre de 2026]:** el DM dice "stop-loss obligatorio **en todas las operaciones**" (cita literal). El propietario aceptó esta interpretación (texto literal en `03_Decisiones.md`): el polvo no negociable **no se considera una operación** y no necesita stop-loss **mientras sea realmente no negociable**. Se mantienen su valoración prudente y su inclusión íntegra en el riesgo agregado. No puede abrir ni ampliar posiciones, y su única salida es una venta reduce-only cuando alcance el mínimo negociable.

### 5.6 Resultado diario (C48, C53)

- $PnL_{p,d} = \Delta Res_{p,d} + \Delta V_{p,d}$, con $V = U - C^{est}$, donde $C^{est}$ es el coste estimado de cierre **al precio actual**. $V = 0$ fuera de la vida de la posición.
- **Identidades:**
  - $\sum_d PnL_{p,d} = Res_p$ = efectivo neto devuelto;
  - $\sum_p PnL_{p,d} = \Delta E^{neta}_d$.
  - con polvo, se añade la línea de la cartera residual: $\sum_p PnL_{p,d} + PnL_{res,d} = \Delta E^{neta}_d$ (5.5.1) [H03].
- **Precios:**
  - de mercado: bid, ask y mark;
  - de ejecución: modelo;
  - contable: precio medio de ejecución;
  - de valoración: bid en Spot y mark en Futures.
- **Al abrir:** $V_0 = U_0 - C_0$. El spread y el slippage se reconocen al instante.

### 5.7 Decimales (C73, C78)

- **Forma canónica en JSON:** cadena `-?(0|[1-9][0-9]*)(\.[0-9]*[1-9])?`.
  - Cero = `"0"`.
  - Primero se normaliza y después se comprueba la escala: como máximo 18 decimales significativos.
  - **Al serializar nunca se redondea**: un exceso produce el error `precision_no_representable`.
- **Contadores:** número JSON con $|n|\le 2^{53}-1$.
- **Marcas de tiempo:** RFC 3339 en UTC con 6 decimales.
- **Redondeos solo con regla explícita** y registrados como suceso:
  - cantidades: `baseOrderStep`, hacia abajo;
  - precios: `tickSize`;
  - comisiones: regla declarada;
  - cocientes contables: escala 18 y `ROUND_HALF_EVEN`, con el **método del resto** (el último movimiento libera el resto exacto).
- **Contexto de cálculo contable [H07]:**
  - [H] Python `decimal` redondea a la precisión del contexto **sin generar ningún error** (por defecto, precisión 28 y sin trampa para `Inexact`).
  - Para los cálculos contables, el contexto lleva **precisión 80** y **la trampa `Inexact` activada**: cualquier redondeo no previsto provoca una excepción.
  - **Los únicos puntos de redondeo** son las cuantizaciones declaradas (lista de arriba). Se hacen en un contexto local que permite ese redondeo y registran el suceso.
  - Aumentar la precisión **no sustituye** la comprobación de la escala ni la serialización canónica.
- **Método del resto, sin cuenta de residuo [H14]:**
  - cada apunte se cuantiza al calcularlo (escala ≤ 18);
  - la diferencia se queda en el saldo de la cuenta de origen;
  - el último movimiento libera el resto exacto.

  Sumar y restar valores de ≤ 18 decimales es exacto, así que la conservación es exacta **sin ninguna cuenta de residuo**.

## 6. Riesgo

### 6.0 Estado del apartado

**Cerrado el 5 de noviembre de 2026.** Este apartado resuelve los hallazgos R01-R11 de la ronda 3 de revisión, que se trataron como lista cerrada. No se reabre salvo que aparezca un error crítico.

**Estados:** especificado · validado mediante pruebas: 0 · implementado: 0.

| Hallazgo | Tema | Estado | Dónde |
|---|---|---|---|
| R01 | Pérdida diaria del 2 % y posiciones abiertas | **Resuelto.** El cierre automático adicional de posiciones queda **pendiente de decisión del propietario** (D08) | 6.5.2 |
| R02 | Drawdown del 10 % | **Resuelto.** La acción sobre las posiciones queda **pendiente de decisión del propietario** (D08, D21) | 6.5.3 |
| R03 | Signo de dirección aplicado dos veces al funding | **Resuelto** | 6.2.3 |
| R04 | Stop frente a liquidación en largos y cortos | **Resuelto.** La fórmula de cortos es derivada [I] y está **pendiente de confirmar con un ejemplo oficial**. Parámetros en D22 | 6.3 |
| R05 | Costes de salida y funding reproducibles | **Resuelto.** Los **valores de los parámetros están pendientes de decisión** (D22, D30) | 6.2.2, 6.2.3 |
| R06 | Límite de riesgo agregado | **Resuelto** (documentado sin decidir). La **elección está pendiente** (D27) | 6.4.3 |
| R07 | Función operativa de la correlación | **Resuelto.** Método, umbral y límite **pendientes** (D14) | 6.4.4 |
| R08 | Ampliaciones que incumplan límites aunque la posición gane | **Resuelto.** La interpretación por activo base está **pendiente** (D20) | 6.6 |
| R09 | Riesgo con el stop atravesado y la ejecución pendiente | **Resuelto.** Los parámetros de $g(\tau)$ están **pendientes** (D22, D26) | 6.4.1 |
| R10 | RC10 reproducible y diferencia de 0,002424 | **Resuelto** | 15.4 |
| R11 | Datos de RC11 | **Resuelto** como caso en magnitudes de riesgo. La fuente no da precios ni cantidades y **no se inventan** | 15.5 |

### 6.1 Límites del Documento Maestro (inmodificables en ejecución)

**DM §6, literal y completo:**
- Capital ficticio: **10.000 USDT**.
- Riesgo máximo planificado por operación: **0,5 %**.
- Pérdida diaria máxima: **2 %**.
- Apalancamiento máximo Futures: **2x**.
- Drawdown máximo: **10 %**.
- **Stop-loss obligatorio en todas las operaciones.**
- "Los límites serán controles obligatorios, no recomendaciones que las estrategias puedan ignorar."
- "El tamaño de posición deberá calcularse según la distancia al stop, el capital disponible y los costes." (6.2)
- "Se contabilizarán comisiones, financiación de Futures, deslizamiento y resultados realizados y no realizados." (sección 5 y 6.2)
- "Al alcanzar límites de riesgo, el sistema deberá impedir nuevas operaciones y generar una alerta." (6.5)
- "El apalancamiento inicial deberá utilizar margen aislado cuando corresponda." (6.3)
- "Queda prohibido aumentar automáticamente posiciones perdedoras para recuperar pérdidas." (6.6)

**Cómo se protegen:**
- Viven en `config.risk_limits`, que solo puede escribir `kbot_owner` (sin login, solo mediante migraciones).
- **Una prueba de CI compara esos valores con el DM.**
- Ni la IA, ni las estrategias, ni Telegram, ni el panel pueden modificarlos.

**Lectura aplicada en todo el apartado:**
- "Nuevas operaciones" son las que **abren o aumentan exposición o riesgo**: entradas, ampliaciones y alejar un stop.
- Las salidas que **solo reducen** exposición (ejecución de stops, salidas de las estrategias y cierres manuales del propietario) **no son nuevas operaciones**. Prohibirlas impediría ejecutar el stop obligatorio del propio DM. Esto coincide con el principio de la sección 0.

### 6.2 Tamaño de posición y costes

#### 6.2.1 Notación común

| Símbolo | Definición |
|---|---|
| $d$ | +1 si es larga, −1 si es corta. En Spot, siempre +1 |
| $X$ | Exposición en unidades base. Futures: $N\cdot m$ (contratos × multiplicador [H]). Spot: $q$ |
| $\bar P$ | Precio medio de entrada (5.3). En Spot, coste ÷ cantidad |
| $P^{val}$ | Precio de valoración con el régimen de antigüedad de 5.2: bid en Spot, mark en Futures, o el precio prudente si está antiguo |
| $P_e$ | Precio de entrada de referencia: ask si es larga, bid si es corta |
| $P_s$ | Stop vigente de la posición |
| $f_e$, $f_x$ | Tasas de comisión de entrada y de salida, como tomador, de la tabla de comisiones versionada del instrumento. Futures: `takerFeeRate` [H]. Spot: tarifa [P] pendiente de verificar |
| $s_e$, $s_x^{est}$ | Slippage estimado de entrada y slippage de salida de estrés (6.2.2) |
| $M_{act}$ | Margen actual de la posición (5.3) |

#### 6.2.2 Coste de salida: definición canónica (R05)

La **misma** definición se usa en el tamaño de posición (6.2.5), en el riesgo abierto (6.4.1) y en la condición de liquidación (6.3):

$$C^{salida}(X,P)=X\cdot P\cdot\big(f_x+s_x^{est}\big)$$

- Se evalúa **al precio del stop** ($P = P_s$) mientras el stop no se ha atravesado, y al precio de valoración cuando sí (6.4.1).
- **Slippage de salida de estrés:** $s_x^{est}=\max\big(s_{min},\ \kappa\cdot s^{mod}(X)\big)$, con

  $s^{mod}(X)=\max\Big(\dfrac{spread}{2P},\ \dfrac{impacto(X)}{P},\ k_{ATR}\dfrac{ATR_{1m}}{P}\Big)$

  donde $impacto(X)$ es el coste de recorrer el **libro válido** para la cantidad $X$. Sin libro válido, ese término se omite y actúa el suelo $s_{min}$.
- **Slippage de entrada:** $s_e = s^{mod}(X)$, evaluado en el lado de la entrada.
- **Parámetros** $s_{min}$, $\kappa \ge 1$ y $k_{ATR}$: **D22, sin valores por defecto.** Si no hay parámetros aprobados, el instrumento no admite entradas. No se inventan valores.
- **Trazabilidad:** cada decisión de riesgo registra $f_x$, $s_x^{est}$, sus componentes y la versión de los parámetros.

#### 6.2.3 Funding: flujo real y proyección (R03, R05)

**Convención** [I: estándar de los contratos perpetuos; P: confirmar en la documentación de KuCoin]: una tasa $\rho>0$ significa que **los largos pagan** a los cortos.

**Flujo real en la liquidación $k$.** Es el **único** sitio donde se aplica $d$ al funding:

$$\varphi_k=d\cdot\rho_k\cdot X\cdot P^{mark}_k\quad\text{(USDT)}$$

- $\varphi_k>0$: la posición **paga** (D `funding` / H `futures_margin`).
- $\varphi_k<0$: la posición **cobra** (al revés). Ver plantillas de 5.4.

**Tasa proyectada** (solo coste, siempre ≥ 0):

$$\rho^{proy}=\min\Big(cap_{abs},\ \max\big(\max(0,\,d\cdot\rho_{sig}),\ q_{95}\big)\Big)$$

- **$d$ se aplica una sola vez**, dentro de $\max(0, d\cdot\rho_{sig})$.
- $q_{95}$ = percentil 95 de $|\rho|$ en las liquidaciones aceptadas del contrato dentro de la ventana $W_\rho$. **No lleva signo ni se multiplica por $d$**: es una cota conservadora para los dos lados.
- $cap_{abs}=\max(|fundingRateCap|,|fundingRateFloor|)$, tomado del **último valor validado y versionado** del contrato [H15]. **Una respuesta rechazada nunca aporta ni actualiza el tope.**
- **Casos en que se usa $\rho^{proy}=cap_{abs}$:**
  - $\rho_{sig}$ no es válida: la respuesta no la acepta el catálogo de C80, su `fundingTime` no es el siguiente $T_k$ pendiente, o se obtuvo antes de la última liquidación contabilizada;
  - la cobertura de la ventana $W_\rho$ es menor que $c_{min}$ de las liquidaciones esperadas.
- **Sin $cap_{abs}$ validado**, no se aceptan entradas cuyo riesgo dependa del funding.

**Funding proyectado** (USDT, siempre ≥ 0, **sin volver a aplicar $d$**):

$$F^{proy}(X)=n_f\cdot\rho^{proy}\cdot X\cdot P_{ref},\qquad P_{ref}=\max(P^{val},P_s)$$

Se usa el nocional al precio más alto entre la valoración y el stop, lo que es conservador para los dos lados.

**$n_f$** = liquidaciones pendientes según el calendario oficial [H: `fundingTime`, `currentGranularity`, `newGranularity`, `newGranularityStartTime`]:
1. Se generan los instantes $T_k$ a partir de `fundingTime`, cambiando de granularidad en `newGranularityStartTime`.
2. $t_{fin}=\max(t_{entrada}+T_{max},\ t_{ahora}+\text{una granularidad})$.
3. $n_f$ = número de $T_k$ con $t_{ahora}<T_k\le t_{fin}$, más los $T_k$ ya pasados **que aún no se han contabilizado**. Como mínimo 1 mientras la posición esté abierta.
4. Un $T_k$ contabilizado (clave única posición-$T_k$) **sale de $n_f$ en la misma transacción en que entra en el PnL**. Cada cobro está o en la proyección o en el PnL, nunca en los dos.

**Al dimensionar** (6.2.5): $t_{ahora}=t_{entrada}$ y $P_{ref}=\max(P_e,P_s)$. **Spot:** $F^{proy}=0$.

**Parámetros** $W_\rho$, $c_{min}$ y $T_{max}$ por estrategia: **D30** (propuesta: 30 días y 80 %).

**Correspondencia contrato → símbolo de funding** (C80): catálogo versionado por (familia, versión, endpoint, producto, contrato). **Nunca se construye concatenando texto.** Si no coincide, el dato no se usa y se abre un incidente.

#### 6.2.4 Equity y capital disponibles (coherencia con la sección 5)

- $E_{liq}=E_{prudente}-\sum_p C^{est}_p$, con $C^{est}$ definido en 5.6. Si la equity prudente no es `determinable` (5.2), **no se dimensiona** y la intención se rechaza.
- **Efectivo disponible** $C_{disp}$ (Spot) y **margen disponible** $M_{disp}$ (Futures) = **saldo de `cash_free`**. Las reservas ya salieron de `cash_free` al crearse (5.1 y 5.4), así que **no se restan otra vez**.
- Con `deficit_simulado` > 0 el sistema está en parada general (5.4) y no hay dimensionado.

#### 6.2.5 Fórmulas de tamaño

- $R_{max}=0{,}005\cdot E_{liq}$.
- **Requisito previo:** el stop está en el lado de la pérdida, es decir, $d\,(P_e-P_s)>0$.

**Spot** (siempre largo):
- Pérdida planificada por unidad: $L_u=(P_e-P_s)+P_e(f_e+s_e)+P_s(f_x+s_x^{est})$.
- Cantidad: $Q=\Big\lfloor\min\big(R_{max}/L_u,\ C_{disp}/(P_e(1+f_e+s_e)),\ maxBaseOrderSize\big)\Big\rfloor_{baseOrderStep}$.

**Futures** (largo o corto):
- Pérdida planificada por contrato:

  $L_c=m\big[d(P_e-P_s)+P_e(f_e+s_e)+P_s(f_x+s_x^{est})\big]+n_f\,\rho^{proy}\,m\,\max(P_e,P_s)$
- Contratos: $N=\lfloor\min(N_1,N_2,N_3,N_4)\rfloor_{lotSize}$, con:
  - $N_1=R_{max}/L_c$;
  - $N_2=M_{disp}\,\lambda/\big(m\,P_e\,(1+\lambda(f_e+s_e))\big)$, con $\lambda$ según 6.3;
  - $N_3=maxRiskLimit_{L1}/(m\,P_e)$;
  - $N_4=maxOrderQty$.

**Reglas comunes:**
- **Cantidad con la que se evalúan los costes (R05):** $s_e$ y $s_x^{est}$ dependen de la cantidad (6.2.2), y la cantidad depende de ellos. Se toma la **mayor** cantidad, en múltiplos de `baseOrderStep` o `lotSize`, que cumple todas las cotas con los costes evaluados **para esa misma cantidad**. Como $s^{mod}$ no decrece con la cantidad, el resultado es único y reproducible.
- **Por debajo del mínimo de la orden, se rechaza. Nunca se redondea hacia arriba.**
- **Coherencia con el riesgo abierto:** en el instante de la entrada ($P^{val}=P_e$), el riesgo planificado es el riesgo abierto (6.4.1) más los costes de entrada: $R_{plan}=R^{ab}+X\,P_e\,(f_e+s_e)$.
- **El riesgo planificado no es la pérdida máxima.** Se registran $R_{plan}$, la pérdida máxima teórica $L_{max}$ y el múltiplo R real.

### 6.3 Stop frente a liquidación, en largos y cortos (R04)

**Precio de liquidación aislada.** La fórmula oficial de KuCoin usa `side` = ±1 [H] y su ejemplo oficial es de un largo [H]. La escritura explícita del corto y la forma única son [I] (derivación abajo; M3):

| Lado | $P_{liq}$ |
|---|---|
| Largo ($d=+1$) | $\dfrac{X\bar P-M}{X\,(1-MMR-LFR)}$ |
| Corto ($d=-1$) | $\dfrac{X\bar P+M}{X\,(1+MMR+LFR)}$ |
| Forma única | $P_{liq}=\dfrac{X\bar P-d\,M}{X\,\big(1-d\,(MMR+LFR)\big)}$ |

- $X\bar P$ es el valor de apertura y $M$ el margen de la posición, es decir, $M_{act}$ (5.3).
- $MMR$ es el del tramo aplicable (nivel 1, por $N_3$). $LFR$ es la tasa de comisión de liquidación [P: por contrato].
- [H] La liquidación se dispara cuando la equity aislada llega al margen de mantenimiento, que se calcula sobre el mark desde el 22 de mayo de 2025.
- **Derivación** [I]: la equity aislada es $M+dX(P-\bar P)$. Se iguala a $X\,P\,(MMR+LFR)$ y se despeja $P$. Con $d=+1$ se obtiene la fórmula oficial y su ejemplo (RC13: 29.535,9).
- **Comprobación del corto** [I; P: confirmar con un ejemplo oficial de corto]: con los mismos datos que RC13 en corto ($X$ = 1, $\bar P$ = 30.000, $M$ = 600, MMR 0,4 %, LFR 0,06 %), el resultado es $30.600/1{,}0046\approx 30.459{,}9$.

**Precio de liquidación de estrés** $P^{est}_{liq}$: la misma fórmula con $M = M_{act}-F^{proy}(X)$ (el margen que quedaría tras el funding proyectado) y el $MMR$ del tramo aplicable.

**Peor precio de salida al dispararse el stop:**

$$P^{peor}_x=P_s\,\big(1-d\,(b_d+g+s_x^{est})\big)$$

- $b_d$: percentil 99,5 de $d\cdot(last-mark)/last$ en 30 días de muestras de 1 minuto, acotado a ≥ 0. Mide cuánto puede estar el mark en peor situación que el last para ese lado.
- $g$: percentil 99 del movimiento absoluto en 1 minuto.
- $s_x^{est}$: 6.2.2.

**Condición de aprobación** (una sola expresión para los dos lados):

$$d\cdot\Big[P^{peor}_x-P^{est}_{liq}\,(1+d\,m_{safe})\Big]\ \ge\ 0$$

- **Largo:** $P_s(1-b_d-g-s_x^{est})\ \ge\ P^{est}_{liq}(1+m_{safe})$. La liquidación queda **por debajo**, con margen.
- **Corto:** $P_s(1+b_d+g+s_x^{est})\ \le\ P^{est}_{liq}(1-m_{safe})$. La liquidación queda **por encima**, con margen.

**Apalancamiento:**
- $\lambda$ es un resultado: el **menor** $\lambda\in[1,2]$ que cumple la condición. En la práctica, $\lambda=1$ si se cumple con 1.
- Si no se cumple con $\lambda=1$, la intención se rechaza: con más apalancamiento la liquidación solo se acerca. Ver la mejora futura M1.

**Reevaluación:**
- Se repite ante cualquier cambio de tramo, MMR, funding o stop.
- Si una posición abierta deja de cumplir la condición: alerta crítica y se bloquean sus ampliaciones. La reducción automática es **D21** (pendiente).

**Parámetros** $b_d$, $g$, $m_{safe}$ y los de $s_x^{est}$: **D22**. Sin parámetros aprobados no se aprueban entradas en Futures.

**Tipos de stop:** solo TP (last) [H]. Los tipos MP e IP no están verificados.

### 6.4 Riesgo abierto, riesgo total y correlación

#### 6.4.1 Riesgo abierto de una posición (R09)

$P^{val}$ sigue el régimen de 5.2.

| Situación | $R^{ab}_p$ |
|---|---|
| **Stop no atravesado:** $d\,(P^{val}-P_s)>0$ | $d\,X\,(P^{val}-P_s)+C^{salida}(X,P_s)+F^{proy}(X)$ |
| **Stop atravesado con cantidad todavía abierta:** $d\,(P^{val}-P_s)\le 0$ (disparo pendiente de ejecución, ejecución parcial o falta de liquidez) | $X\,P^{val}\,\big(f_x+s_x^{est}+g(\tau_{pend})\big)+F^{proy}(X)$ |

- **El riesgo nunca desaparece mientras quede exposición:** con el stop atravesado, $R^{ab}_p\ \ge\ X\,P^{val}\,(f_x+s_x^{est})>0$.
- $g(\tau)$ es el movimiento adverso de estrés para un tiempo $\tau$, no decreciente en $\tau$. Su forma y sus valores son **D22**.
  - $\tau_{pend}=\max(\tau_{ejec},\ t_{ahora}-t_{disparo})$.
  - $\tau_{ejec}$ = latencia del modelo de ejecución más el retraso $L$ (D26).
- **Sin doble conteo:** la pérdida hasta $P^{val}$ ya está en $U$, y por tanto en $PnL_{día}$ (5.2). $R^{ab}$ solo cubre lo que puede pasar **a partir de** $P^{val}$.
- **Mientras haya un stop atravesado sin ejecutar:** entradas bloqueadas en ese instrumento, y alerta si la ejecución tarda más de $\tau_{ejec}$.
- El riesgo de la posición solo baja cuando el cierre se **ejecuta** (6.6).

#### 6.4.2 Riesgo total

$$R_{total}=\sum_p R^{ab}_p+\sum R^{res}+\sum_A q_{dust,A}\,P^{val}_A$$

- $R^{res}$ es el riesgo planificado de cada reserva aprobada y aún no ejecutada ($Q\cdot L_u$ o $N\cdot L_c$, 6.2.5). Al ejecutarse se sustituye por $R^{ab}$ en la misma transacción (C22).
- El polvo cuenta por su valor completo [H18].
- Se suman valores absolutos, sin compensaciones entre posiciones. **La correlación nunca lo reduce.**
- **Pasado y futuro separados:** lo que ya ha ocurrido está en $PnL_{día}$; lo que todavía puede ocurrir, en $R_{total}$.

#### 6.4.3 Límite agregado: alternativas de D27 (R06)

Las opciones se documentan **sin decidir**. $E_0$, $PnL_{día}$ y $L_{día}$ se definen en 6.5.1.

| Opción | Regla para aceptar una intención con riesgo $R_{new}$ |
|---|---|
| A | Ningún límite agregado adicional a los del DM |
| B1 | $R_{total}+R_{new}\le 0{,}02\,E_0-L_{día}$ |
| B2 | $R_{total}+R_{new}\le 0{,}02\,E_0+PnL_{día}$ |
| C | $R_{total}+R_{new}\le L_{total}$ (valor que fija el propietario) |

**Propiedades** (hechos matemáticos, no recomendaciones):
- Con $PnL_{día}\le 0$, B1 y B2 coinciden.
- Con $PnL_{día}>0$, B2 deja más capacidad que B1. Además, la capacidad de B1 cambia con el precio de las posiciones que ganan.
- B2 no cambia cuando el precio se mueve sin atravesar stops, salvo por el término de funding.
- **Ninguna opción garantiza el 2 %.** Todas limitan el riesgo planificado, y los gaps o el slippage pueden superarlo. El límite diario del DM actúa sobre la pérdida real (6.5.2).

**Mientras D27 esté pendiente:**
- En cada decisión de riesgo se calculan y registran $R_{total}$, $Cap_{B1}$ y $Cap_{B2}$ (y $Cap_C$ si se configura $L_{total}$), y se muestran en el panel y en los informes.
- **No se aplica ningún límite agregado adicional.** El efecto provisional coincide con el de la opción A, **pero eso no la aprueba**. D27 debe decidirse antes de la Fase 4 (`03_Decisiones.md`).
- La configuración del límite agregado **no tiene valor por defecto**: solo la fija el propietario.

#### 6.4.4 Correlación: función operativa (R07)

**Mientras D14 esté pendiente, la correlación solo mide y registra:**
- En cada decisión de riesgo calcula los acoplamientos direccionales entre las exposiciones (posiciones, reservas y la intención nueva) y los guarda junto con el identificador de la instantánea.
- El panel y el informe diario muestran los pares más acoplados y las agrupaciones fijas (mismo activo base y modo de estrés).
- **No** bloquea entradas, **no** cambia el tamaño (6.2), **no** cambia $R_{total}$ (6.4.2) y **nunca** reduce riesgo ni compensa largos con cortos.

**Cálculo** (C38 y C42, reglas técnicas ya especificadas):
- Retornos logarítmicos de velas de 1 hora finales. Las horas sin vela se excluyen; no se rellenan con 0.
- Ventanas de 30 y de 7 días, por pares y solo con observaciones alineadas. Hacen falta al menos 576 h y 134 h de solapamiento, respectivamente.
- Acoplamiento direccional: $c_{ij}=\rho_{ij}\,d_i\,d_j$. Acoplamiento efectivo: $c^{ef}_{ij}=\max(c^{30}_{ij},c^{7}_{ij})$.
- Si no hay datos suficientes, se supone $\rho=1$ con el grupo de mercado.
- **Agrupaciones fijas**, que no dependen de D14:
  - el mismo activo base en Spot y en Futures;
  - en modo de estrés (percentil del ATR de BTC > 90), todas las exposiciones juntas.
- Recálculo diario a las 00:15 UTC y en cada cambio de régimen, con instantáneas versionadas.

**Pendiente del propietario (D14):** método de agrupación (enlace simple o completo), umbral $\theta$ y límite por grupo. **D14 requiere decidir antes D27.**

### 6.5 Medición de los límites y comportamiento al alcanzarlos (R01, R02)

#### 6.5.1 Referencias

- **Hora de corte diario:** D08 (propuesta: 00:00 UTC).
- **$E_0$** = $E_{mercado}$ en el snapshot del corte (5.2).
- $PnL_{día}(t)=E_{prudente}(t)-E_0$ y $L_{día}(t)=\max\big(0,-PnL_{día}(t)\big)$.
  - Tomar $E_{mercado}$ como referencia y $E_{prudente}$ como valor actual es conservador: la valoración prudente solo puede aumentar la pérdida medida.
- **Pico** = $\max\big(10.000,\ $ máximo histórico de $E_{mercado}$ en snapshots con **todos** los precios frescos$\big)$. Así, un precio antiguo no puede crear un pico artificial.
- $DD(t)=\big(Pico-E_{prudente}(t)\big)/Pico$.
- Si la equity prudente no es `determinable` o es `invalida`, los límites no se pueden evaluar y se bloquean todas las entradas (5.2).
- Los límites los evalúa **svc-risk** en cada decisión y en cada snapshot de valoración, y **svc-watchdog** de forma independiente.
- Si un límite salta **solo** por el ajuste prudente (5.2), la alerta lo indica.

#### 6.5.2 Pérdida diaria del 2 % (R01)

**Disparo:** $L_{día}(t)\ \ge\ 0{,}02\cdot E_0$.

**Efectos obligatorios (DM §6), automáticos e inmediatos:**
1. **Se impiden nuevas operaciones hasta el siguiente corte.** La puerta de riesgo rechaza toda intención que abra o aumente exposición o riesgo: entradas, ampliaciones y alejar un stop.
2. **Se cancelan las reservas aprobadas que aún no se han ejecutado.** Si no, una operación aprobada antes del límite se ejecutaría después.
3. **Alerta crítica** por Telegram y en el panel, con $L_{día}$, el riesgo abierto restante ($R_{total}$: la pérdida adicional planificada si saltasen todos los stops) y la lista de posiciones abiertas.
4. Las estrategias pasan a **modo sombra**: calculan señales sin operar.

**Posiciones abiertas:**
- **Sus stops siguen activos y se ejecutan** (stop obligatorio del DM).
- Siguen permitidas las salidas que solo reducen exposición: stops, reglas de salida de las estrategias y cierres manuales del propietario (reduce-only, desde el panel con segundo factor).
- **Cerrar automáticamente las posiciones al alcanzar el 2 % es una decisión del propietario (D08).** Mientras no se decida, el sistema **no** hace ningún cierre automático aparte de los anteriores.
- **El 2 % no está garantizado.** Después del límite, una posición abierta puede seguir perdiendo por gaps o slippage. Esa pérdida se mide y se informa como "exceso sobre el límite diario". Si se llega al drawdown del 10 %, se aplica 6.5.3.

**Reanudación:**
- Al siguiente corte se toma un nuevo $E_0$ y el límite diario se vuelve a calcular.
- Las entradas se reanudan **solo si** no queda ningún otro bloqueo activo (drawdown, invariantes, `halt_flags`, equity no determinable, datos antiguos) y la reconciliación es correcta.
- Se notifica por Telegram.

#### 6.5.3 Drawdown del 10 % (R02)

**Disparo:** $DD(t)\ \ge\ 10\,\%$.

**Estado seguro `HALT_DD`**, guardado en `halt_flags` para que sobreviva a los reinicios:
1. Se impiden nuevas operaciones, como en 6.5.2, **sin fecha de fin**.
2. Se cancelan las reservas no ejecutadas.
3. Las estrategias quedan **suspendidas para entradas**. Sus reglas de salida siguen funcionando para las posiciones abiertas, porque solo reducen exposición.
4. **Los stops siguen activos** y se permiten las salidas reduce-only. **Están prohibidos** ampliar y alejar stops.
5. **Alerta crítica y petición al propietario** en el panel (con segundo factor), con tres opciones: mantener las posiciones con sus stops, cerrarlas todas o cerrar una parte. Se le recuerda en cada informe diario hasta que responda.
6. **Mientras el propietario no responda**, no hay cierres automáticos aparte de los stops y las salidas de las estrategias. Es el comportamiento mínimo; cualquier acción automática adicional depende de D08 y D21.

**Reanudación:**
- **Solo manual**, por el propietario, desde el panel con segundo factor, después de un informe automático con las causas, las posiciones y la reconciliación del ledger.
- **El pico no se reinicia** salvo por una acción explícita y auditada del propietario.
- Todo queda auditado.

#### 6.5.4 Resumen

| Evento | Nuevas operaciones | Reservas no ejecutadas | Posiciones abiertas | Estrategias | Alerta | Reanudación |
|---|---|---|---|---|---|---|
| Intención > 0,5 % | Se reduce el tamaño o se rechaza | — | — | — | — | — |
| Pérdida diaria ≥ 2 % | Bloqueadas hasta el corte | Canceladas | Stops activos y salidas reduce-only; cierre automático: D08 | Modo sombra | Crítica | Automática al corte si no hay otro bloqueo |
| Drawdown ≥ 10 % | Bloqueadas | Canceladas | Stops activos y salidas reduce-only; decide el propietario (D08, D21) | Suspendidas para entradas | Crítica y petición | Solo manual, con segundo factor |
| Invariante roto o déficit | Parada general | Canceladas | Congeladas para revisión; stops activos | Detenidas | Crítica | Manual |
| Datos antiguos | Bloqueo del instrumento | — | `no_verificable` y valoración prudente | — | Según la duración | Tras el relleno |
| Equity no determinable | Bloqueo global | — | Salidas reduce-only permitidas | — | Crítica | Al recuperar la valoración |

**Mantener los stops no garantiza que no se superen los límites.**

### 6.6 Cierres y ampliaciones (R08)

**Cierres (reduce-only)** (C27, C57, C64):
- Cantidad $= \min\big(q_{pedida},\ q_{ab}-\sum$ reservas de cierre$\big)$; lado forzado; nunca invierte la posición ni deja la cantidad negativa.
- **El stop tiene prioridad:** cancela la parte **pendiente** de los cierres manuales, conserva las ejecuciones ya hechas y cierra todo lo que quede.
- **El riesgo de la posición se mantiene completo hasta que el cierre se ejecuta** (6.4.1).

**Ampliaciones** (aumentar una posición abierta):

Una ampliación es una **intención de entrada** sobre una posición existente. Pasa por la **puerta de riesgo completa** (C02) y se rechaza si incumple **cualquiera** de estas condiciones, **aunque la posición vaya ganando**:
1. **Sin estados de límite:** ni pérdida diaria, ni drawdown, ni invariantes rotos, ni equity no determinable, ni datos antiguos del instrumento, ni stop atravesado pendiente (6.5).
2. **No promediar a la baja:** la posición existente debe tener $V_p = U - C^{est} \ge 0$ (5.6). La interpretación por activo base, mirando Spot y Futures a la vez, es **D20**.
3. **0,5 % aplicado a la posición resultante**, para que no se pueda esquivar el límite troceando la entrada:

   $R^{ab}_p+N_{add}\cdot L_c\ \le\ R_{max}$

   En Spot, $Q_{add}\cdot L_u$. $R^{ab}_p$ es el riesgo abierto actual (6.4.1) y $L_c$ o $L_u$ se calculan con el **stop de la posición** (6.2.5). **En los dos términos, $s_x^{est}$ se evalúa para la cantidad resultante** ($X_p+X_{add}$), porque el coste de salida de estrés no es aditivo (6.2.2). Por tanto, la cantidad añadida es como máximo $N^{add}_1=(R_{max}-R^{ab}_p)/L_c$, con la regla de cantidad de 6.2.5. Si $R^{ab}_p\ge R_{max}$, no se puede ampliar.
4. **Apalancamiento ≤ 2x y margen aislado** en la posición resultante, con margen disponible para lo añadido ($N_2$).
5. **Tramo de riesgo nivel 1** para el valor **total** de la posición resultante ($N_3$).
6. **Condición de stop frente a liquidación** (6.3), recalculada con el $\bar P$ y el $M_{act}$ nuevos.
7. **Tamaños mínimo y máximo de orden** para la cantidad añadida.
8. **Límite agregado (D27) y límite por grupo (D14)**, cuando estén decididos, aplicados con el riesgo añadido.
9. **Un solo stop por posición:** la ampliación usa el stop existente. Cambiarlo es un movimiento de stop aparte. Acercarlo siempre está permitido. Alejarlo pasa por la puerta de riesgo como aumento de riesgo y está prohibido en los estados de límite. Si se prohíbe siempre, lo decide **D13**.

**Otros:**
- **En Spot no hay cortos:** toda venta es reduce-only.
- **D34** (exposición agregada por activo base): se calcula pero no se limita.

### 6.7 Cuestiones registradas como mejora futura (no bloquean una implementación segura)

| ID | Cuestión | Por qué no bloquea |
|---|---|---|
| M1 | Regla de elección de $\lambda$. Hoy es el menor $\lambda\in[1,2]$ que cumple 6.3, es decir, $\lambda=1$ en la práctica. Usar hasta 2x exigiría cambiar la regla | Es una cuestión de eficiencia del capital, no de seguridad: $\lambda=1$ es la opción más conservadora |
| M2 | Convención de signo del funding en KuCoin ($\rho>0$: pagan los largos) [P] | Se verifica en la documentación antes de la F5. La proyección ya incluye $q_{95}$ sin signo |
| M3 | La fórmula de liquidación de cortos es derivada [I] | No bloquea el cierre de la Fase 0. Se confirma con un ejemplo oficial **antes de la F4**, porque el módulo de riesgo usa la fórmula (`04` B28). La condición de 6.3 lleva además el margen $m_{safe}$ |

## 7. Simulador

**Series:** last, mark (canal UTA `mark-price`, que trae mark e index cada segundo; la API UTA no da velas de mark con `klineType` [H, E1]) e index. **Fuente de las velas de mark e index** [decisión del propietario, 7 nov 2026]: velas propias de 1 minuto construidas con el canal `mark-price`, solo desde que el grabador está en marcha. Los huecos del index se reconstruyen con el histórico de Classic; los del mark quedan como hueco (3.7).

**Liquidación:** se comprueba con el mark **antes** que el stop, según el orden de los mensajes. Supone la pérdida de todo el margen.

**Ejecución:**
- latencia simulada, más el retraso de la marca de agua;
- recorrido del libro y ejecución parcial;
- una orden limitada solo se ejecuta si el precio **la atraviesa**;
- con gap, la ejecución es a la apertura más slippage;
- **sin un precio observado no hay ejecución**.

### 7.1 Criterios

| Criterio | Cumple | Parcial | Falla |
|---|---|---|---|
| **F1. Orden** | No hay sucesos que compitan, o el orden se ha demostrado | Hay sucesos que compiten, pero se pueden acotar | No se pueden acotar |
| **F2-V. Vela** | Dimensiones necesarias consistentes (cruzada) o suficientes con REST | Discrepancia que se puede acotar | Sin evidencia en un minuto relevante |
| **F2-M. Microestructura** (solo si la estrategia la declara) | Flujo en S1 y profundidad válida | — | No se cumple |
| **F3. Entradas del modelo** | Entradas válidas | Modelo de respaldo (rango de precio) | **No hay ejecución ni resultado.** La orden queda en espera o caduca |

**Estados de un resultado** (solo los asigna el clasificador de svc-broker, mediante una función `SECURITY DEFINER`, con auditoría):

| Situación | Estado |
|---|---|
| Todo cumple | **`final_modelo`**: un resultado **modelado**, nunca una ejecución observada |
| Algo parcial y nada falla | `acotada` (rango [mejor, peor]) |
| Falla F1 o F2 | `no_verificable` (valoración prudente) |
| Por defecto | `provisional` (cuenta para el riesgo y el ledger con el peor caso; **nunca entra en estadísticas**) |

**Reconstrucción con velas:** con el stop y la liquidación en minutos distintos, se acepta. En el mismo minuto, `acotada`.

**Huecos de datos (C24):** la posición queda en suspenso, valorada de forma prudente y sin entradas en el instrumento. Se reintenta el relleno durante 24 h. Lo que se resuelva después se corrige con asientos correctivos.

## 8. Concurrencia

- **Orden global de bloqueo:** cuentas (id ascendente) → posiciones → reservas de riesgo → reservas de cierre → órdenes.
- **Protocolo de bloqueo de posición (PBP):** todas las modificaciones pasan por funciones `SECURITY DEFINER`:
  - `reserve_close`, `execute_close`, `execute_stop`, `amplify`, `move_stop`, `release_reservation`, `expire_reservation`, `book_funding`;
  - estas funciones toman los bloqueos;
  - **los roles de servicio no tienen `UPDATE` ni `DELETE` directos**;
  - triggers diferidos comprueban los invariantes al confirmar.
- **Paso atómico de reserva a posición (C22):** ejecuciones, posición, ledger, consumo de la reserva, riesgo abierto y evento outbox en **una sola transacción**.
- **Autorización de riesgo (C02):** `FOR UPDATE` de la cuenta, `READ COMMITTED` y reserva de riesgo con caducidad.
  - [H] En `READ COMMITTED`, `SELECT FOR UPDATE` bloquea y devuelve la versión actualizada de la fila y vuelve a evaluar el `WHERE`.
- **Idempotencia:**
  - clave (cuenta, tipo, `dedupe_key`) más huella SHA-256 de la solicitud en JCS;
  - **misma clave con otra huella = `conflicto_idempotencia`**.
- **Caducidad lógica:** `expires_at <= clock_timestamp()` evaluado después de obtener el bloqueo.
  - [H] `now()` es el inicio de la transacción; `clock_timestamp()` es la hora real.
  - **No depende de que haya procesos vivos.**
- **Barrido:**
  - busca candidatas **sin bloquear**;
  - `expire_reservation`: leer sin bloqueo → bloquear cuenta, posición y reserva → **volver a validar** relaciones, estado y caducidad → liberar dinero y riesgo y cambiar el estado, todo atómico y auditado;
  - si cambia el conjunto, `ROLLBACK` y se reintenta en una transacción nueva.
- **Relaciones inmutables:** cuenta, posición, tipo y origen de una reserva, protegidos por trigger.
- **Reintentos:** `40P01` y `40001` repiten la transacción completa con backoff variable y un máximo. `lock_timeout` deja la reserva para el siguiente barrido.
- **El orden fijo reduce los interbloqueos, pero no los elimina.**
- [H] (PostgreSQL 18, §13.3) Los bloqueos **de fila y de tabla** se liberan al terminar la transacción. Los adquiridos **después de establecer un savepoint** se liberan también al hacer `ROLLBACK TO` ese savepoint. La afirmación se limita a esos dos tipos de bloqueo y no se generaliza a otros mecanismos [H16].

## 9. Seguridad

**Sistema operativo:**
- un usuario por servicio;
- `LoadCredential=` (Anthropic solo para news; Telegram para telegram y watchdog);
- `IPAddressDeny=any` en strategy, risk, broker y web [P: comprobar que el socket Unix sigue funcionando];
- SSH solo con clave e IP restringida, `ufw`, `fail2ban` y **0 puertos de entrada** salvo SSH.

**PostgreSQL:**
- `listen_addresses=''`: sin escucha TCP, solo socket Unix [H];
- `pg_hba.conf` solo con reglas `local ... peer` [H: `peer` solo funciona en conexiones locales];
- límite de conexiones (`CONNECTION LIMIT`) y `statement_timeout` (`ALTER ROLE ... SET`) por rol, con los valores de la tabla de atributos de los roles;
- `REVOKE ALL ON SCHEMA public FROM PUBLIC`;
- **funciones `SECURITY DEFINER` endurecidas** [H, documentación de PostgreSQL]:
  - `SET search_path = trading, pg_temp`, con `pg_temp` el último;
  - nombres con esquema explícito;
  - crear la función, `REVOKE` de PUBLIC y `GRANT` en **una sola transacción**;
  - propietario `kbot_fn_owner` (NOLOGIN, mínimos privilegios);
  - los servicios sin `CREATE` en ningún esquema;
  - sin SQL dinámico;
  - validación de argumentos.

| Rol | Puede | No puede |
|---|---|---|
| `kbot_owner` | DDL, `risk_limits` (solo migraciones) | Ningún servicio lo usa |
| `kbot_ingest` | `market.*` | `trading`, `news`, `config` |
| `kbot_news` | `news.*`, `ai.*` | Nada en `trading` |
| `kbot_strategy` | Leer mercado y noticias confirmadas; insertar señales e intenciones | Aprobar |
| `kbot_risk` | **Único** que inserta reservas de riesgo y órdenes aprobadas | Ejecutar; cambiar límites |
| `kbot_broker` | Funciones de ejecución, ledger y clasificador | Aprobar |
| `kbot_watchdog` | Leer; activar paradas | Desactivarlas |
| `kbot_web` | Leer vistas; solicitudes; `resume_trading` (con precondiciones) | Límites |
| `kbot_telegram` | Leer; solicitar pausa (**no existe `/reanudar`**) | Todo lo demás |
| `kbot_backup` | `pg_read_all_data` | Escribir |

**Atributos de los roles.** `CREATE ROLE` crea los roles sin login por defecto [H], así que cada atributo se declara expresamente. Ningún rol tiene contraseña: con `PASSWORD NULL`, la autenticación por contraseña siempre falla [H].

| Rol | Login | Cómo se conecta | Límite de conexiones [R] | `statement_timeout` [R] |
|---|---|---|---|---|
| `kbot_owner` | NOLOGIN (§6.1) | Nunca directamente. El administrador aplica las migraciones conectado por `peer` como `postgres` y con `SET ROLE kbot_owner`, así que los objetos pertenecen a `kbot_owner` [R] | — | — (los valores por rol solo se aplican al iniciar sesión, no con `SET ROLE` [H]) |
| `kbot_fn_owner` | NOLOGIN | Nunca: solo es propietario de las funciones `SECURITY DEFINER` | — | — |
| Los 8 de servicio | LOGIN | `peer` desde el usuario del sistema de su servicio (§1.1) | 5 | 30 s |
| `kbot_backup` | LOGIN | `peer` desde el usuario del sistema del servicio de copias | 2 | 0 (sin límite: la copia completa puede tardar) |

Los límites y tiempos son provisionales: se ajustan con el consumo medido en la F2 (punto 83 de `04`).

**Roles y credenciales (C50):**
- `roles.sql` en Git contiene **solo objetos globales**, con `PASSWORD NULL`.
- Las funciones y los permisos sobre objetos van en las migraciones.
- **CI en tres capas:**
  1. analizador SQL real (`pglast` [H: versión 8.7 con la gramática de PostgreSQL 18.6, validado en la F1]) con lista blanca de sentencias;
  2. búsqueda de secretos (gitleaks);
  3. **PostgreSQL efímero, instalado en el propio runner de Ubuntu.** No se usa un contenedor de servicio al que se llega por TCP, porque `peer` solo funciona por socket Unix local [H]. Comprueba:
     - `rolpassword IS NULL` y el login, el límite de conexiones y el `statement_timeout` de cada rol, según la tabla de atributos;
     - `pg_hba.conf` solo con reglas `local ... peer`. En la CI, un mapa de `pg_ident.conf` permite al usuario del runner conectarse como cada rol [H];
     - que no hay escucha TCP;
     - la huella del catálogo de las funciones `SECURITY DEFINER`.

     **Que la contraseña no sirve se deduce** de `rolpassword IS NULL` y de que `pg_hba.conf` no tiene reglas de contraseña. **No se demuestra con un intento por TCP:** con `listen_addresses=''`, ese intento ni siquiera llega a la autenticación.
- **Comprobación diaria de deriva.**

**Panel:** argon2, TOTP, CSRF, reautenticación para acciones críticas y auditoría.
**Telegram:** lista blanca de chat ID.

**Amenazas cubiertas:**
- error humano en los límites (PR más CI);
- panel o token de Telegram comprometidos;
- prompt injection a través de noticias (la IA no crea intenciones; su salida se valida contra un esquema);
- cadena de suministro (`uv.lock` con hashes y procesos de riesgo sin red);
- robo de copias (cifrado `age` con la clave privada fuera del servidor);
- manipulación de la base de datos (solo inserción y auditoría encadenada).

## 10. Copias, restauración y sellado

### 10.1 Copias (C40, C44)

- **Artefacto:** cada hora, `pg_dump -Fc` de **toda la base de datos**, excluyendo solo los datos de las particiones ya archivadas en Parquet.
  - [H] `pg_dump` es consistente con uso concurrente e incluye las secuencias, pero no los roles.
  - [H] La documentación avisa de que no suele ser lo adecuado para producción "salvo en casos simples"; [I] este es un caso simple.
- **Retención** [D25, aprobada el 7 de noviembre de 2026]: 3 horarias, 7 diarias, 2 semanales y 2 previas a una actualización. Unos 13,7 GB de los 20 GB de Object Storage [H] en el escenario medio, año 1, peor caso.
- **Manifiesto:**
  - modo por tabla: `incluido` (filas y hash) o `excluido_parquet` (ruta, sha256 y filas);
  - huella del catálogo;
  - versiones (PostgreSQL, `pg_dump`, extensiones, Alembic, sistema y arquitectura).
- **Roles:** `roles.sql` y una exportación real de los objetos globales **cifrada**, nunca en Git.
- **Cifrado:** `age` para dos destinatarios (clave principal y clave de recuperación, guardadas fuera del servidor). Si se pierden ambas, las copias son irrecuperables.
- **RPO:** 1 h. **RTO:** 2-4 h. **Restaurar el software nunca restaura la base de datos** (precisión D).
- **Mientras las copias no vayan a Object Storage, su retención no se da por cumplida** (D25). Las copias horarias cifradas se guardan en el servidor y se llevan también al PC del propietario.
  - Frente a fallos de la base de datos o borrados, el RPO es de 1 h.
  - **Frente a la pérdida del servidor, el RPO es el tiempo transcurrido desde la última copia fuera del servidor.**
  - Cómo se cumple en la F2 lo decide el propietario (pregunta P6 del alcance de la F2).
- **Prueba de restauración en 10 pasos** sobre un cluster vacío:
  1. roles;
  2. `pg_restore --exit-on-error --single-transaction`;
  3. huella del catálogo;
  4. restricciones validadas;
  5. recuentos y hashes;
  6. reconciliación;
  7. permisos;
  8. carga de Parquet;
  9. servicios aislados hasta `ENTRY_BLOCKED`;
  10. tiempo medido.

  Se ejecuta en la F2 (con datos sintéticos) y después cada mes. **Hasta entonces, no está demostrada.**
- **Parquet:**
  - SHA-256 de todos los archivos, cada semana;
  - **lectura semántica completa por rotación de 30 días**;
  - copias en Object Storage comprobadas por rotación;
  - **completitud frente a lo esperado** (3.5).
  - Solo se afirma "todo validado" al terminar un ciclo.

### 10.2 Sellado diario (C55, C62, C68, C73)

- **Sello:**
  - `secuencia`, `seal_day_utc`, `generated_at`, `late`, `recovery_batch_id`;
  - `hash_sello_anterior`, cabeza de `audit_log`, raíz de Merkle, número y rango de manifiestos;
  - $H_{sello}=SHA256(JCS(\text{sello sin } hash\_sello))$.
- **Merkle:** RFC 6962 §2.1, que tiene la misma estructura que la RFC 9162 §2.1.1 [H]. Hojas en JCS (RFC 8785 [H]), ordenadas por `manifest_id`. SHA-256.
- **Cada manifiesto se sella exactamente una vez** y conserva su `origin_day_utc`.
- **Tras una caída,** se genera un sello `late` por cada día que falte.
- **Controles:** días sin sello, duplicados, encadenamiento y manifiestos pendientes.
- **Copias del sello:** Telegram (**no es inmutable**) y la copia del propietario en su PC (es la que aporta independencia). El sellado externo por terceros es D31.
- **Un hash demuestra la integridad de lo sellado, no que los datos originales sean verdaderos.**

### 10.3 Almacenamiento de Oracle (C36)

- [H] 200 GB entre todos los volúmenes. El mínimo del volumen de arranque es de 47 o 50 GB (la documentación se contradice).
- **Opción B, sin centinela [D11, aprobada el 7 de noviembre de 2026]:** 50 + 110, con 40 GB libres; copia cifrada al PC. Región principal: **Frankfurt** [D02, aprobada el 7 de noviembre de 2026]. Con centinela, solo es viable reduciendo los datos a 90 GB [D11].
- **Escenario medio, año 1:** base de datos de unos 22 GB y Parquet de hasta 36 GB sin comprimir. Cabe. **El escenario exigente no cabe**: habría que reducir el alcance o pedir autorización.

## 11. Noticias e IA

**Noticias:**
- **Estados:** confirmada, pendiente o descartada. **Solo las fuentes oficiales confirman.**
- Las publicaciones del mismo hecho se agrupan en `news.events`.
- Las noticias recirculadas se asocian al acontecimiento original.
- **Las noticias se fechan con `first_seen_at`**, no con la fecha de publicación.
- **Los rumores y las redes sociales nunca activan operaciones.**
- **X está excluido:** pago por uso, unos 0,005 $ por publicación leída [H]. Reddit está pendiente [P, D05].
- **Si cae el servicio de noticias:** el motor matemático continúa en estado `ANALISIS_FUNDAMENTAL_DEGRADADO` [D19].

**IA:**
- **Niveles:** nivel 1 sin IA; nivel 2 Haiku 5.5; nivel 3 Sonnet 5.5; Opus 5.5 excepcional [D09].
- **Precios** [H, por millón de tokens de entrada / salida]: Haiku 0,10/0,50 · Sonnet 2/10 · Opus 4/20 · Batch −50 %.
- **La IA nunca crea intenciones ni aprueba operaciones.**

**Presupuesto (C32):**
- reserva atómica previa del coste máximo ($tokens_{entrada}\cdot p + max\_tokens\cdot p_{salida}$, razonamiento incluido);
- reintentos del SDK desactivados;
- una llamada interrumpida se liquida por el máximo reservado;
- tarifas versionadas;
- al 80 %, aviso y restricción del nivel 3; **al 100 %, se suspenden las llamadas de pago y el resto sigue funcionando**;
- **tope en USD** = $0{,}95\cdot25\cdot fx_{min30}/((1+c_{fx})(1+IVA))$ [D23; P: IVA].

## 12. Estrategias y validación

| Familia | Contexto / señal / ejecución | Entrada (resumen) | Invalidación |
|---|---|---|---|
| Tendencia | 4h-1d / 1h / 15m | EMA rápida > lenta, ADX alto, retroceso, MACD | La tendencia superior se invierte o el ADX cae |
| Breakout | 4h / 1h / 15m | Cierre fuera de soporte o resistencia tras estrechamiento de Bollinger, con volumen relativo | Vuelve dentro del rango |
| Reversión a la media | 1h / 15m / 5m | ADX bajo, fuera de 2σ, RSI extremo, lejos del VWAP | El ADX sube o hay ruptura |
| Scalping | 15m / 1m-5m / 1m | VWAP más desequilibrio del libro, **solo con libro válido** y movimiento esperado mayor que k veces los costes | El spread se amplía o el libro es antiguo |
| Volumen y flujo | 15m / 5m / 1m | Pico de volumen relativo, desequilibrio y confirmación | El flujo se invierte |

**Reglas comunes:**
- régimen determinista (ADX, percentil del ATR, anchura de Bollinger);
- **una posición neta por instrumento**; con señales opuestas no se opera;
- las temporalidades superiores, solo cerradas;
- **cuentas sombra** para evaluar cada estrategia;
- `strategy_version` = commit + hash de los parámetros;
- **ninguna estrategia se activa sin una ficha matemática aprobada** (precisión A);
- el aprendizaje solo genera propuestas.

**Backtesting:**
- decisión en la vela cerrada t y ejecución en t+1;
- prueba que "envenena" los datos futuros;
- universo con instrumentos deslistados [P];
- 60/20/20 más walk-forward;
- **el sentimiento generado por IA solo se valida en vivo**;
- **el libro de órdenes solo se valida con profundidad grabada por el propio sistema** [H: no hay histórico].

**Estadística (C37, C41, C45):**
- serie de resultados **diarios** en R por estrategia (cuentas sombra);
- 0 en los días elegibles sin operaciones; los días no elegibles se excluyen;
- bootstrap estacionario por bloques, $n_{ef}$ y Sharpe deflactado;
- **regla de contaminación:** un día con una posición no `final_modelo` sale de la serie consistente;
- **tres series:** consistente, conservadora (peor caso) y optimista;
- se informa del porcentaje de resultados basados solo en REST.

**Criterios de aptitud** (D16, pendientes): límite inferior del intervalo de confianza > 0, sensibilidad ±20 %, costes de estrés, concentración y periodos adversos. Para scalping y flujo de mercado, D28.

## 13. Infraestructura, monitorización y alertas

| Componente | RAM | Prioridad |
|---|---|---|
| PostgreSQL | ≈ 3 GB | Máxima |
| risk, broker, watchdog | ≈ 0,4 GB | Alta |
| strategy | ≈ 0,4 GB | Media |
| ingest | ≈ 0,3 GB | Media |
| news, telegram, web | ≈ 0,6 GB | Baja |
| **Total** | **≈ 5,5 GB de 12** | [I] |

[H] Oracle A1: 2 OCPU y 12 GB.

**Recuperación de instancias inactivas** [H, verificado el 6 de noviembre de 2026; B30]: Oracle puede recuperar una instancia Always Free si, durante 7 días, el percentil 95 de la CPU, la red y la memoria (esta última en A1) están por debajo del 20 %. La documentación no dice si la detiene o la borra, ni si avisa antes, ni cómo calcula el porcentaje de red [P].
- **Se mide con las métricas de Oracle** del espacio `oci_computeagent`: `CpuUtilization` y `MemoryUtilization`, que es el porcentaje de páginas de memoria usadas [H]. Requieren el plugin Compute Instance Monitoring activo, que se selecciona al crear la instancia [H]. La red se mide con `NetworksBytesIn` y `NetworksBytesOut`, en bytes [H].
- **Regla:** el uso real de memoria, según la métrica de Oracle, se mantiene al menos en el 25 %. **Nunca se genera carga artificial.** Si el uso real no basta, se aplica D01: detenerse y presentar alternativas.
- **Hasta la F7 no hay avisos automáticos por Telegram.** El propietario revisa las métricas en la consola de Oracle con un procedimiento guiado [R]. Si una alarma de Oracle con aviso por correo está dentro de lo gratuito [P], se usa además.

**Supervisión:**
- antigüedad de los mensajes;
- retraso de la cola de eventos;
- retraso de la marca de supervisión de stops;
- interbloqueos (`pg_stat_database.deadlocks`);
- disco, RAM, CPU, copias y prueba de restauración.

**Telegram:**
- **Críticas:** paradas, drawdown, pérdida diaria, invariantes, caídas, stops sin supervisar, copias fallidas, sello fallido, IA al 100 %, equity no determinable.
- **Avisos:** IA al 80 %, reconexiones, eventos fallidos, múltiplo R alto, disco al 80 %.
- **Informativas:** oportunidades, noticias confirmadas, operaciones, resúmenes diario y semanal.

## 14. Fases y pruebas

**Fases del DM:** 1 arquitectura · 2 datos · 3 indicadores y estrategias · 4 riesgo · 5 simulador · 6 noticias e IA · 7 Telegram y panel · 8 integración · 9 despliegue. Cada una con documentación, pruebas y aprobación.

**Pruebas obligatorias:**
- unitarias, con los casos de referencia de la sección 15;
- **propiedades** (identidades contables, invariantes, sin doble conteo, riesgo);
- **trayectoria** (el estado después de cada suceso, no solo el final);
- **concurrencia** con PostgreSQL real;
- **inyección de fallos** (matar servicios, PostgreSQL, WebSocket, mitad de un evento);
- **permisos** con cada rol;
- **contrato** por versión de la API (V1 y V2);
- detector de anticipación en el backtest;
- restauración en 10 pasos;
- estabilidad de 72 h;
- presupuesto de IA.

## 15. Casos de referencia (deben dar exactamente estos resultados)

| ID | Caso | Resultado esperado |
|---|---|---|
| RC1 | Spot ETH en 4 días (**detalle completo en 15.1**) | PnL diario 42,950 / −79,920 / 62,338 / 12,588; total **37,956** |
| RC2 | Futures largo de 10 contratos XBTUSDTM, $m$ = 0,001, a 60.000 (**detalle completo en 15.2**) | Total **7,3356** |
| RC3 | Liquidación: margen 300 y comisión 0,36 | Total **−300,36** |
| RC4 | Ciclo de Futures de 12 pasos (C66 corregido; **detalle completo en 15.3**) | Total **1,05395** |
| RC5 | Cancelación: reserva 151, ejecución de 2 a 60.000 | Margen 60, comisión 0,072; se liberan **90,928** |
| RC6 | Compra de 0,1 BTC con comisión en BTC | `spot_cost` 5.994; `fees` 6; cantidad 0,0999 |
| RC7 | Venta de 0,05 BTC a 63.000 con comisión en BTC ($\bar P$ 60.000) | `fees` 3,15; realizado 150,15; **ΔRes = 147** |
| RC8 | Venta total de 0,1 BTC con $r$ = 0,1 % y paso 1e-8 | $q$ = 0,09990009; $f_b$ = 0,00009991; residuo 0 |
| RC9 | Cierre de A (margen 300, $U$ −320) al mark, sin comisiones. Tres variantes: **(a)** `cash_free` 9.700; **(b)** `cash_free` 10 y B con margen 9.690; **(c)** `cash_free` 0 y B con margen 9.700 | En las tres, **E = 9.680 antes y después**. Déficit: (a) 0, (b) 10, (c) 20. Identidad: 10.000 − 320 = 9.680 [H01]. **Prueba de trayectoria pendiente (F5)** |
| RC10 | Cierre parcial de 4 de 10 contratos (largo) a 60.990; $\bar P$ 60.000, mark 61.000, stop 59.000 (**detalle completo en 15.4**) | $\Delta PnL_{día}$ = −0,186376; $\Delta R$ = −8,1888; **ΔCap (B2) = 8,002424** |
| RC11 | Caso 8 en magnitudes de riesgo: (a) se acerca el stop de A con el precio quieto; (b) el precio se mueve 30 en contra de A (**detalle completo en 15.5**) | Capacidad (B1 = B2): inicial 0; (a) **30**; (b) **0** |
| RC12 | Valoración de contingencia, largo de 10 a 60.000, margen 300, último precio 58.000 | $g$ = 25 % → **+135**; $g$ = 60 % → **−68**; ejemplo 100/130 → **−30** |
| RC13 | Fórmula oficial de liquidación (ejemplo de KuCoin) | **29.535,9** |
| RC14 | Merkle | Raíz vacía `e3b0c442…b855`; hoja vacía `6e340b9c…a01d` [H: confirmado en la F1 con `hashlib` y OpenSSL, y con los vectores de transparency-dev] |
| RC15 | Serialización | `-1.50`→`"-1.5"`; `-0`→`"0"`; 19 decimales → error |
| RC16 | Polvo: residuo de 0,00001 BTC, $\bar P$ 60.000, bid al traspaso 63.000; al día siguiente, bid 62.000 | Traspaso: `dust_cost` 0,63; coste dado de baja 0,6; realizado de la posición **+0,03**. Día siguiente: $V_{res}$ = 0,62 − 0,63 → $PnL_{res}$ = **−0,01** [H03] |

### 15.1 Detalle de RC1 (Spot ETH-USDT, 1 ETH)

**Parámetros:**
- Comisión del 0,1 % en la entrada y en la salida, en USDT.
- Valoración al **bid**.
- $C^{est}$ = 0,1 % del valor al bid + **1 USDT por ETH abierto** (slippage estimado).
- $PnL_d = \Delta Res_d + \Delta V_d$, con $V = U - C^{est}$ y $V = 0$ fuera de la vida de la posición.

| Día | Suceso | $\Delta Res$ | Bid al cierre del día | Cantidad abierta y coste | $U$ | $C^{est}$ | $V$ al final | $PnL$ |
|---|---|---|---|---|---|---|---|---|
| D1 | Compra de 1 ETH a 3.000 (ask 2.999, bid 2.998 al ejecutar), comisión 3,000 | −3,000 | 3.050 | 1 ETH, coste 3.000 | 50 | 3,05 + 1 = 4,05 | 45,950 | **42,950** |
| D2 | — | 0 | 2.970 | 1 ETH, coste 3.000 | −30 | 2,97 + 1 = 3,97 | −33,970 | **−79,920** |
| D3 | Venta de 0,4 a 3.020: ingreso 1.208, coste dado de baja 1.200, realizado +8, comisión 1,208 | +6,792 | 3.040 | 0,6 ETH, coste 1.800 | 24 | 1,824 + 0,6 = 2,424 | 21,576 | **62,338** |
| D4 | Venta de 0,6 a 3.060: ingreso 1.836, coste 1.800, realizado +36, comisión 1,836 | +34,164 | — | 0 | 0 | 0 | 0 | **12,588** |
| **Σ** | | **37,956** | | | | | | **37,956** |

**Comprobación económica:** ingresos 3.044 − coste 3.000 − comisiones 6,044 = **37,956**.

### 15.2 Detalle de RC2 (Futures XBTUSDTM, largo)

**Parámetros:**
- **10 contratos** con **multiplicador $m$ = 0,001 BTC**, entrada a **60.000**.
- Apalancamiento 2x, así que el **margen es 300**.
- **Comisión de tomador del 0,06 %** sobre el nocional, en la entrada y en cada salida.
- $C^{est}$ = **0,08 % del nocional al mark** (0,06 % de comisión + 0,02 % de slippage).
- **El funding se expresa como importe en USDT:** tasa × valor de la posición al mark. Se liquida contra el margen.

| Día | Suceso | $\Delta Res$ | Mark al cierre del día y contratos | $U$ | $C^{est}$ | $V$ al final | $PnL$ |
|---|---|---|---|---|---|---|---|
| D1 | Apertura: comisión 0,0006 × 600 = 0,36 | −0,3600 | 61.000; 10 | 10 | 0,488 | 9,5120 | **9,1520** |
| D2 | Funding +0,01 % sobre 612 (mark 61.200) = **pago de 0,0612**; funding −0,02 % sobre 608 (mark 60.800) = **cobro de 0,1216** | +0,0604 | 59.500; 10 | −5 | 0,476 | −5,4760 | **−14,9276** |
| D3 | Cierre de 4 a 60.500: realizado 4 × 0,001 × 500 = +2; comisión 0,0006 × 242 = 0,1452; margen liberado 300,0604 × 4/10 = 120,02416 | +1,8548 | 60.800; 6 | 4,8 | 0,29184 | 4,50816 | **11,83896** |
| D4 | Cierre de 6 a 61.000: realizado 6 × 0,001 × 1.000 = +6; comisión 0,0006 × 366 = 0,2196 | +5,7804 | —; 0 | 0 | 0 | 0 | **1,27224** |
| **Σ** | | **7,3356** | | | | | **7,3356** |

**Comprobación:** realizado +8 + funding neto +0,0604 − comisiones (0,36 + 0,1452 + 0,2196 = 0,7248) = **7,3356**.

### 15.3 Detalle de RC4 (ciclo de Futures de 12 pasos)

**Parámetros:** XBTUSDTM, $m$ = 0,001, apalancamiento 2x (margen = nocional/2), comisión de tomador del 0,06 %.
- Comisiones de **entrada** pagadas **desde `cash_reserved`**; comisiones de **salida** pagadas **desde `cash_free`**.
- Margen liberado: $M_r = M_{act}\cdot N_c/N_{ab}$, y el cierre total libera el resto exacto.

| # | Suceso y cálculo | Movimiento de cuentas | `cash_reserved` | `futures_margin` | $\Delta cash\_free_p$ | $\Delta Res_p$ |
|---|---|---|---|---|---|---|
| 1 | Reserva O1 para 10 contratos | `cash_free` → `cash_reserved` 302 | 302 | 0 | −302 | 0 |
| 2 | Ejecución de 5 a 60.000: nocional 300, margen 150, comisión 0,18 | `cash_reserved` → margen 150; → `fees` 0,18 | 151,82 | 150 | 0 | −0,18 |
| 3 | Ejecución de 5 a 60.200: nocional 301 ($K^F$ = 601, $\bar P$ = 60.100), margen 150,5, comisión 0,1806 | `cash_reserved` → margen 150,5; → `fees` 0,1806 | 1,1394 | 300,5 | 0 | −0,1806 |
| 4 | Liberación del sobrante de O1 | `cash_reserved` → `cash_free` 1,1394 | 0 | 300,5 | +1,1394 | 0 |
| 5 | Funding +0,01 % sobre 601,5 (mark 60.150), **pago de 0,06015** | margen → `funding` | 0 | 300,43985 | 0 | −0,06015 |
| 6 | Reserva O2 para 10 contratos (ampliación permitida: con mark 60.300, $U$ = 2, $C^{est}$ = 0,4824, $V_p$ = 1,5176 ≥ 0) | `cash_free` → `cash_reserved` 302,2 | 302,2 | 300,43985 | −302,2 | 0 |
| 7 | Ejecución de 10 a 60.300: nocional 603 ($K^F$ = 1.204, $N$ = 20, $\bar P$ = 60.200), margen 301,5, comisión 0,3618 | `cash_reserved` → margen 301,5; → `fees` 0,3618 | 0,3382 | 601,93985 | 0 | −0,3618 |
| 8 | Liberación del sobrante de O2 | `cash_reserved` → `cash_free` 0,3382 | 0 | 601,93985 | +0,3382 | 0 |
| 9 | Funding −0,005 % sobre 1.208 (mark 60.400), **cobro de 0,0604** | `funding` → margen | 0 | 602,00025 | 0 | +0,0604 |
| 10 | Cierre de 5/20 a 60.500: $\pi$ = 5 × 0,001 × 300 = +1,5; $M_r$ = 602,00025 × 5/20 = 150,5000625; comisión 0,0006 × 302,5 = 0,1815; $K^F_{ab}$ = 903 | margen → `cash_free` 150,5000625; realizado +1,5; `cash_free` → `fees` 0,1815 | 0 | 451,5001875 | +151,8185625 | +1,3185 |
| 11 | Cierre de 5/15 a 60.000: $\pi$ = 5 × 0,001 × (−200) = −1; $M_r$ = 451,5001875 × 5/15 = 150,5000625; comisión 0,18; $K^F_{ab}$ = 602 | margen → `cash_free`; realizado −1; comisión | 0 | 301,000125 | +149,3200625 | −1,18 |
| 12 | Cierre total de 10 a 60.400: $\pi$ = 10 × 0,001 × 200 = +2; $M_r$ = **resto exacto** 301,000125; comisión 0,0006 × 604 = 0,3624 | margen → `cash_free`; realizado +2; comisión | 0 | 0 | +302,637725 | +1,6376 |
| **Σ** | | | | | **+1,05395** | **+1,05395** |

**Comprobaciones:**
- **Nocional:** 300 + 301 + 603 = 1.204 de entrada; 301 + 301 + 602 = 1.204 dados de baja.
- **Margen:** 150 + 150,5 + 301,5 aportados = 602; funding neto +0,00025; liberado 150,5000625 + 150,5000625 + 301,000125 = **602,00025**.
- **Resultado:** realizado +2,5 − comisiones de entrada 0,7224 − comisiones de salida 0,7239 + funding neto 0,00025 = **1,05395**.

### 15.4 Detalle de RC10 (identidad del cierre parcial, R10)

**Datos** (todos de la fuente, C63):

| Dato | Valor |
|---|---|
| Posición | Futures lineal XBTUSDTM, largo ($d$ = +1), $N_{ab}$ = 10, $m$ = 0,001 BTC, así que $X$ = 0,01 BTC |
| $\bar P$ | 60.000 |
| Mark $P_m$ antes del cierre | 61.000. Es la **única instantánea de mercado** y se usa tanto para $U$ como para $\sigma$ |
| Stop $P_s$ | 59.000 |
| Cierre | $N_c$ = 4 contratos a $P_x$ = 60.990, como tomador |
| Comisión real | 0,06 % del nocional ejecutado: 0,0006 × (4 × 0,001 × 60.990) = 0,0006 × 243,96 = **0,146376** |
| Parámetros **de prueba** (no son valores de D22) | $C^{salida}$ al stop con 0,08 % ($f_x$ 0,06 % + $s_x^{est}$ 0,02 %). $F^{proy}$ = 0, porque el caso no modela funding |

**Cálculo:**

| Magnitud | Antes (10 contratos) | Después (6) | Variación |
|---|---|---|---|
| Término de stop $d\,X\,(P_m-P_s)$ | 0,01 × 2.000 = 20 | 0,006 × 2.000 = 12 | −8 |
| $C^{salida}(X,P_s)=X\cdot 59.000\cdot 0{,}0008$ | 0,472 | 0,2832 | −0,1888 |
| $R^{ab}$ (6.4.1) | 20,472 | 12,2832 | $\Delta R = -8{,}1888$ (así que $\rho_c$ = 8,1888) |
| $U = d\,X\,(P_m-\bar P)$ | 10 | 6 | −4 |
| Resultado realizado $\pi$ | — | 4 × 0,001 × (60.990 − 60.000) = 3,96 | +3,96 |
| Comisión | — | 0,146376 | −0,146376 |
| **$\Delta PnL_{día}$** | | | **3,96 − 4 − 0,146376 = −0,186376** |

**Capacidad:**
- **B2:** $\Delta Cap = \Delta PnL_{día}-\Delta R = -0{,}186376+8{,}1888 = \mathbf{8{,}002424}$.
- **B1** (D27 sigue sin decidir): $\Delta Cap_{B1}=-\Delta L_{día}-\Delta R$.
  - Si $PnL_{día}\le 0$ antes y después, coincide con B2: 8,002424.
  - Si $PnL_{día}>0$ antes y después, vale 8,1888.
- RC10 no fija el $PnL_{día}$ previo, y no se inventa. Por eso su valor de referencia es el de B2 y sus dos componentes, $\Delta PnL_{día}$ y $\Delta R$, que **no dependen de D27**.

**Justificación de la diferencia de 0,002424:**

$$\Delta Cap=\underbrace{8}_{\text{distancia mark-stop de lo cerrado}}+\underbrace{0{,}1888}_{\text{costes de salida planificados}}-\underbrace{0{,}04}_{\sigma\ \text{real}}-\underbrace{0{,}146376}_{\text{comisión real}}=8+0{,}002424$$

- $\sigma$ = 4 × 0,001 × (61.000 − 60.990) = 0,04: el slippage del cierre respecto al mark.
- El riesgo tenía reservado, para esos 4 contratos, un coste de salida de **0,1888**, calculado al **precio del stop** (59.000) con un 0,08 %.
- El cierre real costó **0,186376**: salida a 60.990, con 0,04 de slippage y un 0,06 % de comisión.
- **Los 0,002424 son el coste de salida planificado que no se llegó a consumir.** No es un redondeo: es la diferencia entre el coste modelado y el real, que se calculan sobre bases distintas (el precio del stop frente al precio de ejecución).
- Es la identidad general de C63: $\Delta Cap=\rho_c-\sigma-f_x$, con $\rho_c$ calculado como diferencia exacta de $R^{ab}$.

### 15.5 Detalle de RC11 (caso 8, R11)

**Datos** (todos de la fuente, C56.1). El caso está definido **en magnitudes de riesgo (USDT), no en precios**:

| Dato | Valor |
|---|---|
| $E_0$ | 10.000, así que $0{,}02\cdot E_0$ = 200 |
| Situación inicial | $PnL_{día}$ = 0; $R^{ab}_A$ = 50; resto del riesgo (otras posiciones y reservas) = 150; $R_{total}$ = 200 |
| Hipótesis | Exposición lineal (Spot o Futures lineal); stop no atravesado; $F^{proy}$ = 0; el resto de exposiciones no cambia; $C^{salida}$ evaluado al precio del stop |
| Variante (a) | El stop de A se acerca al precio **sin que el precio cambie**. $R^{ab}_A$ pasa a 20, con todos sus términos; $PnL_{día}$ no cambia |
| Variante (b) | El precio se mueve en contra de A **sin cambiar el stop**, con $\Delta U_A$ = −30. Como el término de stop varía en $d\,X\,\Delta P = \Delta U_A$ y $C^{salida}$ al stop no cambia, $R^{ab}_A$ pasa a 20 y $PnL_{día}$ a −30 |

| Estado | $PnL_{día}$ | $L_{día}$ | $R_{total}$ | $Cap_{B1}=200-L_{día}-R_{total}$ | $Cap_{B2}=200+PnL_{día}-R_{total}$ |
|---|---|---|---|---|---|
| Inicial | 0 | 0 | 200 | 0 | 0 |
| (a) | 0 | 0 | 170 | **30** | **30** |
| (b) | −30 | 30 | 170 | **0** | **0** |

- Como $PnL_{día}\le 0$ en todos los estados, B1 y B2 dan lo mismo: **el resultado no depende de D27**.
- **(a) libera capacidad real:** la pérdida máxima planificada baja de 200 a 170.
- **(b) no libera nada:** la pérdida acumulada sube lo mismo que baja el riesgo pendiente ($-30-170=-200=0-200$).
- **La fuente no da precios ni cantidades, y no se inventan.** Cualquier posición lineal con $d\,X\,\Delta P=-30$ y el stop sin atravesar reproduce (b).

## 16. Fuentes oficiales verificadas (consultadas el 4, el 5 y el 6 de noviembre de 2026)

- **KuCoin:**
  - UTA Klines V1 y V2: https://www.kucoin.com/docs-new/rest/ua/get-klines y https://www.kucoin.com/docs-new/v2/rest/ua/get-klines
  - UTA Funding V1 y V2: https://www.kucoin.com/docs-new/rest/ua/get-current-funding-rate y https://www.kucoin.com/docs-new/v2/rest/ua/get-current-funding
  - UTA Instrument: https://www.kucoin.com/docs-new/rest/ua/get-symbol
  - UTA Trades: https://www.kucoin.com/docs-new/rest/ua/get-trades
  - WebSocket UTA: https://www.kucoin.com/docs-new/websocket-api/base-info/introduction-uta
  - Límites UTA: https://www.kucoin.com/docs-new/rate-limit-rule-uta
  - Límites Classic: https://www.kucoin.com/docs-new/rate-limit-rule-classic
  - Registro de cambios: https://www.kucoin.com/docs-new/change-log
  - Contratos de Futures: https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-all-symbols
  - Tramos de riesgo: https://www.kucoin.com/docs-new/rest/futures-trading/positions/get-isolated-margin-risk-limit
  - Órdenes TP/SL: https://www.kucoin.com/docs-new/rest/futures-trading/orders/add-take-profit-and-stop-loss-order
  - Margen aislado: https://www.kucoin.com/support/48142946141508
  - Actualización de la fórmula de margen aislado: https://www.kucoin.com/announcement/update-to-the-isolated-margin-calculation-formula
  - Límites de riesgo (ayuda): https://www.kucoin.com/support/26693511734809
  - KuCoin EU: https://www.kucoin.com/en-eu/support/48142946141092
  - Libro de órdenes de UTA, canal `obu` (consultado el 6 de noviembre de 2026): https://www.kucoin.com/docs-new/3470221w0
  - Códigos de error de UTA V2 (6 de noviembre): https://www.kucoin.com/docs-new/v2/error-code/uta-api
- **Oracle:**
  - Always Free: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
  - Regiones: https://docs.oracle.com/en-us/iaas/Content/General/Concepts/regions.htm
  - La región principal no se puede cambiar (consultado el 6 de noviembre de 2026): https://docs.oracle.com/en-us/iaas/Content/Identity/regions/managingregions.htm
  - Métricas de las instancias (6 de noviembre): https://docs.oracle.com/en-us/iaas/Content/Compute/References/computemetrics.htm
  - Activar la monitorización de las instancias (6 de noviembre): https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/enablingmonitoring.htm
- **PostgreSQL 18:**
  - NOTIFY: https://www.postgresql.org/docs/current/sql-notify.html
  - SELECT (cláusulas de bloqueo): https://www.postgresql.org/docs/current/sql-select.html
  - pg_dump: https://www.postgresql.org/docs/current/app-pgdump.html
  - Funciones de fecha y hora (§9.9.5): https://www.postgresql.org/docs/current/functions-datetime.html
  - CREATE FUNCTION (`SECURITY DEFINER`): https://www.postgresql.org/docs/current/sql-createfunction.html
  - Bloqueos explícitos (§13.3): https://www.postgresql.org/docs/current/explicit-locking.html
  - Aislamiento de transacciones (§13.2.1): https://www.postgresql.org/docs/current/transaction-iso.html
  - Versiones: https://www.postgresql.org/support/versioning/
  - Autenticación `peer` (consultada el 6 de noviembre de 2026): https://www.postgresql.org/docs/current/auth-peer.html
  - Mapas de usuarios (`pg_ident.conf`) (6 de noviembre): https://www.postgresql.org/docs/current/auth-username-maps.html
  - CREATE ROLE (`NOLOGIN` por defecto, `PASSWORD NULL`, `CONNECTION LIMIT`) (6 de noviembre): https://www.postgresql.org/docs/current/sql-createrole.html
  - ALTER ROLE (valores por rol, solo al iniciar sesión) (6 de noviembre): https://www.postgresql.org/docs/current/sql-alterrole.html
  - `listen_addresses` (6 de noviembre): https://www.postgresql.org/docs/current/runtime-config-connection.html
  - Vista `pg_hba_file_rules` (6 de noviembre): https://www.postgresql.org/docs/current/view-pg-hba-file-rules.html
- **GitHub:**
  - Facturación de GitHub Actions (los runners estándar son gratuitos en repositorios públicos) (6 de noviembre): https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions
- **Estándares:**
  - RFC 6962: https://www.rfc-editor.org/rfc/rfc6962
  - RFC 9162: https://www.rfc-editor.org/rfc/rfc9162
  - RFC 8785: https://www.rfc-editor.org/rfc/rfc8785
- **Otros:**
  - Python `decimal`: https://docs.python.org/3/library/decimal.html
  - TimescaleDB (plataformas soportadas): https://www.tigerdata.com/docs/get-started/timescaledb-supported-platforms
  - Precios de Anthropic: https://platform.claude.com/docs/en/about-claude/pricing
  - Precios de la API de X: https://docs.x.com/x-api/getting-started/pricing

## 17. Origen (para consultar el archivo histórico si hace falta)

| Sección | Correcciones de origen |
|---|---|
| 1-2 | V4 §1-6; C07; C17; D17 |
| 3 | C43, C49, C54, C60, C61, C67, C68, C72, C77, C80; precisión B |
| 4 | C01, C06, C25; V4 §6 |
| 5 | C33, C48, C53, C58, C59, C65, C66, C70, C71, C73, C75 (solo las partes sin hallazgos), C76, C78 |
| 6 | C02, C03, C05, C23, C27, C38, C42, C46, C47, C51, C52, C56, C57, C63, C64 |
| 7 | C04, C24, C34, C49, C54, C60, C67 |
| 8 | C22, C64, C69, C74, C79 |
| 9 | C07, C44, C50, C69 |
| 10 | C35, C36, C40, C44, C50, C55, C61, C62, C68, C73 |
| 11 | C31, C32; V4 §11 y §13 |
| 12 | C30, C37, C41, C45; precisión A |

**C10-C20 no existen.**

**Sustituidas por completo:** C21, C26 (en parte), C28, C29, C35, C39, C41, C46. **Rechazada por H01:** la identidad de C75.

## 18. Registro de cambios

| Fecha | Cambio | Origen |
|---|---|---|
| 5 nov 2026 | Consolidación inicial de la V4 y los anexos V4.1-V4.10 | Claude |
| 5 nov 2026 | **Ronda 1 de revisión aplicada (hallazgos H01-H18):** identidad de equity sin doble resta del déficit, RC9 con tres variantes [H01]; definición de $Res$ [H02]; cartera residual del polvo a valor razonable, RC16 [H03]; invariante de unidades con `dust` [H04]; no ejecutar sin valorar la comisión [H05]; procedimiento de tolerancias por tramos, prueba de sesgo, tope, validación independiente y pérdidas inyectadas [H06]; contexto Decimal de 80 dígitos con la trampa `Inexact` [H07]; matriz V1/V2 y pruebas de contrato por versión [H08]; cita literal del DM y polvo pendiente de ratificar [H09, parcial]; el polvo nunca crea una posición [H10]; comisión al precio de ejecución [H11]; `ausencia_sin_trades_probable` y tabla de correspondencia [H12]; alcance de la regla de cierre de vela [H13, parcial]; método del resto [H14]; origen independiente de $cap_{abs}$ [H15]; casos con cero [H17]; polvo dentro de $R_{total}$ [H18]. **H16:** sin cambios | Confirmación del revisor (ronda 1) |
| 5 nov 2026 | **Ronda 2 (Contabilidad):** sin errores en las fórmulas. Se completa el detalle reproducible de RC1 (15.1), RC2 (15.2, con multiplicador, comisiones y funding en USDT) y RC4 (15.3, con los 12 pasos, sus cálculos y los saldos de `cash_reserved` y `futures_margin`). Se acota la cita de H16 a los bloqueos de fila y de tabla (§8) | Revisor (ronda 2) |
| 5 nov 2026 | **Ronda 3 (Riesgo):** sección 6 reescrita y cerrada con los hallazgos R01-R11 (estado en 6.0): límites del 2 % y del 10 % con posiciones abiertas, funding con el signo aplicado una sola vez, liquidación de largos y cortos, coste de salida único, riesgo con el stop atravesado, D27 y correlación documentadas sin decidir, y ampliaciones. Detalle de RC10 y RC11 en 15.4 y 15.5; RC13 sin cambios | Revisor (ronda 3) |
| 5 nov 2026 | **Decisiones del propietario:** D35, D17 y D33 aprobadas y H09 aceptada (texto literal en `03_Decisiones.md`). Actualizados la cabecera, el alcance contable (sección 0), la base de datos (sección 2) y el polvo (5.5.1) | Propietario |
| 6 nov 2026 | **Revisión de las pruebas y fases de `04`:** A y C correctas; B7 y B9 se verifican solo con documentación o metadatos públicos; B28 (M3) pasa a «antes de la F4». En este documento solo cambia la fila M3 de 6.7 | Revisor |
| 6 nov 2026 | **Cierre de la Fase 0 y versión 1.0 congelada.** El propietario declara cerrada la Fase 0 (criterio de D35 cumplido) y autoriza la Fase 1. Se añaden la regla de congelación y el estado de la Fase 0 en la cabecera | Propietario |
| 6 nov 2026 | **Versión 1.1: revisión de la sección 9 antes de la Fase 1 (D35).** `pg_hba.conf` solo con `local ... peer`; tabla de atributos de los roles (login, límite de conexiones y `statement_timeout`, estos dos provisionales); la CI usa PostgreSQL instalado en el runner, no un contenedor, y deja de afirmar que un intento por TCP demuestra que la contraseña falla. Fuentes de PostgreSQL y GitHub añadidas en la sección 16. El resto de la sección 9 se confirmó sin cambios | Revisor (sección 9); aplicada a petición del propietario |
| 6 nov 2026 | **Decisión del propietario: D18 aprobada** (backtests en su PC con WSL2; el resto de su trabajo, en Windows). Actualizados la cabecera y la sección 2 | Propietario |
| 6 nov 2026 | **Fase 1 implementada y probada** en la rama `fase-1` (A1-A10 superadas; CI en verde con 355 pruebas). Estados actualizados solo con lo probado: cabecera, `pglast` en la sección 9 y RC14 en la sección 15. Sin cambios de reglas | Claude (alcance aprobado) |
| 6 nov 2026 | **Decisión del propietario: D03 aprobada** (la profundidad se graba desde la Fase 2, con el grabador en Oracle). Actualizada la cabecera | Propietario |
| 6 nov 2026 | **Versión 1.2: ronda 4 de revisión, antes de la Fase 2.** (1) Persistencia de la profundidad: canal `obu` con snapshots completos, sin guardar los mensajes originales, intervalos de 10 s con métricas, snapshot completo cada minuto y estimación de almacenamiento ligada a esa elección (3.1, 3.3 y 3.5). (2) Riesgo de recuperación de instancias inactivas de Oracle, medido con las métricas de Oracle y revisión manual hasta la F7 (13). (3) Copias: la retención en Object Storage no se da por cumplida mientras no se use, y el RPO frente a la pérdida del servidor depende de la copia externa (10.1). (4) «Solo GET» se limita a REST; el WebSocket tiene su propia lista blanca (2 y 3.1). (5) El error 429000 distingue cuota agotada y sobrecarga (3.1). Fuentes añadidas en la sección 16 | Revisor (ronda 4); aplicada a petición del propietario |
| 6 nov 2026 | **Alcance de la Fase 2 aprobado por el propietario**, con 10 + 10 candidatos de nivel 50 (en lugar de los 5 + 5 propuestos). Actualizados los candidatos y la estimación de almacenamiento de 3.1 | Propietario |
| 7 nov 2026 | **Versión 1.3: hechos comprobados en la etapa E1** (cliente de KuCoin de solo lectura, rama `fase-2-e1`). (1) `orjson` se sustituye por el `json` de la biblioteca estándar con `Decimal`, porque `orjson` lee los decimales como `float` (2). (2) El límite REST usa una ventana deslizante en lugar de un token bucket, que permitiría el doble del 50 % dentro de una ventana; pesos de todas las rutas medidos (3.1). (3) Formato del ping comprobado a medias (B31) y reapertura de la conexión a las 23,5 h (3.1). (4) Resultados de la matriz V1/V2: columnas de las velas, `klineType` solo `TRADE`, `productType` sin efecto con símbolo y volumen de la vela igual al doble de las operaciones del WebSocket [I] (3.2). (5) La API UTA no da velas de mark ni de index; su fuente histórica se decide en E2 (7). Sin cambios de reglas de negocio. **Tras las dos revisiones de E1 (APROBABLE CON CORRECCIONES):** un solo presupuesto REST por máquina, con un bloqueo del sistema operativo entre procesos (A2-01 y R-E1-01, 3.1) | Claude (alcance aprobado de la F2); **aprobada por el revisor** el 7 nov 2026 con E1 (cuatro revisiones) |
| 7 nov 2026 | **Decisión del propietario: D04 aprobada** (UTA V2 más Classic). Actualizado 3.2; las rutas V1 salen de la lista blanca | Propietario |
| 7 nov 2026 | **Decisión del propietario: fuente de las velas de mark e index.** Velas propias de 1 minuto desde el canal `mark-price`; huecos del index reconstruidos con el histórico de Classic; huecos del mark registrados como hueco. Actualizados 3.7 y 7 | Propietario |
| 7 nov 2026 | **Propuesta del esquema `market` de la etapa E2** (3.8): tablas solo de inserción, columnas y claves, métricas de profundidad, velas propias de mark e index, huecos y universo provisional. Sin `funding_symbol_map` (D04) | Claude (alcance aprobado de la F2); **pendiente de revisión** |
| 7 nov 2026 | **Revisión del esquema y de los módulos de E2: APROBABLE CON CORRECCIONES** (7 hallazgos, ninguno crítico). Aplicadas en 3.8 y en el código: velas propias solo con los 60 segundos y precios válidos (ALTO); `risk_tiers` con mercado en la clave; coherencia entre intervalos, métricas, velas, flujo y minutos impuesta por PostgreSQL; orden de cierre idempotente; `M` ausente como nulo; horas con zona obligatoria | Revisor; **confirmada el 7 nov 2026: los siete hallazgos cerrados** (el 3 tras restringir `ausencia_no_verificable` a S2 y S3) |
| 7 nov 2026 | **Decisiones del propietario:** D01, D02 (Frankfurt), D11 (opción B) y D25 aprobadas, y exclusión de las monedas estables del universo provisional. Actualizados la cabecera, 3.8, 10.1 y 10.3 | Propietario |
| 8 nov 2026 | **Decisión del propietario: comparación provisional de las velas de last hasta D26** (precios a un tick; volumen guardado sin juzgar; margen de 15 s; cierre diario a las 00:00 UTC). Actualizado 3.8 y columnas `ws_volume` y `ws_turnover` en `candles` | Propietario |
| 8 nov 2026 | **El grabador de E2** (3.8): núcleo sin reloj, universo que cambia solo a medianoche, reintentos, cierre del día, índice desde Classic y vigilante de congelaciones. **Hechos de las pruebas en vivo:** en Spot, KuCoin publica velas de volumen 0 sin operaciones, y en Futures no (3.2); el plazo de los intervalos de profundidad incluye el margen de retraso (3.5) | Claude (alcance aprobado de la F2); **auditado el 8 nov 2026 (fila siguiente)** |
| 8 nov 2026 | **Auditoría de E2 (commit `cc627a4`): APROBABLE CON CORRECCIONES**, sin hallazgos críticos ni altos. Correcciones aplicadas en la rama `fase-2-e2-correcciones`: **E2-01** (MEDIO) las velas REST pendientes ya no se pierden en un reinicio: la vela del WebSocket se guarda con su flujo (`ws_candles`) y el arranque reanuda los minutos pendientes desde la base; **E2-02** (MEDIO) igual con el índice desde Classic, distinguiendo rehecho, pendiente y perdido (`exhausted_index_rebuilds`); **E2-03** (MEDIO) un snapshot tardío sin otro vigente es `fuera_de_plazo`, no `no_recibida` (3.5); **E2-04** (MEDIO) una evaluación del universo perdida por una parada a medianoche se recupera para el día que empezó, con su hora real; **E2-05** (BAJO) criterios de aceptación con identificador por fase (F1-A1 a F1-A10 y F2-A1 a F2-A10) y tabla de trazabilidad única en `docs/informes/Trazabilidad_aceptacion.md`. También: una petición REST cubre como mucho 100 minutos objetivo (ventana de 102 velas). El revisor recuerda que los resultados de la CI los comunica el propietario y no equivalen a una auditoría independiente de todos los puntos de parada | Revisor; correcciones de Claude **revisadas el 8 nov 2026 (fila siguiente)** |
| 8 nov 2026 | **Segunda revisión de E2 (commit `0c9b95d`): APROBABLE CON CORRECCIONES MENORES.** E2-02 a E2-05 resueltos y E2-01 resuelto en su alcance. Dos hallazgos MEDIOS, corregidos en la misma rama: (1) una vela REST que agotaba sus reintentos quedaba fuera de toda recuperación en cuanto se cerraba el día; ahora una petición fallida no gasta intentos y se repite con espera creciente durante 48 h, y el día de cada instrumento no se cierra mientras alguno de sus minutos pueda recuperar su vela, porque lo cerrado nunca cambia. Además, los errores de red de REST ya no detienen el grabador; (2) con listados incompletos, la evaluación del universo se repite a los 30 s en lugar de al día siguiente (fallo heredado) | Revisor; correcciones de Claude **revisadas el 8 nov 2026 (fila siguiente)** |
| 8 nov 2026 | **Tercera revisión de E2 (commit `7217deb`), con un revisor nuevo que ejecutó 783 pruebas, 177 de ellas con PostgreSQL 18.6: APROBABLE CON CORRECCIONES.** Confirma R-E2-01 y R-E2-02 y que no se reabre ningún hallazgo de la auditoría. Siete hallazgos MEDIOS, corregidos en la misma rama: **I-E2-01** los `CHECK` exigen con `IS NOT NULL` cada columna necesaria (3.8); **I-E2-02** un trade recibido sin vela de kline cuenta como actividad (3.8); **I-E2-03, I-E2-04 e I-E2-05** nada se cierra sin una prueba de vida posterior a su plazo, y la suspensión no reconecta una conexión caída (3.8); **I-E2-06** el arranque cierra los días que quedaron abiertos (3.8); **I-E2-07** una hora de publicación que retrocede invalida el libro (3.3) | Revisor; correcciones de Claude **revisadas el 8 nov 2026 (fila siguiente)** |
| 8 nov 2026 | **Cuarta revisión de E2 (commit `8cc9179`), del mismo revisor, con 804 pruebas recogidas y PostgreSQL 18.6: APROBABLE CON CORRECCIONES.** I-E2-01, I-E2-02, I-E2-03, I-E2-05, I-E2-06 e I-E2-07 resueltos; ninguna corrección anterior reabierta. Dos hallazgos MEDIOS, corregidos en la misma rama: **R4-E2-01** (nuevo) el cierre del día podía rellenar con S3 un minuto que el núcleo aún no había cerrado por esperar su prueba de vida, y su escritura posterior fallaba; ahora el cierre espera a que el núcleo cierre todos los minutos del día del instrumento, también los de uno retirado (3.8); **I-E2-04** (parcial) la prueba de vida no demuestra que haya llegado todo; lo que llega tarde y contradice un resultado escrito (libros tardíos y operaciones vistas tarde) se guarda y rebaja lo que ese resultado cuenta, sin cambiar sus filas (3.3, 3.5, 3.6 y 3.8, tablas `depth_late_books` y `trading_seen_late`). Además, cada trade cuenta en el minuto de su hora de ejecución (3.8) | Revisor; correcciones de Claude **revisadas el 9 nov 2026 (fila siguiente)** |
| 9 nov 2026 | **Quinta revisión de E2 (commit `75141b4`), del mismo revisor, con 829 pruebas recogidas y PostgreSQL 18.6: APROBABLE CON CORRECCIONES.** R4-E2-01 resuelto; I-E2-04 resuelto en la profundidad; el recuento de trades por `M`, correcto. Dos hallazgos MEDIOS en la evidencia tardía de los minutos, corregidos en la misma rama: **R5-E2-01** los minutos vigilados solo estaban en memoria y un reinicio los perdía; ahora el grabador entrega a la base cada trade y cada vela del WebSocket de un minuto cerrado, y la base guarda los que cambian el minuto juzgándolo con lo que tiene, así que el resultado es el mismo con un reinicio que sin él; **R5-E2-02** una vela del WebSocket de volumen 0 impedía que un trade tardío contradijera una vela REST de volumen 0. Nueva tabla `late_ws_candles`: la vela del WebSocket de un minuto es la última por `P`, a tiempo o tardía. Los minutos ya decididos cuentan como el peor resultado entre lo escrito y decidirlos de nuevo con todo lo guardado (3.5 y 3.8) | Revisor; correcciones de Claude **revisadas el 9 nov 2026 (fila siguiente)** |
| 9 nov 2026 | **Sexta revisión de E2 (commit `970dec0`), del mismo revisor, con PostgreSQL 18: APROBABLE CON CORRECCIONES.** R5-E2-01 y R5-E2-02 resueltos en sus casos; juzgar la evidencia tardía desde la base, correcto. Tres hallazgos MEDIOS y uno BAJO, corregidos en la misma rama: **R6-E2-01** una vela que llegaba mientras se esperaba la respuesta REST se usaba para decidir antes de guardarse; ahora la evidencia tardía solo se usa cuando la base la tiene; **R6-E2-02** descartar una vela reenviada igual perdía su `P`; ahora su hora se guarda en la nueva `late_ws_repeats` y una vela anterior a ella nunca pasa por la última; **R6-E2-03** una vela posterior de volumen 0 hacía olvidar que se habían visto operaciones; ahora no se olvida (3.5 y 3.8); **R6-E2-04** (BAJO) las pruebas del grabador con PostgreSQL compartían días al azar y podían verse entre sí; ahora cada ejecución tiene un día propio | Revisor; correcciones de Claude **revisadas el 9 nov 2026 (fila siguiente)** |
| 9 nov 2026 | **Séptima revisión de E2 (commit `c71f498`), del mismo revisor: APROBABLE CON CORRECCIONES.** R6-E2-01, R6-E2-02 y R6-E2-04 resueltos, sin otras reaperturas ni defectos nuevos. **R6-E2-03** parcial (MEDIO): haber visto operaciones solo se conservaba si sobrevivía la vela que lo mostraba; se perdía si una vela posterior de volumen 0 la sustituía antes del cierre, o si llegaba siendo más antigua que la última, antes o después del cierre. Corregido en la misma rama: ese hecho va aparte de cuál es la última vela. A tiempo, la columna nueva `traded` de `ws_candles`; tarde, una vela anterior con el primer volumen del minuto se guarda en `late_ws_candles` sin pasar nunca por la última (3.5 y 3.8) | Revisor; corrección de Claude **revisada el 9 nov 2026 (fila siguiente)** |
| 9 nov 2026 | **Octava revisión de E2 (commit `3e82f11`), con un revisor que no pudo ejecutar las pruebas (Python 3.13 y sin las dependencias): R6-E2-03 RESUELTO**, en las tres secuencias. No encuentra reaperturas. Un caso límite nuevo, **N-E2-01** (MEDIO, sujeto a que KuCoin pueda producirlo): una vela tardía distinta, con volumen y con la misma `P` que la última, se guardaba y la elección de la última vela por `P` quedaba sin determinar. Corregido en la misma rama: una vela con la misma `P` que la máxima vista no es más reciente; si solo muestra operaciones, se guarda con `latest = false` y la última vela se elige solo entre las guardadas como última, cuya `P` crece siempre. `latest` forma parte de la clave de `late_ws_candles`, porque esa vela puede tener la misma `P` que una vela tardía guardada como última (3.5 y 3.8) | Revisor; corrección de Claude **revisada el 9 nov 2026 (fila siguiente)** |
| 9 nov 2026 | **Novena revisión de E2 (commit `5d4e792`), del mismo revisor, que tampoco pudo ejecutar la batería completa: N-E2-01 RESUELTO**, también en el tercer empate que Claude encontró al preparar la entrega: con una vela tardía guardada como última, resuelto con `latest` en la clave de `late_ws_candles`. R6-E2-03 sigue resuelto. Ninguna reapertura ni defecto nuevo demostrado, y no hace falta otra restricción de unicidad. El revisor aprueba técnicamente la corrección y no pide otra ronda. Su única reserva para integrar la rama en `main` es confirmar la CI completa de ese commit, que no pudo consultar: ejecución 42592485155, en verde con 884 pruebas y gitleaks sin hallazgos | Revisor; **aprobada por el propietario e integrada en `main` el 9 nov 2026** (pull request #4, `3e4142b`) |
| 9 nov 2026 | **Cabecera al día:** estado de implementación y de validación de la Fase 1 y de las etapas E1 y E2 de la Fase 2 | Claude |
| 9 nov 2026 | **E3: histórico público de KuCoin medido** (3.7). El índice de Classic solo cubre unos 100 días (B3). Las velas de un par deslistado solo se conservan unas semanas tras su retirada (B13). Detalle y herramienta en `docs/kucoin/historico.md` y `tools/kucoin_history.py` | Claude (alcance aprobado de la F2); **pendiente de revisión** |
| 9 nov 2026 | **Propuesta de E3, almacenamiento histórico** (3.9): particiones por día UTC en doce tablas de `market`, archivo en Parquet con SHA-256 y manifiesto, borrado a los 90 días, formato Parquet exacto, ampliación de la lista blanca y medición con datos reales. **Dos decisiones del propietario:** quién crea y borra las particiones (recomendación: dos funciones acotadas y un rol `kbot_archive`, con dos excepciones de §9) y cómo se cambia el esquema (recomendación: corregir la `0002`, que ninguna base ha aplicado) | Claude (alcance aprobado de la F2); **revisada el 9 nov 2026 (fila siguiente)** |
| 9 nov 2026 | **Revisión del diseño de E3, en dos rondas** (la primera, con el modelo anterior del revisor; la segunda, con el auditor actual): **APROBABLE CON CORRECCIONES DE DISEÑO**. Hallazgos:<br>· **E3-D01 (ALTO):** archivar no es tener una copia recuperable, así que no se excluye ni se retira nada sin un Parquet verificado fuera del servidor.<br>· **E3-D02:** estados persistentes, una instantánea coherente, recuperación idempotente y una huella canónica.<br>· **E3-D03:** pruebas reales de las claves foráneas entre días, y nunca `CASCADE`.<br>· **E3-D04:** no modificar la `0002`.<br>Condiciones de la segunda ronda:<br>· la evidencia tardía puede cambiar un día archivado, y por eso se sella el día;<br>· retirar el día D mejoraba los estados de D+1, y por eso la evidencia ya no cruza de día;<br>· generaciones completas con referencias fijas;<br>· bloquear las doce tablas antes de comprobar que están vacías, y alinear los minutos a UTC;<br>· preparar el rango de recuperación y fijar UTC.<br>**Decisiones del propietario:** funciones `SECURITY DEFINER` acotadas con el rol `kbot_archive` (opción A) y una migración nueva, la `0003` (opción B). §3.9 reescrito con todo ello. Se añade una tercera función, la de sellar, dentro de la opción A | Revisor y propietario; **pendiente de implementar y de que el propietario confirme la función de sellar** |
