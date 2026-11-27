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
| Backtests | En el PC del propietario. **Los de la Fase 3, en Windows nativo** con el mismo `uv` de siempre, sin WSL2; **WSL2 vuelve cuando un backtest necesite PostgreSQL** (F4 o F5) [D18, aprobada el 6 de noviembre de 2026 con WSL2 y enmendada por el propietario el 17 de noviembre de 2026, P-F3-1]. El resto del trabajo del propietario es en Windows |

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
  - **pong:** KuCoin da los 3 s solo como ejemplo («such as 3 second») [H]. Con 3 s, en el servidor, la conexión de Spot se cortó 30 veces en sus primeras 7 horas y 59 en las 10,7 siguientes (17 y 18 nov 2026), 40 de ellas entre los segundos 20 y 40 de un minuto; la de Futures, 4 y 1 veces. Desde la corrección del grabador (18 nov 2026), **el cliente espera el pong 10 s**, y nunca menos que el `pingTimeout` del welcome si KuCoin lo envía [R, provisional]. Cada pong de más de 1 s queda en el diario con el tiempo hasta que KuCoin lo envió (su `ts`), el tiempo desde entonces y los mensajes recibidos antes que él, y un corte por falta de pong dice cuántos mensajes llegaron desde el ping. **Medido en el servidor** el 18 nov, en los 21 minutos tras instalar la corrección: ningún corte, frente a 2 en los 20 minutos anteriores; 12 pongs de Spot tardaron de 1,1 a 6,4 s (4 habrían sido cortes con 3 s) y ninguno de Futures. Observado: el pong se lee siempre unos 126 ms después de su `ts`, el resto de la espera queda entre el envío del ping y ese `ts`, y entre el ping y el pong llegan de 190 a 1.429 mensajes [H]. Se deduce, sin demostrarlo, que el retraso se produce antes de ese `ts`, en el camino de ida o en KuCoin: comparar relojes distintos no permite separarlo (RFC 7679 §3.7.1), aunque una vuelta tan estable indica que el pong no espera al leerse [I]. La espera de 10 s se sigue midiendo con más días [P];
  - **welcome:** KuCoin anunció el 25 ago 2026 que dirá `"data": "welcome"` en lugar de `"message": "welcome"` y traerá `pingTimeout`, y el 27 ago aplazó el cambio sin fecha nueva [H]. El cliente acepta las dos formas; cualquier otra corta la conexión;
  - **reconexión:** cualquier fallo de la conexión, también un rechazo del handshake (HTTP 400 el 17 nov 2026), es una desconexión que el cliente registra y de la que se recupera solo. Tras una sesión conectada o un primer intento fallido espera 5 s; cada intento fallido seguido duplica la espera máxima, hasta 60 s, y la elige al azar en su mitad superior; las duplicaciones dejan de contar en 30, así que una caída larga nunca desborda el cálculo (revisión GR-02). Nunca hay reintentos rápidos. Cada desconexión y cada reconexión quedan en el diario;
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

**Huecos:** una desconexión abre un hueco en memoria y se inserta en `data_gaps` al cerrarse. **Al pararse, el grabador escribe los huecos que conoce y no ha escrito** [corrección del 18 nov 2026]: los de los ciclos cuya escritura no consta, que si no se perderían con un ciclo que no se pudo escribir (revisión GR-01); los cerrados desde el último ciclo; cada hueco abierto, cerrado en ese instante y con su causa; y, para un instrumento que salió del universo sin cerrar todo lo anterior a su salida, o cuyo último cierre no consta escrito, ese tiempo como `proceso_detenido` desde donde había cerrado en lo escrito (residual de GR-01), porque nadie lo grabará después. Solo escribe huecos: no cierra intervalos ni minutos, porque tras un ciclo que no se pudo escribir eso dejaría tiempo sin filas y sin hueco. Al arrancar, el hueco de la parada (`proceso_detenido`) empieza en el punto de reanudación de cada canal (el fin del último intervalo o minuto escrito), adelantado sobre los huecos ya escritos que lo cubren uno tras otro, como el que escribió la parada: **nunca pasa por un instante que ningún hueco explique**, y si un hueco escrito empieza después del punto de reanudación, el de la parada empieza en el punto y se solapa con él. Todos los intervalos de profundidad afectados quedan `desconectado` (F2-A5). **Un hueco cuyo inicio ya ocupa otro del mismo canal no se pierde** (la clave primaria): se escribe lo que ese otro no cubre, desde donde acaba, y otra vez si ese momento también está ocupado; nada escrito cambia y escribir dos veces el mismo hueco no añade nada (revisión GR-04); `--sink rows` escribe las mismas colas (GR-06). Una parada que no llega a escribir lo abierto (un proceso matado, un corte de luz) lo pierde: los minutos escritos con la conexión caída conservan sus filas S3, y los que no tienen vela quedan `sin_explicar`.

**Universo provisional** (D12 sin aprobar): pares en USDT que cotizan, **sin monedas estables como moneda base** (USDC, USDE, FDUSD, DAI, TUSD, PYUSD) [decisión del propietario, 7 nov 2026], ordenados por volumen de 24 h en la moneda de cotización, los 20 primeros de Spot y de Futures; los 10 primeros de cada mercado son candidatos de nivel 50 (P3). Se reevalúa una vez al día y cada cambio queda en `universe_snapshots`.

**El grabador (`svc-ingest`, etapa E2)** [auditado el 8 nov 2026; segunda revisión: APROBABLE CON CORRECCIONES MENORES; tercera revisión, con un revisor nuevo que ejecutó las pruebas con PostgreSQL: APROBABLE CON CORRECCIONES, siete hallazgos MEDIOS; cuarta revisión: APROBABLE CON CORRECCIONES, seis de ellos resueltos, I-E2-04 parcial y un hallazgo nuevo, R4-E2-01; quinta revisión: APROBABLE CON CORRECCIONES, R4-E2-01 e I-E2-04 resueltos en profundidad y dos hallazgos MEDIOS en la evidencia tardía de los minutos; sexta revisión: APROBABLE CON CORRECCIONES, esos dos resueltos y tres hallazgos MEDIOS y uno BAJO más; séptima revisión: APROBABLE CON CORRECCIONES, tres de ellos resueltos y R6-E2-03 parcial; octava revisión: R6-E2-03 resuelto y un caso límite nuevo (N-E2-01, velas con la misma `P`), corregido; novena revisión: N-E2-01 resuelto, sin reaperturas ni defectos nuevos, y el revisor aprueba la corrección sin pedir otra ronda; corrección tras la primera noche en el servidor, 18 nov 2026: **APROBADA** en su quinta revisión (`2ffd88b`), con GR-01 a GR-08 cerrados]:
- **Un núcleo sin reloj** (`kbot.ingest.core`) recibe los mensajes, los eventos de conexión y la hora, y devuelve lo que hay que guardar; una capa asíncrona (`kbot.ingest.service`) abre las dos conexiones públicas, llama al núcleo una vez por segundo y escribe en un hilo aparte, para que una escritura lenta nunca retrase los pings.
- **El universo cambia a medianoche UTC:** el de mañana se evalúa a las 23:59 con los datos del día y rige desde las 00:00 (`evaluated_at` es el momento desde el que rige). Un instrumento que sale deja de grabarse a las 00:00 y sus velas REST pendientes se deciden igual; uno que entra empieza a las 00:00; si cambia el conjunto, se renuevan las suscripciones (hueco `rotacion_planificada` de unos segundos). Tras un reinicio dentro del día se reutiliza el universo en vigor, y entre las 23:59 y la medianoche, la evaluación ya guardada para esa medianoche; no se evalúa dos veces. **Si el grabador estuvo parado o suspendido a medianoche y no evaluó a las 23:59**, al volver evalúa el universo del día ya empezado, lo guarda con la hora real de la evaluación y lo aplica en ese momento, renovando las suscripciones; nunca aplica a un día un universo evaluado para otro ni finge que evaluó a las 23:59 (auditoría E2-04). Si los listados de KuCoin llegan incompletos, no se guarda nada, sigue en vigor el universo actual y la evaluación se repite a los 30 s; pasada la medianoche, la repetición es una evaluación tardía como la anterior [segunda revisión de E2]. El cierre del día cubre todos los instrumentos que estuvieron en el universo en algún momento del día.
- **Velas REST:** cada minuto se pide un rango por instrumento. **Una petición cubre como mucho 100 minutos objetivo, del primero al último**, aunque entre ellos esperen pocos; la ventana pedida tiene un minuto más por cada lado, así que nunca pasa de 102 velas, por debajo de las 200 de Futures (3.2). Una respuesta sin la vela de un minuto `pendiente_estabilizacion` o `ausencia_no_verificable` cuenta como intento: el minuto se vuelve a pedir una vez por minuto, como mucho 5 veces. **Una petición fallida no es una respuesta y no gasta intentos** (error de red, error de KuCoin o una respuesta con alguna fila ilegible, que se descarta entera): se repite tras 1, 2, 4 y 8 minutos y después cada 15, durante 48 h desde el minuto; pasado ese plazo, el minuto se cierra tal como esté. Un error de red nunca detiene el grabador [segunda revisión de E2]. **Una respuesta con dos velas distintas para un minuto tampoco es una respuesta para ese minuto:** no se elige ninguna, se anota (`rest_candle_conflict`) y el minuto se vuelve a pedir como tras un fallo; el resto de la respuesta vale y dos velas idénticas son una. KuCoin lo hizo con DOT-USDT a las 09:03 UTC del 18 nov 2026 y volvió a dar una sola vela a las 22:10 del día siguiente (§12.1, CD-19 y CD-20) [ronda del grabador del 20 nov 2026, **aprobada** en `687cd43`].
- **Cierre del día, por instrumento:** desde las 00:00:15 UTC, en cuanto el núcleo ha cerrado todos sus minutos de ese día y ninguno espera su vela REST. Lo que escribe el cierre es terminal y nunca cambia. Por eso espera a que el núcleo cierre cada minuto que graba, también los de un instrumento retirado y uno que aún espera su prueba de vida: el relleno S3 es solo para los minutos que nadie grababa, y antes podía adelantarse a un minuto aún abierto, que luego chocaba con él al escribirse [cuarta revisión de E2, R4-E2-01]. Y un minuto cuya vela aún se puede recuperar mantiene abierto el día de su instrumento: las respuestas sin vela terminan en unos minutos, y un minuto sin respuesta espera como mucho 48 h. Los demás instrumentos cierran a su hora [segunda revisión de E2].
- **Tras una parada** el grabador continúa donde dice la base de datos, como mucho un día atrás (los minutos más antiguos reciben su fila S3 al cerrar su día; el hueco registra toda la parada). Las velas REST de la parada se piden también (histórico recuperable, 3.7). **Al arrancar reanuda, solo desde la base, el trabajo pendiente de las últimas 48 h:** los minutos con flujo `trade` sin vela de last ni estado terminal, con su última vela del WebSocket (a tiempo o tardía), el tick en vigor al final del minuto y el trade tardío guardado, si lo hay, y los minutos de index que ni se rehicieron ni se dieron por perdidos (auditoría E2-01 y E2-02). Un minuto con su vela no se vuelve a pedir, y repetir el arranque no duplica nada. Lo que no está en la base no es un resultado: un minuto que se quedó sin vela tras 5 respuestas, si el día de su instrumento aún no se ha cerrado, se vuelve a pedir tras un arranque; una vez cerrado el día, nada cambia. **El arranque busca también en la base los días de los últimos 30 con minutos grabados sin estado terminal** y los cierra, con la vela que tengan si ya pasaron las 48 h; así ningún día queda abierto aunque la parada dure más de dos días [tercera revisión de E2].
- **Índice desde Classic:** una petición trae hasta 400 valores por segundo [H, 7 nov 2026], así que se rehacen hasta 6 minutos por petición; se piden 2 minutos después del minuto, como mucho 30 peticiones por minuto y 3 intentos. Si Classic respondió sin el minuto completo, el minuto se da por perdido y queda en `exhausted_index_rebuilds`, así que ningún reinicio lo vuelve a pedir; si Classic nunca respondió, sigue pendiente para el siguiente arranque.
- **Funding:** el previsto cada 10 minutos y el liquidado cada hora (últimas 24 h, sin duplicados). Instrumentos y tramos de riesgo, a diario y solo si cambian.
- **La salud de la conexión hay que demostrarla, no suponerla:** una desconexión se registra desde el último mensaje recibido (la última prueba de vida), no desde que se detecta; y un vigilante comprueba cada segundo que el proceso avanza: si pasa más de 5 s sin ejecutarse (máquina suspendida, CPU agotada, una llamada bloqueada), ese tiempo es un hueco `proceso_detenido` y sus intervalos `desconectado`. Lo destapó la primera prueba en vivo (8 nov 2026): el PC se suspendió 17 minutos y, sin el vigilante, ese tiempo habría pasado por conexión sana. La suspensión se registra desde la última prueba de vida de cada conexión que estaba sana; **una conexión que ya estaba caída sigue caída** hasta que su propio stream confirme la reconexión [tercera revisión de E2].
- **Nada se cierra sin prueba de vida posterior** [tercera revisión de E2]: con la conexión sana, un intervalo de profundidad o un minuto solo se cierra cuando llegó algún mensaje o un pong al menos el margen L después de su fin. Así una desconexión, fechada en la última prueba de vida, nunca cae sobre un resultado ya escrito como sano. Con la conexión caída se cierra hasta el momento actual. En una conexión tranquila la espera es como mucho de un ping (unos 18 s).
- **La prueba de vida no demuestra que haya llegado todo** [cuarta revisión de E2]: KuCoin no documenta que envíe en orden de publicación ni que un pong salga después de todo lo publicado antes. La completitud por hora de publicación es, por tanto, provisional: lo que llega con su intervalo ya cerrado y contradice un resultado escrito se guarda como evidencia tardía y rebaja lo que ese resultado cuenta, sin cambiar sus filas (3.5). Nunca se descarta en silencio.
- **Si KuCoin no da los listados al arrancar, el servicio falla** y systemd lo vuelve a arrancar más tarde (§0, regla 1). Un instrumento cuyos datos no cumplen su contrato no se graba y queda en el registro.
- **El grabador vigila sus streams** [corrección del 18 nov 2026, tras su primera noche en el servidor]: cada segundo, también mientras un ciclo espera a REST o a la base de datos (revisión GR-03), comprueba que los dos streams y el vigilante siguen en marcha. Un stream se recupera solo de cualquier fallo de la conexión (3.1), así que si termina es por otra cosa, como un error del propio código: el grabador escribe lo que deja abierto, se detiene con error y systemd lo vuelve a arrancar (`Restart=on-failure`). **Nunca sigue grabando sin uno de sus mercados.** Si uno terminó, o si se pide la parada, el ciclo en curso se cancela en su espera, como si el proceso se parase ahí, algo que todo ciclo ya soporta; una llamada a la base de datos ya empezada no se abandona, y la parada la espera antes de escribir, así que la conexión nunca atiende dos llamadas a la vez. La escritura de un ciclo confirma ese ciclo al núcleo en cuanto termina bien, aunque el ciclo ya se haya cancelado, y lo hace dentro de su propia tarea, antes de darse por terminada: quien la ve terminada sabe que ya confirmó, así que lo que escribió nunca se cubre además con un hueco (tercera y cuarta revisión, residual de GR-01). Una parada pedida no se toma por un fallo del vigilante, que termina con ella (revisión GR-05). Al cerrar los streams (cambio de universo o parada), uno que ya había fallado queda en el diario y no impide cerrar los demás.
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
- **Comprobación por catálogo:** cada partición se comprueba en el catálogo (padre, límites, propietario `kbot_owner` y ningún permiso para otro rol, `PUBLIC` incluido, ni en la tabla ni en sus columnas; desde la revisión de E3a, E3a-04).
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
  - Cuando han pasado al menos 72 h desde el final del día, la función de sellar añade a cada una de sus doce particiones `CHECK (false) NOT VALID`. Las filas existentes se quedan y no puede entrar ninguna nueva [H, CI].
  - **Primero las barreras y después la comprobación** [revisión de E3a, E3a-03]. Cada `ALTER` espera a los escritores de su partición, y solo con las doce barreras puestas se comprueba que el día está cerrado, en `READ COMMITTED`, para ver toda escritura confirmada antes. Si no lo está, se deshace todo, barreras incluidas. Fuera de `READ COMMITTED` la función se niega a ejecutarse.
  - **Día cerrado** [E3a-02]: cada instrumento del día tiene sus 1.440 estados terminales. Los instrumentos del día son los de `store.universe_members` (la evaluación en vigor al empezar el día y las hechas durante él) y cualquiera con algún minuto de trade grabado en él. Un día sin ninguno está vacío, y cerrado.
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
- **Desde E4 (§13.1, punto 4):** además de la generación `verificado_fuera`, cuenta como alternativa externa verificada la **copia SQL del día sellado** releída del cubo, que el manifiesto también fija; una copia caliente omite un día sellado solo mientras una de las dos exista, y la retirada sigue exigiendo `elegible_para_exclusion_backup` y la restauración integral.

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
  - `numeric` como `decimal128(38, 24)` (formato 2, 18 nov 2026): hasta 24 decimales y 14 dígitos enteros. Si un valor no cabe sin perder dígitos, no se escribe y se avisa; nunca se redondea. El día no se publica, nada se borra de la base y cada ejecución vuelve a fallar igual hasta que cambie el formato. Pruebas: negativos, ceros, los extremos de 24 decimales y 14 dígitos enteros, el rechazo de 25 decimales o 15 dígitos enteros y los valores reales de 20 y 21 decimales; y, contra PostgreSQL, un día con valores reales que DuckDB lee exactos y otro con 25 decimales que no se publica. **Por qué cambió:** KuCoin envía volúmenes y turnover de Spot con hasta 21 decimales y 11 dígitos enteros (medido en los datos del grabador), que el `decimal128(38, 18)` del formato 1 rechazaba: el archivo del 17 nov, el primer día con datos, se habría detenido el 21 nov. **Por qué no es más ancho:** DuckDB 2.0 lee un decimal de más de 38 dígitos como `DOUBLE`, sin error, y pierde dígitos: `7685.59829732743707761776` volvía como `7685.598297327437` (revisión DC-01). Los 14 dígitos enteros dejan unas mil veces de margen sobre el máximo visto. Con zstd 3 ocupa un 14 % más que `decimal128(38, 18)` en las métricas del libro; en texto ocuparía un 35 % menos, pero dejaría de ser un número tipado. Los 27 días vacíos que se archivaron con el formato 1 siguen siendo válidos: los dos formatos se leen como los mismos decimales exactos, también con DuckDB, aunque nunca en la misma lectura (DC-02).
  - Las marcas de tiempo, en UTC con microsegundos.
  - Los niveles de los snapshots, como listas tipadas. La medición los compara con guardar el JSON tal cual.
- **Compresión:** zstd, con el nivel que dé la medición.
- **Bibliotecas:** `pyarrow` para escribir y verificar, y DuckDB para las consultas de la medición. **Antes de calcular con DuckDB** se comprueban los archivos que cada consulta lee juntos, leídos como ella los lee (`read_parquet` con la misma lista, en su orden y sin opciones): cada decimal de cada archivo, también dentro de listas y estructuras, debe llegarle con su mismo tipo, y nada puede convertirse en decimal (`parquet.check_duckdb`); si no, falla sin calcular. DuckDB lee varios archivos con los tipos del primero, así que una consulta nunca junta archivos de los formatos 1 y 2: los 27 días del formato 1 están vacíos, y quien lea esos días los deja fuera o los lee aparte (DC-02). Un `CAST` de texto a decimal en DuckDB redondea en silencio, así que la medición solo convierte el texto de un libro si es un decimal que cabe en el formato, y con cualquier otro falla (DC-01).

**7. Funciones y roles** (decisión 1, con las condiciones de las dos revisiones)
- **Tres funciones `SECURITY DEFINER` propiedad de `kbot_owner`:** `market.prepare_partitions()`, `market.seal_day(día)` y `market.retire_day(día)`.

  La de sellar se añade a las dos de la propuesta, dentro de la opción A; el propietario la confirmó el 10 nov 2026. Las excepciones a §9 (propietario `kbot_owner` y SQL dinámico acotado) valen **solo para estas tres**.
- **Cada función:**
  - trabaja sobre una lista fija de las doce tablas, escrita dentro de la función;
  - recibe un argumento `date` validado (sellar y retirar) o ninguno (preparar, que siempre trabaja sobre su rango), sin nombres que aporte quien llama;
  - fija `SET search_path = market, pg_temp`, `SET TimeZone = 'UTC'` y `SET lock_timeout = '5s'`, y escribe todos los objetos con su esquema;
  - solo ejecuta `format()` con `%I` y `%L` y una de las cinco plantillas aprobadas: crear una partición, sellarla, contar sus filas, separarla y borrarla. Nunca `CASCADE`;
  - toma un bloqueo consultivo (`pg_advisory_xact_lock`) contra dos ejecuciones simultáneas y usa `lock_timeout`;
  - se crea con su `REVOKE ALL ... FROM PUBLIC` en la misma transacción [H].
- **`EXECUTE`:** `kbot_archive`, solo en preparar y sellar. Retirar no lo tiene nadie en E3.
- **`kbot_archive`:** LOGIN, conexión `peer` desde su propio usuario del sistema y sin pertenecer a ningún rol. Tiene `SELECT` solo en las doce tablas particionadas y en las cinco del archivo, no en las cinco pequeñas. Tiene `INSERT` solo en las generaciones, sus archivos y sus estados.
- **`sql_guard`** admite, en las migraciones:
  - `PARTITION BY RANGE` de una columna `timestamptz` en esas doce tablas, y particiones con límites constantes;
  - `CREATE FUNCTION` solo `SECURITY DEFINER` en PL/pgSQL, con esas tres opciones `SET`, su `REVOKE` obligatorio y `GRANT EXECUTE` solo según la matriz;
  - dentro de esas funciones, `EXECUTE` solo con las cinco plantillas, y como sentencias fijas solo lecturas e inserciones en sus propias tablas (sellos y retiradas);
  - `LOCK`, `SET LOCAL lock_timeout`, `CHECK (false)` y `DROP TABLE` sin `CASCADE`, solo en la `0003` y solo sobre las doce tablas;
  - en un `CHECK`, solo `date_trunc` y `timezone` con argumentos constantes.

  **Validación cerrada** [revisión de E3a, E3a-01]. Una función `SECURITY DEFINER` solo se admite si su cuerpo coincide byte a byte (SHA-256) con el aprobado en `sql/security_definer_catalog.json`, el mismo archivo que la CI compara con el catálogo real. Un cuerpo nuevo es un cambio revisado de ese archivo. Además, como defensa adicional, `sql_guard`:
  - rechaza los `WITH` que modifican datos;
  - solo admite `PERFORM` para el bloqueo consultivo;
  - no cuenta como revocación un `REVOKE GRANT OPTION FOR`, que deja `EXECUTE`.

  El revisor demostró que, sin esta validación, el análisis por clases aceptaba un `DELETE` dentro de un `WITH` y una plantilla aprobada aplicada a otra tabla.

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

**Implementado en E3a** (10 nov 2026; **APROBADO** por el revisor el mismo día, tras tres revisiones: E3a-01 a E3a-04 cerrados):
- **La migración `0003`**, las cinco tablas del archivo (sellos, generaciones, archivos, estados de la generación y retiradas), las tres funciones y el rol `kbot_archive`.
- **La ampliación de `sql_guard`.** Analiza el cuerpo PL/pgSQL de las funciones con `pglast`.
- **La huella de las funciones** en `sql/security_definer_catalog.json`.
- **Las pruebas** de las migraciones congeladas, de la lista blanca y de la matriz, y en PostgreSQL 18: particiones, funciones, sello, retirada y la `0003` sobre una base nueva con datos y con contención.

Lo aprendido al probarlo:
- **PostgreSQL impide separar una partición a la que todavía apunta otra de otro día** [H, CI]: retirar el día D+1 antes que D falla sin cambiar nada, y en orden cronológico se retiran los dos.
- **Una partición referenciada no se puede borrar sin separarla antes** (`DependentObjectsStillExist`) [H, CI]. Por eso retirar separa primero y borra después.
- **Una consulta sobre una tabla particionada bloquea cada partición que no puede descartar**, con sus índices. Con unas 3.000 particiones de prueba, la CI agotó la tabla de bloqueos («out of shared memory») [H, CI]. Por eso:
  - las pruebas crean y borran sus propias particiones;
  - la CI usa `max_locks_per_transaction = 256` [R: el mismo valor en el servidor, E4; con unas 1.250 particiones activas se mide en E5].

**Falta, para E3b y E3c:**
- que la evidencia no cruce de día (`depth_late_books` por día);
- que el grabador descarte la evidencia de un día sellado;
- el archivador (generación, huella canónica, verificación y publicación);
- `--sink rows` y su equivalencia con `DatabaseSink`;
- la medición del Parquet.

**Implementado en E3b** (10 nov 2026; **APROBADO** por el revisor el mismo día, tras tres revisiones: E3b-01 a E3b-03 cerrados):
- **La evidencia no cruza de día** [H, CI]. `depth_late_books` tiene `first_slot_start`, que es su clave de partición:
  - cada fila nombra intervalos de un solo día UTC, con claves foráneas a `first_slot_start` y `last_slot_start` y un `CHECK` del mismo día;
  - `slot_start` guarda el intervalo de su `P`;
  - `write_late_books` parte por días el libro que cruza la medianoche, y `slot_states` lee el rango de cada fila.

  Retirar el día D ya no cambia lo que cuentan los intervalos de D+1.
- **El grabador no escribe evidencia de un día sellado:** `write_late_minutes` y `write_late_books` la descartan con un aviso en el registro, sin fallar. Por eso `kbot_ingest` puede leer `archive_seals`. Además, cada inserción de evidencia tardía va en su propio *savepoint*. Si el día se sella entre la comprobación y la inserción, solo esa fila se deshace y se descarta, porque viola el `CHECK` `*_sellada`; el resto del tick se guarda, y cualquier otro error se propaga (revisión de E3b, E3b-01).
- **El archivador** (`kbot.archive`, que se ejecuta como `kbot_archive`) tiene cuatro pasos:
  - `prepare` llama a `market.prepare_partitions()`;
  - `seal` sella cada día de [hoy − 30, hoy − 3] cerrado y no sellado;
  - `archive` da a cada día sellado una generación:
    - sus doce archivos salen de una sola instantánea `REPEATABLE READ`;
    - se escriben en un directorio temporal, se sincronizan con el disco, se releen y se comparan con sus filas, por número y por huella;
    - se sincroniza el directorio que los contiene; se renombra a su generación, y se sincronizan el día, `market` y la raíz. Cualquier error de E/S, también de un directorio, detiene la publicación (E3b-02). La única excepción es Windows, que no puede abrir un directorio para sincronizarlo y solo se usa en las pruebas;
    - **la raíz del archivo debe existir de antemano.** La crea de forma durable la instalación del servidor (E4), nunca el archivador. Si no existe, se niega antes de escribir nada. Dentro de ella, `market` y el día se crean nivel a nivel, así que cada entrada nueva queda en un directorio que se sincroniza (segunda revisión de E3b);
    - solo entonces se publica, en una transacción;
    - lo que deja un intento que no llegó a publicarse se borra en la siguiente ejecución;
    - un bloqueo consultivo de sesión impide que trabajen dos archivadores a la vez;
  - `verify` es la comprobación diaria: cada día archivado que sigue en la base conserva el sello en sus doce particiones y la huella de su última generación.

  La comprobación diaria detecta y avisa; no rehace generaciones por su cuenta, porque con el sello solo un administrador puede cambiar un día sellado [R].
- **Formato en E3b:**
  - `numeric` como `decimal128(38, 24)` (formato 2; el 1 usaba `decimal128(38, 18)`), exacto o rechazado, nunca redondeado, y leído por DuckDB como el mismo decimal, también cuando una consulta lee varios archivos juntos (`check_duckdb`);
  - marcas de tiempo UTC en microsegundos;
  - `jsonb` como su texto JCS (la medición de E3c lo compara con las listas tipadas). Se lee como texto, así que el `null` de JSON se archiva como `null` y nunca como un valor que falta (E3b-03);
  - zstd de nivel 3, provisional.

  La huella canónica está en `kbot/archive/canonical.py`. Dependencia nueva: `pyarrow` 26.

**Falta, para E3c:**
- `--sink rows` y su equivalencia con `DatabaseSink`;
- la grabación en el PC;
- la medición: tamaño por tabla y día con zstd 3 y 9, consultas con DuckDB y tiempo de planificación con unas 1.250 particiones.

**Implementado en E3c** (11 nov 2026; **APROBADO** por el revisor el mismo día, tras cuatro revisiones: E3c-01 a E3c-07 cerrados):
- **`--sink rows`** (`kbot/ingest/rows.py`). Ejecuta los mismos escritores de `store` que `DatabaseSink` sobre una conexión que no ejecuta nada, y escribe cada INSERT como una línea JSON por tabla.
  - Descarta una fila con la clave primaria de otra, como PostgreSQL: con `ON CONFLICT DO NOTHING` conserva la primera, y sin él falla (E3c-04). Para ello guarda un resumen de 16 bytes de cada clave, unos 150 MB por día.
  - Responde a las dos preguntas de `store.write_gap` con los huecos que ha escrito: si una inserción `RETURNING` se escribió, y dónde acaba el hueco que ocupa un inicio. Así un hueco cuyo inicio está ocupado conserva su cola también aquí, como en PostgreSQL (revisión GR-06, 18 nov 2026).
  - **Marcas de progreso** (E3c-01, segunda revisión). Cada archivo tiene su propio búfer, así que una grabación parada de golpe puede conservar más de un archivo que de otro. Tras cada escritura, `RowSink` vacía todos sus archivos y después añade a `_progress.jsonl` las filas que tenía cada uno. La medición solo usa las filas de la última marca que alcanzan todos los archivos. Las marcas cuentan desde el principio de los archivos de su ejecución, así que la carpeta de una grabación debe ser nueva o estar vacía. Si no lo está, `RowSink` y la orden `--sink rows` la rechazan antes de abrir nada (E3c-07).
  - No produce lo que se decide en SQL al cerrar el día (`minute_status` y las filas S3 con que `fill_missing_flow` rellena los minutos sin flujo) ni la evidencia tardía de minutos. Son pocas filas, y una grabación sin cortes no tiene relleno.
  - **Equivalencia** [H, CI] (`tests/db/test_rows_equivalence.py`). Con los ticks del núcleo, un libro tardío repetido y una ejecución completa del grabador con sus escritores REST, los archivos y PostgreSQL tienen las mismas filas, tabla a tabla. La comparación es en la forma canónica del archivo, sobre todas las columnas salvo `recorded_at`; las columnas que ningún INSERT nombra llevan su valor por defecto. Las únicas tablas que solo tiene PostgreSQL son las excluidas a propósito.
- **Grabación** en el PC del propietario, del 10 nov 2026 a las 22:55 UTC al 11 nov a las 00:54 UTC, con el universo de referencia de §3.1 (20 + 20 pares, 10 + 10 candidatos de nivel 50). Debía durar 3 h, pero el proceso se paró al cerrarse la sesión que lo lanzó.
  - **Ventana medida:** los **117 minutos completos**, de 22:56 a 00:53 UTC (E3c-01). Un minuto es completo si tiene tantas filas de calidad como el más lleno y sus seis intervalos tantos estados como el más lleno. Los dos bordes, a medio escribir, quedan fuera, y las tablas de filas escasas nunca fijan la ventana. Además, en cada minuto de la ventana debe haber tantas métricas como intervalos válidos, porque las métricas de un intervalo se escriben justo después de su estado. Si los últimos minutos no cuadran, quedan fuera; si falla uno intermedio, la grabación se rechaza.
  - **La muestra es anterior a las marcas**, así que la herramienta solo la lee con `--unmarked`. Pasa la comprobación anterior: 41.190 intervalos válidos y 41.190 métricas, como confirmó el revisor.
  - **La extrapolación a un día y a un año es condicional:** supone que el volumen por minuto de esas dos horas se mantiene.
  - **Huecos:** 506 en la ventana (≈ 6.200 al día). Vienen de unos pocos episodios que afectan a todos los canales a la vez: conexiones sin pong en la red doméstica, una desconexión y la rotación del universo a medianoche. Cada episodio crea una fila por canal.
- **Medición** (`tools/measure_archive.py`, reproducible byte a byte con la muestra). Convierte la grabación en los archivos del archivo: por tabla y día UTC, con los tipos y el orden de `0003` y grupos de 20.000 filas. `minute_status` se estima con una fila por minuto de `trade`, y `recorded_at` se aproxima. La carpeta de salida debe ser nueva o estar vacía (E3c-05). Resultados por día, escalados de 117 a 1.440 minutos [H, PC]:

  | Tabla | Filas al día | Parquet sin comprimir | zstd 3 | zstd 9 | PostgreSQL (B/fila) |
  |---|---|---|---|---|---|
  | `depth_metrics` | 506.954 | 92,4 MB | 55,2 MB | 51,9 MB | 290 |
  | `depth_snapshots` | 86.031 | 71,9 MB | 17,3 MB | 14,8 MB | 1.019 |
  | `depth_slot_status` | 518.400 | 10,4 MB | 4,5 MB | 4,5 MB | 283 |
  | `candles` | 114.572 | 10,7 MB | 5,8 MB | 5,4 MB | 425 |
  | `ws_candles` | 57.182 | 6,5 MB | 3,5 MB | 3,2 MB | 246 |
  | `data_quality_minute` | 144.000 | 1,8 MB | 0,7 MB | 0,7 MB | 307 |
  | `minute_status` (estimada) | 57.600 | 0,7 MB | 0,3 MB | 0,3 MB | 218 |
  | **Total** | 1.484.739 | **194 MB** | **87,3 MB** | **80,8 MB** | **501 MB al día** |

  Parquet al año: 70,9 GB sin comprimir, **31,9 GB con zstd 3** y 29,5 GB con zstd 9.
- **PostgreSQL 18 en la CI** (`tests/db/test_storage_scale.py`) [H, CI]:
  - **Bytes por fila** (montón, TOAST e índices: la última columna de la tabla), con una hora de un universo sintético realista con la mezcla de referencia: dos libros de nivel 5 por cada uno de nivel 50, que la prueba exige (E3c-02). Un libro ocupa 430 B de datos en nivel 5 y 1.749 B en nivel 50.
  - **Los índices son el 42 % del total**, porque `data_quality_minute` y `depth_slot_status` tienen dos índices únicos: la clave primaria y el único con el estado, destino de las claves foráneas.
  - **WAL escrito:** entre 487 y 758 B por fila según la ejecución, porque depende de las imágenes de página completa tras cada punto de control. Son unos 0,7-1,1 GB al día.
  - **Con 1.260 particiones** (90 días calientes, hoy y los 14 preparados):
    - planificar las consultas de reanudación del grabador, que recorren las 105 particiones de su tabla, tarda 5-8 ms, o 21-31 ms en una conexión nueva;
    - una consulta de un día, podada a una partición, tarda menos de 1 ms;
    - una inserción, 0,01 ms.
  - **`prepare_partitions()`** crea 492 particiones en 1,8-2,8 s (4-6 ms cada una). Su ejecución diaria, sin nada que crear, tarda 0,03-0,04 s.
  - **Cada partición vacía** ocupa unos 25 KB: los archivos de sus índices y de su TOAST, y sus filas del catálogo. Las 1.260 suman 32 MB.
- **DuckDB** (2.0, dependencia de desarrollo; mediana de 5) [H, PC]:
  - la serie de un instrumento tarda 7,9 ms sobre lo grabado;
  - el agregado de todo el universo tarda 12 ms;
  - los «90 días» son un **ensayo sintético**: 1.108 lecturas repetidas de los mismos archivos. Procesan las filas de 90 días, pero no sus archivos ni su caché, y dan ≈ 1,0 s y ≈ 2,4 s.
- **Formato de los libros:** se mantiene el **texto JCS**. Las listas tipadas `decimal128` ocupan un 53 % más con zstd 3 (26,5 frente a 17,3 MB al día), y leer el primer nivel de todos los libros solo ahorra unos 7 ms.
- **Proyección** (§3.1 y §10.3), con la extrapolación condicional de arriba. El montón medido, 284 MB al día, coincide con la estimación de §3.1 (259 MB), pero **la estimación no contaba los índices**, que añaden 211 MB:
  - **base de datos con 90 días calientes:** 45,1 GB, más las particiones (0,03 GB) y una **reserva estimada** de WAL de 1,07 GB, son 46,2 GB. **Con el 20 % de margen de §3.1, 55,5 GB**; la estimación era de ≈ 28 GB. La reserva de WAL es `max_wal_size`, un límite blando que PostgreSQL puede superar, así que no es una cota (E3c-06);
  - **`data_gaps`:** sin particionar y sin retención decidida, así que crece cada día y no entra en los 90 días (E3c-03). Con ≈ 6.200 filas al día y la mayor medida por fila (1.019 B, una cota superior), son 2,8 GB tras el año 1 y 5,6 GB tras el año 2, con el margen;
  - **Parquet:** 31,9 GB al año con zstd 3, en el extremo alto del rango estimado (12-32 GB);
  - **volumen de datos de 110 GB (§10.3):** **el año 1 cabe (90,1 GB)**; **el año 2 no cabe (124,8 GB)**.
- **Decisión pendiente del propietario**, antes del segundo año y tras la medición definitiva de E5 (§3.1: «se reducen las frecuencias o el universo, con decisión del propietario»). Opciones, ninguna decidida:
  - **reducir los índices** (técnica): por ejemplo, que el índice único con estado sustituya a la clave primaria en las dos tablas citadas. Requiere otra migración y su revisión;
  - **zstd 9:** ahorra un 7 % del Parquet (2,4 GB al año). Se archiva una vez al día, así que el coste de CPU no importa. **Propuesta [R]: zstd 9**, pendiente de la revisión de E3c; de momento sigue el 3;
  - **una retención para `data_gaps`**, ya prevista como asunto aparte;
  - **menos días calientes** ([D17]);
  - **una retención del Parquet;**
  - **reducir el universo o las frecuencias.**

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
- SSH solo con clave e **IP restringida en la lista de seguridad de Oracle** [decisión del propietario del 17 nov 2026], **el cortafuegos que trae la imagen de Oracle** (`iptables`, cargado al arrancar por `netfilter-persistent`, con sus reglas IPv4 tal como vienen), no `ufw` [decisión del propietario del 16 nov 2026], `fail2ban` y **0 puertos de entrada** salvo SSH. **Enmienda del propietario del 26 nov 2026, provisional mientras se desarrolla:** la regla admite el bloque de su proveedor, `79.112.0.0/12`, en vez de una sola IP, porque su IP doméstica cambia cada pocos días (79.112…, 79.117… y 79.116… en nueve días). El acceso sigue siendo solo con clave, y `fail2ban` sigue activo.

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

El diseño del despliegue, las copias y su restauración en la etapa E4 está en §13.1.

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
- régimen determinista (ADX, percentil del ATR, anchura de Bollinger; §12.3.2);
- **una posición neta por instrumento**; con señales opuestas no se opera;
- las temporalidades superiores, solo cerradas;
- **cuentas sombra** para evaluar cada estrategia;
- `strategy_version` = commit + hash de los parámetros (§12.3.3);
- **ninguna estrategia se activa sin una ficha matemática aprobada** (precisión A). Las de tendencia, breakout y reversión a la media están en §12.3;
- el aprendizaje solo genera propuestas.

**Backtesting** (el motor de la F3, en §12.4):
- decisión en la vela cerrada t y ejecución en t+1, precisadas por la regla causal de §12.3.1;
- prueba que "envenena" los datos futuros;
- universo con instrumentos deslistados [P];
- 60/20/20 más walk-forward (las particiones y los tramos, en §12.5);
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

### 12.1 Contrato de datos de la Fase 3 (F3-E1 y F3-E2) [**APROBADO** por el revisor el 18 nov 2026 tras tres revisiones (commit `7e0307b`): CD-R01 a CD-R03 resueltos; F3-AL-01. **Enmienda del 19 nov 2026, APROBADA por el revisor el 20 nov (commit `663c59c`):** la presencia `en_conflicto` (punto 2), el plazo de 48 h para cerrar un día (punto 1; política de congelación de la copia local), CD-19 y CD-20, y la precisión del minuto en que un instrumento empieza o deja de cotizar (punto 2 y CD-16)]

Fija qué datos ve un indicador y cuándo puede dar un valor. Vale para el histórico que se descarga en el PC (F3-E1) y, sin cambios, para los datos del grabador cuando los usen las estrategias (F4 en adelante). **Nada de F3-E1 ni de F3-E2 se programa antes de que el revisor lo apruebe** (`08` §3). Los puntos siguen los siete del contrato en `08` §3.

**1. Datos de partida**
- **Serie:** las velas de 1 minuto de `last` (operaciones) de cada instrumento. `mark` e `index` no alimentan indicadores en la F3.
- **Histórico (F3-E1):** solo REST, con el cliente de la E1 (solo `GET`, lista blanca, el único presupuesto REST de la máquina, sin claves). Cada vela descargada es `cerrada_rest` con evidencia `vela_rest_suficiente`. No hay flujo grabado: su flujo cuenta como S3 y **nunca** se infiere `ausencia_sin_trades_probable` (§3.4 [H12]).
- **Universo:** los 20 + 20 instrumentos del último `universe_snapshots` del grabador en la fecha de la descarga, guardados como lista versionada en el repositorio con esa fecha, más los deslistados que KuCoin aún sirva [P: `docs/kucoin/historico.md`]. El sesgo de supervivencia que quede se declara en cada informe.
- **Archivo:** Parquet (zstd) con las columnas y los tipos de `candles` de E3 (§3.8 y §3.9, punto 6: decimales en `decimal128(38, 24)` desde el formato 2 del 18 nov 2026, horas en UTC con µs). Un archivo por mercado, símbolo y día UTC, escrito de forma atómica (temporal y renombrado), con su SHA-256 en un manifiesto. El manifiesto de cada día guarda también sus peticiones, con su resultado, y los minutos `en_conflicto` con sus velas. La descarga es **reanudable e idempotente**: repetirla no cambia ningún archivo cerrado. Lo pedido se guarda aparte, en el `solicitado.json` de cada instrumento: los de toda la orden, antes de su primera petición, también la de los contratos (F3E1-02).
- **Un día se cierra** cuando ha terminado, ninguno de sus minutos queda `no_descargado` y se descargó **al menos 48 h después de su fin** [**enmienda del 19 nov 2026, aprobada**; CD-20]. KuCoin aún puede cambiar las velas de un día reciente: el 19 nov 2026 dio dos velas para un minuto del día anterior al menos de las 08:36 a las 09:39 UTC, y a las 22:10 UTC ya solo daba una (CD-19 y CD-20). Es el mismo plazo de 48 h durante el que el grabador sigue pidiendo las velas REST (§3.8). Mientras un día está abierto, cada descarga lo vuelve a pedir entero y lo reescribe con la respuesta nueva; uno cerrado nunca se reescribe. Es una política de congelación de la copia local: un día cerrado no se vuelve a pedir aunque KuCoin lo cambie después.

**2. Ventana, elegibilidad y presencia de cada minuto** (punto 1; CD-R01)

Son tres cosas distintas, y ninguna se deduce de otra:
- **Ventana solicitada:** los minutos que se piden a KuCoin para un instrumento. Es el denominador de la cobertura, y ni la primera ni la última vela obtenida la recortan.
- **Elegibilidad conocida** (§3.5). Un minuto es `no_elegible` solo con **evidencia independiente de las velas**:
  - la fecha de apertura del contrato que publica KuCoin en Futures (`firstOpenDate` [P: se confirma con la respuesta real en F3-E1]);
  - la fecha de alta o de retirada de un anuncio oficial, guardada con su enlace en la lista versionada del universo;
  - el estado de cotización que haya observado el grabador (`instruments`).

  Con esa evidencia, el minuto es `elegible`. La evidencia de que un minuto no era elegible tiene que cubrirlo **entero**; la de que lo era basta con que toque parte de él. Así, el minuto en que un instrumento empieza o deja de cotizar no queda fuera de sus fechas [precisión del 19 nov 2026, aprobada; CD-16]. Sin evidencia, la elegibilidad es `desconocida` y el minuto **cuenta en el denominador**: un minuto de un instrumento que quizá aún no cotizaba se informa como ausente de elegibilidad desconocida, y nunca se descarta.
- **Velas obtenidas.** Cada minuto de la ventana tiene una y solo una presencia:

| Presencia | Cuándo | Para los indicadores |
|---|---|---|
| `presente` | KuCoin devolvió su vela | Utilizable según su calidad (punto 5) |
| `ausente` | La petición que lo cubre tuvo éxito y no trajo vela para él, también si la respuesta vino vacía o con huecos al principio o al final. Es `ausencia_no_verificable` (§3.4) | Ausencia: **nunca** «no hubo operaciones», nunca se rellena ni se copia el cierre anterior |
| `en_conflicto` | La respuesta que lo cubre trajo **dos velas distintas** para él [**enmienda del 19 nov 2026, aprobada**; CD-19]. No se usa ni se elige ninguna: las dos quedan en el registro del día, tal como llegaron. El resto de la respuesta vale. Como `ausente`, no se reintenta como un fallo: queda resuelto por esa respuesta, y es definitivo cuando el día se cierra (punto 1). Antes, la descarga siguiente vuelve a pedir el día, por si KuCoin lo corrige (CD-20). Dos velas idénticas son una sola | Igual que ausente |
| `no_descargado` | La petición que lo cubre falló o no se ha hecho. No es definitivo: la descarga lo reintenta | Igual que ausente |

- **Informe de cobertura**, por instrumento y día:
  - los minutos de la ventana por presencia y por elegibilidad;
  - los tramos fallidos;
  - las **contradicciones**: minutos `no_elegible` con vela, una o dos distintas. Conservan su presencia (`presente` o `en_conflicto`), porque la elegibilidad no cambia la presencia, pero se informan aparte y no cuentan en la cobertura;
  - los minutos **en conflicto**, aparte;
  - la **cobertura** (residual de CD-R01). Sea $E$ el conjunto de los minutos de la ventana cuya elegibilidad no es `no_elegible`, es decir, `elegible` o `desconocida`. La cobertura es $|\{m \in E : m \text{ presente}\}| \,/\, |E|$, siempre entre 0 y 1. Si $E$ está vacío, es «no aplicable», nunca 0 ni 100 %. Un minuto `en_conflicto` de $E$ cuenta en el denominador y nunca en el numerador.

  El informe recorre la ventana solicitada guardada, nunca los archivos que encuentre (F3E1-02). Un día sin archivos cuenta sus minutos como `no_descargado`. Uno cuyos archivos no forman un par válido (falta uno, la huella no coincide o su ventana sale de lo pedido) también, y se nombra con su motivo. Los días con archivos fuera de lo pedido se listan aparte y no cuentan.

  F3-A1 comprueba que el informe coincide con los archivos, con el numerador, el denominador y las contradicciones por separado.

**3. El tiempo no se comprime** (punto 2)
- Toda serie se indexa por la apertura UTC de cada intervalo, $[apertura, apertura + Δ)$.
- Dos velas son **consecutivas** solo si la apertura de la segunda es la de la primera más Δ. **La posición en una lista nunca decide la contigüidad:** entre dos velas presentes separadas por un hueco hay ausencias, aunque no haya filas.

**4. Temporalidades superiores** (punto 3)
- 5 m, 15 m, 1 h, 4 h y 1 d, con límites en UTC: cada intervalo empieza en un múltiplo de su duración contado desde las 00:00 UTC. Las de 4 h empiezan a las 00, 04, 08, 12, 16 y 20 UTC, y la de 1 d a las 00:00 UTC.
- La vela del intervalo $I$ se forma con los minutos presentes de $I$:
  - apertura del primero y cierre del último;
  - máximo y mínimo de todos;
  - volumen y turnover sumados en decimal exacto, con el contexto contable de §5.7 (precisión 80 e `Inexact` atrapado) y nunca con el del entorno: una suma que no cabe falla, nunca se redondea (F3E1-01);
  - `minutos_presentes`, cuántos hay de los $N$ del intervalo ($N$ = 5, 15, 60, 240 o 1440).
- **Completa** si `minutos_presentes` = $N$. **Incompleta** si $0 < n < N$: se guarda con su $n$ para los informes, pero **no alimenta indicadores ni señales**. Sin ningún minuto, no hay vela: es una ausencia.
- **Solo existe cerrada, y tiene un instante de disponibilidad** (CD-R02). No se forma la vela de un intervalo que no haya terminado dentro de lo descargado. Además, hay que distinguir dos cosas:
  - **Disponibilidad intradía de un minuto.** Una vela de 1 minuto está disponible cuando es **cerrada** según §3.4: intervalo terminado, reloj sincronizado, margen de estabilización cumplido, evidencia y campos validados.
    - En vivo, ese instante es cuando se valida su última evidencia, y nunca antes del fin del minuto más el margen (hoy 15 s, provisional hasta D26).
    - En el histórico, la regla declarada es el fin del minuto más el mismo margen.
  - **Estado terminal diario** (`minute_status`, §3.5). Se asigna al cerrar el día y sirve para la completitud. **No es condición para usar una vela**: un agregado nunca espera al cierre del día.

  Una vela superior completa está **disponible en el instante en que lo está el último de sus $N$ minutos**, el máximo de los de sus componentes. Antes no existe para nadie. Si le falta un minuto, no alimenta indicadores nunca: es incompleta, y se sabe en cuanto ese minuto queda cerrado como ausencia. Este instante es la disponibilidad de la regla causal de F3-E4 (`ejecución ≥ decisión ≥ disponibilidad`). Lo que llegue después (evidencia tardía, §3.5) no cambia retroactivamente un valor ya usado para decidir.
- **Determinista e independiente del orden:** el resultado depende solo del conjunto de minutos por su clave (mercado, símbolo, apertura). Cargarlos en otro orden o repetir una vela idéntica no cambia nada. Dos velas distintas con la misma clave son un error que detiene el proceso: nunca se elige una.

**5. Calidad** (punto 4)
- Cada vela de 1 minuto lleva sus dimensiones P, V y T (§3.4) y su evidencia. En el histórico: P y T válidas sin contraste, V sujeta al punto 7 y evidencia `vela_rest_suficiente` en todas.
- La vela superior hereda, en cada dimensión, **la peor de sus minutos**: de mejor a peor, contrastada y consistente, solo REST, discrepante. Una discrepancia invalida la dimensión en la que discrepa. Su evidencia es `vela_rest_suficiente` si alguno de sus minutos lo es.
- **Porcentaje solo REST de cada resultado** (§12; CD-R03). Si esos resultados se excluyen de la serie consistente lo decide D29; esta regla solo fija cómo se cuenta:
  - **Unidad:** el minuto original, es decir, la vela de 1 minuto, nunca la vela agregada. Una vela de 1 h cuenta como sus 60 minutos.
  - **Procedencia** (las entradas consideradas): todos los minutos que influyen en el valor.
    - En un indicador de ventana (SMA, Bollinger, volumen relativo, soportes y resistencias), los de las $w$ velas de su ventana.
    - En uno recursivo (EMA, MACD, RSI y ATR de Wilder, ADX), **todos desde su último inicio o reinicio, la semilla incluida**: aunque ya no estén entre las últimas $w$ velas, siguen influyendo en su estado.
    - Un reinicio (punto 6) borra la procedencia anterior.
  - Como un indicador solo da valor sin huecos, su procedencia es siempre un tramo continuo de minutos presentes. La de un resultado que combina varios indicadores del mismo instrumento es la unión de sus tramos, y cada minuto cuenta una sola vez.
  - **Porcentaje:** minutos `vela_rest_suficiente` de la procedencia entre todos los minutos de la procedencia. En el histórico, el 100 %.

**6. Ventanas de los indicadores** (punto 5)
- Un indicador con ventana de $w$ velas da valor en la vela $t$ solo si las $w$ velas de $t - (w-1)Δ$ a $t$ están **presentes, completas y con las dimensiones que el indicador necesita** (tabla de §3.4). Si no, no hay valor: nunca se interpola, nunca se repite el último y nunca se salta el hueco.
- **Recalentamiento:** tras cualquier vela no utilizable, el indicador vuelve a su estado inicial. Los recursivos (EMA, MACD, RSI y ATR de Wilder, ADX) se reinician y no dan valor hasta completar otra vez su calentamiento. Cada indicador declara su calentamiento en F3-E2; por ejemplo, una EMA de periodo $n$ arranca con la media simple de sus $n$ primeras velas y da valor desde la $n$-ésima.
- **Sin anticipación:** el valor en $t$ depende solo de velas cerradas hasta $t$; se prueba envenenando todo lo posterior (F3-A4).
- **Aritmética:** las velas guardan decimales exactos y el indicador los convierte a coma flotante de 64 bits al entrar, como regla declarada (`08`, F3-E2).

**7. Volumen mientras B8 siga abierta** (punto 6)
- **Permitido:** los usos invariantes a escala dentro de un mismo instrumento. Es decir, el volumen relativo (el volumen de la vela entre la media de las $w$ anteriores del mismo instrumento) y la ponderación del VWAP de ese instrumento.
- **Prohibido hasta que E5 cierre B8:** comparar volúmenes entre instrumentos, umbrales absolutos de volumen y el volumen de la vela frente al flujo de operaciones.
- En Futures el volumen va en contratos, con el `multiplier` al lado (§3.8). Si el multiplicador cambia dentro de una ventana, la ventana se rompe para los usos de volumen, como con un hueco.
- **El código lo impide:** cada indicador declara su uso del volumen, y un uso prohibido se rechaza con un error mientras B8 esté abierta, nunca en silencio.

**8. Casos obligatorios** (punto 7; pruebas de F3-E1 y F3-E2)

| # | Caso | Resultado exigido |
|---|---|---|
| CD-01 | Falta un minuto dentro de una vela de 5 m | Vela incompleta con `minutos_presentes` = 4; ningún indicador la usa |
| CD-02 | Hueco dentro de la ventana de un indicador | Sin valor mientras la ventana lo contenga; después, recalentamiento completo (la EMA, reiniciada) |
| CD-03 | Falta la vela de ejecución, la primera de 1 minuto posterior a la decisión (F3-E4) | La señal caduca, sin ejecución |
| CD-04 | Datos solo REST | Cada resultado informa su porcentaje solo REST (100 %) |
| CD-05 | Un uso de volumen condicionado a B8, por ejemplo un umbral absoluto | Rechazado con error |
| CD-06 | Los mismos minutos en otro orden y con duplicados idénticos | Velas superiores idénticas |
| CD-07 | Dos velas distintas con la misma clave | Error; no se elige ninguna |
| CD-08 | Dos velas de 1 h completas separadas por una hora sin minutos | No son consecutivas: una ventana de 2 no da valor en la segunda |
| CD-09 | Límites: el minuto de las 23:59 UTC y las velas de 4 h | El minuto pertenece al día en que empieza; las de 4 h empiezan a las 00, 04, 08, 12, 16 y 20 UTC |
| CD-10 | Intervalo sin terminar al final de lo descargado | No hay vela |
| CD-11 | Tramo cuya descarga falló | Sus minutos quedan `no_descargado` en el informe y cuentan como ausentes |
| CD-12 | Cambio del `multiplier` dentro de una ventana de volumen | La ventana se rompe para los usos de volumen |
| CD-13 | Ventana `[12:00, 12:05)` de un instrumento cotizando; la respuesta trae solo 12:01, 12:02 y 12:03 (CD-R01) | 12:00 y 12:04 quedan `ausente` (elegibilidad `desconocida`, o `elegible` si hay evidencia); cobertura 3/5, nunca 3/3 |
| CD-14 | Respuesta correcta y vacía para toda la ventana | Todos sus minutos `ausente`; cobertura 0 |
| CD-15 | Fallan las peticiones de los dos extremos de la ventana | Esos minutos quedan `no_descargado` y la ventana no se recorta |
| CD-16 | Contrato de Futures con `firstOpenDate` dentro de la ventana, o par con fecha de retirada en un anuncio; además, una vela en un minuto `no_elegible` y una ventana entera `no_elegible` | Solo los minutos fuera de esas fechas son `no_elegible`. La vela en uno de ellos es una contradicción: se informa aparte y no entra ni en el numerador ni en el denominador; con cuatro minutos elegibles presentes y esa vela, la cobertura es 4/4, nunca 5/4. Una ventana entera `no_elegible` da cobertura «no aplicable». El minuto que contiene la fecha no está fuera de ella: KuCoin abrió ASTERUSDTM a las 12:19:20 del 19 sep 2025, y el minuto de las 12:19, con su vela, es elegible y no es una contradicción [precisión del 19 nov 2026, aprobada] |
| CD-17 | Agregado de 5 m `[10:00, 10:05)` con margen de 15 s y sin filas de `minute_status`; su último minuto se recibe y valida a las 10:05:16 (CD-R02) | A las 10:05:10 no está disponible; a las 10:05:16, sí. Una decisión posterior se ejecuta en la apertura del minuto de las 10:06, sin esperar al cierre del día |
| CD-18 | Datos mixtos (minutos solo REST y contrastados), agregados de 5 m y una EMA cuya semilla solo REST ya salió de sus últimas $w$ velas (CD-R03) | El porcentaje cuenta minutos originales e incluye la semilla mientras la EMA no se reinicie; tras un reinicio por un hueco, la procedencia anterior deja de contar |
| CD-19 | Una respuesta trae dos velas distintas para el mismo minuto, como hizo KuCoin el 19 nov 2026 con el día entero del 18 nov de DOT-USDT de Spot (09:03 UTC: la vela completa y otra parcial) [enmienda del 19 nov 2026, aprobada] | Ese minuto queda `en_conflicto`, sin vela en el archivo y con las dos en el registro del día, exactas. Cuenta en el denominador y no en el numerador; si es `no_elegible`, es además una contradicción. El resto de la respuesta vale y la descarga sigue. El conflicto no deja el día abierto: se cierra según el punto 1. Dos velas idénticas son una sola |
| CD-20 | El día de CD-19 se descarga 8,6 h después de su fin, con las dos velas, y KuCoin lo corrige después: a las 22:10 UTC del 19 nov 2026 ya solo daba la completa [enmienda del 19 nov 2026, aprobada] | Antes de 48 h desde su fin, el día queda abierto aunque todas sus peticiones respondieran. La descarga siguiente lo pide de nuevo y guarda la respuesta nueva: el minuto pasa a `presente`, con la vela completa. Descargado a las 48 h o después, se cierra y nunca se vuelve a pedir |

Estas pruebas responden de F3-A1 (descarga, elegibilidad y cobertura), F3-A2 (temporalidades y disponibilidad), F3-A4 (anticipación) y F3-A5 (calidad, porcentaje solo REST, huecos, recalentamiento y volumen). El calentamiento concreto de cada indicador se fija en F3-E2, con su tabla.

### 12.2 Indicadores de F3-E2 [**APROBADO** por el revisor el 20 nov 2026 en la segunda revisión (commit `63822c4`): F3E2-01 a F3E2-06 resueltos]

Fija qué calcula cada indicador de la F3, cuándo da su primer valor, qué velas puede usar y cómo se comprueba. Aplica el contrato de datos (§12.1, puntos 3 a 8) y la tabla de dimensiones de §3.4 sin cambiarlos. La profundidad del libro es de F3-E6. **Aprobada el 20 nov 2026: los indicadores se programan con ella.** Programados en `src/kbot/indicators/` (§18) y **APROBADOS** por el revisor en `7b1c670`.

**1. Común a todos**
- **Entrada:** la serie de un instrumento en una temporalidad (1 m, 5 m, 15 m, 1 h, 4 h o 1 d), indexada por la apertura UTC de cada vela (§12.1, punto 3). Cada vela llega con estos datos:
  - su presencia o su completitud;
  - sus dimensiones P, V y T;
  - su instante de disponibilidad (§12.1, puntos 2, 4 y 5).
- **Vela utilizable** para un indicador. Tiene que cumplir tres condiciones:
  - Está presente (1 m) o completa (temporalidad superior).
  - Tiene válidas **todas** las dimensiones que el indicador necesita según §3.4: P + T para los indicadores de precio y P + V + T para VWAP y volumen relativo (F3E2-01). Una dimensión es válida si es contrastada o solo REST, nunca si es discrepante.
  - Es consecutiva con la utilizable anterior: su apertura es la de esa más Δ (§12.1, punto 3). La contigüidad no sustituye a T: una vela con T discrepante no es utilizable aunque su apertura sea la siguiente.
  - Una dimensión que el indicador no necesita no le afecta. Por ejemplo, una V discrepante deja una SMA con valor.
- **Valor:** en la vela $t$ solo si todas las velas que necesita desde su último inicio son utilizables y consecutivas. Si no, no hay valor en $t$ y el indicador vuelve a su estado inicial: los recursivos se reinician, y el siguiente valor llega tras un calentamiento completo desde la primera vela utilizable posterior (§12.1, punto 6).
- **Calentamiento:** cuántas velas utilizables y consecutivas hacen falta, contando la del primer valor. Está en la tabla y coincide con el primer valor de TA-Lib (su *lookback* más uno), comprobado con TA-Lib 0.8.3.
- **Entradas de un valor** (F3E2-02 y F3E2-03). Son las velas de las que depende, según su clase (§12.1, punto 5):
  - **De ventana** (SMA, Bollinger, volumen relativo, soportes y resistencias): las velas de su ventana, y nada más. La tabla dice cuáles.
  - **Recursivos** (EMA, MACD, RSI, ATR, ±DI, DX y ADX): todas las velas desde su último inicio o reinicio, con la semilla incluida, aunque ya no estén entre las últimas.
  - **De sesión** (VWAP): todas las velas del día UTC hasta $t$.
- **Procedencia y porcentaje solo REST** (§12.1, punto 5; F3E2-02):
  - La procedencia de un valor son los minutos originales de sus entradas. Una vela superior cuenta como sus $N$ minutos, y cada minuto cuenta una sola vez, aunque esté en varias entradas.
  - El porcentaje solo REST son los minutos `vela_rest_suficiente` de la procedencia entre todos los de la procedencia. En el histórico es el 100 %.
  - Ejemplo: una SMA(2) cuya primera vela es solo REST y cuyas dos siguientes están contrastadas da 0 % en la tercera, porque la primera ya no está en su ventana. Una EMA, en cambio, conserva la influencia de su semilla solo REST hasta que se reinicia.
- **Disponibilidad** (§12.1, punto 4; F3E2-03):
  - Un valor está disponible en el **máximo** de los instantes de disponibilidad de sus entradas. Las velas no se validan necesariamente en orden.
  - Ejemplo: en una SMA(2), si la vela de las 10:00 está disponible a las 10:05 y la de las 10:01 a las 10:02:15, el valor de las 10:01 no existe hasta las 10:05.
  - Lo que llegue después nunca cambia un valor ya usado para decidir (§12.1, punto 4).
- **Aritmética:** precios y volúmenes entran como `float` de 64 bits, convertidos desde su `Decimal` exacto, y todo el cálculo es en `float` (§12.1, punto 6; regla declarada).
- **Sin anticipación:** el valor en $t$ depende solo de las velas hasta $t$ (F3-A4).

**2. Los indicadores**

$C_t$, $H_t$, $L_t$ y $V_t$ son el cierre, el máximo, el mínimo y el volumen de la vela $t$. Las velas se numeran desde la primera utilizable del tramo: $1, 2, \dots$ En los suavizados de Wilder, $S_t = S_{t-1} - S_{t-1}\cdot(1/n) + x_t$, escrito así porque es como lo calcula TA-Lib.

| Indicador | Definición | Parámetros de partida | Calentamiento (velas) | Clase y entradas | Dimensiones | Volumen | Referencia |
|---|---|---|---|---|---|---|---|
| SMA($n$) | Media de los $n$ últimos cierres | $n$ = 20 | $n$ | Ventana: las $n$ últimas velas | P + T | No | TA-Lib `SMA` |
| EMA($n$) | $\alpha = 2/(n+1)$. En la vela $n$, la SMA de los $n$ primeros cierres; después, $E_t = \alpha C_t + (1-\alpha)E_{t-1}$ | $n$ = 20 | $n$ | Recursivo | P + T | No | `EMA` |
| RSI($n$) | De Wilder. $\Delta_t = C_t - C_{t-1}$. Medias de subidas $G$ y de bajadas $B$: en la vela $n+1$, las medias simples de los $n$ primeros cambios; después, $G_t = (G_{t-1}(n-1) + \max(\Delta_t, 0))/n$, e igual $B$ con $\max(-\Delta_t, 0)$. RSI $= 100\,G/(G+B)$ si $G+B > 0$; si no, 50 (serie plana, como TA-Lib 0.8.3) | $n$ = 14 | $n+1$ | Recursivo | P + T | No | `RSI` |
| MACD($f$, $s$, $q$) | Línea $=$ EMA$_f$ − EMA$_s$, **las dos empezadas en la vela $s$**: la lenta con la media de sus $s$ cierres, y la rápida con la de los $f$ cierres que acaban en la vela $s$. Señal $=$ EMA$_q$ de la línea, empezada en la vela $s+q-1$ con la media de sus $q$ primeros valores. Histograma $=$ línea − señal. Las tres salen desde la vela $s+q-1$ | 12, 26, 9 | $s+q-1$ (34) | Recursivo | P + T | No | `MACD`: así lo calcula TA-Lib 0.8.3. Con cada EMA empezada por su cuenta, la línea difería 0,17 en el primer valor |
| Bollinger($n$, $k$) | Media $=$ SMA($n$); bandas $=$ media $\pm\, k\sigma$, con $\sigma$ la desviación típica **poblacional** de los $n$ cierres (0 si la varianza calculada no es positiva) | 20, 2 | $n$ | Ventana: las $n$ últimas velas | P + T | No | `BBANDS` (media simple) |
| ATR($n$) | De Wilder. $TR_t = \max(H_t - L_t,\ |H_t - C_{t-1}|,\ |L_t - C_{t-1}|)$, desde la vela 2. En la vela $n+1$, la media de los $n$ primeros $TR$; después, $(ATR_{t-1}(n-1) + TR_t)/n$ | $n$ = 14 | $n+1$ | Recursivo | P + T | No | `ATR` |
| ±DI, DX y ADX($n$) | Wilder, **exactamente como TA-Lib 0.8.3** (F3E2-04), en cuatro partes. **DM:** $+DM_t = H_t - H_{t-1}$ si es mayor que $L_{t-1} - L_t$ y que 0, y si no, 0; $-DM_t$, al revés. **Suavizado:** $TR$, $+DM$ y $-DM$ empiezan con la suma de sus $n-1$ primeros valores y siguen con $S_t$. **±DI** $= 100\,S_{\pm DM}/S_{TR}$ si $S_{TR} > 0$ (comparación exacta); si no, 0. **DX** está *definido* en $t$ si $S_{TR} > 0$ y $|{+DI} + {-DI}| \ge 10^{-14}$, el umbral fijo de TA-Lib (`TA_EPSILON`), válido porque los DI no tienen unidades; entonces $DX = 100\,|{+DI} - {-DI}|/({+DI} + {-DI})$. Si no está definido, la salida DX repite la anterior (0 en la primera). **ADX:** en la vela $2n$, la suma de los DX *definidos* de las $n$ velas desde la $n+1$ entre $n$ (un DX no definido suma 0); después, si DX está definido, $ADX_t = ADX_{t-1} - (ADX_{t-1} - DX_t)\cdot(1/n)$, y si no, $ADX_t = ADX_{t-1}$ | $n$ = 14 | ±DI y DX: $n+1$; ADX: $2n$ | Recursivo | P + T | No | `PLUS_DI`, `MINUS_DI`, `DX` y `ADX` |
| VWAP | Del día UTC: $\sum p_i V_i / \sum V_i$ sobre las velas del día hasta $t$, con $p_i = (H_i + L_i + C_i)/3$. Empieza a las 00:00 UTC. **Si alguna vela del día hasta $t$ no es utilizable, no hay valor hasta el día siguiente**, porque un VWAP rehecho a mitad del día ya no sería el del día. Sin valor mientras $\sum V_i = 0$. Solo en temporalidades de menos de 1 d | Día UTC | 1 | Sesión: las velas del día hasta $t$ | P + V + T | Invariante a escala: pondera dentro del propio instrumento (§12.1, punto 7) | `VWAP` con las velas del día como rango (TA-Lib no tiene sesiones), con las diferencias declaradas del punto 4 |
| Volumen relativo($w$) | $V_t$ entre la media de $V_{t-1}, \dots, V_{t-w}$. Sin valor si esa media es 0 | $w$ = 20 | $w+1$ | Ventana: las $w+1$ últimas velas | P + V + T | Invariante a escala | `RVOL`, con las diferencias del punto 4 |
| Soportes y resistencias($k$, $m$) | **Pivote alto** en la vela $j$: $H_j$ estrictamente mayor que los $H$ de las $k$ velas anteriores y de las $k$ siguientes. Se conoce al cerrar la vela $j+k$, nunca antes. **Caduca** cuando han pasado $m$ velas desde la del pivote: vale en $t$ solo si $t - j < m$. **Resistencia** en $t$: el pivote alto confirmado, sin caducar, más reciente que ningún cierre posterior a $j$ ha superado. **Soporte**: igual, con pivotes bajos ($L_j$ estrictamente menor) y cierres por debajo. Sin pivote vigente, no hay valor | $k$ = 5, $m$ = 100 | $2k+1$ | Ventana: las $m+k$ últimas velas, las $m$ de los pivotes posibles y las $k$ anteriores que los confirman | P + T | No | Casos propios (TA-Lib no lo tiene) |

**3. Volumen** (§12.1, punto 7)
- El VWAP y el volumen relativo solo comparan volúmenes del mismo instrumento, así que están permitidos con B8 abierta.
- Cualquier uso que compare volúmenes entre instrumentos, o con un umbral absoluto, se rechaza con un error (CD-05).
- En Futures, un cambio del `multiplier` dentro de la ventana la rompe para los dos, como un hueco (CD-12).
- **En el histórico solo se conoce el multiplicador actual de cada contrato** [I]: KuCoin no da su historial. Se supone constante y se declara en cada informe.

**4. Diferencias declaradas con TA-Lib y casos propios** (F3E2-06)
- **VWAP:** TA-Lib acumula desde el principio del rango que se le pasa, así que se compara pasándole las velas de cada día UTC. Hay tres diferencias, que se prueban aparte:
  - mientras $\sum V = 0$, TA-Lib da 0 y aquí no hay valor;
  - tras un hueco, aquí no hay valor el resto del día, mientras que TA-Lib no conoce los huecos;
  - el día empieza a las 00:00 UTC.
  - Casos: tres velas de un día con precios típicos 10, 11 y 12 y volúmenes 1, 2 y 1 dan 10, 10,667 y 11, como TA-Lib. Con volúmenes 0, 0, 1 y 2 y precios 10, 10, 10 y 11, las dos primeras velas no tienen valor (TA-Lib da 0), y las siguientes dan 10 y 10,667.
- **Volumen relativo:** es igual que `RVOL` de TA-Lib, salvo con media 0. **Limitación declarada** (F3E2-07, de la revisión de `7b1c670`): el total acumulado redondea, como el de TA-Lib. Un volumen unas $10^{16}$ veces mayor que los demás se come los que se suman mientras está en el total, y el error se ve cuando sale. Así, con 10¹², 10⁻⁶, 10⁻⁶, 10⁻⁶ y 10⁻⁶, RVOL(3) da 3 en la quinta vela, igual que TA-Lib, aunque la media recalculada de su ventana da 1. Coincidir con TA-Lib no garantiza la exactitud matemática. Ahí TA-Lib da infinito, o NaN si el volumen de la vela también es 0, y aquí no hay valor.
  - Casos: con volúmenes 10, 20, 30 y 40, la cuarta vela da $40/20 = 2$, como TA-Lib. Con 0, 0, 0 y 5, la cuarta no tiene valor (TA-Lib da infinito).
- **Soportes y resistencias** ($k$ = 2):
  - Con máximos 1, 2, 5, 2, 1, la vela de 5 es un pivote alto. Se conoce al cerrar la quinta vela, y la resistencia vale 5 desde ahí hasta que un cierre supera 5.
  - Si después se confirma un pivote más bajo y un cierre lo supera, la resistencia vuelve a ser 5, si sigue sin superar.
  - Dos máximos iguales no hacen pivote.
  - Con $m$ = 6, el pivote de la vela 3 caduca en la vela 9 aunque nadie lo haya superado.

**5. Tolerancias de la comparación con TA-Lib** (F3E2-05)

Cada salida se compara según sus unidades. $b$ es el valor de TA-Lib y $a$ el propio. $S$ es la **escala de precios** de la serie comparada: el mayor $|H|$, $|L|$ o $|C|$ de sus velas.

| Salidas | Unidades | Tolerancia |
|---|---|---|
| SMA, EMA, Bollinger (las tres), ATR, línea, señal e histograma del MACD, VWAP | Precio | $|a - b| \le 10^{-9}\,|b| + 10^{-12}\,S$ |
| RSI, ±DI, DX y ADX | Sin unidades, de 0 a 100 | $|a - b| \le 10^{-9}$ |
| Volumen relativo | Cociente sin unidades | $|a - b| \le 10^{-9}\,|b| + 10^{-12}$ |

- **El término absoluto de las salidas en unidades de precio escala con $S$.** Así, reescalar los precios reescala la tolerancia, y un resultado que se anula cerca de cero no pasa nunca.
  - Ejemplo: con precios de $10^{-8}$, $S \approx 10^{-8}$. Una referencia de $10^{-10}$ admite un error de unos $10^{-19}$, y un 0 falla.
  - Las diferencias medidas con TA-Lib 0.8.3 son de $10^{-16} S$ a $10^{-13} S$, de 1.000 a 10.000 veces por debajo.
- **Las salidas de 0 a 100 usan una tolerancia absoluta fija,** porque no dependen de la escala de los precios.
- **Fuera de comparación:** los degenerados con umbral (DX y ADX) y las diferencias declaradas del punto 4 se prueban con casos exactos, no por tolerancia.

**6. Pruebas** (F3-A3, F3-A4 y F3-A5)
- **Contra TA-Lib:**
  - Series aleatorias sin huecos y tramos reales del histórico, valor a valor desde el primero, con las tolerancias del punto 5.
  - Las mismas comparaciones con las velas reescaladas por $10^{-8}$ y por $10^{4}$ (también el volumen en VWAP y volumen relativo).
  - Una implementación de prueba que devuelve 0 con precios pequeños tiene que fallar.
  - Con huecos, cada tramo continuo se compara por separado, porque TA-Lib no conoce los huecos.
  - Corre en x86-64 (el PC y la CI) y en aarch64, con un trabajo nuevo de la CI en el ejecutor `ubuntu-24.04-arm` de GitHub, gratuito para repositorios públicos (P-F3-3).
  - TA-Lib es solo de desarrollo: TA-Lib 0.8.3, BSD, con ruedas para Windows y Linux en x86-64 y aarch64. Trae NumPy.
- **DX y ADX degenerados** (F3E2-04):
  - 40 velas, periodo 14, con $H = 10, 10{,}000000000000002, \dots$, $L = 1$ y $C = 5$: +DI de unos $1{,}4 \cdot 10^{-15}$ y −DI 0, así que DX no está definido. DX da 0 en la vela 15 y ADX 0 en la 28, como TA-Lib.
  - Una tendencia solo alcista inicia el ADX, y después llega un rango constante, sin DM. Los DI decaen hasta que su suma baja de $10^{-14}$ en la vela 505. Desde ahí, DX repite 100 y el ADX sigue en 100, como TA-Lib, en lugar de caer hacia 0.
- **Envenenamiento:** cambiar todas las velas posteriores a $t$ no cambia ningún valor hasta $t$ (F3-A4).
- **Calidad** (F3E2-01):
  - Una T no válida deja sin valor cualquier indicador y lo reinicia.
  - Una P no válida deja sin valor el volumen relativo.
  - Una V no válida, sola, deja con valor una SMA y sin valor el VWAP.
  - Una vela incompleta tampoco es utilizable, y una ausente rompe la contigüidad (CD-02 y CD-08).
- **Recalentamiento:** tras un hueco, la EMA vuelve a dar su primer valor $n$ velas después, y ese valor es la media simple de esas $n$ velas (CD-02).
- **Procedencia y disponibilidad** (F3E2-02 y F3E2-03):
  - el caso de la SMA(2) con 0 % solo REST;
  - una EMA cuya semilla solo REST sigue contando hasta su reinicio;
  - la SMA(2) con una predecesora disponible tarde, que retrasa el valor hasta las 10:05.
- **Volumen:** CD-05 y CD-12. **Porcentaje solo REST:** CD-04 y CD-18.

**7. Decisiones de la primera revisión:**
- El VWAP de sesión UTC, sin valor el resto del día tras un hueco, y la definición de soportes y resistencias son admisibles, esta última con la caducidad de $m$ contada desde la vela del pivote y su prueba.
- El RSI de 50 en una serie plana es conforme con TA-Lib 0.8.3.
- La tolerancia es la del punto 5.

### 12.3 Régimen y fichas de F3-E3 [diseño del 20 nov 2026, **APROBADO** por el revisor en la segunda revisión (commit `1974a25`): F3E3-01 y F3E3-02 resueltos. **Las tres fichas, APROBADAS por el propietario el 20 nov 2026** con sus parámetros de partida («apruebo las tres»). Programados en `src/kbot/strategies/` (§18) y **APROBADOS** por el revisor en la segunda revisión del código (`67e0f9f`): F3E3-03 y F3E3-04 resueltos]

Fija el régimen de mercado y las fichas matemáticas de tres familias: tendencia, breakout y reversión a la media (`08`, F3-E3). Usa los indicadores de §12.2 y el contrato de datos de §12.1 sin cambiarlos. **Ninguna familia se programa como estrategia sin su ficha aprobada por el propietario**, después de la revisión (§12; `08` §6). El scalping y el volumen y flujo de mercado son de F3-E6.

#### 12.3.1 Común a las tres fichas

**Dirección.** $d = +1$ es largo y $d = -1$, corto. En Spot solo hay largos (§6.2.1): una condición de corto en Spot no da señal. En Futures, las dos.

**Temporalidades.** Cada ficha tiene tres:
- **contexto** ($\tau_c$): el estado del mercado mayor;
- **señal** ($\tau_s$): la preparación de la entrada;
- **ejecución** ($\tau_e$): la vela que confirma la entrada, el **disparo**.

$K$ velas de ejecución duran lo que una de señal: $K\,\Delta_e = \Delta_s$.

**Qué se ve en un instante $D$.** Todo se evalúa con lo disponible en $D$:
- Un valor de un indicador se puede usar en $D$ si su disponibilidad (§12.2, punto 1) es $\le D$.
- La **vela de contexto en $D$** es la de $\tau_c$ cuyo intervalo es el último terminado en $D$ o antes. Tiene que ser utilizable y tener disponibles en $D$ los valores que se usan; si no, el contexto no se cumple. **Nunca se usa otra anterior.**
- Ejemplo: en $D$ = 15:00:15, la vela de contexto de 4 h es la de [08:00, 12:00), porque la de [12:00, 16:00) aún no ha terminado.
- En el histórico, una vela completa está disponible al terminar su intervalo más el margen de 15 s (§12.1, punto 4). Así, las de 1 h y de 4 h que terminan a la vez están disponibles a la vez.

**Señal.** En la vela $t$ de $\tau_s$, en el instante $D_s$:
- $D_s$ es la disponibilidad más tardía de los valores de $\tau_s$ que usa la ficha, en $t$ y en sus velas anteriores.
- Si en $D_s$ se cumplen el contexto y la preparación, hay señal. Su clave es (familia, versión, instrumento, $\tau_s$, apertura de $t$, $d$) (§1.3).
- Con la señal se guardan el régimen de su vela de contexto y el de $t$, y su porcentaje solo REST (§12.1, punto 5).
- Si el ATR que fija el stop no es positivo, no hay señal.

**Disparo.** Las velas de ejecución de la señal son las $K$ de $\tau_e$ que empiezan al terminar $t$: $e_1, \dots, e_K$.
- Se recorren en orden, y **sus instantes nunca retroceden** (F3E3-01): $D_0 = D_s$, y $e_i$ se mira en $D_i = \max(D_s,\ D_{i-1},\ \text{resolución de } e_i)$.
- Una vela se **resuelve** cuando está disponible o cuando se sabe que no será utilizable (§12.1, punto 4). En el histórico, un minuto ausente se sabe al terminar más el margen, la misma regla que la disponibilidad de los presentes [precisión de la implementación].

En cada $D_i$:
1. Si $e_i$ no es utilizable (ausente, incompleta o sin las dimensiones que necesita), la señal **caduca**.
2. Si en $D_i$ el contexto ya no se cumple, caduca.
3. Si $e_i$ cumple el disparo de la ficha, la **decisión** es en $D_i$. Si no, se pasa a $e_{i+1}$.
4. Si ninguna de las $K$ dispara, caduca.

Ejemplo: la señal está disponible a las 15:00:15, y dispara un cierre mayor que 100.
- La primera vela de ejecución cierra en 99, pero no está disponible hasta las 15:50:00.
- La segunda cierra en 101 y está disponible a las 15:30:15.
- La primera se mira a las 15:50:00 y no dispara. La segunda se mira en $D_2 = \max(15{:}00{:}15,\ 15{:}50{:}00,\ 15{:}30{:}15)$ = 15:50:00, con el contexto de ese instante.
- La decisión es a las 15:50:00, y la entrada, a las 15:51; nunca a las 15:31, cuando aún no se sabía qué hacía la primera.
- Si la primera cerrara en 101, la decisión sería con ella, también a las 15:50:00. Si no fuera utilizable, la señal caducaría, aunque la segunda cerrara en 101.
- En el histórico, cada vela está disponible al terminar más el margen, así que $D_i$ es la disponibilidad de $e_i$, y los ejemplos de las fichas no cambian.

**Stop inicial.** Es obligatorio (§6.1) y se fija en la decisión: $P_s = C^{e}_{i} - d\,m_s\,\mathrm{ATR}^{\tau_s}_t$.
- $C^{e}_{i}$ es el cierre de la vela de disparo; $m_s$, el múltiplo de la ficha; y $\mathrm{ATR}^{\tau_s}_t$, el ATR(14) de la señal en $t$.
- Así, el stop queda siempre del lado de la pérdida respecto a la decisión.
- Un stop que no es positivo impide la entrada (F3E3-02; véase «Ejecución»).

**Ejecución** (regla causal de F3-E4, `08` §3):
- El precio de entrada $P_e$ es la apertura de la primera vela de 1 minuto que empieza después de la decisión. Si esa vela falta, la señal caduca (CD-03).
- **No se entra** en ninguno de estos casos:
  - si $d\,(P_e - P_s) \le 0$, porque el precio ya pasó el stop (requisito de §6.2.5);
  - si $P_s \le 0$ (F3E3-02);
  - con un objetivo $T$, si $T \le 0$ (F3E3-02) o si $d\,(T - P_e) \le 0$.
- **Los niveles nunca se recortan.** Un stop o un objetivo que no es positivo no lo puede tocar ningún precio, porque la ingesta rechaza toda vela con un precio que no sea positivo (`kbot.kucoin.models`).
  - Ejemplo: con un cierre de disparo de 1, un ATR de 1, $m_s$ = 2 y una entrada a 1, un largo tendría el stop en −1. Un breakout corto tendría el stop en 3 y el objetivo en −3.
  - En los dos casos no se entra, aunque $r$ = 2 > 0.
- El riesgo por unidad es $r = d\,(P_e - P_s) > 0$. Los resultados se miden en múltiplos de $r$ (R; §12).
- Esta regla precisa la «decisión en la vela cerrada t y ejecución en t+1» del backtesting de §12.

**Salidas.** Son de tres tipos:
- **Por nivel** (stop y objetivo): se miran en las velas de 1 minuto desde la de entrada, incluida.
  - El stop de un largo se toca si $L \le P_s$, y el de un corto, si $H \ge P_s$. El objetivo de un largo, si $H \ge T$, y el de un corto, si $L \le T$.
  - Si en un mismo minuto se tocan los dos, cuenta el stop.
  - Un minuto ausente no se puede mirar; se mira el siguiente presente.
  - El precio de la salida lo fija F3-E4. Los stops nunca esperan al cierre de una vela (§3.4 [H13]).
- **Por cierre** (invalidación): se mira en la temporalidad que diga la ficha, en cada vela que termina después de la entrada, en el instante de su disponibilidad.
  - Se sale en la apertura de la primera vela de 1 minuto **presente** que empieza después de ese instante. Una salida no caduca.
  - Si la vela de la ficha no es utilizable o le falta algún valor, no se mira, y la posición sigue con su stop.
- **Por tiempo:** solo en la reversión a la media.

**El stop solo se acerca al precio, nunca se aleja.** En la tendencia, con el arrastre; en las otras dos, queda fijo. Así se cumple D13, se decida lo que se decida.

**Una posición por familia e instrumento.** Mientras está abierta, las señales nuevas de la familia en ese instrumento se registran, pero ni amplían la posición ni la cierran (D20 sigue pendiente). Lo que pasa entre familias lo decide el combinador de F3-E4.

**Aritmética.** Los niveles ($P_s$ y $T$) se calculan en `float`, como los indicadores (§12.2, punto 1), y se comparan con los precios de las velas convertidos igual. Su redondeo al tick del instrumento es de la F4, con el tamaño de la posición.

**Volumen.** Solo los usos invariantes a escala de §12.1, punto 7: el volumen relativo (breakout) y el VWAP (reversión a la media).

**Fuera de las fichas:**
- el tamaño de la posición (§6.2.5, F4);
- los costes: fijos y escritos en el informe exploratorio de F3-E5, y reales en la F5;
- los filtros opcionales de D15, para los que el régimen dará datos (`08` §6);
- $T_{max}$ de D30, que no es la salida por tiempo de la reversión a la media.

#### 12.3.2 Régimen (F3-A7)

En cada vela $t$ de una temporalidad, el régimen tiene dos etiquetas, y cada una puede quedar **sin valor**. Es determinista: sale solo de indicadores de §12.2, así que los mismos datos dan el mismo régimen.

**Dirección**, con el ADX(14) de $t$:
- `tendencia`, si ADX ≥ 25;
- `rango`, si ADX ≤ 20;
- `transicion`, si está entre los dos;
- sin valor, si el ADX no lo tiene.

**Volatilidad**, con dos medidas sin unidades:
- **ATR relativo:** $\mathrm{NATR}_t = \mathrm{ATR}(14)_t / C_t$.
- **Anchura de Bollinger:** $\mathrm{BW}_t = (\text{banda superior} - \text{banda inferior}) / \text{media}$, con Bollinger(20, 2).

Cada una se compara con sus $W$ = 100 últimos valores, de $t - 99$ a $t$, que tienen que existir todos:
- **Expansión:** el NATR de $t$ es **estrictamente mayor** que al menos el 80 % de los valores de su ventana: $100\cdot\#\{i : \mathrm{NATR}_i < \mathrm{NATR}_t\} \ge 80\,W$.
- **Compresión:** la BW de $t$ es **estrictamente menor** que al menos el 80 % de los de su ventana: $100\cdot\#\{i : \mathrm{BW}_i > \mathrm{BW}_t\} \ge 80\,W$.

La etiqueta es `expansion` si se cumple la primera, `compresion` si solo se cumple la segunda, y `normal` si no se cumple ninguna. Queda sin valor si falta alguna de las dos ventanas.
- **Los empates nunca hacen un extremo.** En una serie plana ninguna medida supera a otra, así que la volatilidad es `normal`, nunca expansión ni compresión.
- La cuenta es entera y la comparación, exacta. Con $W$ = 100, hay expansión con 80 o más valores menores, y compresión con 80 o más mayores.
- **Calentamiento:** la dirección necesita las 28 velas utilizables y consecutivas del ADX. La volatilidad necesita 119, porque la BW da su primer valor en la vela 20 y faltan otros 99. Tras un reinicio, vuelven a hacer falta.
- Las entradas, la procedencia y la disponibilidad del régimen son las de sus indicadores (§12.2, punto 1).
- Cada ficha usa una etiqueta y deja la otra para los informes: la tendencia y la reversión a la media usan la dirección, y el breakout, la volatilidad.

**Ejemplos** (las pruebas usan también $W$ = 100):
- Un ADX de 25,00 da `tendencia`; uno de 24,99, `transicion`; y uno de 20,00, `rango`.
- Con $W$ = 5, para que quepa, y BW de 0,050, 0,041, 0,038, 0,045 y 0,036: cuatro valores son mayores que 0,036, y $400 \ge 400$, así que hay compresión. Si el último fuera 0,038, empataría con el tercero: solo tres serían mayores, y no habría compresión.
- Con $W$ = 100, 80 valores menores dan expansión, y 79, no. Un NATR en expansión con una BW en compresión da `expansion`.

#### 12.3.3 Versión de la estrategia

`strategy_version` = commit + hash de los parámetros (§12):
- **Commit:** los 40 caracteres hexadecimales del commit del código que se ejecuta. Si el árbol tiene cambios sin confirmar, no hay versión, y nada se registra como de una versión. Los archivos que git no sigue no forman parte de ningún commit y no cuentan [precisión de la implementación].
- **Parámetros:** un objeto JSON con la familia, sus temporalidades, el régimen y cada parámetro de la ficha. Los periodos y los recuentos son enteros. Los umbrales y los múltiplos son `Decimal`, en texto canónico (§5.7).
- **Hash:** SHA-256, en hexadecimal, del JSON canónico de RFC 8785, calculado con `kbot.core.jcs`, como el de `config.risk_limits`.
- **Texto:** `<commit>+<hash>`.

Las tres fichas llevan los mismos parámetros del régimen:

```json
"regimen": {"adx": 14, "adx_tendencia": "25", "adx_rango": "20", "atr": 14, "bollinger": 20, "bollinger_k": "2", "ventana": 100, "expansion_mayor_que": "80", "compresion_menor_que": "80"}
```

Cada ficha da los suyos y su hash, y las pruebas reproducen los tres.

#### 12.3.4 Ficha de tendencia [diseño **APROBADO** por el revisor (`1974a25`); **APROBADA por el propietario** el 20 nov 2026]

**En pocas palabras.** Cuando el gráfico de 4 h tiene una tendencia fuerte, espera a que el de 1 h retroceda hasta su media y vuelva a coger impulso, y entra si una vela de 15 minutos lo confirma. El stop empieza 2 ATR por detrás y sube con el precio, nunca baja. Sale cuando se acaba la tendencia de 4 h.

| Temporalidad | Papel | Largo ($d$ = +1) | Corto ($d$ = −1, solo Futures) |
|---|---|---|---|
| 4 h | Contexto, en la vela de contexto $c$ | EMA(20) > EMA(50), dirección `tendencia` y +DI > −DI | EMA(20) < EMA(50), `tendencia` y −DI > +DI |
| 1 h | Señal, en la vela $t$ | (a) $C_t$ > EMA(50)$_t$; (b) retroceso: $L_i \le$ EMA(20)$_i$ en alguna $i$ de $t-4$ a $t$; (c) el histograma del MACD(12, 26, 9) pasa de $\le 0$ en $t-1$ a $> 0$ en $t$ | (a) $C_t$ < EMA(50)$_t$; (b) $H_i \ge$ EMA(20)$_i$ en alguna $i$; (c) pasa de $\ge 0$ a $< 0$ |
| 15 m | Disparo, con $K$ = 4 | $C^{e}_i > H_t$ | $C^{e}_i < L_t$ |

- Todos los valores que usan (a), (b) y (c) tienen que existir: la EMA(20) de $t-4$ a $t$, y el MACD en $t-1$ y en $t$.
- **Stop inicial:** $m_s$ = 2, con el ATR(14) de 1 h en $t$.
- **Arrastre:** en cada vela $u$ de 1 h que termina después de la entrada, $P_s \leftarrow \max(P_s,\ C_u - 3\,\mathrm{ATR}(14)_u)$ en un largo, y $\min(P_s,\ C_u + 3\,\mathrm{ATR}(14)_u)$ en un corto.
  - Vale para los minutos que empiezan después de la disponibilidad de $u$.
  - Si $u$ no es utilizable o no tiene ATR, el stop no cambia.
- **Invalidación**, en cada vela de 4 h que termina después de la entrada: la EMA(20) deja de estar del lado de la posición (en un largo, EMA(20) ≤ EMA(50)), o la dirección pasa a `rango`.
- **Sin objetivo fijo ni salida por tiempo:** la tendencia sale por el stop o por la invalidación.
- **El 1 d del resumen de §12 no se usa en esta versión.** Una vela diaria exige sus 1.440 minutos (§12.1, punto 4), y la EMA(50) diaria, 50 días seguidos completos. El informe exploratorio puede comparar esa variante.

**Parámetros** (hash `8866b51449f119568d5012acb47487d37d411b66fda8d615661564b144fa36d3`):

```json
{"familia": "tendencia", "temporalidades": {"contexto": "4h", "senal": "1h", "ejecucion": "15m"}, "regimen": {…}, "contexto": {"ema_rapida": 20, "ema_lenta": 50}, "senal": {"ema_rapida": 20, "ema_lenta": 50, "retroceso": 5, "macd": [12, 26, 9], "atr": 14}, "ejecucion": {"velas": 4}, "stop": {"multiplo_atr": "2"}, "arrastre": {"multiplo_atr": "3"}}
```

**Datos que exige:**
- Velas de 4 h, de 1 h y de 15 m con P + T (§3.4), y las de 1 minuto para ejecutar y salir.
- Calentamiento: 50 velas de 4 h y 50 de 1 h, utilizables y consecutivas, por la EMA(50). Lo demás necesita menos.

**Ejemplo 1: largo en Futures ETHUSDTM.** Los valores de los indicadores se dan; no se calculan.
1. **Señal:** vela de 1 h [14:00, 15:00), con $D_s$ = 15:00:15.
   - Contexto en la vela de 4 h [08:00, 12:00): EMA(20) 3.052,40 > EMA(50) 2.987,10; ADX 27,80, `tendencia`; y +DI 26,50 > −DI 13,20.
   - (a) $C_t$ = 3.041,20 > EMA(50) = 3.002,60.
   - (b) En [12:00, 13:00), $L$ = 3.019,00 ≤ EMA(20) = 3.021,75.
   - (c) El histograma vale −0,85 en $t-1$ y 1,30 en $t$.
   - Además, $H_t$ = 3.046,00 y el ATR es 12,50. Hay señal larga.
2. **Disparo:**
   - [15:00, 15:15) cierra en 3.043,80, que no supera 3.046,00.
   - [15:15, 15:30) cierra en 3.048,60, que sí. La decisión es en $D$ = 15:30:15, con el mismo contexto.
   - Stop: $3.048{,}60 - 2 \times 12{,}50 = 3.023{,}60$.
3. **Entrada:** la vela de 1 minuto de las 15:31 abre en 3.049,10, así que $r$ = 25,50.
4. **Arrastre:**
   - [15:00, 16:00): 3.061,00 − 3 × 12,60 = 3.023,20, sin cambio.
   - [16:00, 17:00): 3.092,00 − 3 × 13,00 = 3.053,00, que es el stop desde las 17:00:15.
   - [17:00, 18:00): 3.085,40 − 3 × 13,20 = 3.045,80, sin cambio.
   - La vela de 4 h [12:00, 16:00), a las 16:00:15, no invalida: la EMA(20) sigue por encima y el ADX es 26,10.
5. **Salida:** el minuto de las 18:12 tiene $L$ = 3.051,50 ≤ 3.053,00, así que toca el stop. Al nivel del stop, el resultado es $+3{,}90/25{,}50 \approx +0{,}15$ R, antes de costes.

**Variantes del ejemplo 1:**
- Si el stop no se toca y la vela de 4 h [16:00, 20:00) da ADX 19,60 (`rango`), se sale en la apertura de las 20:01.
- Si los cuatro cierres de 15 m son 3.043,80, 3.045,90, 3.040,10 y 3.046,00, ninguno supera 3.046,00 (el último empata), y la señal caduca.
- Si la vela [15:15, 15:30) es incompleta, la señal caduca, aunque la siguiente disparara.
- Si el minuto de las 15:31 abre en 3.020,00, que no supera el stop de 3.023,60, no se entra. Si ese minuto falta, la señal caduca.
- Con un ADX de 4 h de 25,00 hay señal; con 24,99, no.
- Las mismas condiciones en corto no dan señal en Spot ETH-USDT, y sí en Futures.

#### 12.3.5 Ficha de breakout [diseño **APROBADO** por el revisor (`1974a25`); **APROBADA por el propietario** el 20 nov 2026]

**En pocas palabras.** Cuando el precio de 1 h ha estado comprimido (bandas de Bollinger estrechas) y cierra fuera de una resistencia o de un soporte con más volumen del habitual, entra a favor de la ruptura si una vela de 15 minutos la confirma. En 4 h solo pide no ir contra la media lenta. El stop queda a 2 ATR y el objetivo, a dos veces el riesgo. Sale si el precio vuelve al otro lado del nivel roto.

| Temporalidad | Papel | Largo | Corto (solo Futures) |
|---|---|---|---|
| 4 h | Contexto | $C_c$ > EMA(50)$_c$ | $C_c$ < EMA(50)$_c$ |
| 1 h | Señal | (a) volatilidad `compresion` en alguna vela de $t-5$ a $t-1$; (b) $C_t > R_{t-1}$, la resistencia(5, 100) de $t-1$, que tiene que existir; (c) volumen relativo(20)$_t$ ≥ 1,5 | (a) igual; (b) $C_t < S_{t-1}$, el soporte(5, 100) de $t-1$; (c) igual |
| 15 m | Disparo, con $K$ = 4 | $C^{e}_i > H_t$ | $C^{e}_i < L_t$ |

- (b) es el **primer cierre fuera del nivel**, porque ningún cierre hasta $t-1$ ha superado un nivel vigente en $t-1$ (§12.2).
- **Stop inicial:** $m_s$ = 2, con el ATR(14) de 1 h en $t$.
- **Objetivo:** $T = P_e + 2\,d\,r$, fijado al entrar. Si $T \le 0$, no se entra (§12.3.1, «Ejecución»; F3E3-02).
- **Invalidación**, en cada vela de 1 h que termina después de la entrada: el precio vuelve al otro lado del nivel roto, que queda fijo. En un largo, $C \le R_{t-1}$; en un corto, $C \ge S_{t-1}$.
- **Sin salida por tiempo.**

**Parámetros** (hash `eac620fee86fed9ae4e3f5eeee8ded14986311445ccd71843bf5597d6ae94917`):

```json
{"familia": "breakout", "temporalidades": {"contexto": "4h", "senal": "1h", "ejecucion": "15m"}, "regimen": {…}, "contexto": {"ema_lenta": 50}, "senal": {"pivote_k": 5, "pivote_m": 100, "compresion_velas": 5, "volumen_relativo": 20, "volumen_relativo_minimo": "1.5", "atr": 14}, "ejecucion": {"velas": 4}, "stop": {"multiplo_atr": "2"}, "objetivo": {"multiplo_r": "2"}}
```

**Datos que exige:**
- Velas de 1 h con P + T, y con P + V + T para el volumen relativo (§3.4: «breakout con volumen»). Velas de 4 h y de 15 m con P + T, y las de 1 minuto para ejecutar y salir.
- Calentamiento: 120 velas de 1 h, porque la etiqueta de volatilidad en $t-1$ necesita 119; y 50 de 4 h.
- El volumen relativo compara volúmenes solo dentro del instrumento. En Futures, se supone constante el multiplicador (§12.2, punto 3).

**Ejemplo 2: corto en Futures SOLUSDTM.**
1. **Señal:** vela de 1 h [09:00, 10:00), con $D_s$ = 10:00:15.
   - Contexto en la vela de 4 h [04:00, 08:00): $C$ = 142,30 < EMA(50) = 145,80.
   - (a) La vela [06:00, 07:00) está en `compresion`: su BW es menor que 86 de las 100 de su ventana, y su NATR solo supera a 41 valores.
   - (b) $S_{t-1}$ = 140,00 y $C_t$ = 139,20.
   - (c) El volumen relativo es 2,10.
   - Además, $L_t$ = 138,90 y el ATR es 0,80. Hay señal corta.
2. **Disparo:**
   - [10:00, 10:15) cierra en 139,40, que no baja de 138,90.
   - [10:15, 10:30) cierra en 138,70, que sí. La decisión es a las 10:30:15.
   - Stop: $138{,}70 + 2 \times 0{,}80 = 140{,}30$.
3. **Entrada:** la vela de las 10:31 abre en 138,65. Así, $r$ = 1,65 y $T = 138{,}65 - 2 \times 1{,}65 = 135{,}35$.
4. **Invalidación:**
   - [10:00, 11:00) cierra en 138,20, por debajo de 140,00, y la posición sigue.
   - [11:00, 12:00) cierra en 140,10 ≥ 140,00: el precio ha vuelto. Hasta entonces, el stop no se ha tocado (máximo 140,25 < 140,30).
5. **Salida:** en la apertura de las 12:01, a 140,05. El resultado es $-1{,}40/1{,}65 \approx -0{,}85$ R, antes de costes.

**Variantes del ejemplo 2:**
- Si antes el mínimo de un minuto llega a 135,35, sale por el objetivo: +2 R, antes de costes.
- Con un volumen relativo de 1,50 hay señal; con 1,49, no.
- Con $C_t$ = 140,00 no hay ruptura, porque tiene que ser estrictamente menor.
- Si ninguna vela de $t-5$ a $t-1$ está en `compresion`, no hay señal.
- En Spot, la ruptura a la baja no da señal, y la alcista, sí.

#### 12.3.6 Ficha de reversión a la media [diseño **APROBADO** por el revisor (`1974a25`); **APROBADA por el propietario** el 20 nov 2026]

**En pocas palabras.** Cuando el gráfico de 1 h no tiene tendencia y el de 15 minutos se aleja mucho de su media, entra esperando que vuelva a ella. «Mucho» es salir de las bandas de Bollinger, con el RSI en un extremo y lejos del VWAP del día. Entra si una vela de 5 minutos lo confirma. El objetivo es la media y el stop, 2 ATR. Sale si aparece una tendencia, si se rompe el rango o a las 4 horas.

| Temporalidad | Papel | Largo | Corto (solo Futures) |
|---|---|---|---|
| 1 h | Contexto | Dirección `rango`, y $C_c$ dentro de sus bandas de Bollinger(20, 2) | Igual |
| 15 m | Señal | (a) $C_t$ < banda inferior$_t$; (b) RSI(14)$_t$ ≤ 30; (c) VWAP$_t - C_t \ge 1 \cdot$ ATR(14)$_t$ | (a) $C_t$ > banda superior$_t$; (b) RSI(14)$_t$ ≥ 70; (c) $C_t$ − VWAP$_t \ge$ ATR(14)$_t$ |
| 5 m | Disparo, con $K$ = 3 | banda inferior$_t$ < $C^{e}_i$ < $T$ | $T$ < $C^{e}_i$ < banda superior$_t$ |

- **Objetivo:** $T$ es la media de Bollinger de 15 m en $t$, la SMA(20), fijada en la señal. El disparo exige que el cierre quede entre la banda y $T$.
- «Dentro de sus bandas» es banda inferior ≤ $C_c$ ≤ banda superior.
- El VWAP es el del día UTC en las velas de 15 m (§12.2). Sin VWAP no hay señal.
- **Stop inicial:** $m_s$ = 2, con el ATR(14) de 15 m en $t$.
- **Invalidación**, en cada vela de 1 h que termina después de la entrada: la dirección pasa a `tendencia`, o el cierre sale de su banda en contra de la posición. En un largo, $C$ < banda inferior; en un corto, $C$ > banda superior.
- **Salida por tiempo:** en la apertura de la primera vela de 1 minuto presente que empieza 240 minutos o más después de la de entrada.

**Parámetros** (hash `adee5c8a2a900e797bf62abbc22aef3f441de36ffd79e4425f72a7087bad63d1`):

```json
{"familia": "reversion_media", "temporalidades": {"contexto": "1h", "senal": "15m", "ejecucion": "5m"}, "regimen": {…}, "contexto": {"bollinger": 20, "bollinger_k": "2"}, "senal": {"bollinger": 20, "bollinger_k": "2", "rsi": 14, "rsi_largo": "30", "rsi_corto": "70", "atr": 14, "distancia_vwap_atr": "1"}, "ejecucion": {"velas": 3}, "stop": {"multiplo_atr": "2"}, "salida_tiempo_minutos": 240}
```

**Datos que exige:**
- Velas de 15 m con P + T, y con P + V + T para el VWAP: todas las del día hasta $t$ tienen que ser utilizables.
- Velas de 1 h y de 5 m con P + T, y las de 1 minuto para ejecutar y salir.
- Calentamiento: 20 velas de 15 m, por Bollinger, y 28 de 1 h, por el ADX.

**Ejemplo 3: largo en Spot LINK-USDT.**
1. **Señal:** vela de 15 m [13:15, 13:30), con $D_s$ = 13:30:15.
   - Contexto en la vela de 1 h [12:00, 13:00): ADX 17,40, `rango`; y $C$ = 19,95, entre las bandas 19,60 y 20,30.
   - (a) $C_t$ = 19,62 < banda inferior 19,70.
   - (b) El RSI es 27,50.
   - (c) El VWAP es 20,05: 20,05 − 19,62 = 0,43 ≥ ATR = 0,18.
   - La media es $T$ = 20,10. Hay señal larga.
2. **Disparo:**
   - [13:30, 13:35) cierra en 19,66, sin superar 19,70.
   - [13:35, 13:40) cierra en 19,74, entre 19,70 y 20,10. La decisión es a las 13:40:15.
   - Stop: $19{,}74 - 2 \times 0{,}18 = 19{,}38$.
3. **Entrada:** la vela de las 13:41 abre en 19,75. Así, $r$ = 0,37, y $T - P_e$ = 0,35 > 0.
4. **Invalidación:**
   - [13:00, 14:00): ADX 18,20, y $C$ = 19,90 ≥ banda inferior 19,55. Sigue.
   - [14:00, 15:00): ADX 18,90, y $C$ = 20,02 ≥ 19,58. Sigue.
5. **Salida:** el minuto de las 15:07 tiene $H$ = 20,11 ≥ 20,10 y toca el objetivo. El resultado es $+0{,}35/0{,}37 \approx +0{,}95$ R, antes de costes.

**Variantes del ejemplo 3:**
- Si la vela de 1 h [13:00, 14:00) da ADX 25,30 (`tendencia`), se sale en la apertura de las 14:01.
- Si no pasa nada más, se sale en la apertura de la vela de las 17:41, o de la primera presente después.
- Con un RSI de 30,00 hay señal; con 30,01, no. Con VWAP − $C$ = 0,18, exactamente un ATR, también hay señal.
- Un cierre de 5 m de 20,12 ≥ $T$ no dispara.
- Si el día tiene una vela de 15 m no utilizable antes de $t$, el VWAP no tiene valor y no hay señal.
- En Spot, el corto no da señal.

#### 12.3.7 Pruebas (F3-A6 y F3-A7)

- **F3-A6, los ejemplos:** cada ejemplo de las tres fichas, con sus variantes, se reproduce dando a las funciones de la ficha los mismos valores. Los niveles se comparan con la tolerancia de precio de §12.2, punto 5, porque en `float`, por ejemplo, $138{,}70 + 2 \times 0{,}80$ da 140,29999999999998.
- **F3-A6, además:**
  - los tres hashes de los parámetros;
  - en Spot nunca hay cortos;
  - sin un valor necesario, no hay señal;
  - la vela de contexto es la última terminada en $D$; si no está disponible, no hay contexto, y nunca se usa una anterior. Se vuelve a mirar en cada disparo;
  - una vela de ejecución no utilizable hace caducar la señal;
  - **los instantes del disparo nunca retroceden** (F3E3-01): el ejemplo de §12.3.1, sin ninguna decisión antes de las 15:50:00, y sus variantes, con la primera vela que dispara o que no es utilizable;
  - el stop queda del lado de la pérdida y solo se acerca;
  - no se entra con el precio al otro lado del stop o del objetivo;
  - **no se entra con un stop o un objetivo cero o negativo** (F3E3-02): los dos casos de §12.3.1 y un nivel exactamente 0; con niveles positivos, los ejemplos de las fichas dan lo mismo;
  - si el stop y el objetivo se tocan en el mismo minuto, gana el stop.
- **Sin anticipación** (F3-A4, en lo que toca a las señales): cambiar todos los datos posteriores a $D_s$ no cambia la señal, y cambiar los posteriores a la decisión no cambia ni la decisión ni el stop. El envenenamiento del motor entero es de F3-E4 (F3-A9).
- **F3-A7:**
  - las fronteras del ADX (20 y 25) y de las cuentas (79 y 80 de 100);
  - los empates y la serie plana, que da `normal`;
  - la precedencia de la expansión;
  - la falta de valores en la ventana, con el calentamiento de 119 velas tras un reinicio;
  - el mismo régimen con los mismos datos, también con los minutos cargados en otro orden.

#### 12.3.8 Qué se programa y qué decide el propietario

- **Se programa en F3-E3, después de las aprobaciones:**
  - el régimen y la versión;
  - las reglas de cada ficha aprobada, como funciones puras sobre los valores de los indicadores: la preparación, el disparo, los niveles, el arrastre y las invalidaciones.
- **Es de F3-E4:** guardar las señales, el combinador, el motor de backtest con sus posiciones, los precios de salida y el envenenamiento del motor entero.
- **El propietario aprueba cada ficha por separado**, después de la revisión (`08` §6). Puede aprobarla con los parámetros de partida o pedir otros; si cambian, cambia su hash.
- **Esta sección no decide:**
  - D13, porque el stop solo se acerca y vale con cualquier decisión;
  - D15, porque no añade filtros opcionales;
  - D20, porque no amplía posiciones;
  - $T_{max}$ de D30;
  - ni D16, D28 ni D12, que son de la F8.
- **Los parámetros de partida son valores habituales, elegidos sin mirar el histórico.** No se ha consultado ninguna partición de F3-AL-03 para fijarlos.

### 12.4 Señales, combinador y motor de backtest de F3-E4 [diseño del 20 nov 2026, **APROBADO** por el revisor en la tercera revisión (commit `0c5d098`): F3E4-01 a F3E4-03 resueltos. Programado en `src/kbot/backtest/` y en `kbot.strategies` (§18) y **APROBADO** por el revisor en la segunda revisión del código (`676711b`): F3E4-04 y F3E4-05 resueltos]

Fija cómo se ejecutan en el tiempo las señales de las fichas de §12.3, cómo se combinan las de varias familias en un instrumento y cómo funciona el motor de backtest de la F3 (`08`, F3-E4). No cambia ninguna regla de §12.3. **El modelo de ejecución es simple y declarado:** el simulador de la F5 (§7) lo sustituirá, con su libro, su latencia y sus estados de resultado.

#### 12.4.1 La señal y sus cuatro instantes

- **Unicidad:** una señal por (familia, versión, instrumento, temporalidad, vela, dirección) (§1.3). El motor lo comprueba y se detiene con un error si se repite una clave. La unicidad en la base de datos es de F3-E5 (F3-A10).
- **Los cuatro instantes** (F3-AL-02):
  1. el **fin** del intervalo de la vela de señal $t$;
  2. su **disponibilidad**, $D_s$ (§12.3.1);
  3. la **decisión**, $D$, al final del disparo (§12.3.1);
  4. la **ejecución**: la apertura del primer minuto que empieza después de $D$. Si ese minuto falta, la señal caduca (CD-03).
- Siempre: fin + margen ≤ $D_s$ ≤ $D$ < ejecución. Se comprueba en cada operación (F3-A9).
- **Referencia idealizada:** la apertura del primer minuto de la vela $t+1$, que empieza antes de que la señal exista. Si aparece, va aparte y rotulada «idealizada». Nunca se usa para decidir ni en los resultados.

#### 12.4.2 Posiciones y ocupación

- **Una posición** nace de una decisión ejecutada: su familia, su señal, su dirección, su precio de entrada $P_e$, su riesgo por unidad $r$ (§12.3.1) y su stop, que solo se acerca. Sale por las reglas de su ficha: nivel (stop u objetivo), cierre (invalidación) o tiempo.
- **Ocupación.** Una posición ocupa su instrumento en el intervalo **[decisión, liberación)**: desde el instante de su decisión, incluido, hasta el de su **liberación**, excluido (F3E4-01). En el instante de la liberación, el instrumento ya está libre. La liberación es la disponibilidad del minuto en que se resuelve:
  - en una salida por nivel durante el minuto $m$: la disponibilidad de $m$, al terminar más el margen;
  - en una salida por cierre o por tiempo en la apertura de $m$: también la disponibilidad de $m$;
  - si la entrada no llega a hacerse (falta el minuto de ejecución, o no pasa los controles de §12.3.1): la disponibilidad de ese minuto, también si falta (en el histórico, al terminar más el margen; §12.3.1).
- Así, el motor nunca usa el precio de un minuto antes de que esté disponible. Entre la decisión y la ejecución, la posición ya ocupa el instrumento.

#### 12.4.3 El combinador (F3-A8)

**Una posición neta por instrumento** (§12). El combinador recorre los instantes en orden. En cada instante $X$ (F3E4-01):
1. **Primero, las resoluciones de $X$:** las salidas y las entradas fallidas cuyo minuto está disponible en $X$. Las posiciones que se liberan en $X$ dejan libre el instrumento.
2. **Después, todas las decisiones de $X$ juntas**, de todas las familias:
   - **Ocupado:** si en $X$ hay una posición que ocupa el instrumento, no se ejecuta ninguna, del mismo sentido o del contrario. No se amplía (D20 sigue pendiente) y no se da la vuelta. La posición sigue con sus salidas.
   - **Señales opuestas:** si hay decisiones de los dos sentidos, no se ejecuta ninguna (§12).
   - **Del mismo sentido:** se ejecuta una sola, la de la familia que va antes en el orden fijo tendencia, breakout y reversión a la media, y, dentro de una familia, la de la vela de señal más antigua. El orden de familias solo sirve para este desempate.
3. Cada decisión no ejecutada queda registrada con su motivo: `ocupado`, `opuestas` o `posicion_tomada`.

El resultado no depende del orden en que lleguen los sucesos de un mismo instante.

**Modo por familia:** el motor puede correr una familia sola, con las mismas reglas de posición y de ocupación. Es como una cuenta sombra (las contables son de la F5) y es lo que usará el informe exploratorio de cada familia (F3-E5).

#### 12.4.4 El modelo de ejecución de la F3

- **Entrada**, a mercado en la apertura del minuto de ejecución. Los controles de §12.3.1 (stop y objetivo positivos y del lado correcto) se hacen con esa apertura, $P_e$.
- **Stop**, como orden stop a mercado. Se dispara en el primer minuto presente, desde el de entrada, con $L \le P_s$ (un largo) o $H \ge P_s$ (un corto). El precio es el del stop, o la apertura del minuto si ya abre al otro lado del stop: con hueco, a la apertura (§7).
- **Objetivo**, como orden limitada. Se toca con $H \ge T$ (un largo) o $L \le T$ (un corto), y se ejecuta a $T$, nunca a un precio mejor. Si el stop y el objetivo se tocan en el mismo minuto, cuenta el stop (§12.3.1).
- **Salidas por cierre y por tiempo**, a mercado:
  - por cierre, en la apertura del primer minuto presente que empieza después del instante de la invalidación;
  - por tiempo, en la apertura del primer minuto presente que empieza en su límite o después (§12.3.6).
- **Sin precio observado no hay ejecución** (§7). Un minuto ausente no ejecuta nada; se espera al siguiente presente. La entrada es la excepción: su minuto es uno solo, y si falta, la señal caduca (CD-03).
- **Prioridad dentro de un minuto:** primero la salida a mercado pendiente, en la apertura; después, el stop y el objetivo del minuto. Un stop actualizado por el arrastre vale para los minutos que empiezan después de su instante (§12.3.4).
- **Costes:**
  - una comisión $f$ por lado, sobre el nocional;
  - un deslizamiento $s$ en cada ejecución: una compra a $P(1+s)$ y una venta a $P(1-s)$.

  Son fijos, y sus valores los fija y los escribe F3-E5. El funding de Futures no entra en la F3 (F5): cada informe lo declara.
- **Resultado** de una operación, por unidad y en múltiplos de $r$:
  - bruto: $d\,(P_x - P_e)/r$, con los precios sin costes;
  - neto: $\big[d\,(P'_x - P'_e) - f\,(P'_e + P'_x)\big]/r$, con los precios con deslizamiento $P'$.
- **Ventana:** el motor corre sobre $[inicio, fin)$ (F3E4-02).
  - **Solo recibe los minutos anteriores a $fin$.** Nada de lo que viene después entra en ningún cálculo. Las velas anteriores a $inicio$ solo sirven para calentar los indicadores.
  - Actúa sobre las decisiones con $D$ en la ventana **y con su minuto de ejecución también en ella**. Si ese minuto empieza en $fin$ o después, la decisión no se ejecuta (`fin_de_ventana`) y no ocupa el instrumento.
  - Tampoco hay salidas en $fin$ o después: una posición que aún espera su salida a mercado se liquida como sigue.
  - **La ventana se resuelve**, para una posición abierta, cuando están resueltos todos sus minutos desde el de la entrada: cada uno disponible, o sabido ausente (en el histórico, al terminar más el margen). Ese instante es el máximo de sus resoluciones (residual de F3E4-02).
    - Con las disponibilidades en orden, como en el histórico, es $fin$ más el margen.
    - Si alguna llega tarde, la resolución también.
  - **Liquidación de evaluación.** Una posición que sigue abierta cuando se resuelve la ventana se liquida al cierre del último minuto presente de la ventana, con deslizamiento. Va rotulada «liquidación de fin de ventana».
  - **Su instante de conocimiento es el de esa resolución**, no el del minuto liquidado: elegir el último minuto presente y valorarlo exige conocer todos esos minutos.
  - Las particiones y el *walk-forward* son de F3-E5.

#### 12.4.5 El motor

- **Por instrumento**, y cada uno por separado: en la F3 no hay riesgo entre instrumentos (F4).
- **La misma lógica que el paper trading** (§0, principio 3): las fichas, el combinador y las reglas de posición son las mismas funciones. Solo cambian el reloj y la capa de datos.
  - **Datos:** en la F3, los minutos del histórico (F3-E1), con la disponibilidad de §12.1, punto 4. De ellos salen los marcos de §12.3.
  - **Reloj:** los instantes de disponibilidad, en orden.
- **Orden de los sucesos.** Se procesan por instante. En cada uno, primero las resoluciones y liberaciones; después, los cambios del stop y las invalidaciones; y al final, todas las decisiones juntas, con el combinador (§12.4.3; F3E4-01). Dentro de cada grupo, el resultado no depende del orden de llegada, así que es determinista.
- **Registro de la ejecución:**
  - la versión de cada familia (§12.3.3), o «sin versión»;
  - el instrumento, la ventana, el modelo de costes y el modo (por familia o combinado);
  - las señales, con sus cuatro instantes;
  - las decisiones, con lo que se sabe en su instante (caducada con su motivo, no ejecutada con su motivo, o elegida) y, si se eligió, el resultado de su entrada (ejecutada, `caduca_sin_minuto` o `no_entra`) con su instante de conocimiento: la disponibilidad del minuto de entrada, o, si falta, cuando se sabe (F3E4-04);
  - las operaciones, con su entrada, su stop inicial y cada cambio del stop, su salida, su motivo, sus precios, $r$ y su resultado bruto y neto;
  - **dos porcentajes solo REST** por operación (§12.1, punto 5; F3E4-03), con su procedencia por separado y sus dos recuentos, total y solo REST, guardados en el registro (F3E4-05):
    - **el de la decisión:** su procedencia (§12.3.1);
    - **el del resultado:** la de la decisión más todo lo que usó después. Son los minutos de la entrada a la salida, todos examinados, y los valores de cada cambio del stop y de cada invalidación examinada antes de la salida. Si hay liquidación de evaluación, también los minutos de la ventana hasta el liquidado. Nada posterior a la salida, y cada minuto una sola vez.
- **Instante de conocimiento** de cada suceso: el más tardío de los datos que usa.
  - Una decisión es $D$.
  - Una ejecución, la disponibilidad de su minuto.
  - Una salida, la disponibilidad de su minuto, y no antes de que se resuelvan todos los minutos desde la entrada, porque también dice que ninguno anterior la disparó. Con las disponibilidades en orden, es la de su minuto [precisión de la implementación]. La liberación (§12.4.2) es este instante.
  - Un cambio del stop, el de su vela, y no antes de que se resuelvan los minutos desde la entrada hasta el que contiene su instante, porque necesita la posición aún abierta [precisión de la implementación].
  - Una liquidación de evaluación, el de la resolución de la ventana para su posición (§12.4.4; F3E4-02).

#### 12.4.6 Ejemplos

Los costes de los ejemplos, $f$ = 0,06 % y $s$ = 0,05 %, solo sirven para ilustrar: sus valores los fija F3-E5.

**A. Una operación con costes** (la del ejemplo 1 de §12.3.4):
1. La decisión es a las 15:30:15. El minuto de las 15:31 abre en 3.049,10: $P_e$ = 3.049,10 y $r$ = 25,50. Con deslizamiento, la entrada es 3.050,62.
2. El stop sube a 3.053,00 desde las 17:00:15.
3. El minuto de las 18:12 abre en 3.054,20 y su mínimo es 3.051,50. El stop se ejecuta a su nivel, 3.053,00, porque el minuto no abre por debajo. Con deslizamiento, 3.051,47.
4. Resultado bruto: $+3{,}90/25{,}50 \approx +0{,}15$ R. Resultado neto: $\big[(3.051{,}47 - 3.050{,}62) - 0{,}0006 \times (3.050{,}62 + 3.051{,}47)\big]/25{,}50 \approx -0{,}11$ R.
5. La posición se libera a las 18:13:15.

**B. Un hueco en el stop:** un largo con el stop en 100,00. El minuto de las 12:07 abre en 99,20, con mínimo 98,90: el stop se ejecuta a la apertura, 99,20 (99,15 con deslizamiento), no a 100,00.

**C. El combinador** en un instrumento de Futures:
1. A las 10:00:15 deciden a la vez la tendencia en largo y la reversión a la media en corto: no se ejecuta ninguna (`opuestas`).
2. A las 11:00:15 decide el breakout en largo, con el instrumento libre: entra a las 11:01.
3. A las 11:15:15 decide la tendencia en largo: no se ejecuta (`ocupado`).
4. El breakout sale por su stop en el minuto de las 12:07 y libera el instrumento a las 12:08:15. Una decisión a las 12:00:15 no se ejecuta; una a las 12:15:15, sí, y entra a las 12:16.
5. Con el instrumento libre, a las 14:00:15 deciden en largo la tendencia y el breakout: entra la tendencia; el breakout queda `posicion_tomada`.

**D. El minuto de ejecución:** con una decisión a las 10:05:16, la ejecución es en la apertura del minuto de las 10:06 (CD-17). Si ese minuto falta, la señal caduca (CD-03) y el instrumento se libera a las 10:07:15.

**E. El fin de una ventana** $[inicio, fin)$, con $fin$ el 1 feb 2025 a las 00:00 (F3E4-02):
1. Una decisión a las 23:59:15 del 31 de enero no se ejecuta (`fin_de_ventana`): su minuto, las 00:00, ya no es de la ventana.
2. La ventana se resuelve a las 00:00:15. Una posición abierta se liquida al cierre del minuto de las 23:59, con deslizamiento, rotulada.
3. Si faltan los minutos de 23:31 a 23:59, se liquida al cierre del de las 23:30. Su instante de conocimiento sigue siendo las 00:00:15, no las 23:31:15.
4. Si el último presente es el minuto de las 23:58, falta el de las 23:59 (que se resuelve a las 00:00:15), y el de las 23:58 no está disponible hasta las 00:10, la liquidación se conoce a las 00:10, no a las 00:00:15 (residual de F3E4-02).

**F. Liberación y decisión a la vez** (F3E4-01):
1. Un breakout sale por su stop durante el minuto de las 11:59 y libera el instrumento a las 12:00:15.
2. Una decisión de la tendencia a las 12:00:15 encuentra el instrumento libre, porque la liberación se aplica antes, y entra a las 12:01.
3. Igual tras una entrada fallida: la del ejemplo D libera a las 10:07:15, y una decisión de las 10:07:15 entra a las 10:08.
4. Si a las 12:00:15 hubiera también una decisión en corto, no entraría ninguna (`opuestas`), en cualquier orden de llegada.

#### 12.4.7 Pruebas (F3-A8, F3-A9 y F3-A4 del motor entero)

- **F3-A8, el combinador:**
  - los cinco casos del ejemplo C y el modo por familia;
  - **F3E4-01:** el ejemplo F, con la liberación y la decisión en el mismo instante, también tras una entrada fallida, y con decisiones opuestas y del mismo sentido;
  - permutar el orden de llegada de los sucesos de un mismo instante no cambia el registro.
- **F3-A9, los instantes:**
  - en cada operación de las tres fichas, fin + margen ≤ $D_s$ ≤ $D$ < ejecución;
  - la ejecución es en el primer minuto que empieza después de la decisión (CD-17);
  - si ese minuto falta, la señal caduca (CD-03);
  - retrasar la disponibilidad de una vela retrasa su uso: la señal, la decisión y la ejecución llegan después, y ninguna antes de la nueva disponibilidad;
  - repetir la ejecución da exactamente el mismo registro.
- **F3-A4 del motor entero:** cambiar todos los datos posteriores a un instante $X$ no cambia ningún suceso cuyo instante de conocimiento sea ≤ $X$: señales, decisiones, ejecuciones, salidas y cambios del stop. Se prueba con varios $X$ sobre series de minutos.
- **F3E4-02, el fin de ventana:**
  - la decisión de las 23:59:15 del ejemplo E, sin entrada fuera de la ventana;
  - una ventana con una cola de minutos ausentes: la liquidación se conoce al resolverse la ventana;
  - cambiar los datos posteriores a un corte, también añadir un minuto en esa cola, no cambia ningún suceso conocido antes del corte;
  - el paso 4 del ejemplo E: con el minuto de las 23:58 disponible a las 00:10, la liquidación no se conoce antes. Envenenar lo posterior a un corte a las 00:05, también ese minuto, no cambia ningún suceso conocido antes del corte.
- **F3E4-03, los dos porcentajes:** con datos solo REST únicamente después de la decisión, esta conserva el 0 %. El resultado cuenta los minutos posteriores que usó y deja fuera los que no.
- **El modelo de ejecución:**
  - el hueco en el stop del ejemplo B;
  - el stop antes que el objetivo en el mismo minuto;
  - el objetivo a $T$, nunca mejor;
  - un minuto ausente no ejecuta, y se espera al siguiente presente;
  - las salidas por cierre y por tiempo;
  - los resultados en R del ejemplo A.

#### 12.4.8 Qué se programa y qué no se decide aquí

- **Se programa en F3-E4:**
  - las posiciones y su ocupación;
  - el combinador;
  - el modelo de ejecución;
  - el motor por instrumento y ventana, con el histórico de F3-E1 como capa de datos.
- **Es de F3-E5:**
  - los valores de los costes;
  - las particiones y el *walk-forward*;
  - los informes;
  - la migración `0004` (señales en la base de datos, F3-A10);
  - `svc-strategy`.
- **Esta sección no decide:**
  - D13: el stop solo se acerca;
  - D15: el combinador no es un filtro opcional;
  - D20: no se amplía ninguna posición;
  - D22: los parámetros de deslizamiento del riesgo son de la F4;
  - D30: $T_{max}$;
  - ni D12, D16 ni D28, que son de la F8.

### 12.5 Evaluación exploratoria de F3-E5 [diseño del 21 nov 2026, **APROBADO** por el revisor en la segunda revisión (commit `7eab2b4`): F3E5-01 a F3E5-04 resueltos. Programado en `src/kbot/backtest/` (§18) y **APROBADO** por el revisor en la segunda revisión del código (`2ba8e2f`), con los tres informes del desarrollo: F3E5-05 a F3E5-07 resueltos]

Fija las particiones del histórico y su uso (F3-AL-03), el *walk-forward* dentro del desarrollo, los costes del informe y el informe exploratorio de cada familia (`08`, F3-E5). **La migración `0004` (las señales en la base de datos) y `svc-strategy` son la segunda parte de F3-E5 (F3-E5b)**, con su propio diseño y su propia revisión.

#### 12.5.1 Particiones (F3-AL-03)

Son de calendario UTC y tienen **las mismas fechas para todos los instrumentos**. Parten el histórico descargado, [19 nov 2024 00:00, 19 nov 2026 22:10), por los puntos del 60 % y del 80 % de su duración, cada uno en la medianoche UTC más cercana:

| Partición | Intervalo | Días | Parte |
|---|---|---|---|
| Desarrollo | [19 nov 2024, 1 feb 2026) | 439 | 60,06 % |
| Validación | [1 feb 2026, 27 jun 2026) | 146 | 19,97 % |
| Prueba | [27 jun 2026, 19 nov 2026 22:10) | 145,92 | 19,96 % |

- **Las mismas fechas en Spot y en Futures**, aunque las velas de Futures empiezan el 1 ene 2025 y su desarrollo tiene 396 días. Los dos mercados se mueven juntos: un día de validación en uno no puede ser de desarrollo en el otro.
- **Uso** (`08` §3):
  - **Desarrollo:** toda exploración y toda decisión de la F3, como qué familias siguen o sus parámetros.
  - **Validación:** como mucho una vez por familia y versión (§12.3.3), **reservada antes de leerla** (F3E5-01):
    - La herramienta crea la reserva de forma atómica y persistente antes de leer el primer minuto de la validación: un archivo por familia y versión en `C:\kbot_historico\validacion\`, creado solo si no existe. Si dos procesos lo intentan a la vez, solo uno lo crea.
    - La reserva tiene tres estados: `iniciada` al crearse, y `completada` o `fallida` al terminar. Un fallo después de leer la deja `fallida`, nunca borrada.
    - Una reserva existente, en cualquier estado, impide otra consulta de esa familia y versión.
    - Su copia, con el resultado, va en `docs/f3/consultas_validacion.json`, en el repositorio. Se lee, se mezcla y se publica bajo un cierre exclusivo del sistema operativo, y se reemplaza entera con un archivo temporal: dos consultas que terminan a la vez conservan sus dos entradas, y la copia siempre es un JSON válido (F3E5-06).
  - **Prueba:** sellada hasta la F8. **La herramienta no lee ningún minuto de la prueba:** su lector se detiene el 27 jun 2026, y una ventana que la toque se rechaza con un error.
  - Cada informe dice qué particiones consultó y qué decisiones se tomaron con ellas. Un periodo usado para elegir nunca se presenta después como prueba independiente.
- **Los datos que graba el propio sistema** solo son una muestra temporal independiente desde el **19 nov 2026 a las 22:10 UTC**, el final del histórico fijado (F3E5-04; `08` §3). Los grabados del 17 al 19 nov son los mismos instrumentos y minutos que la prueba, por otro canal: no añaden observaciones nuevas y quedan sellados con ella.
- **Se fijan antes de ver ningún resultado:** hasta hoy, ningún motor ha corrido sobre el histórico real.

#### 12.5.2 *Walk-forward* en el desarrollo

El desarrollo se parte en **cuatro tramos consecutivos**, cada corte en la medianoche UTC más cercana a su cuarto:

| Tramo | Intervalo | Días |
|---|---|---|
| 1 | [19 nov 2024, 9 mar 2025) | 110 |
| 2 | [9 mar 2025, 27 jun 2025) | 110 |
| 3 | [27 jun 2025, 14 oct 2025) | 109 |
| 4 | [14 oct 2025, 1 feb 2026) | 110 |

- **Cada tramo es una ventana del motor** (§12.4.4): empieza sin posiciones y calienta los indicadores con los **30 días anteriores**, o desde el primer minuto del histórico si hay menos. Una posición abierta al final del tramo se liquida para evaluarla.
- **El calentamiento de 30 días es una regla declarada.** Un indicador recursivo calentado más tiempo daría valores algo distintos. Con la EMA(50) de 4 h, 30 días son 180 velas: 50 forman su semilla y quedan 130 actualizaciones. La influencia de la semilla es $(49/51)^{130} \approx 0{,}0055$, un 0,55 % (F3E5-03).
- **La F3 no ajusta parámetros:** los fijan las fichas aprobadas. Por eso el *walk-forward* mide la **estabilidad**: el informe da cada tramo por separado y señala la familia que solo gana en uno.
- **Un cambio de parámetros es una versión nueva** (§12.3.3). Se evalúa otra vez en el desarrollo, con los mismos tramos, y solo después, si acaso, en la validación.

#### 12.5.3 Costes del informe

- **La comisión por lado**, como tomador, es la de cada instrumento. La herramienta la lee de KuCoin con el cliente de solo lectura de la E1 el día del informe, y la guarda con él:
  - **Futures:** el `takerFeeRate` del contrato. Es 0,06 % en la respuesta real guardada en la E1 [H].
  - **Spot:** la tarifa de tomador del nivel 0 para la clase del par (`feeCategory`), multiplicada por su `takerFeeCoefficient`. El modelo de clase y coeficiente lo comprobó el revisor el 21 nov 2026 en la página de tarifas de KuCoin y en su SDK oficial [H]. La tarifa de cada clase es la de la tabla de tarifas VIP de KuCoin, leída el 23 nov 2026 (https://www.kucoin.com/support/48142946141635): en el nivel VIP 0, 0,1 % en la clase A, 0,2 % en la B y 0,3 % en la C [H]. Una clase que no esté en esa tabla detiene el informe: no se supone ninguna tarifa.
- **Deslizamiento:** 0,05 % por ejecución, fijo y declarado. La F3 no tiene libro: la profundidad es de F3-E6 y de la F5.
- **Prueba de esfuerzo:** cada informe repite los resultados netos con la comisión y el deslizamiento dobles.
- **Sin funding** (§12.4.4). Los parámetros de costes del riesgo de la F4 son D22 y D30, que esto no decide.

#### 12.5.4 El informe exploratorio de cada familia (F3-A11)

- **Formato:** uno por familia y versión, en `docs/informes/exploratorio/`, en Markdown, con un JSON al lado con todas sus cifras.
- **Registro completo:** las señales, las decisiones y las operaciones de cada ejecución quedan en el PC, junto al histórico (`C:\kbot_historico\informes\`), fuera de Git. Se pueden rehacer con los datos y la versión.
- **Ejecución:** la familia corre **sola** (el modo por familia, §12.4.3), en cada instrumento del universo y en cada tramo.
- **Primera línea:** «Este informe no es la validación de la estrategia (D16, F8): sirve para ver si la familia merece seguir.»
- **Siempre lleva:**
  - la familia, su versión (commit y hash) y su ficha;
  - el periodo, las particiones consultadas y las decisiones tomadas con ellas;
  - el universo (`docs/f3/universo_2026-11-18.json`) y su sesgo de supervivencia;
  - la cobertura del histórico en el periodo, por instrumento (F3E5-05):
    - la del contrato (§12.1, punto 2), con la presencia de cada minuto pedido y la evidencia de elegibilidad de F3-E1: «no aplicable» si no hay ningún minuto que no sea `no_elegible`;
    - aparte, sus ausencias, lo no descargado, lo que nunca se pidió y las contradicciones;
    - y, rotulada como otra medida, la de los minutos presentes sobre todo el calendario de las ventanas;
  - los porcentajes solo REST de las decisiones y de los resultados, que en el histórico son del 100 %;
  - el modelo de ejecución y los costes (§12.4.4 y §12.5.3), y que no hay funding.
- **Cifras**, por instrumento, por tramo y en total:
  - señales, por dirección; decisiones, ejecutadas, caducadas por motivo o no ejecutadas por motivo; y operaciones;
  - **por régimen:** las operaciones y su R medio, según la dirección y la volatilidad de la vela de contexto y de la de señal (§12.3.2);
  - **resultados en R**, brutos y netos: número de operaciones, aciertos (R neto > 0), R medio, mediana, suma, peor, mejor y caída máxima;
  - **la caída máxima** (F3E5-02): la curva de R acumulado empieza en 0 y avanza por el minuto de salida (`exit_minute`). Las operaciones que salen en el mismo minuto, de uno o de varios instrumentos, se suman en un solo paso. La caída máxima es la mayor bajada desde un máximo anterior de la curva, el 0 inicial incluido. Así no depende del orden de las operaciones;
  - los mismos resultados netos con los costes dobles;
  - las salidas por motivo: stop, objetivo, invalidación, tiempo y liquidación.
- **La referencia idealizada** (la apertura de $t+1$), si aparece, va en una tabla aparte, rotulada «idealizada», y no se usa para nada.
- **Datos para D15:** los resultados por volatilidad servirán para proponer, si procede, el filtro de volatilidad extrema, que decide el propietario.

#### 12.5.5 La herramienta

`python -m kbot.backtest.explore`:
- **Argumentos:** la familia (o las tres), la partición (`desarrollo` o `validacion`) y la carpeta del histórico.
- **Versión:** comprueba la de cada familia (§12.3.3). Sin árbol limpio no hay informe.
- **Datos:** lee los minutos de cada instrumento con F3-E1 (`kbot.history.store`): solo los de su ventana y su calentamiento, y **nunca uno de la prueba**.
- **Memoria acotada:** corre el motor por instrumento y por tramo, con unos 140 días de minutos a la vez.
- **Comisiones:** las lee de KuCoin una vez, dentro del presupuesto REST de la E1, y las guarda con el informe.
- **Validación:** crea la reserva atómica antes de leer nada, y al terminar la marca `completada` o `fallida` y copia el resultado en `consultas_validacion.json` (§12.5.1; F3E5-01).
- **Determinista:** los mismos datos, la misma versión y los mismos costes dan el mismo informe.
- **Precisiones de la implementación** (`src/kbot/backtest/`):
  - **Las comisiones se leen antes de la reserva**, con una petición `uta.v2.instrument` por instrumento. La reserva sigue siendo anterior al primer minuto leído, y un fallo de la red al leer las comisiones no consume la consulta de la validación.
  - **La validación se consulta familia a familia**, una por ejecución: nunca se reserva una familia que no se vaya a leer.
  - **El lector no abre ningún archivo de un día de la prueba.** Un día cuyos dos archivos no forman un par válido no da minutos, y la cobertura lo nombra.
  - **Nombres:** el informe es `<familia>_<partición>_<7 cifras del commit>_<12 del hash>.md`, con su `.json`. El registro completo va en `C:\kbot_historico\informes\` con el mismo nombre, en `registro.jsonl` y `comisiones.json`.
  - **La referencia idealizada no aparece en el informe:** queda solo en el registro completo (`ideal_open` de cada operación).
  - **Las sumas se redondean exactamente** (`math.fsum`): ninguna cifra depende del orden de las operaciones.
  - **La prueba de F3E5-03 usa la EMA(50) del código** sobre velas de un minuto: la cuenta no depende de la temporalidad.
  - **La evidencia de elegibilidad de la cobertura** es la de F3-E1 (`kbot.history.universe.evidence`): la apertura de cada contrato de Futures (`contratos_futures.json` del histórico), lo que vio el grabador y los anuncios (F3E5-05).
  - **Los decimales de los parámetros** van en el informe con el texto canónico del proyecto (§5.7), el mismo que usa su hash. Así `2` y `2,00` dan el mismo texto, y el hash se puede rehacer desde el informe (F3E5-07).

#### 12.5.6 Pruebas (F3-A11)

- **Con un histórico sintético pequeño** en una carpeta temporal, con dos instrumentos y unos días:
  - el informe sale con todos sus apartados y con su primera línea;
  - una ventana que toca la prueba se rechaza, y el lector nunca devuelve un minuto de la prueba;
  - una segunda consulta de la validación con la misma familia y versión se rechaza; con una versión nueva, se admite;
  - **F3E5-01:** un fallo provocado después de leer la validación deja la reserva `fallida` y no permite otra consulta; y de dos procesos que la piden a la vez, solo uno la consigue;
  - **F3E5-02:** tras −1 R, dos operaciones de dos instrumentos salen en el mismo minuto con +2 R y −1 R. La caída máxima es 1 R en cualquier orden;
  - **F3E5-03:** dos semillas de la EMA(50) separadas por una unidad, seguidas de 130 actualizaciones iguales, siguen separadas por unos 0,0055;
  - **F3E5-04:** ninguna observación contada como muestra adicional comparte instrumento y minuto con las particiones;
  - sin versión, con el árbol sucio, no hay informe;
  - los costes se aplican como dice §12.5.3, y la variante doble los dobla;
  - el calentamiento de 30 días y la liquidación al final de cada tramo;
  - la misma entrada da el mismo informe;
  - **F3E5-05:** un instrumento que abre después de la ventana tiene la cobertura «no aplicable»; con cuatro minutos elegibles presentes y uno no elegible, la cobertura es 4/4;
  - **F3E5-06:** cuatro procesos de cuatro familias escriben a la vez 25 entradas cada uno en la misma copia, y quedan las 100 en un JSON válido;
  - **F3E5-07:** con `2` y con `2,00`, el informe da los mismos parámetros, y desde ellos se rehace el hash aprobado.
- **Las particiones y los tramos**, con sus fechas exactas.

#### 12.5.7 Qué decide el propietario y qué sigue

- **Tras los informes del desarrollo, el propietario decide qué familias siguen** y si cambia algún parámetro. Un cambio es una versión nueva, que vuelve a pasar por su ficha.
- Con los datos de los informes se le podrá proponer D15 (filtros opcionales).
- Esta sección no decide D12, D16, D22, D28 ni D30.
- **Sigue en F3-E5b:** la migración `0004` y `svc-strategy` (F3-A10), en §12.6.

#### 12.5.8 Lo que muestran los informes del desarrollo: Futures [23 nov 2026; la regla del contrato se mantiene; **pregunta abierta para el propietario**]

Los informes del desarrollo están en `docs/informes/exploratorio/` (§18).

- **Muchos contratos de Futures no dan ninguna señal:** 13 de los 20 en la tendencia, 11 en el breakout y 3 en la reversión a la media. En Spot solo callan EVAA-USDT y PIEVERSE-USDT, que no tienen minutos en el desarrollo.
- **Tres reglas aprobadas actúan juntas:**
  - en Futures, KuCoin no da vela de un intervalo sin operaciones (§3.2 [H]);
  - el histórico deja ese minuto como `ausente`, nunca «sin operaciones» (§12.1);
  - una vela incompleta no la usa ningún indicador, y un hueco reinicia los recursivos (CD-02 y CD-08).

  Los huecos de Futures son sobre todo de un minuto suelto, y con uno basta para que una vela de 4 h no sea utilizable. **Una ausencia no demuestra por sí sola que no hubiera operaciones:** KuCoin también avisa de que sus velas pueden estar incompletas (revisor, 23 nov 2026).
- **Medido en el tramo 3** [27 jun 2025, 14 oct 2025):

| Instrumento | Minutos presentes | Huecos (de un minuto) | Velas de 4 h completas |
|---|---|---|---|
| FUTURES XBTUSDTM | 99,13 % | 1.234 (1.122) | 60,1 % |
| FUTURES SOLUSDTM | 99,96 % | 64 (62) | 95,1 % |
| FUTURES AVAXUSDTM | 87,62 % | 12.678 (8.828) | 6,3 % |
| SPOT BTC-USDT | 100 % | 0 | 100 % |

  En Spot, KuCoin publica una vela de volumen 0 cuando no hay operaciones: BTC-USDT tiene 7 en ese tramo.
- **Por qué calla cada contrato,** separado como pidió el revisor. Se cuentan, en las ventanas de los cuatro tramos, las velas de cada temporalidad de la ficha con todos sus indicadores calculados:
  - **sin periodo elegible:** el contrato abrió después del desarrollo;
  - **el contexto nunca se calienta:** ninguna vela de la temporalidad de contexto (4 h, o 1 h en la reversión) tiene todos sus valores;
  - **valores a ratos, sin señal:** algunas velas los tienen, pero pocas, y la ficha nunca cumple sus condiciones a la vez.

| Familia | Sin periodo elegible | El contexto nunca se calienta | Valores a ratos, sin señal |
|---|---|---|---|
| tendencia (13) | SOXL | AVAX, BCH, FHE, INJ, LINK, NIGHT, PIEVERSE, UNI | ASTER, ENA, ICP, XBT |
| breakout (11) | SOXL | AVAX, BCH, FHE, INJ, LINK, NIGHT, PIEVERSE, UNI | HYPE, XBT |
| reversion_media (3) | SOXL | AVAX | INJ |

  - SOXLUSDTM abrió el 15 may 2026, y su cobertura es «no aplicable» (F3E5-05).
  - FHE, NIGHT y PIEVERSE abrieron entre el 14 nov y el 9 dic 2025: solo tienen unos dos meses del desarrollo.
  - Con valores a ratos, son pocos. XBTUSDTM tiene completas 1.676 de sus 2.376 velas de 4 h, pero solo 51 con la EMA(50) calculada: necesita 50 completas seguidas.
  - Todas las señales de Futures tuvieron completas sus velas de disparo, porque solo aparecen en tramos de datos continuos.
- **Opinión técnica del revisor** (23 nov 2026): mantener el contrato actual como referencia.
  - Rellenar con velas planas introduce datos imputados.
  - Tolerar minutos ausentes deja desconocidos los máximos, los mínimos y los volúmenes.
  - Las dos opciones requerirían una enmienda versionada, comparada solo en el desarrollo y con reglas explícitas de calidad y procedencia.
  - Una vela sintética nunca debe convertirse en un precio observado para ejecutar.
  - Su dictamen no aprueba ninguna modificación del contrato.
- **Estado:** la regla se mantiene. Los informes son correctos con ella, pero sus conclusiones de Futures descansan en pocos contratos. Estudiar una enmienda es decisión del propietario; si la pide, se diseña con esas condiciones y con su propia revisión.

### 12.6 Las señales en la base de datos y `svc-strategy` (F3-E5b) [diseño del 23 nov 2026, **APROBADO** por el revisor en la tercera revisión (commit `e2fa97d`): F3E5B-01 a F3E5B-04 resueltos. Programado en `src/kbot/strategies/` y en la migración `0004` (§18) y **APROBADO** por el revisor en la segunda revisión del código (`d169f17`): F3E5B-05 a F3E5B-07 resueltos. Sin desplegar]

Fija la migración `0004`, con `trading.signals` y `config.strategy_versions`; los permisos de `kbot_strategy` (F3-A10); y `svc-strategy` como proceso sin red probado en la CI (`08`, F3-E5).

- **No cambia** ninguna regla de §12.1 a §12.5: las señales son las de las fichas aprobadas (§12.3), calculadas con las mismas funciones que el motor (§12.4).
- **No incluye:**
  - las intenciones, las decisiones ni las posiciones en la base, que son de la F4;
  - las noticias;
  - la rebaja de lo que cuenta un minuto con evidencia tardía (§3.5), que es de la F4 y de D29;
  - el despliegue de `svc-strategy` en el servidor. Se propondrá con su propia revisión cuando haya una versión aprobada (§12.6.2) y el propietario lo pida.

#### 12.6.1 `trading.signals`: una fila por señal

| Columna | Tipo y regla |
|---|---|
| `signal_id` | `uuid`, clave primaria, creado por el servicio. Será el `correlation_id` de la señal al cierre (§1.2) |
| `family` | `text`: `tendencia`, `breakout` o `reversion_media` |
| `code_commit` | `text`, 40 caracteres hexadecimales: el commit del código que la calculó |
| `params_hash` | `text`, 64 caracteres hexadecimales. La versión de §12.3.3 es `code_commit` + `params_hash` |
| `market`, `symbol` | `text`. `market` es `SPOT` o `FUTURES` |
| `timeframe` | `text`: la temporalidad de la señal (`5m`, `15m`, `1h`, `4h` o `1d`) |
| `open_time` | `timestamptz`: la apertura de la vela de señal $t$, alineada al minuto UTC |
| `direction` | `int2`: +1 o −1, y solo +1 en Spot (§6.2.1) |
| `signal_at` | `timestamptz`: $D_s$, la última disponibilidad de lo que usa (§12.4.1), posterior a `open_time` |
| `context_open` | `timestamptz`: la vela de contexto en $D_s$ |
| `context_direction`, `context_volatility`, `signal_direction`, `signal_volatility` | `text` o nulo: el régimen de §12.3.2 de las dos velas; nulo si no hay etiqueta |
| `levels` | `jsonb`: los niveles fijados en la señal, como números (el ATR, el extremo de la vela…) |
| `provenance` | `jsonb`: los intervalos de minutos originales, unidos |
| `provenance_minutes`, `rest_only_minutes` | `int4`: los minutos de la procedencia y cuántos son solo REST (§12.1, punto 5), con 0 ≤ solo REST ≤ total y total > 0 |
| `run_id` | `uuid`: la ejecución del servicio que la escribió (§4.2) |
| `recorded_at` | `timestamptz`, por defecto `clock_timestamp()`, nunca anterior a `signal_at` |

- **Unicidad** (§1.3): (`family`, `code_commit`, `params_hash`, `market`, `symbol`, `timeframe`, `open_time`, `direction`).
- **Clave foránea** (`family`, `params_hash`) → `config.strategy_versions`: no se puede guardar la señal de una versión no aprobada.
- **Solo inserciones:** nadie tiene `UPDATE` ni `DELETE` (§8). Sin particiones ni índices extra: son pocas filas.
- **Las restricciones las impone PostgreSQL** (§0, regla 6): los formatos hexadecimales, las listas de valores, la dirección en Spot, los recuentos, el minuto alineado y `recorded_at ≥ signal_at`.

#### 12.6.2 `config.strategy_versions`: las versiones aprobadas

- **Una fila por versión aprobada de los parámetros de una ficha.** Sus columnas:
  - `family` y `params_hash`, la clave primaria;
  - `params`, el texto JSON canónico de RFC 8785 cuyo SHA-256 es `params_hash`;
  - `ficha`, el apartado de su ficha (por ejemplo, `§12.3.4`);
  - `approved_on` (`date`) y `approval`, el texto de la aprobación (quién y cómo);
  - `created_at`.
- **Solo la escriben las migraciones** (`kbot_owner`), solo con inserciones, como `config.risk_limits` (§6.1). Ningún servicio escribe en `config` (§9).
- **Aprueba los parámetros, no el commit.** La versión de §12.3.3 incluye el commit del código, pero una migración no puede conocer el commit en el que ella misma entra. El código se aprueba con su revisión y su integración, y cada señal guarda además el commit con el que se calculó (§12.6.1).
- **La migración `0004` no inserta ninguna fila.** Qué familias siguen lo decide el propietario (§12.5.7).
  - Cada aprobación llega después en su propia migración, con su fecha y su texto.
  - `sql_guard` admitirá entonces el `INSERT` en esta tabla, en esa revisión, como hoy en `config.risk_limits`.
  - Sin fila, `svc-strategy` no da señales de esa familia (§0, regla 1).

#### 12.6.3 Los permisos de `kbot_strategy` (F3-A10)

| Objeto | Privilegio |
|---|---|
| Esquemas `market`, `trading` y `config` | `USAGE` |
| `market.candles`, `market.instruments` y `market.universe_snapshots` | `SELECT` |
| `config.strategy_versions` | `SELECT` |
| `trading.signals` | `INSERT`, y nada más |

- **Nada más:**
  - ni `SELECT` sobre las señales: la reinserción usa `ON CONFLICT DO NOTHING`, que solo necesita `INSERT`, como en el grabador (§3.8);
  - ni `UPDATE` ni `DELETE`;
  - ni otras tablas ni funciones;
  - ni escribir en `config`.
- **Ningún otro rol escribe `trading.signals`.**
- **La política** (`tools/grant_policy.py`) añade una regla de §9: `kbot_strategy` solo escribe señales e intenciones, y nunca aprueba.
  - `sql_guard` rechaza cualquier otro `GRANT`.
  - La CI comprueba que la base tiene exactamente esta matriz (`tests/db/test_privileges.py`).

#### 12.6.4 `svc-strategy`

`python -m kbot.strategies`, un proceso que escribe las señales de las versiones aprobadas a partir de lo que graba el grabador.

- **Versión:** el commit del código que se ejecuta (§12.3.3). Sin versión no arranca.
- **Familias:** las de las fichas cuyo (`family`, `params_hash`) está en `config.strategy_versions`. Las demás no corren.
- **Lo visible en un corte:** cada ciclo fija al empezar su `corte`, la hora actual menos 60 s [R], el margen para que se confirmen las inserciones del grabador. Desde entonces solo usa las filas de `market.candles`, `market.instruments` y `market.universe_snapshots` con `recorded_at` ≤ `corte`, y solo escribe señales con $D_s$ ≤ `corte`.
- **Universo, en cada instante** (F3E5B-01):
  - Un instrumento cuenta en $D_s$ si la última evaluación visible con `evaluated_at` ≤ $D_s$ lo tiene en el universo (`in_universe`). `evaluated_at` es el momento desde el que rige (§3.8).
  - Para un día se calculan los instrumentos que estén en el universo en algún instante de él. Después cada señal se queda solo si su instrumento contaba en su $D_s$.
  - Así, una evaluación posterior nunca quita una señal anterior a ella. Y el cálculo de cada ciclo, el de la recuperación y el de un arranque nuevo usan los mismos límites.
- **Datos:** las velas de 1 minuto de `last` de `market.candles`. Cada fila se convierte en un minuto del contrato (§12.1):

| `state` | Evidencia | P | V | T |
|---|---|---|---|---|
| `cerrada_cruzada` | `vela_cruzada_consistente` | contrastada | solo REST (el volumen del WebSocket no juzga hasta D26, §3.8) | contrastada |
| `discrepante` | `vela_cruzada_discrepante` | discrepante: no utilizable | solo REST | contrastada |
| `cerrada_rest` | `vela_rest_suficiente` | solo REST | solo REST | solo REST |

  - **Disponibilidad de un minuto:** el máximo de su `recorded_at` y su fin más el margen de 15 s. Es la regla «en vivo» de §12.1, punto 4: la vela existe cuando el grabador la escribe ya cerrada.
  - Un minuto sin fila visible es una ausencia.
- **El multiplicador** (CD-12; F3E5B-04 y su residual):
  - **De dónde sale:** `market.instruments` guarda una fila cuando cambia algo del instrumento, no en cada comprobación (§3.8). Las comprobaciones sin cambios no quedan, y un listado fallido retrasa la siguiente. **No hay ninguna cota fija** entre una observación guardada y la siguiente.
  - **Qué multiplicador tiene cada minuto,** con las observaciones visibles en el corte, en orden:
    - antes de la primera observación del instrumento: **desconocido**;
    - entre dos observaciones seguidas con el mismo multiplicador (la segunda guardada por otro cambio): el de las dos;
    - **entre dos observaciones seguidas con distinto multiplicador: desconocido**, en todo el intervalo desde la anterior hasta la que detecta el cambio. El cambio pudo ocurrir en cualquier momento de él;
    - desde la última observación visible: el suyo. Es la única información que hay en ese corte.
  - **El minuto entero** (F3E5B-05): un minuto tiene multiplicador solo si es conocido e igual en todo él, de la apertura al cierre. Si cualquier parte cae en un intervalo desconocido, su multiplicador es desconocido. Por ejemplo, con observaciones a las 00:00 y a las 11:59:30 de 0,01 y a las 14:00:20 de 0,001, son desconocidos los minutos de las 11:59 a las 14:00, los dos extremos incluidos.
  - **Un minuto de multiplicador desconocido** tiene la dimensión V no válida. Ningún uso del volumen lo toma, y rompe sus ventanas como un hueco. Los usos del precio no cambian.
  - **El minuto y la vela superior llevan su multiplicador.** El de una vela es el de sus minutos, que solo puede ser uno: una vela con minutos de dos multiplicadores contiene alguno desconocido, así que su V no es válida.
  - **Con eso, el cálculo de §12.2 rompe la ventana** de VWAP y del volumen relativo cuando cambia el multiplicador (CD-12). Sin él, un cambio de 0,01 a 0,001 con volúmenes de 100 y 1.000 daría un volumen relativo de 10, cuando en unidades base no hay cambio (caso del revisor).
  - **Límite declarado del tiempo real.** Hasta que una observación detecta el cambio, los minutos posteriores a la anterior llevan el multiplicador antiguo: en ese corte no hay otra información.
    - Las señales escritas así se quedan (F3E5B-02).
    - En cuanto el cambio es visible, todo el intervalo pasa a desconocido en los recálculos.
    - Acortarlo exigiría que el grabador guardara también las comprobaciones sin cambios. Es un cambio del grabador, que no entra en F3-E5b: se propondrá aparte si el propietario lo pide.
  - **En Spot el multiplicador es siempre 1** (F3E5B-06), sin depender de las observaciones. En el histórico no hay ninguno, como hasta ahora (§12.2, punto 3): el backtest no cambia.
- **Las señales de un día:** las de las fichas con $D_s$ en $[d, d+1)$, calculadas con los minutos visibles desde $d$ − 30 días. Es la misma regla declarada que el calentamiento de los tramos (§12.5.2), y cada día tiene así un comienzo fijo.
- **Equivalencia con el motor, en cada corte** (F3E5B-02): las señales que un ciclo con corte $c$ escribe para el día $d$ son exactamente las del motor (`engine.run` con la ventana $[d, d+1)$) sobre los mismos minutos, los visibles en $c$, con $D_s$ ≤ $c$. Son las mismas funciones (§0, principio 3).
- **Lo escrito frente al recálculo final** (F3E5B-02): son dos cosas distintas, cada una correcta en su papel.
  - **Lo escrito** es la unión de lo que escribió cada ciclo, y nunca se borra: es lo que el sistema supo en vivo.
  - **El recálculo con todos los datos** es el backtest.
  - **Pueden diferir** cuando llega un minuto después de un corte: una vela REST tardía del grabador, la recuperación tras una parada o una inserción confirmada tarde. El minuto que faltaba reinició los indicadores, y el recálculo, con él, puede no tener señales ya escritas o tener otras.
  - **El revisor lo reprodujo:** con un minuto del 5 de enero de las 01:10 disponible el día 6 a las 12:00, el corte de las 11:00:30 del día 6 da 9 señales, y el motor con todos los datos, 0 con $D_s$ ≤ ese corte.
  - Es un límite declarado del tiempo real. La F4 medirá esa divergencia comparando lo escrito con el recálculo.
- **Ciclos y recuperación** (F3E5B-03):
  - **Cuándo:** cada 15 minutos, en los minutos 01, 16, 31 y 46 de cada hora, a los 30 s. Así entra cada vela de 15 m y de 1 h, cuyo último minuto está disponible unos 15 s después de su fin.
  - **Ventana de recuperación:** cada ciclo mira los días que tocan $[$`corte` − 48 h, `corte`$]$, dos o tres. Es el plazo durante el que el grabador aún recupera velas REST (§3.8). Lo anterior es cosa del backtest.
  - **Qué recalcula:** un día, por instrumento y familia, se recalcula si no hay un cálculo bueno de este proceso con un corte posterior a su fin, o si cambió lo que usa. Lo que usa es el número de sus filas visibles: las velas de su rango, las observaciones del instrumento y las evaluaciones del universo. Las filas solo se insertan, así que un número distinto es una fila nueva, también una confirmada tarde.
  - **Sin reiniciar:** un ciclo que falla no cambia ese estado, y el siguiente reintenta cada día pendiente de la ventana. Un día que queda pendiente se recupera mientras siga dentro de ella.
  - **Al arrancar,** el estado está vacío: se calculan todos los días de la ventana.
- **Escritura:** cada ciclo inserta sus señales en una transacción, con `ON CONFLICT DO NOTHING`. Repetirlo, o arrancar otra vez, no duplica nada, y una señal escrita nunca se borra ni se cambia.
- **Sin red** (§1.1):
  - el proceso solo abre el socket Unix de PostgreSQL, con `peer` como `kbot_strategy`;
  - nunca importa el cliente de KuCoin;
  - desplegado, llevará `IPAddressDeny=any` (§9).
- **Ante un fallo se bloquea** (§0, regla 1). Un ciclo que falla no escribe nada, y el siguiente lo reintenta. Sin universo o sin versiones aprobadas, no hay señales.
- **Coste,** medido en el PC el 26 nov 2026 con 31 días reales de un instrumento (Spot ETH-USDT) y las tres fichas aprobadas:
  - un ciclo normal, solo con el día en curso, tarda unos 6 s;
  - el primero, con los tres días de la ventana, unos 18 s.

  Con los 40 instrumentos serían unos 4 minutos por ciclo normal, dentro de sus 15 minutos, y unos 12 el primero. En el servidor, con 2 OCPU de ARM, se medirá antes de proponer el despliegue [P].
- **Precisiones de la implementación** (`src/kbot/strategies/`):
  - **El minuto y la vela superior llevan su multiplicador** (`MinuteCandle.multiplier`, `Bar.multiplier`). En el histórico es nulo, como hasta ahora, así que el backtest no cambia.
  - **Un multiplicador desconocido** marca la V del minuto como no válida, con el mismo valor que una discrepancia (`Quality.DISCREPANTE`): ningún uso del volumen la toma.
  - **Lo visible en un corte** se lee en una transacción `REPEATABLE READ` de solo lectura, así que todos los recuentos y filas salen de la misma instantánea. Las señales se escriben en otra transacción.
  - **El estado** de cada día, por familia e instrumento, solo vive en la memoria del proceso y se olvida al salir de la ventana.
  - **`python -m kbot.strategies`** hace un ciclo al arrancar y después los de su horario. `--una-vez` hace uno solo, y `KBOT_STRATEGY_DSN` cambia la conexión.
  - **`deploy/install/03_postgresql.sh`** aplica hasta la cabeza, que ahora es `0004`. Sus tablas nacen vacías, y `svc-strategy` no se instala en F3-E5b.

#### 12.6.5 Pruebas (F3-A10)

- **Con PostgreSQL, en la CI:**
  - `0004` se aplica, y la matriz de privilegios de la base es exactamente la de la política, con los nuevos de `kbot_strategy`;
  - `kbot_strategy` inserta una señal y no puede:
    - leerla, cambiarla, borrarla ni vaciar la tabla;
    - escribir en `config.strategy_versions` ni en `market`;
    - usar otras tablas;
  - **la unicidad:**
    - la misma clave dos veces deja una sola fila, y con `ON CONFLICT DO NOTHING` no da error;
    - con otro commit, es otra señal;
  - la clave foránea: la señal de una versión no aprobada se rechaza;
  - **cada restricción, en su frontera:**
    - la dirección, y Spot solo largo;
    - los hexadecimales;
    - los recuentos;
    - el minuto alineado;
    - `recorded_at` antes de `signal_at`;
  - **el servicio, sobre velas sintéticas del grabador** (los tres estados, una ausencia y una fila tardía), con dos instrumentos, un universo y versiones aprobadas de fichas de periodos cortos:
    - un ciclo como `kbot_strategy`, después del día, escribe exactamente las señales del motor para ese día con los mismos minutos visibles;
    - a mitad del día, escribe exactamente las del motor con los minutos visibles en su corte y $D_s$ ≤ `corte`. Sin filas tardías, los ciclos siguientes completan las del día entero;
    - repetirlo no añade nada;
    - una versión sin aprobar no escribe nada;
    - una fila con `recorded_at` posterior al corte no se usa;
    - **F3E5B-01:** un cambio del universo dentro del día, con señales antes y después: cada una cuenta según la evaluación vigente en su $D_s$, igual en el cálculo de cada ciclo, en la recuperación y en un arranque nuevo;
    - **F3E5B-02:** el caso del revisor (un minuto del 5 de enero de las 01:10, visible el día 6 a las 12:00). Cada corte da exactamente las señales del motor con sus minutos visibles. Lo escrito conserva las del corte de las 11:00:30, aunque el recálculo con todos los datos no las tenga, y las dos comparaciones se comprueban por separado;
    - **F3E5B-03:** fallan los ciclos de las 23:46, 00:01, 00:16, 00:31 y 00:46, y el de las 01:01, en el mismo proceso, escribe la señal de ayer con $D_s$ = 23:45:15; y una fila confirmada tarde, con `recorded_at` anterior a un corte ya usado, hace recalcular su día;
    - **F3E5B-04, con su residual:** 20 velas de volumen 100 con multiplicador 0,01 y después de volumen 1.000 con 0,001. El caso del revisor:
      - una comprobación sin cambios el 1 nov a las 23:59, que no se guarda;
      - el cambio el día 2 a las 00:00;
      - listados fallidos que retrasan la detección al día 3 a las 12:00, más de 24 h después.

      Todo el intervalo desde la observación guardada anterior hasta la detección tiene la V no válida, también en los agregados. El VWAP y el volumen relativo se reinician y no dan valor hasta recalentar después de la detección, y no hay señales que dependan de ellos mientras tanto. Los indicadores de precio no cambian. Con un corte anterior a la detección, el minuto lleva el multiplicador antiguo (el límite declarado);
  - **sin red** (F3E5B-07): el mismo ciclo corre en un espacio de red propio del sistema operativo (`unshare --net`), con el usuario de las pruebas. Es una barrera que detiene también las conexiones nativas de libpq: una conexión TCP de psycopg a un puerto que escucha la prueba se rechaza dentro, mientras fuera ese puerto contesta. El ciclo funciona por el socket Unix. Un gancho de auditoría de Python no bastaba: no ve las conexiones de libpq, y Python no lo ofrece como aislamiento.
- **Sin base, en cualquier máquina:**
  - la conversión de cada fila (estado, evidencia, dimensiones y disponibilidad);
  - el multiplicador de cada minuto y de cada vela superior, con sus intervalos desconocidos según las observaciones visibles en cada corte;
  - las fechas de cada ciclo, su corte, su ventana de recuperación y los días que recalcula;
  - el texto de `params` de las tres fichas aprobadas, que da sus tres hashes.

#### 12.6.6 Qué decide el propietario

- **Qué versiones se aprueban,** tras §12.5.7. Cada una es una fila de `config.strategy_versions`, en su propia migración.
- **Cuándo se despliega `svc-strategy`,** con su revisión.
- **Si el grabador debe guardar también las comprobaciones de los instrumentos sin cambios.** Acortaría el intervalo de multiplicador desconocido (§12.6.4), y sería una ronda del grabador, aparte.
- Esta sección no decide D12, D16, D22, D28, D29 ni D30.

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

### 13.1 Despliegue del grabador en Oracle (etapa E4) [diseño del 11 nov 2026, **APROBADO** por el revisor el mismo día tras cuatro revisiones: E4-D01 a E4-D07 y R-E4-01 a R-E4-05 cerrados; pendiente de implementar]

**Qué pide la etapa** (`07` §2, E4, y F2-A10, F2-A11, F2-A12, F2-A13 y F2-A15): la instancia Always Free con el sistema endurecido (§9), PostgreSQL 18 de producción por socket Unix con `peer`, `svc-ingest` como servicio de systemd, las copias de §10.1 con el destino de P6 (Object Storage y una copia al PC), la prueba de restauración en 10 pasos, el riesgo de inactividad (B30) y las medidas desde la región (punto 90). Cierra, o deja medidos, los puntos 31, 33, 35, 82, 83 y 90 y las verificaciones B14 a, B15 y B19 de `04`. Hechos comprobados el 11 nov 2026 [H]: **20 GB en total de Object Storage** Always Free entre sus tres niveles y 1.000 correos al mes; `age` 1.1.1 en Ubuntu 24.04 para arm64; `MemoryUtilization` en `oci_computeagent`; una instancia llama a la API sin credenciales almacenadas si pertenece a un grupo dinámico con una política (*instance principals*), y Oracle concede esos privilegios **a la instancia, no al usuario de Linux**; el SDK obtiene y renueva el testigo en el **servicio de autenticación regional** (`auth.eu-frankfurt-1.oraclecloud.com`), además del de metadatos; los permisos de Object Storage distinguen `OBJECT_DELETE` de `OBJECT_VERSION_DELETE`, y con el versionado del cubo borrar un objeto solo crea una marca y conserva la versión anterior; PGDG publica arm64 para Ubuntu LTS, y `uv.lock` tiene ruedas arm64 de `pyarrow`, `psycopg` y `websockets` para Python 3.12 (falta instalarlas de verdad). La cuota de A1 **difiere entre páginas oficiales** (2 OCPU y 12 GB frente a 4 y 24): el propietario la confirma en la consola; la instancia propuesta cabe en las dos.

**Dato nuevo del propietario (11 nov 2026):** su PC no está encendido las 24 h, y lo antiguo irá a un **disco duro externo**, porque el PC tiene poco disco. El diseño lo asume: nada depende de que el PC esté encendido a una hora concreta.

**Lo que E3c cambia en las copias.** Con lo medido en E3c, un día de la base ocupa unos 500 MB en PostgreSQL (el tamaño de su `pg_dump` comprimido se mide en E4c), así que 14 copias completas de los 90 días calientes no caben en 20 GB. Por eso E4 activa la exclusión que §3.9 (punto 4) diseñó y E3 dejó desactivada, y además **copia cada día sellado una sola vez** (punto 4): un día sellado es inmutable, así que volcarlo en cada copia horaria sería repetir lo mismo. **La retirada de particiones sigue desactivada** (§3.9, condición expresa) hasta que la prueba integral de restauración de E4 esté superada y el propietario decida.

**1. Instancia** (E4a)
- `VM.Standard.A1.Flex` en Frankfurt [D02], **2 OCPU y 12 GB** [R: la regla del 25 % de memoria (§13) se cumple mejor con 12 GB, y la cuota de la cuenta podría ser justo esa]; Ubuntu Server 24.04 ARM64; **plugin Compute Instance Monitoring activado al crearla** [H]. Si Frankfurt no tiene capacidad («Out of host capacity»), se reintenta en otro dominio de disponibilidad o más tarde; **nunca se paga** [D01].
- **Discos** [D11, opción B]: volumen de arranque de **50 GB** (B15: la documentación da 47 o 50; se toma 50 [R]) y un volumen de bloques de **110 GB** montado en `/srv/kbot` (ext4, `noatime`), con los datos de PostgreSQL, la raíz del archivo (`/srv/kbot/archive`, creada por la instalación y sincronizada, E3b-02), las copias locales y el clúster aislado de restauración. Quedan 40 GB sin asignar. Punto 82 cerrado al comprobar en la consola que todo es Always Free.
- **Acceso:** SSH solo con clave `ed25519` generada en el PC del propietario, `PasswordAuthentication no`, `PermitRootLogin no`, `AllowUsers ubuntu kbot_sync` (el administrador, que es el usuario de la imagen, y el usuario de sincronización del punto 4, con **claves distintas**). **IP restringida** a la IP del propietario **solo en la lista de seguridad de Oracle** [§9; decisión del propietario del 17 nov 2026, FW-02]. Cuando la IP doméstica cambia, basta con editar el origen de la regla de SSH en la consola web de Oracle, que no depende de SSH ni del servidor (`docs/operacion/oracle_instancia.md` §4). Repetir el filtro en el servidor obligaría a entrar en él para cambiarlo, y la consola serie no da esa vía: el administrador entra solo con clave y no tiene contraseña [R].
- **Endurecimiento:** **el cortafuegos que trae la imagen de Oracle** (`iptables`, cargado al arrancar por `netfilter-persistent`), no `ufw` [decisión del propietario del 16 nov 2026]. La imagen ya rechaza toda entrada salvo SSH, las respuestas y los avisos ICMP, y lleva las reglas que la instancia necesita para llegar a su disco de arranque y a los servicios de Oracle (cadena `InstanceServices`). **Oracle advierte de no usar `ufw` en sus imágenes de Ubuntu**, porque puede chocar con esas reglas y dejar la instancia sin arrancar [H: [documentación de Oracle](https://docs.oracle.com/en-us/iaas/Content/Compute/known-issues.htm#ufw) y [blog de Oracle](https://blogs.oracle.com/developers/post/enabling-network-traffic-to-ubuntu-images-in-oracle-cloud-infrastructure); las imágenes de 24.04 ya no lo traen]. `01_sistema.sh` **no cambia las reglas IPv4 de la imagen**: comprueba que `/etc/iptables/rules.v4` sigue siendo el archivo de la imagen y se detiene si no; solo instala `rules.v6`, que cierra IPv6 (la imagen lo deja abierto, aunque la instancia no tiene IPv6 pública) [decisión del propietario del 17 nov 2026]. Carga los dos archivos **reemplazando toda la tabla** (`iptables-restore` sin `--noflush`), nunca con `netfilter-persistent reload`: la imagen configura `IPTABLES_RESTORE_NOFLUSH=yes` en `/etc/default/netfilter-persistent`, y esa recarga añade las reglas a las ya cargadas en lugar de reemplazarlas (en la instancia, el 17 nov 2026, duplicó todas las de `INPUT`, `FORWARD` y `OUTPUT`); al arrancar, las cadenas están vacías y `netfilter-persistent` carga los mismos archivos; `fail2ban` sobre `sshd`; `unattended-upgrades` solo con actualizaciones de seguridad y **sin reinicios automáticos** (los decide el propietario); `chrony` (viene en la imagen); ningún otro puerto a la escucha (la imagen trae `rpcbind`, por `nfs-common`, escuchando en el puerto 111 de todas las direcciones; nada usa NFS y `01_sistema.sh` lo para y lo enmascara, comprobado en la instancia el 17 nov 2026).
- **Usuarios del sistema:** uno por servicio, **con el mismo nombre que su rol SQL** (`kbot_ingest`, `kbot_archive`, `kbot_backup`), sin shell, más `kbot_sync` (solo SFTP, sin rol SQL). Así `peer` no necesita ningún mapa en producción (§9: «sin el mapa de la CI»), y la CI conserva el suyo [R]. Pregunta P-E4-3.
- **Código:** clon de `main` en `/opt/kbot`, propiedad del administrador y solo lectura para los servicios; `uv sync --frozen --no-dev`, con los hashes de `uv.lock` [H]; Python 3.12 es el del sistema. `uv` se instala con su versión fijada y su suma de comprobación [R]. Se despliega una etiqueta que el propietario aprueba, nunca «lo último».
- **Comprobación automática (F2-A10):** `python -m kbot.ops.check` en el servidor, que comprueba y escribe en un informe que: solo el puerto 22 escucha (`ss -ltn`), `sshd` no acepta contraseñas, el cortafuegos cargado es **exactamente** el esperado, cadena a cadena y en orden (IPv4: las reglas de la imagen tal como vienen, con entrada nueva solo para SSH y las de Oracle; IPv6: entrada cerrada; lo único variable es lo que añade `fail2ban` al banear) [FW-01], `netfilter-persistent` y `fail2ban` están activos, PostgreSQL no escucha TCP y su `pg_hba.conf` solo tiene reglas `local ... peer`, las actualizaciones de seguridad están activas, cada usuario de servicio no tiene shell, **y el aislamiento del servicio de metadatos del punto 2 funciona**. El informe se guarda en el repositorio (`docs/operacion/`) con su fecha.
- **F1-A6 en el servidor (punto 31):** un guion que, como cada usuario de servicio (`sudo -u`), comprueba que conecta con su rol y solo con él (cualquier otro rol falla en `peer`), que `rolpassword IS NULL`, y que no tiene `CREATE` en ningún esquema; además las matrices de privilegios de la CI (`tests/db/test_privileges.py`) se ejecutan en el servidor como `postgres`.

**2. Servicios de systemd** (E4a)
- `svc-ingest.service`: `User=kbot_ingest`, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, `NoNewPrivileges=yes`, `MemoryMax=1G` [R, se ajusta con el punto 83], `Restart=on-failure` con espera creciente, y `ExecStart=/opt/kbot/.venv/bin/python -m kbot.ingest --sink db`.
- **Aislamiento del servicio de metadatos** (`169.254.169.254`, que entrega los testigos de *instance principal*; E4-D04). Oracle concede los privilegios a la instancia entera, así que el aislamiento lo pone el propio servidor, en **dos capas**:
  - **Cortafuegos por usuario** (`nftables`, en su propia tabla y cargado después del cortafuegos de la imagen): la salida hacia `169.254.169.254` solo se permite a `root`, a `snap_daemon` (el usuario con el que corre el agente de Oracle, un *snap*: comprobado en la instancia el 17 nov 2026) y a `kbot_backup`; a cualquier otro usuario se le rechaza. Los dos primeros van por su número, igual en todo Ubuntu (`0` y `584788`, el que `snapd` fija para `snap_daemon`). Es una regla del núcleo, que no depende de cgroups ni de eBPF y cubre también las sesiones SFTP de `kbot_sync` y cualquier proceso fuera de systemd.
  - **`IPAddressDeny=`** en cada unidad de servicio, como defensa adicional: en `svc-ingest` y en los temporizadores del archivo, las redes privadas, el enlace local y `169.254.169.254`; en `svc-backup`, las redes privadas y el enlace local salvo `169.254.169.254` y el resolutor local (`127.0.0.53`, `systemd-resolved`). **No hay lista blanca por dirección IP para Oracle** (R-E4-03): el servicio necesita tres destinos, el de metadatos, el **servicio de autenticación regional** (`auth.eu-frankfurt-1.oraclecloud.com`, donde obtiene y renueva el testigo) y Object Storage (`objectstorage.eu-frankfurt-1.oraclecloud.com`), y los dos últimos son nombres cuyas direcciones públicas cambian; una lista de direcciones mantenida a mano se rompería sin avisar. Lo que acota los destinos es el código: el SDK se configura con esos dos puntos de acceso explícitos y nada más [R]. Prueba de aceptación, desde la unidad real y sin testigo previo: crear el firmante, subir y leer un objeto, y renovar el testigo (`refresh_security_token()`); las pruebas negativas desde los demás contextos se conservan. systemd avisa de que sin soporte de cgroups v2 y eBPF estas reglas pueden no tener efecto [H], así que **la instalación comprueba que surten efecto y se detiene si no**.
  - **Contextos afectados y comprobación obligatoria** (F2-A10): desde `kbot_ingest`, `kbot_archive` y `kbot_sync` (sesión SFTP real) una petición al servicio de metadatos falla; desde `kbot_backup` la subida funciona. Es el aislamiento que se acredita, en lugar de una unidad artificial con `IPAddressDeny=any`.
  - Las direcciones de KuCoin cambian (CDN), así que «solo KuCoin» (§1.1) lo garantiza la lista blanca de hosts del cliente de E1, no systemd [R]. Prueba B19 (punto 35): un servicio de prueba con `IPAddressDeny=any` conecta a PostgreSQL por el socket y no llega a KuCoin.
- **`KBOT_REST_LOCK`** (condición de E1; E4-D06): `/run/kbot/kucoin-rest.lock`, **precreado** por `tmpfiles.d` con el directorio `/run/kbot` de `root:kbot` y modo `0750` (ningún servicio puede sustituir el archivo) y el archivo de `root:kbot` y modo `0660`. Cada servicio que use REST lleva `SupplementaryGroups=kbot`, `ReadWritePaths=/run/kbot/kucoin-rest.lock` (lo único escribible con `ProtectSystem=strict` [H]) y la misma variable. Prueba en el servidor: dos usuarios de servicio distintos comparten el bloqueo, el segundo recibe `RestBudgetInUse`, y cuando el primero lo suelta el segundo lo adquiere.
- **Temporizadores del archivo** (`kbot.archive`, como `kbot_archive`): `prepare` antes de arrancar `svc-ingest` (`Before=`/`Requires=`) y a diario a las 00:05 UTC; `seal`, `archive` y `verify` a diario, en ese orden.

**3. PostgreSQL 18 de producción** (E4b)
- Paquetes de PGDG para arm64 [H], como en la CI; clúster con los datos en `/srv/kbot/postgresql`.
- `listen_addresses = ''`, `max_locks_per_transaction = 256` (§3.9), `shared_buffers` de 3 GB [R, §13; ni demuestra residencia ni dice cómo lo contará Oracle: se mide en E5], `pg_hba.conf` con **una sola regla**: `local all all peer`. Roles de `roles.sql` aplicados como `postgres`; migraciones `0001 → 0003` con `SET ROLE kbot_owner`; la huella de las funciones `SECURITY DEFINER` comprobada contra `sql/security_definer_catalog.json`.

**4. Copias** (E4c; §10.1, D25, P6)
- **Dos clases de artefacto**, ambas cifradas:
  - **copia caliente, cada hora:** `pg_dump` de toda la base **sin los datos de los días sellados ya copiados** (punto siguiente) ni de los excluidos; lleva los días abiertos (como mucho 3-4) y las cinco tablas pequeñas;
  - **copia de un día sellado, una sola vez:** cuando `seal` sella un día, el siguiente ciclo vuelca sus doce particiones (`pg_dump --table` de cada partición, en un archivo por día). Un día sellado es inmutable, así que el archivo es definitivo; su manifiesto lleva recuento y huella por partición. Tras subirla, el servicio **la vuelve a leer del cubo y comprueba su SHA-256**: solo entonces cuenta como dependencia externa verificada. **Cuándo se puede borrar (residual de R-E4-01).** Que el día sea elegible no basta: puede haber otra generación del mismo día, y una copia retenida fija la suya. Antes de borrar una copia de día sellado *S*, el servicio recorre **todas las copias retenidas en el cubo cuyo manifiesto fija *S*** y, para cada una, comprueba que la **generación que fija ese mismo manifiesto** para ese día está `verificado_fuera`, es decir, que la copia puede restaurarse completa con las identidades exactas que fija, sin *S*. Si alguna no la tiene, *S* se conserva hasta que esa copia salga de la retención D25; después vuelve a comprobarse. Así ninguna copia retenida pierde nunca una dependencia. El servicio guarda el índice de dependencias (copia → *S* y generación) junto a los manifiestos, y lo reconstruye leyéndolos del cubo si falta. Aceptación: con una copia caliente *H* retenida que fija *S* y *G1*, hacer elegible *G1* o una *G2* distinta y pedir el borrado de *S*: solo procede cuando *H* puede restaurarse con las identidades de su propio manifiesto (*G1* verificada fuera); con *G2* elegible y *G1* no, *S* se conserva hasta que *H* expire.
- **Dependencias de una copia caliente (R-E4-01).** Una copia caliente omite los datos de un día sellado **solo si ese día tiene una alternativa externa verificada**, de una de estas dos clases, y el manifiesto la fija con su identidad exacta: (1) su copia de día sellado, con nombre de objeto, SHA-256 y huella de su manifiesto, que el servicio ha releído del cubo y que **sigue existiendo** (lo comprueba en cada copia, dentro del presupuesto) y que no caduca nunca por sí sola; o (2) su generación Parquet `verificado_fuera`, con la restauración integral superada, como exige §3.9. Si una dependencia deja de existir (por ejemplo, el propietario la borró desde la consola) o nunca llegó a verificarse, **el día vuelve a incluirse en las copias calientes siguientes**: la copia crece, pero nunca queda incompleta. Las **copias previas a una actualización**, que se conservan sin plazo, **son autocontenidas**: llevan los datos de todos los días que no sean `elegible_para_exclusion_backup`, sin dependencias de copias de día sellado. Caso de aceptación: en el día 121 sin Parquet externo verificado, una copia reciente y una previa a actualización restauran completas.
- **Instantánea única (E4-D01 de E3 y respuesta (a) del revisor):** el servicio de copias, como `kbot_backup`, abre una transacción `REPEATABLE READ`, exporta su instantánea (`pg_export_snapshot()`) y **la mantiene abierta hasta terminar el volcado y calcular el manifiesto** [H: documentación de `pg_dump` 18]. En esa misma instantánea lee los sellos, la elegibilidad, los recuentos, las huellas y la huella del catálogo; `pg_dump -Fc --compress=zstd:3 --snapshot=<id>` excluye con `--exclude-table-data` por partición y nunca con `--exclude-table-data-and-children` [H]. El manifiesto fija la generación exacta y todos sus archivos (ruta, tamaño, filas y SHA-256 de cada uno); restaurar nunca busca «la última». **Una copia solo se publica como completa cuando han terminado bien el volcado, el manifiesto, el cifrado y la subida**; hasta entonces es un intento, con nombre temporal.
- **Cifrado:** `age` para dos destinatarios: la clave principal y la de recuperación. En el servidor solo hay claves públicas; las privadas, en una tarjeta SD del propietario y en papel [decisión del propietario del 17 nov 2026, en lugar del disco externo], nunca en el servidor ni en Git.
- **Objetos globales:** `pg_dumpall --globals-only` necesita leer `pg_authid`, que `pg_read_all_data` no puede [H], así que lo ejecuta un servicio aparte como `postgres`, una vez por semana y antes de cada actualización, también cifrado [R].
- **Object Storage** (punto 33): un cubo privado `kbot-backups` en Frankfurt, **con versionado**. La instancia pertenece a un **grupo dinámico** cuya política permite en ese cubo `OBJECT_CREATE`, `OBJECT_INSPECT`, `OBJECT_READ` y `OBJECT_DELETE`, pero **no `OBJECT_OVERWRITE` ni `OBJECT_VERSION_DELETE`** [H: referencia de políticas de Object Storage]. Con el versionado, borrar un objeto solo crea una marca y conserva la versión anterior [H], que la instancia no puede destruir: un servicio de copias comprometido puede, como mucho, ocultar copias durante la ventana del punto siguiente, y el propietario las recupera desde la consola. El servicio usa el SDK de Python de Oracle (`oci`, fijado en `uv.lock`) con `InstancePrincipalsSecurityTokenSigner`, sin ninguna credencial en disco; las operaciones concretas del SDK se contrastan con esos permisos en la subida de prueba, que cierra el punto 33.
- **Retención (R-E4-02):** la aplica **el propio servicio**, exactamente como D25: conserva 3 horarias, 7 diarias, 2 semanales y 2 previas a una actualización, y borra las demás al terminar cada subida; borra también la copia de un día sellado cuando el día es `elegible_para_exclusion_backup`. Una política de ciclo de vida del cubo, que aplica Oracle [P: confirmar sin coste en la consola], borra de forma permanente **las versiones anteriores al día de su borrado**: esa es la ventana en la que un borrado indebido se puede deshacer desde la consola. Ningún objeto caduca por edad: lo que una copia necesita no desaparece sin que el servidor lo sepa (R-E4-01). **Clase nueva para el propietario (P-E4-4):** las copias de día sellado y la ventana de un día de versiones anteriores no están en D25; se le proponen como ampliación expresa, dentro de los mismos 20 GB.
- **Presupuesto:** antes de subir, el servicio suma lo que hay en el cubo, versiones anteriores incluidas; si la subida superaría 19 GB, no sube, deja la copia solo en el servidor y lo registra como error, y en el estado de copias consta **la antigüedad de la última subida completa**, que es el RPO efectivo frente a la pérdida del servidor (E4-D07). Sin Telegram hasta la F7. F2-A15: todo dentro de Always Free y la cuenta nunca pasa a pago.
- **Medición del presupuesto (E4c, E4-D07 y R-E4-02):** con datos sintéticos se mide el `pg_dump` comprimido de un día y de la copia caliente, y una **simulación de al menos 120 días**, con esos tamaños, los objetos globales, los manifiestos, las versiones anteriores de la ventana, la cadencia del disco externo de P-E4-2 y retrasos de limpieza, calcula el **máximo del cubo antes de cada subida**. La simulación modela la secuencia completa de la copia externa: **descarga, recibo, desafío en la visita siguiente, respuesta y verificación**, de modo que con visitas semanales una generación puede necesitar dos visitas (unos 14 días) para quedar `verificado_fuera`; a eso se suman los 3 días del sellado, los bordes del calendario y el tiempo que una copia de día sellado se conserva porque una copia retenida la fija (hasta 2 semanas por las semanales de D25). **No se usa ninguna cota fija**: el máximo lo da la simulación. Si no cabe en 19 GB, se reduce la cadencia del disco externo o la retención, con decisión del propietario.
- **Copias locales:** en `/srv/kbot/backups` quedan las 3 últimas horarias, la última diaria, los objetos globales, y las copias de los días sellados hasta que su día sea elegible para exclusión; es lo que el disco externo recoge.
- **Copia al disco externo** (P6, D11; E4-D01 y E4-D03): un guion para Windows (`tools/pc/sincronizar.ps1`) que el propietario ejecuta cuando quiere, con el disco conectado. Entra por SFTP como `kbot_sync`, con su **propia clave** y este perfil efectivo de `sshd_config` [H: `sshd_config`]: `Match User kbot_sync` con `ForceCommand internal-sftp`, `ChrootDirectory /srv/kbot/sftp`, `DisableForwarding yes` (desactiva reenvío TCP, de agente, de X11, de sockets Unix y túneles), `PermitTTY no`, `PasswordAuthentication no`. El chroot y todos sus ancestros pertenecen a `root` y no son escribibles por otros; dentro, `copias/` y `archivo/` son **montajes de solo lectura** (`bind`, `ro`) y `recibos/` y `restauracion/` son los únicos directorios escribibles. El guion descarga la última copia diaria, las copias de los días sellados que falten, los objetos globales y las generaciones Parquet que no tenga; comprueba cada archivo con su SHA-256 y escribe un **recibo** por generación: `generation_id`, manifiesto, y el conjunto completo de archivos con tamaño, filas y SHA-256. **El PC nunca necesita credenciales de Oracle, y el servidor nunca tiene credenciales del PC.**
- **Lo que un recibo demuestra y lo que no (E4-D01).** Un cliente con la clave de `kbot_sync` podría calcular las huellas sin guardar nada, así que un recibo **no activa por sí solo** la elegibilidad. El temporizador `verify`, como `kbot_archive`:
  1. rechaza un recibo incompleto, de otra generación o con alguna huella, tamaño o recuento distintos de los que el servidor ya conoce;
  2. con un recibo válido, inserta `replicado_fuera` y emite un **desafío**: un desplazamiento y una longitud aleatorios dentro de cada archivo de la generación, que escribe en `recibos/` para el PC;
  3. **en una sincronización posterior**, el guion lee esos bytes **del disco externo** y devuelve su SHA-256; si coincide con lo que el servidor calcula de su propia copia, inserta `verificado_fuera`.
  4. **Lo que esto es y lo que no (R-E4-05):** una **lectura diferida hecha por un cliente confiable**, no una prueba criptográfica de ubicación ni de persistencia. Quien tenga la clave de `kbot_sync` puede releer el rango pedido del propio servidor y contestar con el disco vacío. La garantía descansa en el guion auditado del repositorio y en que el PC y el disco externo son hardware del propietario, que se declaran **parte confiable**. Lo que sí se comprueba: el guion legítimo **falla cuando el disco externo no está conectado** (lee solo de su ruta, nunca del servidor ni de una caché), y un recibo incompleto, de otra generación o con huellas distintas se rechaza. Frente a un PC comprometido quedan la copia del día sellado en el cubo, que no caduca por edad, y los 90 días calientes en PostgreSQL.
- **RPO:** frente a fallos de la base de datos o borrados, 1 h (copia horaria local). Frente a la pérdida del servidor, el tiempo desde la **última subida completa** al cubo (como mucho unas 6 h si las subidas van bien: desde el 26 nov 2026 la copia caliente sube al cubo cada 6 horas, por decisión del propietario que enmienda D25; el estado de copias lo muestra); la copia del disco externo tiene el RPO del tiempo desde la última sincronización, y queda documentado así (F2-A11).
- **Entorno de restauración aislado (R-E4-04):** un clúster propio en `/srv/kbot/restore`, **de otro usuario del sistema, `kbot_restore`**, que es su superusuario (`initdb -U`), con su propio directorio de socket y sin TCP. `kbot_restore` no tiene rol SQL en producción (cualquier conexión al socket de producción falla en `peer`), no pertenece a ningún grupo de los servicios y no puede leer los archivos de producción (`0700` de `postgres`). PostgreSQL advierte de que restaurar un volcado ejecuta lo que decidió su origen [H: `pg_restore` 18], así que **ningún volcado se ejecuta sin autenticarlo antes**: cada copia registra al crearse, en su manifiesto y en un registro del servidor que solo `kbot_backup` escribe, el SHA-256 del volcado **en claro**; el restaurador copia el archivo recibido en `restauracion/` a un directorio privado de `kbot_restore`, calcula su SHA-256 sobre esa copia y solo si coincide con el registrado para la copia que se pidió restaurar ejecuta `pg_restore`. Un archivo sustituido se rechaza antes de ejecutar SQL. Prueba controlada: desde el clúster restaurado no se puede escribir en producción ni conectarse a su socket.
- **Restauración en 10 pasos** (§10.1) **por fases (E4-D02)**, en ese clúster aislado, para no gastar otra instancia [R]:
  1. roles;
  2. `pg_restore --exit-on-error --single-transaction` de la copia caliente, que reconstruye vacías, **con su sello `CHECK (false) NOT VALID`**, las particiones excluidas;
  3. para cada día sellado que la copia caliente no lleva: **quitar el sello de sus doce particiones, cargar los datos y reponer el mismo sello, con su nombre y `NOT VALID`**. Los datos vienen de la copia del día sellado (si existe en el cubo o en el disco) o del Parquet de la generación que fija el manifiesto, con su huella comprobada (la inversa del archivador). El revisor reprodujo que cargar con el sello puesto falla con `23514`, y que validar esos sellos no procede: están sin validar a propósito;
  4. huella del catálogo y restricciones, **comprobadas al final, con todos los datos ya cargados**, incluidos los sellos `NOT VALID` originales;
  5. recuentos y hashes del manifiesto;
  6. reconciliación;
  7. permisos;
  8. carga de Parquet: ya hecha en el paso 3; aquí se comprueba que cada generación cargada es exactamente la que fija el manifiesto;
  9. servicios aislados (en la F2 no aplica: no hay servicios de trading);
  10. tiempo medido.

  Este DDL (quitar y reponer sellos) solo existe en la herramienta de restauración y solo actúa sobre el clúster aislado; `sql_guard` sigue sin admitirlo en las migraciones. Caso obligatorio: una partición sellada incluida y otra excluida con Parquet, restauradas juntas, con sus filas y huellas originales y los sellos `NOT VALID` conservados.
- **Dos pruebas distintas (E4-D05):**
  - **ensayo automático mensual**, en el servidor, de la canalización completa: una copia hecha para el ensayo, cifrada para un **destinatario de prueba** cuya clave privada solo existe en el servidor y solo sirve para esa copia, se restaura en el clúster aislado con los 10 pasos. Demuestra que la canalización funciona; **no cuenta como restauración integral**;
  - **restauración integral de un artefacto real:** la inicia el propietario cuando tiene el PC y el disco: el guion descifra en el PC, con su clave privada, una copia real conservada (nunca se traslada la clave al servidor), la sube a `restauracion/` por SFTP, y el servidor la restaura en el clúster aislado con los 10 pasos y mide tiempos. **Sin acceso autorizado a la clave, la prueba queda pendiente y nunca se registra como superada.** Es la que exigen F2-A11, la condición expresa de §3.9 y `elegible_para_exclusion_backup`.
- **Demostración del RPO (F2-A11):** se destruye el clúster aislado y se restaura desde la última copia horaria, con los tiempos de transferencia y restauración medidos.

**5. Operación y medición** (E4d)
- **Inactividad (B30, P4, F2-A13):** procedimiento guiado para el propietario, cada dos días: consola → Observación → Explorador de métricas → `oci_computeagent`: `MemoryUtilization`, `CpuUtilization`, `NetworksBytesIn/Out`, 7 días; anota los valores en `docs/operacion/metricas_oracle.md`. Regla propia: **memoria al menos al 25 % según Oracle**, sin carga artificial; el percentil 95 lo documenta Oracle para la CPU, y no se extiende por cuenta propia a las otras métricas [H]. La semántica de `MemoryUtilization` respecto de la caché de páginas sigue abierta [P]: en E5 se correlaciona con `/proc/meminfo` del servidor. Además una **alarma de Oracle por correo** (`MemoryUtilization` < 25 % durante un día), dentro de los 1.000 correos gratuitos al mes [H]. Si el uso real no llega al 25 %, se aplica D01.
- **Latencia (punto 90) y B14 a desde el servidor:** `tools/kucoin_latency.py`, con el cliente guardado de E1: p50 y p99 de 100 peticiones GET dentro del presupuesto, y las pruebas de contrato de E1 ejecutadas desde la región.
- **Consumo (punto 83, F2-A12):** un temporizador cada 5 min anota CPU, memoria y disco de cada servicio (`systemd-cgtop`, cgroups) en `/var/log/kbot/consumo.csv`; las 72 h seguidas se evalúan en E5 con el grabador en marcha.

**6. Quién hace qué**
- **El propietario, en la consola de Oracle, guiado paso a paso:** confirmar la cuota de A1 de su cuenta; crear la instancia y el volumen, la lista de seguridad con su IP, el grupo dinámico, la política, el cubo con su política de ciclo de vida y la alarma; generar las dos claves SSH en su PC (administrador y `kbot_sync`); guardar las claves privadas de `age` en el disco externo y en papel. Entrega a Claude: IP pública, OCID de la instancia y del compartimento, espacio de nombres y nombre del cubo. **Nunca entrega claves ni contraseñas.**
- **Claude:** los guiones y unidades del repositorio, probados en la CI (sintaxis, `systemd-analyze verify`, copia y restauración por fases en el PostgreSQL de la CI con el caso obligatorio del paso 3, cifrado con `age`, recibos y desafíos, política de retención simulada), y su ejecución en el servidor según P-E4-1.

**7. Preguntas al propietario** (respondidas el 11 nov 2026; solo P-E4-4 amplía una decisión, D25)
- **Respuestas:** **P-E4-1 (a)**: por defecto ejecuta Claude por SSH, con cada comando visible y aprobado; el propietario puede cambiarlo en cualquier momento, y se mantiene un **diccionario de comandos** en castellano (`docs/operacion/comandos.md`) con qué hace cada uno. **P-E4-2:** al menos una vez por semana, salvo imprevistos que pueden alargarlo; la simulación del presupuesto incluye un hueco de 14 días, y el guion avisa a partir de 7. **P-E4-3: sí.** **P-E4-4: sí**, D25 ampliada (`03`).
- **P-E4-1.** Quién ejecuta los comandos en el servidor. (a) Claude por SSH desde el PC del propietario, que ya tiene el cliente OpenSSH de Windows, con la clave en el PC y **aprobación de cada comando**; (b) el propietario pega los comandos. **Recomendación: (a)**, con los comandos visibles antes de ejecutarse.
- **P-E4-2.** Con qué frecuencia conectará el disco externo. Determina el RPO de esa copia y cuántos días sellados esperan en el cubo. **Recomendación:** al menos una vez por semana; el guion avisa si han pasado más de 7 días. La medición del punto 4 dirá si basta.
- **P-E4-3.** Usuarios del sistema con el mismo nombre que su rol SQL (sin mapa en producción). **Recomendación: sí.**
- **P-E4-4.** Ampliar D25 con dos elementos que no están en ella: la clase «copia de día sellado» (una por día sellado, hasta que el día es elegible para exclusión) y la ventana de un día de versiones anteriores del cubo. Sin ellas, las 14 copias de D25 no caben en 20 GB con el tamaño medido en E3c. **Recomendación: sí**, y la simulación de 120 días de E4c comprueba que todo cabe. D25 no cambia en nada más.

**8. Pruebas de aceptación** (F2-A10, F2-A11, F2-A15 en E4; F2-A12 y F2-A13 terminan en E5)
- CI: unidades válidas (`systemd-analyze verify`), guiones con sintaxis comprobada, copia caliente y copia de día sellado con manifiesto en la misma instantánea (con una inserción concurrente que no aparece), exclusión según el estado de la generación, restauración por fases con el caso obligatorio, cifrado y descifrado con `age`, recibos rechazados (incompletos, de otra generación, huellas distintas) y desafíos, simulación de siete días sin disco externo y de un fallo de presupuesto con la antigüedad de la última subida correcta, y el guion de Windows probado con `pwsh`.
- Servidor (informe en `docs/operacion/`): comprobación de F2-A10 en verde, con el aislamiento del servicio de metadatos probado desde cada contexto real; F1-A6 con los usuarios reales; B19 y el bloqueo REST compartido entre dos usuarios; el perfil SFTP (funciona, solo escribe en `recibos/` y `restauracion/`, y fallan shell, terminal y reenvíos TCP y Unix); 24 h de copias horarias cifradas en Object Storage con la retención aplicada; ensayo automático y restauración integral de un artefacto real con tiempos; RPO demostrado; subida sin credenciales (punto 33); latencia y contratos desde Frankfurt; primera lectura de las métricas de Oracle.

**9. Riesgos conocidos:** bloqueo del acceso por cambio de IP (se corrige editando la lista de seguridad desde la consola web de Oracle, porque el servidor no filtra por IP: decisión del propietario del 17 nov 2026, FW-02); falta de capacidad A1 en Frankfurt (esperar, nunca pagar); semántica de `MemoryUtilization` [P, E5]; presupuesto de Object Storage si el disco externo tarda semanas (la copia se queda en el servidor y el RPO remoto crece, visible en el estado); **espacio transitorio** en el volumen de 110 GB para copias y para el clúster aislado (unos 55 GB de base, 32 GB de Parquet al año, copias locales y una restauración de unos 10 días: se mide en E4c y se vigila en E5); **pérdida o corrupción del único disco externo** (mitigación: cada copia de día sellado se conserva en el cubo mientras alguna copia retenida la necesite y hasta que su día sea elegible, y los 90 días calientes siguen en PostgreSQL; el guion recalcula los SHA-256 del disco en cada sincronización, §10.1; un segundo disco es una opción del propietario); **compromiso del servicio de copias** (puede borrar objetos, pero no sus versiones ni sobrescribir: la versión anterior sobrevive un día, recuperable desde la consola, y el estado de copias mostraría la pérdida); ruedas arm64 (instalación real pendiente).

**Subetapas:** **E4a** instancia, endurecimiento y servicios; **E4b** PostgreSQL y archivo; **E4c** copias y restauración; **E4d** operación y medición. Cada una con su revisión.

**Implementado en E4a** (11 nov 2026; la parte sin servidor, **APROBADA** por el revisor el 12 nov tras cuatro revisiones, E4a-01 a E4a-07 cerrados; el cortafuegos de la imagen y la exención del agente de Oracle, **aprobados** el 17 nov en `5d7e344`, FW-01 a FW-03 cerrados; la instancia existe desde el 16 nov, sin cambios todavía):
- **`deploy/etc`:** `sshd` solo con clave (`AuthenticationMethods publickey`), sin root ni reenvíos (`DisableForwarding yes`) y solo `ubuntu`; `fail2ban` sobre `sshd` (con `banaction = iptables-multiport`: sus bloqueos van al cortafuegos de la imagen, en la forma que `kbot.ops` admite); actualizaciones de seguridad diarias sin reinicio; `tmpfiles.d` con `/run/kbot` (`root:kbot`, `0750`) y el bloqueo REST (`0660`) (E4-D06); y la **guarda del servicio de metadatos** en `nftables` (E4-D04): salida a `169.254.169.254` por los puertos 80 y 443 solo para `root` y, desde el 17 nov 2026, `snap_daemon` (el agente de Oracle), con rechazo para cualquier otro usuario (en E4c se añade `kbot_backup`). Los puertos 53 y 123 de esa misma dirección quedan abiertos porque Oracle sirve en ella DNS y NTP. La guarda la carga **su propio servicio** (`kbot-metadata-guard.service`, después del cortafuegos, que desde el 16 nov 2026 es el de la imagen cargado por `netfilter-persistent` y antes era `ufw`; solo la tabla `inet kbot_guard`); **nunca se toca `/etc/nftables.conf`**, porque el archivo por defecto de Ubuntu empieza por `flush ruleset` y habría vaciado también las reglas del cortafuegos (E4a-01). Por la misma razón, `01_sistema.sh` retira y enmascara `nftables.service` **antes** de cargar el cortafuegos (su parada ejecuta `nft flush ruleset`; residual de E4a-01), y al final comprueba que la regla de SSH y las de Oracle siguen cargadas y que IPv6 está cerrado; la comparación completa la hace `kbot.ops` en `04_verificar.sh`.
- **`deploy/systemd`:** `svc-ingest.service` (`User=kbot_ingest`, `ProtectSystem=strict`, `MemoryMax=1G`, `IPAddressDeny` de redes privadas, enlace local y metadatos, `ReadWritePaths` solo del bloqueo), `kbot-archive-prepare.service` (antes del grabador, `Requires=`), `kbot-archive-daily.service` y su temporizador (00:20 UTC: preparar, sellar, archivar y verificar, sin red), y dos plantillas que prueban el aislamiento **como el usuario real** de cada servicio (E4a-04): `kbot-netcheck@.service`, con `IPAddressDeny=any`, para B19 (el socket de PostgreSQL acepta a los usuarios con rol y rechaza a `kbot_sync` y `kbot_restore`; KuCoin inalcanzable), y `kbot-guardcheck@.service`, **sin ningún bloqueo propio**, para E4-D04: solo la regla del núcleo puede rechazar los metadatos, y se exige que lo haga para todos salvo `root` y `kbot_backup`. La regla actúa por identificador de usuario, así que ejecutarla como `kbot_sync` es el mismo contexto que su sesión SFTP, que no puede ejecutar programas.
- **`deploy/install`:** `01_sistema.sh` (idempotente: **primero el volumen de datos**, que debe estar conectado y, si ya está montado, ser el de etiqueta `kbot-data`; si no, se niega antes de crear nada, salvo `KBOT_ALLOW_BOOT_VOLUME=1` para una instancia de pruebas (E4a-03); después paquetes, usuarios de servicio con el nombre de su rol y sin shell, la raíz del archivo creada y sincronizada, `sshd`, el cortafuegos (hoy, el archivo IPv4 de la imagen sin cambios y `rules.v6`, que cierra IPv6; en E4a era `ufw` solo con SSH desde la IP del propietario), la guarda como servicio, `fail2ban`, actualizaciones, bloqueo y unidades, **sin verificarlas todavía**) y `03_codigo.sh` (hoy `02_codigo.sh`, por E4b-02; etiqueta aprobada, `uv` 0.13.7 descargado por HTTPS y comprobado contra `deploy/install/uv.sha256`, la huella que Claude verificó en el PC contra el archivo y el `.sha256` que publica la versión (E4a-07), `uv sync --frozen --no-dev`, código de solo lectura para los servicios, y al final `systemd-analyze verify` de las unidades, ya con su intérprete (E4a-02), que desde E4b-02 hace `04_verificar.sh`). `02_postgresql.sh` (hoy `03_postgresql.sh`) llega con E4b.
- **`kbot.ops`:** `python -m kbot.ops` es la comprobación automática de F2-A10 (puertos a la escucha, `sshd -T`, el cortafuegos (`iptables -S` e `ip6tables -S`; antes `ufw`), `fail2ban`, el servicio de la guarda y su regla, actualizaciones, usuarios sin shell y PostgreSQL sin TCP con `pg_hba` solo `local ... peer`), con informe para `docs/operacion/`. Salida, error y código de retorno van por separado: un comando que falla, un archivo ilegible o una salida que no se entiende hacen **fallar** su comprobación, nunca pasarla (E4a-06). el cortafuegos se compara con el esperado **cadena a cadena y en orden**, en IPv4 y en IPv6 (FW-01; en E4a se analizaba `ufw` fila a fila, E4a-05): las reglas de la imagen exactamente como vienen, IPv6 cerrado y, como única parte variable, lo que añade `fail2ban` al banear (el salto al principio de `INPUT` y su cadena, solo con bloqueos de una dirección y el `RETURN` final); cualquier otra regla, cadena o política, o una línea no entendida, hace fallar la comprobación; la guarda se comprueba sobre la salida **JSON** de `nft`: la regla de rechazo debe estar en una cadena con gancho `output`, **antes de cualquier `accept` incondicional** (una regla en una cadena que nadie llama no filtra nada), y eximir exactamente a los usuarios permitidos; un elemento del conjunto que el analizador no interpreta (un rango o un prefijo de identificadores) hace fallar la comprobación en lugar de descartarse, porque podría ampliar la exención (E4a-04 y sus residuales). Hoy los exentos son `root` y `snap_daemon`, con una sola lista para `netcheck` y `kbot.ops` (`METADATA_ALLOWED`) y la misma en la guarda; `kbot_backup` se añade en E4c en los tres. `kbot.ops.netcheck b19|guard` es el programa de las dos plantillas.
- **CI** (`tests/ci/test_deploy.py`): `shellcheck` y `bash -n` sobre los guiones, `systemd-analyze verify` sobre las unidades, `sshd -t` y `sshd -T` sobre el *drop-in* y `nft -c` sobre la guarda; más las pruebas unitarias de las comprobaciones sobre salidas grabadas. Desde FW-01, además, las reglas de la imagen (el archivo grabado en la instancia, `tests/fixtures/firewall/`) y `rules.v6` se cargan con el `iptables` del ejecutor (Ubuntu 24.04, la versión del servidor) en un espacio de red desechable, y `firewall_status()` se contrasta con lo que `iptables -S` imprime de verdad: las reglas tal cual, con un bloqueo de `fail2ban` y con el ejemplo del revisor.
- **Documentación:** `docs/operacion/comandos.md` (el diccionario de comandos de P-E4-1) y `docs/operacion/oracle_instancia.md` (la guía de la instancia para el propietario).

**Hecho en el servidor el 17 nov 2026** (`docs/operacion/instalacion_2026-11-17.md`, **aprobado** por el revisor el mismo día en `7409ca6`; queda mirar las métricas del agente en la consola): ejecutar `01_sistema.sh` (con el dispositivo del volumen: `/dev/sdb` en esta instancia) y `02_codigo.sh`, el informe de F2-A10, `kbot-netcheck@` y `kbot-guardcheck@` desde cada usuario, F1-A6 con los usuarios reales, la prueba del bloqueo REST entre dos usuarios y la comprobación de que el agente de Oracle sigue llegando a los metadatos y su monitorización continúa (`ss`, `kbot-guardcheck@snap_daemon` y las métricas en la consola).

**Implementado en E4b** (12 nov 2026; la parte que no necesita el servidor, **APROBADA** por el revisor el 16 nov tras dos revisiones, E4b-01 a E4b-06 cerrados):
- **`deploy/etc/postgresql/kbot.conf`:** `listen_addresses = ''`, `max_locks_per_transaction = 256` (§3.9), `shared_buffers` 3 GB y `effective_cache_size` 8 GB [R, §13; se miden en E5], `max_wal_size` 1 GB (la reserva de la proyección, límite blando), `wal_compression = zstd`, `max_connections = 60`, registro en UTC con todo el DDL, las consultas de más de 2 s, los puntos de control, las esperas de bloqueo y las conexiones.
- **`deploy/etc/postgresql/pg_hba.conf`:** **una sola regla**, `local all all peer`, sin mapa (P-E4-3).
- **Orden de la instalación (E4b-02):** `01_sistema.sh` → `02_codigo.sh` → `03_postgresql.sh` → `04_verificar.sh`. Solo el último verifica las unidades de systemd, cuando ya existen su intérprete y `postgresql.service`, y ejecuta todas las comprobaciones aunque falle alguna; ninguno de los tres primeros verifica dependencias que aún no están.
- **`03_postgresql.sh`** (exige `01` y `02` antes): PGDG para arm64 sin clúster por defecto, clúster `18/main` con sus datos en `/srv/kbot/postgresql` (comprobado), la configuración anterior, roles y base con **`kbot.ops.bootstrap`** y las migraciones `0001 → 0003` con Alembic como `postgres` y `SET ROLE kbot_owner` (el mismo `migrations/env.py` de la CI). No arranca ningún servicio.
- **`kbot.ops.bootstrap` (E4b-03):** aplica `sql/roles.sql` sentencia a sentencia en *autocommit* (`CREATE DATABASE` no admite transacción); un `CREATE ROLE` o `CREATE DATABASE` se salta solo si **su** objeto ya existe, y lo demás (`ALTER ROLE ... SET`, `GRANT`, `REVOKE`) es idempotente y se repite. Nada se borra nunca, así que una ejecución cortada en cualquier punto se completa repitiéndola; el resultado lo decide `pgcheck`.
- **`kbot.ops.pgcheck`** (como `postgres`, con `--repo` el repositorio desplegado): la huella de las funciones `SECURITY DEFINER` contra `sql/security_definer_catalog.json`; los atributos de los roles de §9; **ningún ajuste de rol ligado a una base de datos** (prevalecería sobre el global y cambiaría el `statement_timeout` real, E4b-05); **`kbot.ops.privileges`**, las mismas matrices de la CI: pertenencias (cualquiera salvo `kbot_backup` en `pg_read_all_data`, tenga o no `INHERIT`, permitiría `SET ROLE`), privilegios efectivos de tablas, esquemas, **funciones** y base de datos, incluido `PUBLIC`, propietarios y privilegios por defecto (E4b-04); `pg_hba_file_rules` solo `local peer` y sin opciones (en la CI se admite su mapa); `listen_addresses` vacío; `max_locks_per_transaction ≥ 256`; y que la migración aplicada sea **exactamente el head del código** desplegado (E4b-06).
- **CI** (`tests/db/test_pgcheck.py`): el clúster de la CI no tiene hallazgos (salvo el mapa, sin la tolerancia), y cada hallazgo de la revisión se reproduce en vivo, falla y se deshace: la pertenencia `kbot_owner` a `kbot_ingest` con `INHERIT FALSE, SET TRUE`, `EXECUTE` sobre `market.retire_day(date)`, `ALTER ROLE kbot_ingest IN DATABASE kbot SET statement_timeout = 0`, una migración anterior, desconocida o vacía; y `bootstrap` repetido sobre el clúster completo no crea nada.
- **`deploy/install/f1a6_servidor.sh`** (punto 31; E4b-01): con los usuarios reales, cada usuario de servicio conecta con su rol y con ningún otro y ve su `statement_timeout`; `kbot_sync`, `kbot_restore` y `nobody` son rechazados; los propietarios no pueden entrar. Un rechazo de SQL solo cuenta con el **SQLSTATE 42501** (`VERBOSITY=sqlstate`), nunca por el código de salida de `psql` (que es 1 para cualquier error de SQL) ni por un fallo de conexión; cada `CREATE` va en una transacción que se deshace, y nadie puede hacer `SET ROLE kbot_owner`.

**Hecho en el servidor el 17 nov 2026:** `03_postgresql.sh` y `04_verificar.sh`, con su salida en `docs/operacion/instalacion_2026-11-17.md` y `docs/operacion/comprobacion_2026-11-17.md` (**aprobado** por el revisor el 17 nov).

**Implementado en E4c-1** (17 nov 2026; las copias locales, **APROBADAS** por el revisor el 18 nov tras tres revisiones, E4c1-01 a E4c1-04 cerrados, en `8560778`; **instaladas en el servidor** el 17 nov a las 23:53 UTC con `v0.2.4-e4c1` (`docs/operacion/copias_2026-11-18.md`), pendiente de revisión; Object Storage llega con E4c-2):
- **`kbot.backup`** (`python -m kbot.backup run`, cada hora como `kbot_backup`):
  1. borra los intentos que dejó una ejecución detenida y **toda copia publicada que el registro no avale**, que vuelve así a quedar pendiente (E4c1-04);
  2. hace la **copia de cada día sellado** que aún no la tiene, una sola vez: los datos de sus doce particiones (`pg_dump --data-only`), con el recuento y la **huella canónica de cada partición, la misma que la de su archivo de E3** (`archiver.day_fingerprint`), de modo que una copia y una generación del mismo día se comparan sin leer ninguna;
  3. hace la **copia caliente** de toda la base, desde una sola instantánea exportada que sigue abierta mientras se leen los sellos, **cada tabla y cada partición con su modo (`incluido`), sus filas y su huella canónica** (§10.1; E4c1-02), la huella del catálogo (por nombre, con los sellos `NOT VALID` y **el particionado: la clave, el padre y los límites de cada partición**; E4c1-03) y **las versiones de §10.1** (PostgreSQL, `pg_dump`, extensiones, Alembic, sistema y arquitectura, más `age`, Python y `kbot`), y mientras corre `pg_dump -Fc --compress=zstd:3 --snapshot`. Una partición sellada no puede cambiar, así que su huella es la que tomó una vez su copia de día sellado; la copia caliente la cuenta de nuevo en su instantánea y **se detiene si las filas no coinciden**, en lugar de recorrer cada hora todos los días sellados;
  4. aplica la retención local: las 3 últimas horarias y la última diaria (la primera de cada día UTC); la copia de un día sellado se queda hasta que su día es `elegible_para_exclusion_backup`.
- **Cifrado:** cada volcado se cifra con `age` para los dos destinatarios de `deploy/etc/kbot/age-recipients.txt` (solo claves públicas; una prueba prohíbe una privada), y **el volcado en claro se borra antes de publicar**. Una copia es un directorio con `volcado.age` y `manifiesto.json`. **Primero se registra y después se publica** al renombrarlo (E4c1-04): el SHA-256 del volcado en claro, el del cifrado **y el del manifiesto entero** van al registro (`registro.jsonl`, que solo escribe su autor) antes del renombrado. Así, un manifiesto cambiado después, aunque sea en un dígito de una huella, deja de estar avalado: nunca se reutiliza, se borra y la copia se hace de nuevo (residual de E4c1-02). Toda copia publicada tiene su entrada, así que toda copia publicada tiene su entrada, con la que se autentica el volcado antes de restaurar (R-E4-04). Si un paso falla, el intento se borra al momento con lo que tuviera dentro; un fallo entre el registro y el renombrado deja como mucho una entrada sin copia, que no autentica nada que exista; una línea cortada por un disco lleno se ignora y la entrada siguiente empieza en línea nueva. Solo una caída del proceso puede dejar un intento, que la ejecución siguiente quita.
- **Objetos globales** (`python -m kbot.backup globals`, cada domingo como `postgres`): `pg_dumpall --globals-only`, cifrado igual, en `/srv/kbot/backups/globales` (`postgres:kbot_backup`, con el bit setgid), se conservan los 2 últimos. Para llegar a su directorio debe atravesar `/srv/kbot/backups` (`kbot_backup`, `0750`), así que su unidad lleva `SupplementaryGroups=kbot_backup`: solo esa unidad, y la escritura sigue siendo solo de `kbot_backup` (E4c1-01).
- **Unidades:** `kbot-backup.service` y su temporizador (minuto 40 de cada hora, lejos del archivo diario de las 00:20 UTC, cuyo sellado no debe esperar a los bloqueos de un volcado) y `kbot-backup-globals.service` y el suyo. Las dos **sin red** (`IPAddressDeny=any`, solo `AF_UNIX`, el socket de PostgreSQL) y con el aislamiento de las demás, salvo que no niegan `@resources`, porque `age` está escrito en Go y sube su límite de archivos al arrancar.
- **`05_copias.sh`:** instala las claves públicas (se detiene ante una privada), los directorios y las unidades; hace una primera copia de cada clase como comprobación (se detiene si no se publica o si queda un volcado en claro) y activa los temporizadores.
- **Pruebas:** las unitarias de retención, intentos y destinatarios; en la CI, contra PostgreSQL y con dos identidades de prueba (nunca las del propietario): una fila insertada después de la instantánea no se cuenta ni se vuelca; el volcado se descifra con cualquiera de las dos identidades y su SHA-256 es el del manifiesto; la copia de un día sellado tiene las mismas filas y huellas que su generación de E3; una copia que falla no deja nada en el disco, tampoco el volcado en claro; los objetos globales llevan los roles. Tras la revisión: la copia caliente reutiliza las huellas de los días sellados y se detiene ante unas filas distintas; una fila cambiada con el mismo recuento cambia la huella de su tabla; mover el límite de una partición cambia la huella del catálogo, y devolverlo la repite; el registro antes de publicar, con fallos inyectados en el registro y en el renombrado, una línea cortada y una copia sin su entrada; y, como root en la CI con los usuarios, grupos y modos reales de `05_copias.sh`, `postgres` llega a su directorio solo con el grupo de su unidad y `kbot_backup` lee la copia, pero no puede escribir allí.
- **Hasta E4c-2, ningún día sellado tiene una alternativa externa verificada** (R-E4-01): la copia caliente lo lleva todo. Su tamaño y su duración crecen con los días calientes y se miden en el servidor; la exclusión llega con la subida y la relectura del cubo.

**E4c-2: diseño de detalle de la subida a Object Storage** [26 nov 2026; **APROBADO** por el revisor en `ae0b217`, tras tres revisiones, con E4C2-01 a E4C2-03 resueltos e incorporados abajo; su código, **APROBADO** en `8a83cc2` tras cinco revisiones, en la misma rama y sin instalar; la copia caliente sube al cubo **cada 6 horas**, por decisión del propietario del 26 nov 2026 (abajo, «Implementado en E4c-2»)]. Aplica el diseño aprobado de este punto 4 (cuarta revisión, 11 nov 2026). Precisa lo que aquel dejaba abierto y corrige dos cosas que no contaba: el **límite de peticiones** del nivel gratuito y **quién tiene red**.

- **Lo medido en el servidor** el 26 nov 2026, con el grabador desde el 17 nov:
  - la copia caliente cifrada ocupa unos **577 MB** y tarda unos 6 minutos (5 min 48 s de CPU). Lleva todos los días, porque ninguno tiene aún alternativa externa;
  - cada copia de día sellado con datos ocupa unos **63 MB**;
  - los objetos globales, unos 4 KB;
  - en total, 2,5 GB en `/srv/kbot/backups`.
- **Lo que fija Oracle para el nivel gratuito** [H, página de recursos Always Free, leída el 26 nov 2026]:
  - **20 GB** entre los tres niveles de almacenamiento;
  - **50.000 peticiones de la API de Object Storage al mes**. El diseño aprobado no las contaba.
  - **Las versiones anteriores ocupan** como las actuales, también las de objetos borrados, hasta que se eliminan de verdad [H, página de versionado, confirmado en la revisión de E4C2]. El presupuesto y la simulación las cuentan hasta entonces.
  - **La subida en partes** (`CreateMultipartUpload`, `UploadPart` y `CommitMultipartUpload`) exige `OBJECT_OVERWRITE`, aunque el nombre sea nuevo [H, referencia de políticas de Object Storage, E4C2-01]. Un solo `PutObject` admite hasta **50 GiB** [H, límites de Object Storage].

**1. Dos procesos, y solo uno con red** [precisión nueva, para revisar]
- **`kbot-backup.service`** (`kbot_backup`) sigue **sin red**: vuelca, cifra, registra y publica en local, como en E4c-1. Además decide qué días sellados omite la copia caliente (punto 4).
- **`kbot-upload.service`**, nuevo, con un usuario del sistema propio, `kbot_upload`:
  - **sin rol de PostgreSQL** y sin acceso a la base;
  - lee solo las copias ya publicadas, que están cifradas, y sube, relee, aplica la retención y escribe su estado en `/srv/kbot/subidas` (`kbot_upload`, `0750`, con el grupo `kbot_backup` para que el otro proceso lo lea).
- **El aislamiento de metadatos de E4** (E4-D04) exime solo a `kbot_upload`, nunca a `kbot_backup`. Con esa exención, el proceso que lee toda la base tendría a la vez red y los testigos de la instancia. Separados:
  - quien lee la base no tiene red;
  - quien tiene red solo ve copias cifradas, con claves públicas.
- **Lo que cambia en el sistema:**
  - el guardián de metadatos, `netcheck` y `check` se amplían juntos, con el nombre del usuario y no con un uid fijo, como pedía E4c. `nft` resuelve `skuid "kbot_upload"` al cargar la regla [H], así que `05_copias.sh` crea el usuario antes de cargarla;
  - las copias pasan a `0640` y sus directorios a `0750`, con el grupo `kbot_upload` en lectura (los directorios llevan `setgid`, así que cada copia nueva hereda ese grupo). `kbot_backup` cambia su `UMask` a `0027`;
  - el registro sigue siendo solo de su autor.
- **La red de `kbot-upload`:**
  - `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`;
  - sin lista de IP de Oracle (R-E4-03): el SDK usa solo los puntos de acceso del servicio de autenticación y de Object Storage de Frankfurt;
  - el resto del aislamiento, como en `kbot-backup`.

**2. El cubo y sus permisos** (guía del propietario: `docs/operacion/copias_object_storage.md`)
- **El cubo:** `kbot-backups`, privado, en Frankfurt, nivel Standard, **con versionado** y sin escalonado automático.
- **Grupo dinámico** `kbot-servidor`, con la regla `instance.id = '<OCID de la instancia>'`. Así solo esta instancia, no todo el compartimento. La política lo nombra `'Default'/'kbot-servidor'`, con su dominio de identidad [H, sintaxis de sujetos de IAM].
- **Política** `kbot-copias`, en el compartimento raíz:
  - `manage objects` en el cubo, con `OBJECT_CREATE`, `OBJECT_INSPECT`, `OBJECT_READ` y `OBJECT_DELETE`, y **sin `OBJECT_OVERWRITE` ni `OBJECT_VERSION_DELETE`** [H: referencia de políticas de Object Storage]. Por eso no hay subida en partes, que también exige `OBJECT_OVERWRITE` (E4C2-01, punto 3);
  - `read buckets` en ese cubo, para que el servicio compruebe al arrancar que el versionado está activo. Si no lo está, no sube nada.
- **Regla de ciclo de vida:** borrar las **versiones anteriores** a 1 día (`target: previous-object-versions`, `DELETE`, 1 día). Oracle la ejecuta solo si el propio servicio tiene permiso: `Allow service objectstorage-eu-frankfurt-1 to manage object-family in tenancy where target.bucket.name = 'kbot-backups'` [H: Oracle publica esa forma con `target.bucket.name`]. Que la regla borre de verdad las versiones anteriores se comprueba en la instalación (punto 7).
- **Sin `OBJECT_OVERWRITE`,** ningún nombre se sube dos veces. Además, cada `PutObject` lleva `If-None-Match: *` [H, referencia de `PutObject`], así que no sobrescribiría ni con ese permiso. Cada objeto lleva el nombre único de su copia:
  - `caliente/<nombre>/volcado.age` y `caliente/<nombre>/manifiesto.json`;
  - lo mismo en `dia/`, `globales/` y `previa/`.

  No hay un índice en el cubo, porque cambiarlo exigiría sobrescribir.
- **`GetNamespace` no necesita permiso** [H]. El servicio lo pide una vez y lo guarda en el estado. Cuenta como petición (punto 5).

**3. La subida, la relectura y la retención** (`python -m kbot.upload`, cada hora en el minuto 55, cuando la copia de las 40 ha terminado)
1. Comprueba que el versionado está activo y lista **todas las versiones** del cubo, paginadas. Con ellas tiene el espacio ocupado y lo que hay.
2. **Presupuesto** (E4-D07):
   - si lo ocupado más lo que va a subir pasa de **19 GB**, no sube;
   - lo registra como error, y el estado da la antigüedad de la última subida completa, que es el RPO frente a la pérdida del servidor.
3. **Qué sube, en orden:**
   - las copias de día sellado que faltan;
   - la copia caliente más reciente;
   - los objetos globales nuevos;
   - las copias previas a una actualización.

   Sube solo las que su registro avala, y de cada una el volcado y el manifiesto.

   **Cada objeto va en un único `PutObject`, nunca en partes** (E4C2-01):
   - el servicio no usa `UploadManager` ni ninguna llamada de subida en partes;
   - el límite de 50 GiB de un `PutObject` queda muy por encima de todo el cubo (19 GB);
   - un `PutObject` que falla no crea el objeto, y se repite entero, contando cada intento (punto 5);
   - si se pierde la respuesta y el reintento da `412` porque el objeto ya existe, lo relee: vale si su SHA-256 coincide, y si no, queda como error sin verificar.
4. **Relectura:** lee de nuevo cada objeto subido y comprueba su SHA-256 contra el registro. Solo entonces lo anota como `verificada_fuera`, con su nombre, su SHA-256, la huella de su manifiesto y la hora.
5. **Retención D25**, contando solo las copias verificadas:
   - **horarias:** las 3 copias calientes más recientes;
   - **diarias:** la primera de cada día UTC, de los 7 últimos días;
   - **semanales:** la primera de cada semana ISO (del lunes UTC), de las 2 últimas;
   - **previas a una actualización:** las 2 últimas;
   - **globales:** las 2 últimas.

   Una copia puede estar en varias clases, y se guarda una vez. Las demás se borran con `DeleteObject`, que con versionado deja la versión anterior durante 1 día.
6. **Copias de día sellado:** se borran solo con la condición del residual de R-E4-01. Su día ha de ser `elegible_para_exclusion_backup`, y cada copia retenida que la fija ha de poder restaurarse sin ella con las identidades de su propio manifiesto. El índice de dependencias se guarda en `/srv/kbot/subidas` y se rehace leyendo los manifiestos del cubo si falta.
7. **El estado** (`/srv/kbot/subidas/estado.json`, que solo escribe `kbot_upload`) da:
   - las copias verificadas fuera y cuándo se vieron por última vez en el listado;
   - lo ocupado y las peticiones contadas (punto 5);
   - cada intento de listado: cuándo empezó y si terminó bien (punto 4);
   - la antigüedad de la última subida completa.

**4. Qué omite la copia caliente** (R-E4-01, del lado de `kbot-backup`)
- Omite los datos de un día sellado solo si `estado.json` dice que su copia de día está verificada fuera y que estaba en el último listado del cubo. **Además, ese listado ha de cumplir tres condiciones** (E4C2-02):
  - **es el último intento:** `kbot-upload` anota en el estado cuándo empieza cada listado, antes de pedirlo, y si termina bien. Si el intento siguiente falla o no termina, no se omite nada;
  - **tiene menos de 1 hora;**
  - **ninguna copia caliente lo ha usado antes:** cada listado autoriza omisiones en una sola copia caliente. El manifiesto guarda su identificador, y `kbot-backup` no repite el de la copia anterior.
- El manifiesto fija la dependencia: el nombre del objeto, su SHA-256, la huella de su manifiesto y la hora de esa verificación.
- **Sin estado, o con uno viejo, no omite nada:** la copia crece, pero nunca queda incompleta.
- **Límite declarado:** si el propietario borra una copia de día desde la consola, **como mucho una copia caliente omite ese día:** la primera que empieza tras el borrado y antes del listado siguiente.
  - La copia de después lo incluye siempre: tanto si ese listado detecta el borrado como si falla o no llega a hacerse.
  - Con la copia a las :40 y la subida a las :55, el día vuelve a estar en una copia caliente terminada como mucho unas 2 h después del borrado.
  - Mientras tanto, el objeto borrado sigue como versión anterior durante al menos 1 día.
- **El caso de la revisión:** listado bueno a las 10:55 y borrado a las 10:55:01. La copia de las 11:40 omite el día, con el listado de las 10:55, de 45 minutos. Falla el listado de las 11:55. **La de las 12:40 incluye el día** por tres razones: el último intento falló, el listado de las 10:55 tiene 1 h 45 min, y ya lo usó la copia de las 11:40.
- **Las copias previas a una actualización** son autocontenidas (punto 4): `python -m kbot.backup run --previa` no omite nada. `02_codigo.sh` la pedirá antes de cambiar el código, cuando E4c-2 esté instalada.

**5. Peticiones: la cuenta y un tope obligatorio** [precisión nueva; E4C2-03]
- **Una hora normal** son unas 9 peticiones:
  - la comprobación del versionado, 1;
  - el listado de versiones, 1 por página;
  - la copia caliente y su manifiesto, 2 `PutObject`;
  - la relectura, 2 `GetObject`;
  - el borrado de la copia caliente que sale de la retención, 2 `DeleteObject`;
  - 1 de margen para la paginación.
- **Un día** suma la copia del día sellado: 2 subidas y 2 lecturas. **A la semana,** la de los globales: 4.
- **En total,** unas **7.000 peticiones en 31 días**, de las 50.000. Sin subida en partes son menos que en la primera versión del diseño.
- **El tope obligatorio: 45.000 peticiones en cualquier ventana de 32 días UTC seguidos.**
  - Oracle cuenta por mes. Cualquier mes, empiece cuando empiece y en la zona horaria que sea, cabe en 32 días UTC seguidos. Así el tope no depende de cuándo empieza el mes de Oracle.
  - Los 5.000 de margen cubren lo que el servicio no ve: la consola del propietario y el ciclo de vida.
- **La reserva, antes de enviar:**
  - antes de cada petición, el servicio suma 1 al contador `/srv/kbot/subidas/peticiones.json`, que lleva la cuenta de cada día UTC;
  - lo hace persistente con esta secuencia, y solo entonces envía (residual de E4C2-03): **escribe el archivo temporal → `fsync` del archivo → cambio de nombre → `fsync` del directorio `/srv/kbot/subidas` → la petición HTTP**. Sin el `fsync` del directorio, una caída del servidor podría devolver el contador anterior, que sigue siendo válido, y dejar pasar una petición de más;
  - **si falla cualquier paso de persistencia, no envía la petición:** registra el error y termina el ciclo;
  - cuenta así cada página de un listado, cada reintento, `GetNamespace` y la comprobación del versionado;
  - si la reserva pasaría del tope, no envía nada: registra el error con la antigüedad de la última subida completa, que es el RPO, y termina el ciclo;
  - una caída tras reservar deja la petición contada aunque no saliera. Se equivoca hacia el lado seguro, nunca por debajo;
  - `estado.json` se escribe con la misma secuencia, también la anotación de cada intento de listado (punto 4).
- **Sin reintentos ocultos:** los reintentos automáticos del SDK van desactivados. Cada reintento pasa por la reserva.
- **Operaciones enteras:** antes de una operación de varias peticiones, comprueba que lo que queda la cubre entera. Por ejemplo, subir y releer una copia son 4. Si no la cubre, no la empieza.
- **Por encima de 40.000 en 32 días,** solo hace lo imprescindible: el listado, y la subida y la relectura de las copias de día. La copia caliente se queda en el servidor, y lo registra como error.
- **Si el contador falta o no se puede leer,** no envía nada y lo registra como error. `05_copias.sh` lo crea vacío en la instalación.
- **Sin listado, la copia caliente no omite nada** (punto 4). Agotar las peticiones la hace más grande, pero nunca incompleta.
- **La relectura descarga cada copia entera:** con la copia caliente de hoy, que aún no omite días, unos 415 GB al mes. El tráfico dentro de la región no se cobra [P]. Aunque contara como salida, los 10 TB gratuitos al mes [H, página de Always Free] lo cubren.

**6. La simulación del presupuesto** (`tools/simular_copias.py`, en la CI)
- **Su entrada:**
  - los tamaños medidos (la copia caliente sin días omitidos, de unos 4 días calientes y las tablas pequeñas, y los 63 MB de cada día);
  - un crecimiento declarado;
  - la retención anterior;
  - las versiones anteriores, contadas hasta que se eliminan de verdad: el día de la regla más un retraso declarado del ciclo de vida de 2 días [P: Oracle no da un plazo];
  - las peticiones, con el tope de 32 días;
  - la cadencia del disco externo.
- **Lo que da:** el máximo del cubo antes de cada subida durante al menos **120 días**, y la fecha en que llegaría a 19 GB.
- **Mientras no exista E4c-3,** ninguna generación queda `verificado_fuera`. Así ningún día se hace elegible, y sus copias de día se quedan en el cubo: unos 63 MB al día. La simulación da cuánto tiempo cabe y, con eso, para cuándo hace falta E4c-3. **No se usa ninguna cota fija.**

**7. Pruebas** (en la CI, con un Object Storage falso en memoria que aplica el versionado y los permisos de la política)
- **Sin sobrescritura ni subida en partes** (E4C2-01):
  - el falso rechaza `CreateMultipartUpload`, `UploadPart` y `CommitMultipartUpload` sin `OBJECT_OVERWRITE`, como Oracle, y el servicio nunca las llama;
  - un volcado de más de 128 MiB sube en un único `PutObject` y se relee;
  - subir un nombre que ya existe falla, por `If-None-Match: *` y por falta de permiso, y el objeto anterior sigue igual;
  - un `412` tras una respuesta perdida se resuelve releyendo: vale solo si el SHA-256 coincide.
- **Relectura:** una copia cambiada en el cubo no queda verificada.
- **Presupuesto:**
  - pasar de 19 GB no sube nada y deja el error con la antigüedad de la última subida;
  - pasar de 40.000 peticiones deja solo lo imprescindible;
  - **el tope** (E4C2-03): con 0, 1, 2 y 3 peticiones restantes, con un listado de 2 páginas, con un reintento y con un reinicio entre dos ciclos, en el que un proceso nuevo lee el contador. El falso cuenta lo que recibe, y nunca recibe una petición que pase del tope;
  - una reserva sin envío por un fallo sigue contada, y los días que salen de la ventana de 32 días dejan de contar;
  - sin contador, o con uno ilegible, no envía nada;
  - **la persistencia de la reserva** (residual de E4C2-03): las operaciones del sistema de archivos se registran y su orden es exactamente: temporal, `fsync` del archivo, cambio de nombre, `fsync` del directorio, y después la petición. Un fallo inyectado en cada uno de esos pasos, también en el `fsync` del directorio, deja la petición sin enviar.
- **La retención D25** sobre 30 días de copias horarias, con las clases que se solapan.
- **El caso de aceptación del residual de R-E4-01:** la copia caliente H retenida fija S y G1, y se pide el borrado de S. Solo procede con G1 verificada fuera; con G2 elegible y G1 no, S se conserva hasta que H expire.
- **La omisión del lado de `kbot-backup`:**
  - con estado verificado y reciente, se omite;
  - con estado viejo o ausente, o con la copia de día que falta en el último listado, se incluye;
  - **el caso de la revisión** (E4C2-02): listado bueno a las 10:55, borrado a las 10:55:01, la copia de las 11:40 omite el día, falla el listado de las 11:55 y la de las 12:40 lo incluye;
  - sus variantes: el listado de las 11:55 detecta el borrado; no hay intento a las 11:55; el intento empieza y no termina. En todas, solo la copia de las 11:40 omite el día;
  - la copia previa nunca omite.
- **El índice de dependencias** se rehace desde los manifiestos.
- **Los permisos del sistema,** como root en la CI:
  - `kbot_upload` lee las copias y no puede escribirlas;
  - `kbot_backup` lee el estado y no puede escribirlo;
  - el guardián de metadatos deja pasar a `kbot_upload` y rechaza a `kbot_backup`.
- **La simulación de 120 días.**
- **En el servidor,** con informe en `docs/operacion/`:
  - la subida de prueba sin credenciales, que cierra el punto 33;
  - con la política exacta, un archivo de más de 128 MiB sube sin partes y se relee, y repetir su nombre falla sin cambiar el objeto (E4C2-01);
  - la regla de ciclo de vida borra de verdad una versión anterior;
  - 24 h de copias en el cubo con la retención aplicada;
  - la relectura;
  - el presupuesto y las peticiones medidas.

**8. Dependencia nueva:** el SDK de Oracle para Python (`oci`, licencias UPL 1.0 y Apache 2.0), fijado en `uv.lock`. Solo lo importa `kbot.upload`.

**9. Qué decide el propietario:**
- crear el cubo, el grupo dinámico, la política y la regla de ciclo de vida, siguiendo la guía;
- la instalación en el servidor, con su permiso, como siempre.

**Revisión del diseño de detalle de E4c-2 (26 nov 2026, `b4c42f6`): APROBABLE CON CORRECCIONES.** El revisor da por coherente la separación entre `kbot_backup` sin red y `kbot_upload` sin acceso a PostgreSQL. Hallazgos, los tres MEDIOS e incorporados arriba:
- **E4C2-01:** la subida en partes exige `OBJECT_OVERWRITE`, que la política excluye → cada objeto en un único `PutObject`, sin partes, con su prueba en la CI y en el servidor;
- **E4C2-02:** el listado de 2 horas dejaba que dos copias calientes omitieran un día borrado, contra el límite de una hora → el listado ha de ser el último intento, de menos de 1 hora, y sirve a una sola copia caliente;
- **E4C2-03:** el modo imprescindible podía pasar de 50.000 peticiones → un tope obligatorio de 45.000 en cualquier ventana de 32 días, reservado en el disco antes de cada petición, con páginas y reintentos.

Las cuatro dudas [P] del diseño, contrastadas por el revisor: la política del servicio con `target.bucket.name` está publicada, y que el ciclo de vida se ejecute se comprueba en la instalación; `'Default'/'kbot-servidor'` es la sintaxis documentada; las versiones anteriores cuentan en el espacio hasta que se eliminan; y `nft` admite el nombre del usuario si existe antes de cargar la regla.

**Tercera revisión del diseño de detalle de E4c-2 (26 nov 2026, `ae0b217`): APROBADO.** E4C2-03, RESUELTO: la secuencia sincroniza el archivo y, tras el cambio de nombre, el directorio antes de la petición HTTP; cualquier fallo de persistencia impide el envío, y `estado.json` tiene la misma garantía. E4C2-01 y E4C2-02 siguen RESUELTOS. Sin residuales ni hallazgos nuevos. La aprobación es del **diseño**: las pruebas nuevas, la simulación y las comprobaciones reales en Oracle se verifican con el código.

**Segunda revisión del diseño de detalle de E4c-2 (26 nov 2026, `79fac56`): APROBABLE CON CORRECCIONES.** E4C2-01 y E4C2-02, RESUELTOS. E4C2-03, PARCIAL: el tope y la reserva son coherentes, pero la secuencia sincronizaba el archivo temporal y lo renombraba sin sincronizar el directorio, y tras una caída podía reaparecer el contador anterior. Corregido arriba con la secuencia completa y su prueba. Sin hallazgos nuevos. El revisor acepta los 2 días de retraso del ciclo de vida como supuesto de la simulación: un retraso mayor puede alargar el RPO, pero el límite sobre lo ocupado de verdad sigue actuando.

**Implementado en E4c-2** [26 nov 2026, rama `fase-2-e4c2`; **APROBADO** por el revisor el 27 nov 2026 en `8a83cc2`, tras cinco revisiones del código (`cc79eea`, `24ba53a`, `d3a4c5b`, `7b4c374` y `8a83cc2`), con E4C2-04 a E4C2-11 resueltos e incorporados abajo; **sin instalar**: quedan las comprobaciones reales en Oracle, con la subida de prueba de `05_copias.sh`]. Aplica el diseño de detalle aprobado en `ae0b217`.

- **El código:**
  - `kbot.upload`:
    - `service` es el ciclo (puntos 3 y 4);
    - `budget` es el contador con su tope, y `durable` la secuencia de persistencia (punto 5);
    - `store` define lo que el servicio pide a Object Storage, y `CountedStore` cuenta cada petición, cada página y cada reintento;
    - `oci_store` habla con Oracle con el principal de instancia, sin reintentos del SDK;
    - `retention` aplica D25 y el residual de R-E4-01;
    - `state` es el estado que lee `kbot-backup`.
  - **`kbot.backup`:** `omissions` decide qué días omite la copia caliente, y `previa_copy` hace la copia previa a una actualización (`run --previa`).
  - **Las unidades:** `kbot-upload.service`, con su temporizador a los :55, y `kbot-backup-previa.service`.
  - **La simulación:** `tools/simular_copias.py`.
  - **La dependencia nueva:** `oci` 2.189.0. Todas sus dependencias tienen versión para aarch64.
- **Precisiones de la implementación:**
  - **`kbot_upload` no tiene acceso a la base.** Lo que necesita para borrar una copia de día lo lee del manifiesto de la copia caliente más reciente, que lo leyó en su instantánea: los días elegibles y las generaciones verificadas fuera (`archivo`). Ese manifiesto está avalado por el registro.
  - **Un día que ya es elegible no se vuelve a subir:** su copia puede acabar de salir del cubo.
  - **Cada omisión se ata al listado** (E4C2-02 y E4C2-04):
    - antes de hacer la copia caliente, `kbot-backup` anota de forma persistente, en `/srv/kbot/backups/listado_consumido.json`, el listado en el que se apoya, aunque luego no omita nada o la copia falle;
    - un listado anotado ya no autoriza nada más, haga lo que haga la copia de en medio, y aunque se reinicie el proceso o la retención local borre copias. Si la anotación no se puede leer, no se omite nada;
    - el manifiesto guarda también ese identificador (`listado`).

    Cada día omitido lleva la identidad de su copia en el cubo y la generación de ese día (la más reciente, en la instantánea).
  - **Una copia de día rehecha en el servidor con el mismo nombre** se cifra distinto, pero lleva los mismos datos. La copia del cubo se queda, porque nunca se sobrescribe. Se verifica con su propio manifiesto: tiene que avalar el volcado que hay en el cubo y tener las mismas particiones, filas y huellas, comparadas con un SHA-256 de esa lista. La copia caliente omite el día solo si esa comparación coincide.
  - **Los restos de un ciclo interrumpido** (copias calientes, previas o globales que nadie verificó, que no están en el servidor y tienen más de 2 días) se borran. Las copias de día solo salen por la regla del residual.
  - **El RPO frente a la pérdida del servidor** (E4C2-06):
    - `ultima_subida_completa` es la hora de la copia caliente más reciente verificada en el cubo con todas las copias de día de las que depende, y `ultima_caliente_fuera` su nombre;
    - un ciclo que no sube nada nuevo, o solo los objetos globales, no los mueve, y una copia caliente cuya copia de día sale del cubo deja de contar;
    - el último ciclo sin errores va aparte, en `ultimo_ciclo_sin_errores`;
    - **cada dependencia cuenta con su identidad exacta** (E4C2-09): el índice guarda, de cada copia de día de la que depende una copia caliente, el objeto y el SHA-256 de su volcado y de su manifiesto, tal como los fija el manifiesto de la caliente. La caliente cuenta solo si la copia de día verificada en el cubo tiene esa misma identidad. Si el índice no la tiene, se vuelve a leer del manifiesto en el cubo.
  - **Cada verificación se ata a la versión leída** (E4C2-09): la lectura de cada objeto devuelve su versión (la cabecera `version-id` de `GetObject`), y el estado la guarda. Si el listado ve otra versión en su lugar, por ejemplo porque el propietario repuso un objeto desde la consola, la verificación se deshace y la copia se vuelve a leer antes de que nada dependa de ella.
  - **Si el reloj retrocede** (E4C2-08), el contador sigue contando las reservas de los días que ahora parecen futuros, hasta que salen de la ventana. La cuenta solo puede equivocarse hacia arriba.
  - **La versión actual de un objeto** es la más reciente por su hora, porque el listado no la marca, y solo si ninguna otra versión comparte esa hora (E4C2-10). Ni la posición en el listado ni el identificador de versión ordenan dos versiones de la misma hora. Un objeto así nunca es actual, así que ninguna verificación suya se mantiene:
    - si una de las versiones empatadas es una marca de borrado, puede no existir, y se intenta subir;
    - si ninguna lo es, es **ambiguo**: no se sube, se vuelve a leer, y deciden la versión y el SHA-256 que llegan.
  - **El presupuesto de espacio cuenta solo lo que se subiría** (E4C2-11): los bytes de los objetos de la copia que no están en el cubo, ni actuales ni ambiguos. Volver a leer lo que ya está no añade nada, así que en el límite se puede releer, y una subida que sí añadiría bytes por encima de 19 GB se sigue rechazando.
  - **Una copia de día sale del cubo solo cuando la copia caliente más reciente ya omite ese día sin ella,** por su generación verificada fuera (la alternativa 2 de este punto, que llega con E4c-3). Si saliera antes, las copias calientes siguientes volverían a llevar el día hasta que saliera de PostgreSQL.
- **Los permisos en el servidor:**
  - `/srv/kbot/backups` pasa a `kbot_backup:kbot_upload`, `2750`, y `globales` a `postgres:kbot_upload`, `2750`. `kbot_backup` ya no lee los globales: los sube `kbot_upload`.
  - Las tres unidades que escriben copias usan `UMask=0027`, y la de los globales tiene `SupplementaryGroups=kbot_upload`.
  - `05_copias.sh` pasa al grupo nuevo también las copias hechas en E4c-1, que eran solo de `kbot_backup`.
  - `/srv/kbot/subidas` es `kbot_upload:kbot_backup`, `2750`. El contador lo crea `05_copias.sh` con cero peticiones.
- **La red de `kbot-upload`:**
  - `IPAddressDeny=` las redes privadas, `100.64.0.0/10`, el enlace local y sus equivalentes IPv6;
  - `IPAddressAllow=169.254.169.254`, para el principal de instancia.
  - El DNS va por `systemd-resolved`, en el bucle local.
- **El guardián de metadatos** exime a `kbot_upload` por su nombre, en IPv4 y en IPv6. `01_sistema.sh` crea el usuario para un servidor nuevo, y `05_copias.sh` para este, antes de recargar la regla. Comprueba desde el contexto real que `kbot_upload` llega a los metadatos y que `kbot_backup`, `kbot_ingest`, `kbot_archive` y `kbot_sync` no.
  - **La comprobación del servidor** (E4C2-07): `nft -j` imprime los usuarios por su número, y el de `kbot_upload` no es fijo. `kbot.ops` los traduce con `/etc/passwd` y compara el conjunto exacto; si no puede leer ese archivo, la comprobación falla.
- **`02_codigo.sh`,** una vez instalada E4c-2, arranca antes de cambiar el código la copia previa y la de los objetos globales.
- **`05_copias.sh` termina con una primera subida de prueba.** Para hacerla tienen que existir el cubo, el grupo dinámico y la política de la guía. Se detiene si el listado no terminó bien o si la subida no quedó completa.
- **Lo que dio la simulación** (26 nov 2026, con lo medido; `python -m tools.simular_copias`).
  - **Corregida en E4C2-05:** la copia previa es la que hace el código, autocontenida, con todos los días que siguen en PostgreSQL. Un día elegible sigue en la base hasta que tiene más de 90 días, así que sigue contando.
  - **Con `--elegible-tras`, el modelo supone lo que traerá E4c-3:** la copia caliente omite un día elegible por su generación, y la copia de ese día sale del cubo.
  - **En todos los casos** el máximo de peticiones en 32 días queda por debajo de 6.400, de las 45.000 del tope.
  - **Las cifras son de la prueba `test_the_scenarios_published_in_02_come_from_this_model`,** que las vuelve a calcular en la CI:

  | Copia caliente al cubo | Disco externo (E4c-3 y E4c-4) | Copia previa | Días | Máximo del cubo | Primera subida que no cabe |
  |---|---|---|---|---|---|
  | cada hora (lo aprobado al principio) | no | la del código | 120 | 19 GB | día 4 |
  | cada 6 horas | no | la del código | 120 | 19 GB | día 50 |
  | cada 6 horas | sí, elegibles a los 14 días | la del código, con todo lo que sigue en la base | 120 | 18,5 GB | día 64 |
  | cada 6 horas | sí, a los 14 días | sin los días elegibles | 120 | 10,3 GB | ninguna |
  | cada 6 horas, y un 10 % más cada 30 días | sí, a los 14 días | sin los días elegibles | 180 | 17,6 GB | ninguna |

  - **Con la subida horaria no cabe:** cada copia caliente que sale de la retención deja una versión anterior de unos 240 MB durante uno a tres días, y eso suma unos 14 GB.
  - **Sin el disco externo no cabe nunca:** sin E4c-3 y E4c-4 ningún día se hace elegible, así que el cubo guarda todos los días, en las copias de día y en las previas.
  - **Con el disco externo, la copia previa también tiene que dejar fuera los días elegibles.** Lo permite el diseño aprobado de este punto 4 («llevan los datos de todos los días que no sean `elegible_para_exclusion_backup`»), apoyándose en la generación verificada fuera de cada uno. Si llevara todo lo que sigue en la base, el cubo se llenaría el día 64. **Por eso E4c-3 debe traer a la vez:** la omisión por generación en la copia caliente y en la previa, y los primeros días elegibles. Hasta entonces, ningún día es elegible y la previa lo lleva todo, como ahora.
  - **El límite gratuito durante la prueba** [H, página de Always Free, 26 nov 2026]. Mientras dura la prueba gratuita de la cuenta (hasta hacia el 8 dic 2026), lo gratuito son 10 GB de nivel Standard, y lo que pase se descuenta del crédito de prueba. Al terminar la prueba, la cuenta queda con los 20 GB de los tres niveles juntos, que es el límite de este diseño.
    - **Si al terminar la prueba hay más de 20 GB, Oracle borra todos los objetos.** Los 19 GB del presupuesto lo impiden.
    - El cubo no pasará de 10 GB antes de esa fecha: la primera subida ronda 1 GB.
  - **La cadencia de la copia caliente al cubo** es la constante `HOT_UPLOAD_HOURS` de `kbot.upload.service`. Sube solo las copias hechas a una hora divisible por ella. **Vale 6 por decisión del propietario del 26 nov 2026, que enmienda D25** («elijo tu recomendacion»): suben las copias de las 00:40, 06:40, 12:40 y 18:40 UTC, y las «horarias» de la retención del cubo son las 3 últimas de esas subidas. En el servidor la copia sigue siendo horaria: frente a un fallo de la base, el RPO sigue siendo 1 h, y frente a la pérdida del servidor pasa a ser esa cadencia.
- **Las pruebas,** en la CI:
  - **El servicio,** con un Object Storage falso en memoria que aplica el versionado y los permisos de la política. Cubren:
    - la subida y la relectura;
    - un volcado de 129 MiB en un único `PutObject`, sin ninguna llamada de subida en partes, que el falso rechaza sin `OBJECT_OVERWRITE`;
    - un nombre en uso que nunca se sobrescribe, y el `412` tras una respuesta perdida;
    - una copia cambiada en el cubo que no se verifica, y la copia de día rehecha;
    - los 19 GB, el modo de ahorro y el bucket sin versionado;
    - el intento de listado anotado antes de pedirlo, y la cadencia;
    - la retención D25 en 72 ciclos horarios y en 30 días;
    - el caso de aceptación del residual de R-E4-01, directo y a través del servicio, con el índice perdido y rehecho desde el cubo, y la copia de día que no sale mientras la copia caliente más reciente lleve el día;
    - el punto recuperable, que no se mueve en cinco ciclos sin copias nuevas ni en uno que solo sube globales (E4C2-06);
    - el caso de E4C2-09: la copia de día rehecha con otro cifrado y vuelta a subir lleva el punto recuperable a la caliente anterior; con los objetos originales repuestos, vuelve la que dependía de ellos;
    - las versiones de la misma hora con una marca de borrado, y dos versiones sin marca empatadas en las dos ordenaciones del listado: ninguna conserva la verificación sin volver a leer (E4C2-10);
    - en el límite de espacio, una copia con sus dos objetos ambiguos se vuelve a leer sin subir nada y recupera el punto recuperable, y una copia nueva se rechaza (E4C2-11).
  - **El tope de peticiones:**
    - con 0, 1, 2 y 3 restantes, un listado de 2 páginas con un reintento, y un reinicio;
    - el orden exacto de la secuencia de persistencia, con un fallo inyectado en cada paso;
    - la ventana de 32 días;
    - un contador ausente o ilegible;
    - el reloj que retrocede cruzando la medianoche, con reinicio (E4C2-08).
  - **La omisión:** el caso de la revisión y sus tres variantes, cada condición por separado, y tres o más copias dentro de la hora con una copia completa en medio, reinicio y retención: solo la primera usa el listado (E4C2-04).
  - **El guardián con el JSON real del núcleo,** en un espacio de red desechable, con `kbot_upload` creado sin UID fijo: pasa, y con otro usuario en su lugar falla (E4C2-07).
  - **La simulación:** un día elegible que sigue en la base cuenta en la previa, y la tabla de arriba sale del modelo (E4C2-05).
  - **Con PostgreSQL:** la copia caliente deja fuera los datos de un día por partición y conserva sus tablas, y la previa lo lleva todo.
  - **Como root:** los permisos reales de las copias y del estado, con los usuarios reales.
  - **El resto:** el guardián con `nft -c` y el usuario creado, las unidades con `systemd-analyze`, los guiones con `shellcheck`, y la simulación.

**Quinta revisión del código de E4c-2 (27 nov 2026, `8a83cc2`): APROBADO.** E4C2-11, RESUELTO: el revisor repitió el caso con el límite igual a lo ocupado, y obtuvo dos lecturas, ninguna subida, ningún aumento de espacio y la vuelta a H06:40. Las copias nuevas que no caben se siguen rechazando, y `holds()` mantiene su exigencia. Sin hallazgos nuevos ni residuales en el código revisado. La aprobación cubre el código: quedan las comprobaciones reales de la instalación en Oracle (punto 7, «En el servidor»). **Verificación del revisor:** el ZIP con los 314 archivos del commit y 60 pruebas de subida.

**Cuarta revisión del código de E4c-2 (27 nov 2026, `7b4c374`): APROBABLE CON CORRECCIONES.** E4C2-10, RESUELTO: las dos ordenaciones deshacen la verificación anterior, y la relectura decide sin sobrescribir los objetos ambiguos. Hallazgo nuevo, corregido arriba:
- **E4C2-11 (MEDIO):** antes de volver a leer una copia con objetos ambiguos, el ciclo exigía sitio para la copia entera, aunque no iba a subir nada. En el límite se registraba «no cabe», no se hacía ninguna lectura y el RPO retrocedía → el presupuesto cuenta solo los bytes que se subirían.

**Verificación del revisor:** el ZIP con los 314 archivos y 59 pruebas de subida.

**Tercera revisión del código de E4c-2 (26 nov 2026, `d3a4c5b`): APROBABLE CON CORRECCIONES.** E4C2-09, RESUELTO en su caso de aceptación (el punto recuperable sigue H06:40 → H00:40 → H06:40). Hallazgo nuevo, corregido arriba:
- **E4C2-10 (MEDIO):** con dos versiones de la misma hora y ninguna marca de borrado, se tomaba la primera del listado. Una verificación antigua podía mantenerse sin volver a leer, y con ella el punto recuperable y la omisión → ese empate también es ambiguo y se vuelve a leer.

El revisor lo reprodujo de forma sintética, no en Oracle. **Verificación del revisor:** el ZIP con los 314 archivos y 100 pruebas locales.

**Segunda revisión del código de E4c-2 (26 nov 2026, `24ba53a`): APROBABLE CON CORRECCIONES.** E4C2-04 a E4C2-08, RESUELTOS en sus casos originales. Los dos ajustes de más le parecen coherentes: que la retirada de una copia de día exija la omisión por generación, y que un empate con una marca de borrado se trate de forma conservadora. Hallazgo nuevo, corregido arriba:
- **E4C2-09 (MEDIO):** el punto recuperable comprobaba el nombre de la copia de día de la que depende una caliente, no su identidad exacta. Una copia de día rehecha con otro cifrado y vuelta a subir dejaba como recuperable una caliente cuyo manifiesto fija otros SHA-256 → identidad completa en el índice y verificación atada a la versión leída.

**Verificación del revisor:** el ZIP con los 314 archivos del commit y 99 pruebas locales.

**Revisión del código de E4c-2 (26 nov 2026, `cc79eea`): APROBABLE CON CORRECCIONES.** Cinco hallazgos MEDIOS, corregidos arriba:
- **E4C2-04:** un listado podía autorizar omisiones dos veces si en medio había una copia completa → la anotación persistente del listado consumido;
- **E4C2-05:** la simulación quitaba de la previa los días elegibles, que el código lleva mientras sigan en la base → modelo corregido y tabla rehecha;
- **E4C2-06:** el estado rejuvenecía el RPO en ciclos que no subían nada → la hora de la copia caliente verificada;
- **E4C2-07:** la comprobación del guardián rechazaba el UID numérico de `kbot_upload` → traducción con `/etc/passwd`;
- **E4C2-08:** un retroceso del reloj podía borrar reservas y pasar del tope → los días posteriores siguen contando.

**Sobre las tres dudas [P]:**
- el conjunto mixto de `nft` tiene el respaldo de la CI;
- `version_id` no ordena en el tiempo, así que se quitó como desempate;
- Oracle confirma que subir sobre una marca de borrado crea una versión actual nueva, y la combinación con `OBJECT_CREATE` e `If-None-Match` se comprueba en la instalación.

**Verificación del revisor:** 87 pruebas locales y las reproducciones de cada hallazgo; sin acciones en Oracle.

**E4c-3: diseño de detalle del disco externo** [27 nov 2026; **APROBADO** por el revisor el 27 nov 2026 en `4227572`, tras tres revisiones, con E4C3-01 a E4C3-08 resueltos e incorporados abajo; **el código, en revisión** en la misma rama (abajo, «Implementado en E4c-3»)]. Aplica el diseño aprobado de este punto 4 («Copia al disco externo» y «Lo que un recibo demuestra y lo que no»; E4-D01, E4-D03 y R-E4-05) y precisa lo que aquel dejaba abierto.

- **Lo medido en el servidor** el 27 nov 2026:
  - el archivo Parquet ocupa 225 MB; un día completo con datos, unos 90 MB (el 18 nov, 94 MB, de ellos 60 MB de `depth_metrics`);
  - cada copia de día sellado, unos 63 MB, y la copia caliente, unos 600 MB mientras lleva todos los días;
  - los archivos del archivo son hoy solo de `kbot_archive` (`0700` y `0600`, por su `UMask=0077`), y `/srv/kbot/sftp` existe, de `root`, vacío.
- **Lo que se lleva el disco en una visita semanal:** los Parquet de unos 7 días (unos 630 MB), sus copias de día (unos 440 MB), la última copia diaria (de 250 a 600 MB) y los globales, es decir, de 1,3 a 1,7 GB.
- **Lo que ocupa en un año** (E4C3-05). El disco lo guarda todo sin plazo:
  - los Parquet y las copias de día: 365 × (90 + 63) MB, unos 56 GB;
  - una copia caliente diaria por visita semanal: 52 × (250 a 600) MB, de 13 a 31 GB;
  - en total, **de 69 a 87 GB al año**, más los globales, los manifiestos y el crecimiento, que no se cuentan.

  **Supuestos:** los tamaños de hoy y una visita por semana. El dispositivo y lo que se conserve en él los decide el propietario; un pen drive de 128 GB da para algo más de un año con estos supuestos.
- **Lo que E4c-3 no hace:**
  - ninguna generación pasa a `elegible_para_exclusion_backup`, porque eso exige la restauración integral de E4c-4 (§3.9);
  - la copia caliente y la previa no omiten aún nada por generación: llega con E4c-4, a la vez en las dos y con los primeros días elegibles (punto 4, «Implementado en E4c-2»; revisión del código de E4c-2).

**1. El acceso SFTP de `kbot_sync`** (`deploy/etc/ssh/sshd_config.d/10-kbot.conf`)
- `AllowUsers ubuntu kbot_sync`, y un bloque `Match User kbot_sync` al final del archivo con:
  - `ForceCommand internal-sftp -u 0027 -P symlink,hardlink`: crea con `0640`, y el servidor rechaza crear enlaces simbólicos y duros;
  - `ChrootDirectory /srv/kbot/sftp`;
  - `AuthorizedKeysFile /etc/kbot/kbot_sync_authorized_keys` (de `root`, `0644`): la clave pública que genera el propietario en su PC, distinta de la del administrador;
  - `AuthenticationMethods publickey`, `PasswordAuthentication no`, `PermitTTY no`, `DisableForwarding yes` y `PermitTunnel no`.
- **El chroot:** `/srv/kbot/sftp` y todos sus ancestros son de `root` y nadie más puede escribir en ellos [H: `ChrootDirectory`]. Dentro:
  - `copias/`: montaje `bind` de solo lectura de `/srv/kbot/backups`;
  - `archivo/`: montaje `bind` de solo lectura de `/srv/kbot/archive`;
  - `desafios/`: montaje `bind` de solo lectura de `/srv/kbot/externo/publico`, donde `kbot_archive` publica el índice de generaciones y los desafíos;
  - `recibos/`: el único sitio donde escribe el PC, para sus recibos y respuestas:
    - es de `kbot_sync:kbot_archive`, `2750`, y `kbot_archive` lo lee;
    - es **un sistema de archivos propio de 256 MiB**: un archivo imagen `ext4` en `/srv/kbot/externo/recibos.img`, montado ahí. Así `kbot_sync` nunca puede llenar el volumen de datos, del que dependen el archivo y las copias;
  - `restauracion/` no se crea todavía: llega con E4c-4, con su propio límite de espacio.
- **Los montajes** son unidades de systemd, no líneas de `/etc/fstab`, así que se instalan y se verifican como las demás unidades:
  - `srv-kbot-sftp-copias.mount`, `…-archivo.mount` y `…-desafios.mount`, con `Options=bind,ro`;
  - `…-recibos.mount`, la imagen con `nodev,nosuid,noexec`.
- **Cambio respecto al diseño aprobado:** los desafíos van en `desafios/`, de solo lectura para `kbot_sync`, y no en `recibos/`. Un directorio con dos escritores obligaría a dar a cada uno permisos sobre lo del otro.
- **Los permisos de lectura:**
  - `kbot_sync` entra en el grupo `kbot_upload`, que ya lee las copias y su registro desde E4c-2;
  - **el verificador** (E4C3-07): `kbot-archive-externo.service` corre como `kbot_archive` con `SupplementaryGroups=kbot_upload` solo en esa unidad, como la de los globales. Así lee el registro y los manifiestos de las copias, y nada más:
    - no puede modificar ninguna copia (`2750`, y `ProtectSystem=strict` con `ReadWritePaths` solo en `/srv/kbot/externo`);
    - no lee el estado de las subidas, porque `/srv/kbot/subidas` es del grupo `kbot_backup`;
    - las demás unidades de `kbot_archive` siguen sin ese grupo;
  - `/srv/kbot/archive` pasa a `kbot_archive:kbot_sync`, `2750`, y las dos unidades del archivo a `UMask=0027`. `05_copias.sh` pasa también lo ya archivado, como hizo con las copias.
  - **`kbot_sync` solo lee:** copias cifradas, Parquet de mercado (datos públicos de KuCoin) y lo que `kbot_archive` publica para él. No lee el estado privado de la verificación ni nada de `/srv/kbot/subidas`.
- **`fail2ban`** vigila también a `kbot_sync`, porque es el mismo `sshd`.
- **El guardián de metadatos** sigue rechazando a `kbot_sync`. Su sesión SFTP no puede ejecutar programas, y la comprobación desde su contexto real ya existe (E4-D04).

**2. Lo que publica el servidor para el PC** (`kbot_archive`)
- **`/srv/kbot/externo`** es de `kbot_archive`, `0700`. Dentro:
  - `publico`, `kbot_archive:kbot_sync`, `2750`, montado en `desafios/`;
  - `privado`, `0700`;
  - la imagen de `recibos/`.

  Un montaje `bind` no exige atravesar el directorio de origen, así que `kbot_sync` lee `publico` solo por el chroot.
- `generaciones.json`: cada generación `archivado_local`, con su día y el conjunto completo de sus archivos (ruta relativa, bytes, filas y SHA-256), tal como lo fijan `market.archive_generations` y `market.archive_files`.
- `desafio_<id>.json`: un desafío por cada recibo válido. Por cada archivo de la generación lleva:
  - un desplazamiento y una longitud aleatorios, de 4 KiB, o el archivo entero si es más pequeño;
  - un nonce aleatorio de 32 bytes.

  La respuesta esperada, el SHA-256 de `nonce ‖ bytes`, la calcula `kbot_archive` de su propia copia y la guarda en `/srv/kbot/externo/privado` (`0700`), que `kbot_sync` no ve.
- **Nada de esto es secreto:** son metadatos de datos públicos. Los nonces solo sirven si no se conocen antes de la visita siguiente.

**3. El guion del PC** (`tools/pc/sincronizar.ps1`; PowerShell 5.1 de Windows y PowerShell 7, sin módulos externos)
- **Lo usa el propietario cuando quiere,** con el disco conectado:

  `sincronizar.ps1 -Destino E:\kbot -Clave $HOME\.ssh\kbot_sync`

  Usa el `sftp.exe` de Windows con `BatchMode=yes`, `IdentitiesOnly=yes` y la huella del servidor fijada en `known_hosts`, que el PC ya tiene del administrador.
- **El disco correcto:** la primera vez, `-Inicializar` escribe en el destino un archivo `.kbot-disco` con un identificador aleatorio. Después, sin ese archivo el guion se detiene. Así **falla si el disco no está conectado** (R-E4-05) y nunca escribe en otra carpeta del PC por error.
- **Qué descarga:**
  - las generaciones de `generaciones.json` que no tenga;
  - las copias de día sellado y los globales que no tenga;
  - la última copia caliente diaria, la primera de su día UTC.

  **Nada se sobrescribe.** Si llega una copia del mismo día con otra identidad, por ejemplo una copia de día rehecha en el servidor con otro cifrado, se guarda aparte (`dia-<día>~<8 primeros del SHA-256>`) y la anterior se queda.
- **Las dependencias exactas de la copia caliente** (E4C3-02). Su manifiesto, que no va cifrado, fija la identidad de cada copia de día de la que depende: objeto, SHA-256 del volcado y SHA-256 del manifiesto.
  - Una copia caliente cuenta como recuperable desde el disco solo si el disco tiene, para cada una de esas dependencias, **una copia con esa misma identidad**, comprobada sobre el propio disco.
  - Si falta alguna, por ejemplo porque el servidor solo tiene ya otra versión de esa copia de día y la que fija la caliente está únicamente en el cubo, esa caliente no se acredita. El punto recuperable del disco se queda en la anterior que sí está completa, y el resumen dice qué falta.
  - El PC nunca tiene credenciales de Oracle, así que no la descarga del cubo: queda pendiente hasta que una copia caliente posterior deje de depender de ella.
- **Cómo:** cada archivo va primero a una carpeta temporal del mismo disco. Se comprueba su SHA-256: los Parquet, contra el índice; las copias, contra `registro.jsonl` de `copias/`, con el volcado y el manifiesto. Solo entonces se mueve a su sitio. Un archivo que no coincide se borra y se informa.
- **Los recibos de generación:** en cada visita, por cada generación del disco que aún no esté `verificado_fuera` (no solo las nuevas), un `recibo_<generation_id>.json`. Lleva la generación, el día, el identificador del disco (`.kbot-disco`) y el conjunto completo de archivos con bytes y SHA-256, **calculados de nuevo sobre el disco** en esa visita.
  - Las filas se copian del índice: el PC no lee Parquet, y no se presentan como comprobadas por él.
  - Un recibo es el mismo de una visita a otra mientras el disco no cambie, así que reenviarlo no genera nada nuevo en el servidor (punto 4).
  - **Las generaciones ya verificadas** se recomprueban enteras una vez al mes, o con `-Completa`. Así leer el disco entero no se vuelve semanal a medida que crece. Un fallo se avisa en el resumen y llega al servidor como recibo inválido, que lo anota como error. No hay estados nuevos, porque son de solo inserción: qué hacer entonces con la elegibilidad de esa generación lo decide E4c-4.
- **El recibo del punto recuperable** (E4C3-03 y E4C3-08): un `recibo_punto.json` con la copia caliente más reciente que es recuperable desde el disco. Lleva:
  - su nombre y el SHA-256 de su volcado y de su manifiesto, calculados sobre el disco;
  - **su manifiesto entero**: hoy unos 130 KB, y crece con los días que hay en la base. Por eso este recibo admite hasta 4 MiB, y los de generación, 1 MiB;
  - la identidad de cada dependencia que el disco cumple.

  Así el servidor puede validarlo aunque la retención local ya haya borrado esa copia.
- **Los desafíos:** el guion responde solo los desafíos de **su** disco y solo si la generación ha pasado entera, en esa misma visita, la comprobación de bytes y SHA-256 de todos sus archivos (E4C3-01). Lee esos bytes **del disco**, nunca del servidor, y escribe `respuesta_<id>.json`.
  - Si algún archivo de la generación no coincide, no responde: lo descarga de nuevo, lo comprueba, y el recibo de esa visita ya refleja el arreglo.
  - Un desafío nuevo no se puede responder en la misma visita en que se envió su recibo, porque el servidor lo emite después (punto 4).
- **Antes de subir nada,** el guion borra de `recibos/` sus propios recibos y respuestas que el servidor ya da por procesados en `desafios/procesados.json` (E4C3-04).
- **Al final:** sube los recibos y las respuestas a `recibos/`. Escribe un resumen en castellano con:
  - qué descargó, qué comprobó y qué desafíos respondió;
  - lo que falta, si lo hay;
  - el espacio libre del disco;
  - el punto recuperable del disco (punto 6), con aviso si tiene más de 7 días (P-E4-2).
- **Credenciales:**
  - el PC no tiene ninguna credencial de Oracle, y el servidor ninguna del PC;
  - la clave de `kbot_sync` la genera el propietario con `ssh-keygen -t ed25519`, con frase de paso guardada en el agente de Windows, como la del administrador, y solo pasa a Claude la pública.

**4. La verificación en el servidor** (`python -m kbot.archive.externo`, como `kbot_archive`, temporizador cada 15 minutos)
1. Rehace `generaciones.json` cuando cambian las generaciones, y `procesados.json` con los nombres y SHA-256 de los archivos de `recibos/` que ya procesó, para que el guion los borre.
2. **Discos admitidos** (E4C3-04): como mucho 2 identificadores de disco a la vez. El primero que llega con un recibo válido se admite. Uno más se rechaza y se anota, salvo que el propietario retire otro con `python -m kbot.archive.externo retirar-disco <id>`, como `kbot_archive`.
3. **Por cada recibo de generación** en `recibos/`:
   - lo abre con `O_NOFOLLOW` y solo si es un archivo normal de `kbot_sync`, de como mucho 1 MiB (4 MiB el del punto recuperable) y entre los 1.000 primeros de la ejecución. Los demás se ignoran y se anotan. Así un enlace u otro objeto en `recibos/` nunca le hace leer otra cosa con sus permisos;
   - **la idempotencia se mide contra el estado vigente del par** (residual de E4C3-01): si su SHA-256 es el del último recibo del par, se ignora sin generar nada. Cualquier otro se procesa, también uno ya visto antes. Así A → A no multiplica desafíos, y A → B → A, con B inválido y A otra vez tras reparar, devuelve el par a válido con un único desafío nuevo;
   - se **rechaza** si no es JSON válido, si su disco no está admitido, si su generación no existe o si su conjunto de archivos no es exactamente el del servidor: mismas rutas, bytes y SHA-256 (E4-D01);
   - **el último recibo de cada par (generación, disco) es el que vale** (E4C3-01). Si es inválido, el par queda «con fallo»: el desafío pendiente de ese par se anula, y sus respuestas ya no cuentan, hasta un recibo válido posterior;
   - si es válido, inserta `replicado_fuera`, si no estaba, y emite el desafío del par, **atado a ese recibo** (su SHA-256), a la generación y al disco. Un par tiene como mucho un desafío pendiente: uno nuevo sustituye al anterior.
4. **Por cada respuesta nueva:** inserta `verificado_fuera` solo si se cumplen tres condiciones; si no, la rechaza y el par conserva o renueva su desafío:
   - nombra el desafío pendiente de su par, y ese desafío está atado al último recibo válido del par;
   - el desafío se emitió en una ejecución anterior a la llegada de la respuesta;
   - cada SHA-256 coincide con la respuesta esperada.
5. **El recibo del punto recuperable** (E4C3-03 y E4C3-08): la copia caliente que nombra tiene que estar en el registro de las copias, que nunca se borra, con ese SHA-256 de volcado y de manifiesto.
   - **El manifiesto que trae el recibo** tiene que dar exactamente ese SHA-256 del registro: así queda autenticado sin depender de que la copia siga en el servidor. Uno alterado se rechaza.
   - **Sus dependencias** tienen que ser exactamente las que fija ese manifiesto.
   - Si se cumple, anota el **punto recuperable del disco**: la hora de esa copia caliente.
   - Nunca lo adelanta por una visita sin una caliente nueva y completa.
6. **El estado, acotado** (E4C3-04), en `/srv/kbot/externo/privado/estado.json`, durable, con la secuencia de E4C2-03. Guarda:
   - por cada par (generación, disco admitido): el último recibo, su resultado, el desafío pendiente con su respuesta esperada, y si quedó verificado;
   - los nombres y SHA-256 de los archivos de `recibos/` procesados de los últimos 30 días, solo para `procesados.json`: la idempotencia no depende de ellos;
   - por disco: la última visita, la última sincronización sin errores y el punto recuperable.

   Su tamaño está acotado por las generaciones por los 2 discos, y el de `desafios/` también: un desafío por par, que se borra cuando el par queda verificado. `kbot_archive` no borra lo que escribe `kbot_sync`: lo borra el guion, cuando el servidor lo da por procesado.
7. Los estados se insertan con el permiso que `kbot_archive` ya tiene sobre `market.archive_generation_states` (§3.9). **Nunca inserta `elegible_para_exclusion_backup`.**

**5. Lo que esto demuestra y lo que no** (R-E4-05, sin cambios): una lectura diferida hecha por un cliente confiable.
- **Lo que sí se comprueba:**
  - el guion falla sin el disco;
  - un recibo incompleto, de otra generación o con otras huellas se rechaza;
  - un desafío se responde con los bytes del disco y en otra visita.
- **Lo que no:** quien tenga la clave de `kbot_sync` puede responder leyendo del servidor. El PC y el disco son parte confiable.

**6. El RPO de la copia externa** (E4C3-03): la antigüedad del **punto recuperable del disco**, que es la hora de la copia caliente más reciente que el disco tiene completa, con todas sus dependencias y comprobada sobre el propio disco. No es la hora de la visita.
- **Lo que se ve, y dónde:** el estado del servidor y el resumen del guion dan tres datos por separado: la última visita, la última sincronización sin errores y el punto recuperable.
- **Un ejemplo:** a las 23:00 termina de copiarse la caliente diaria de las 00:40. El RPO es entonces de 22 h 20 min, no de cero.
- **Lo que no lo adelanta:** una visita sin caliente nueva, una descarga a medias o una dependencia que falta.
- **Las generaciones:** con visitas semanales, una queda `verificado_fuera` en una o dos visitas, el recibo en una y el desafío en la siguiente: de 7 a 14 días.

**7. Pruebas** (en la CI)
- **`sshd -T`** con el bloque `Match` da para `kbot_sync`: `forcecommand internal-sftp -u 0027`, el chroot, `disableforwarding yes`, `permittty no` y solo clave.
- **De extremo a extremo, como root:**
  - en un `sshd` de prueba en otro puerto, con el bloque real, el chroot y los montajes `bind,ro`, se ejecuta `sincronizar.ps1` con `pwsh` y una clave de prueba;
  - descarga, comprueba y escribe los recibos;
  - `kbot.archive.externo`, con PostgreSQL, los acepta (`replicado_fuera`) y emite los desafíos;
  - una segunda visita los responde (`verificado_fuera`).
- **Rechazos:**
  - un recibo con un byte o un SHA-256 cambiado, o al que le falta un archivo;
  - una respuesta con bytes de otra posición;
  - **E4C3-01:** un byte alterado fuera del rango desafiado entre dos visitas. El recibo nuevo falla, el desafío anterior se anula, y no aparece `verificado_fuera` aunque la respuesta parcial coincida. Tras reparar el archivo y completar el protocolo, sí aparece;
  - **su residual:** A válido, B inválido y A otra vez tras reparar, dentro de los 30 días y con reinicios del servicio entre los pasos. La verificación se completa con un único desafío nuevo, y A → A sin cambios no multiplica desafíos;
  - **E4C3-07:** bajo el UID y los grupos efectivos de `kbot-archive-externo`, como root en la CI, se valida un punto completo leyendo el registro y el manifiesto. No puede modificar ninguna copia ni leer `/srv/kbot/subidas`;
  - **E4C3-08:** se envía un recibo válido, la retención borra la copia caliente y el verificador se reinicia antes de procesarlo. Se valida con el manifiesto del recibo, y se rechaza uno con el manifiesto alterado;
  - **E4C3-02:** con S1 en el cubo, S2 en el servidor, H dependiendo de S1 y sin generación alternativa, el disco no acredita H, y el punto recuperable se queda en la caliente anterior completa;
  - **E4C3-03:** la caliente de las 00:40 copiada a las 23:00 da 22 h 20 min; una visita sin caliente nueva, una descarga a medias o una dependencia ausente no adelantan el punto;
  - **E4C3-04:** retransmisiones del mismo recibo, identificadores de disco desconocidos más allá de 2, y reinicios del servicio. Lo generado en `desafios/` y en el estado sigue acotado, y un intercambio legítimo se completa;
  - un enlace simbólico o un directorio en `recibos/`, creados por fuera del SFTP, que el servicio no sigue;
  - el guion sin el disco, o sin `.kbot-disco`, se detiene sin escribir nada.
- **Lo que `kbot_sync` no puede hacer:**
  - escribir en `copias/`, `archivo/` o `desafios/`, ni pasar de 256 MiB en `recibos/`;
  - crear un enlace simbólico o duro;
  - leer `/srv/kbot/externo/privado`;
  - abrir una shell, reenviar un puerto o salir del chroot.
- **El servicio no inserta nunca `elegible_para_exclusion_backup`.**
- **En el servidor,** con informe en `docs/operacion/`: la primera sincronización real del propietario, la siguiente con los desafíos y las comprobaciones de aislamiento desde la sesión SFTP real.

**8. Qué hace el propietario:**
- un pen drive de 128 GB, o un disco externo, formateado en NTFS;
- la clave de `kbot_sync`, con una guía paso a paso (`docs/operacion/disco_externo.md`): solo pasa la pública;
- conectar el disco al menos una vez por semana y ejecutar el guion (P-E4-2);
- la instalación en el servidor, con su permiso, como siempre.

**Implementado en E4c-3** [27 nov 2026, rama `fase-2-e4c3`; primera revisión del código en `79cbbdc`: APROBABLE CON CORRECCIONES, con E4C3-09 a E4C3-15 corregidos; segunda revisión en `de577c9`: APROBABLE CON CORRECCIONES, con el residual de E4C3-09, E4C3-16 y E4C3-17 corregidos; tercera revisión en `bf69fd9`, cuarta en `e25f1b3` y quinta en `d6a20f3`: APROBABLE CON CORRECCIONES, con los tres residuales de E4C3-16 corregidos; todo incorporado abajo; **pendiente de la sexta revisión**; sin instalar]. Aplica el diseño de detalle aprobado en `4227572`.

- **El código:**
  - **`kbot.archive.externo`:** `Verifier.run` es una ejecución del verificador (puntos 2 y 4), `retire_disk` retira un disco y `main` da las dos órdenes (`run` y `retirar-disco <id>`). El estado privado se escribe con la secuencia de persistencia de E4c-2 (`kbot.upload.durable`).
  - **`tools/pc/sincronizar.ps1`:** el guion del PC (punto 3).
  - **El despliegue:**
    - el bloque `Match User kbot_sync` en `10-kbot.conf`;
    - las unidades `srv-kbot-sftp-copias.mount`, `…-archivo.mount`, `…-desafios.mount` y `…-recibos.mount`;
    - `kbot-archive-externo.service` con su temporizador, cada 15 minutos (en los minutos 3, 18, 33 y 48);
    - `UMask=0027` en `kbot-archive-daily` y `kbot-archive-prepare`;
    - `deploy/install/06_externo.sh`.
  - **Para el propietario:** la guía `docs/operacion/disco_externo.md` y las órdenes nuevas en `docs/operacion/comandos.md`.
- **Precisiones de la implementación:**
  - **La clave pública de `kbot_sync`** va en el repositorio, en `deploy/etc/kbot/kbot_sync.pub`, como las claves públicas de `age`. `06_externo.sh` se detiene si falta, si tiene más de una línea o si contiene `PRIVATE KEY`, y la instala en `/etc/kbot/kbot_sync_authorized_keys` (de `root`, `0644`).
  - **El orden de `06_externo.sh`:**
    1. la clave;
    2. `kbot_sync` en el grupo `kbot_upload`, y el archivo pasado a `kbot_archive:kbot_sync` con `2750`;
    3. los directorios del verificador y la imagen de `recibos/` (E4C3-15): se prepara aparte, como `recibos.img.tmp`, y se publica con un cambio de nombre, así que un corte nunca deja media imagen. Una imagen que ya existe puede tener recibos: solo se comprueba con `e2fsck -n`, y si no pasa la instalación se detiene sin formatearla;
    4. el chroot y los montajes;
    5. las comprobaciones como `kbot_sync`, con `setpriv`: lee las tres vistas y escribe en `recibos/`; no escribe en las otras tres ni lee `privado` ni `/srv/kbot/subidas`;
    6. **solo después, `sshd`:** guarda el drop-in anterior y pasa `sshd -t`; si falla, vuelve a poner el anterior y no recarga nada. Luego comprueba con `sshd -T` lo efectivo para `kbot_sync`, incluida la línea `Subsystem sftp` de la imagen (sin ella, `sshd` rechaza la sesión SFTP antes de aplicar `ForceCommand`), y que el administrador no recibe `ForceCommand internal-sftp` ni un chroot;
    7. una primera verificación y el temporizador.
  - **El índice** (`generaciones.json`) lleva también `verificada_en_discos`: los discos en los que cada generación quedó verificada y cuyo último recibo sigue siendo válido. El guion vuelve a probar en cada visita las generaciones que no están verificadas en su disco, y todas en la comprobación mensual. **Tras un recibo con fallo,** el disco sale de esa lista, así que la generación se prueba de nuevo en la visita siguiente, no al mes.
  - **Nada se anuncia antes de guardarse** (E4C3-09). Una ejecución procesa en memoria, guarda el estado con la secuencia de persistencia y solo entonces publica lo que dice el estado guardado:
    - el índice (`generaciones.json`), así que lo que esa ejecución verificó se ve en el acto;
    - cada desafío pendiente, y ningún otro: los anulados o sustituidos se retiran ahí;
    - `discos.json`, con la última observación aceptada de cada disco;
    - `procesados.json`, los acuses.

    El desafío público se guarda en el estado privado junto con sus respuestas esperadas. Si la publicación se corta, la ejecución siguiente la rehace al empezar, desde el estado guardado. Si el estado no se puede guardar, no se publica nada nuevo, por muchas ejecuciones que fallen. `retirar-disco` guarda primero y retira después.

    **Antes de anunciar nada desde el estado cargado** (residual de E4C3-09), cada ejecución, y también `retirar-disco`, confirma que ese estado está en el disco: `fsync` del archivo y de su directorio. Un guardado cuyo último `fsync` falló deja el estado nuevo visible pero sin asegurar. Mientras esa confirmación falle, la ejecución se detiene sin publicar nada, por muchas veces que se reinicie.
  - **Lo ya procesado** (E4C3-13): el verificador anota cada archivo de `recibos/` con su SHA-256 y su sello (inodo, tamaño y hora de modificación, en nanosegundos). Un archivo con el mismo sello se salta **sin leerlo**, y el límite de 1.000 por ejecución cuenta solo los que se leen, así que la cola siempre avanza. Lo que no se llega a leer (un enlace, un directorio, un archivo de otro dueño o demasiado grande) se anota igual, por su sello. La idempotencia de los recibos no depende de esto: se mide contra el último recibo del par.
  - **Lo que el PC borra de `recibos/`** (E4C3-09): un archivo solo si el servidor lo dio por procesado con exactamente el SHA-256 que este disco subió con ese nombre, que el guion guarda en `.kbot-estado.json` (`subidos`). Uno que se vuelve a subir en esa visita simplemente se sustituye. **El guion no olvida lo pendiente de borrar hasta verlo borrado** (E4C3-17): guarda `subidos` antes de conectarse y, tras la subida, lista `recibos/` y solo olvida lo que ya no está. Si la conexión se corta antes de borrar o a mitad, la visita siguiente lo vuelve a intentar.
  - **El caché de huellas del guion** (`.kbot-huellas.json`, en el disco) guarda el SHA-256 de cada archivo junto con su tamaño y su hora de modificación.
    - Se calcula de nuevo sobre el disco todo lo que se acaba de descargar y todos los archivos de las generaciones que se prueban en esa visita. Cada archivo se lee como mucho una vez por visita.
    - **El punto recuperable:** la copia caliente y las copias de día de las que depende se comprueban al descargarlas, y de nuevo, enteras, en la comprobación mensual o con `-Completa`. Entre medias vale el caché, si no han cambiado ni el tamaño ni la hora. Es el mismo criterio que fija el diseño para las generaciones ya verificadas, y por la misma razón: que no haya que leer el disco entero cada semana.
  - **Las copias del mismo nombre con otra identidad** se guardan como `<nombre>~<8 primeros del SHA-256 del volcado>` (con `-2`, `-3`… si también ese nombre está ocupado). Una dependencia se busca en el nombre exacto y en esas variantes.
  - **Una copia entra en el disco entera o no entra:** su volcado y su manifiesto se comprueban en la carpeta temporal del mismo disco y la carpeta de la copia se mueve a su sitio de una vez, solo si coinciden los dos. Si falla uno, se borran los dos y se informa. Un Parquet entra por sí solo.
  - **Un archivo que no se puede descargar** se informa como error y la visita sigue con los demás.
  - **La copia caliente diaria** que se descarga es la primera del día UTC más reciente que tiene el servidor. Las que ya están en el disco se quedan.
  - **Un disco se admite solo con un recibo de generación válido.** El recibo del punto recuperable de un disco no admitido se rechaza.
  - **Lo rechazado por falta de plaza se vuelve a mirar** (E4C3-12): un recibo rechazado solo por qué discos están admitidos queda marcado. Cuando cambia ese conjunto, al admitir o retirar un disco, se olvida su marca y se vuelve a leer, aunque sus bytes no hayan cambiado.
  - **El punto recuperable sigue al disco** (E4C3-10):
    - cada visita envía su observación, también cuando el disco no tiene ninguna copia caliente completa (`copia` nula). **Cada disco la envía en su propio archivo,** `recibo_punto_<disco>.json`, que se sustituye en cada visita y nunca se borra. El servidor rechaza la observación de un disco que llegue en el archivo de otro;
    - **ningún archivo de `recibos/` se queda a medias** (segundo residual de E4C3-16). `put` vacía su destino antes de escribir, así que el guion sube cada archivo como `.subiendo-<nombre>` y lo pone en su sitio con `rename`, que en OpenSSH sustituye el destino de una vez (`posix-rename`). Un corte deja el archivo anterior entero, nunca uno vacío. El verificador no lee los archivos que empiezan por punto, y cada visita borra primero los `.subiendo-*` que dejó una visita cortada;
    - **un número es fiable solo con una de estas referencias** (tercer residual de E4C3-16):
      - el número que guardó el propio disco, que solo se guarda cuando era fiable;
      - su última observación, leída de vuelta entera de `recibos/`;
      - un disco que se inicializa en esa misma visita, del que nunca se pudo aceptar nada;
      - una resincronización que el servidor ha confirmado.

      `discos.json` solo no basta, porque puede ir atrasado.
    - **Sin número fiable, el guion no envía la observación, no guarda ningún número y pide una resincronización:**
      - sube `resincronizar_<disco>.json` con un nonce aleatorio, el mismo hasta que se conteste. El nonce se guarda en el disco y también se puede leer de vuelta de ese archivo;
      - la visita termina con un error que pide repetirla al cabo de 15 minutos;
      - el servidor contesta poniendo ese nonce en `discos.json`, junto a su contador, y solo desde su estado guardado. Mientras espera, el disco no envía ninguna observación, así que ese contador es el vigente: su número siguiente es fiable;
      - **mientras la publicación falle,** no aparece ninguna respuesta y el disco sigue sin enviar observaciones, por muchas visitas que haga;
      - al usar la respuesta, el guion **cierra la petición** (`nonce` nulo) antes de subir la observación, así que ese nonce no vuelve a valer aunque el disco pierda su estado más tarde. El servidor también lo retira al aceptar una observación posterior.
    - cada observación lleva un número (`secuencia`), **sin depender del reloj** (E4C3-16). Es mayor que tres cosas:
      - el último que el servidor aceptó para ese disco, que publica en `discos.json`;
      - el último que guardó el propio disco en `.kbot-estado.json`;
      - **el último que envió ese disco, leído de vuelta de su archivo en `recibos/`** al empezar cada visita.

      Lo que el servidor aceptó salió de ese archivo, y cada envío lleva un número mayor que el anterior. Así no se repite, aunque el disco pierda su estado mientras `discos.json` va atrasado. El guion guarda el número en el disco antes de enviarlo;
    - **si el servidor no aceptó la observación anterior** (E4C3-16), el guion lo avisa. `discos.json` lleva el SHA-256 de la última observación aceptada, y el guion lo compara con la última que envió ese disco, leída de vuelta de `recibos/`, si el servidor ya la procesó. La visita envía en su lugar una nueva, con un número mayor;
    - el servidor acepta solo una observación con un número mayor que el último aceptado, y entonces **la sustituye entera**: el punto puede ir hacia atrás (H27 pierde una dependencia y queda H20) o quedarse vacío. Una observación antigua, aunque llegue de nuevo byte a byte, se rechaza.
  - **Una observación rechazada no cambia nada del disco** (E4C3-14): ni el punto, ni la última visita, ni la última sincronización sin errores. Las tres se anotan solo cuando la observación se acepta entera.
  - **El estado y el caché del disco** (E4C3-11): `.kbot-estado.json`, `.kbot-huellas.json` y `.kbot-disco` se escriben en un temporal, se vuelcan al disco y lo sustituyen de una vez. Si el estado o el caché no se pueden leer, se descartan con un aviso y esa visita lo comprueba todo, sin confiar en ninguna huella guardada.
  - **`retirar-disco` y la verificación nunca escriben el estado a la vez:** las dos toman un candado (`flock`) en `/srv/kbot/externo/privado/.lock`, y la segunda espera.
  - **Con dos discos, los recibos de generación tienen el mismo nombre** (`recibo_<generación>.json`). Las observaciones del punto no: cada disco tiene la suya. Si los dos discos visitan el servidor dentro de los mismos 15 minutos, el recibo de generación de uno puede sustituir al del otro antes de procesarse. Eso solo lo retrasa a la visita siguiente de ese disco.
  - **El resumen del guion** da la antigüedad del punto recuperable desde la hora de su copia caliente (punto 6), y avisa si pasa de 7 días.
  - **Con Windows PowerShell 5.1,** una línea de `sftp` en stderr no se convierte en error: solo cuenta su código de salida. De los listados solo se toman nombres seguros (letras, cifras y `._~-`).
- **Las pruebas** (punto 7):
  - **`tests/unit/archive/test_externo.py`:**
    - el índice, y una generación que pierde `verificada_en_discos` tras un recibo con fallo y la recupera al repararse;
    - recibo y respuesta en ejecuciones distintas, y las dos barreras de una misma ejecución;
    - recibos con otros bytes o sin un archivo, y una respuesta con los bytes de otra posición;
    - E4C3-01 y su residual: A, luego B con un byte cambiado fuera del rango desafiado, y A otra vez. Cada paso usa un verificador nuevo, como tras un reinicio;
    - E4C3-04 y E4C3-12: retransmisiones, un tercer disco y la retirada, tras la que se admite el mismo archivo del tercero, sin volver a enviarlo;
    - E4C3-02, E4C3-03 y E4C3-08 en el punto recuperable:
      - el manifiesto autenticado por el registro, con la copia ya borrada del servidor;
      - un manifiesto alterado, otras dependencias, otros SHA-256 o una copia desconocida, rechazados sin cambiar nada del disco (E4C3-14);
      - E4C3-10: H27 completa, luego H20 al perder H27 una dependencia, luego ninguna; la observación antigua, enviada de nuevo byte a byte, se rechaza, y también una que llega en el archivo de otro disco;
      - la caliente de las 00:40 a las 23:00 da 22 h 20 min, y una visita sin caliente nueva no mueve el punto;
    - E4C3-09: tres ejecuciones sin poder guardar el estado no publican ningún desafío ni acuse; una invalidación sin guardar no se pierde y la respuesta antigua se rechaza; un desafío que no se pudo publicar lo publica la ejecución siguiente;
    - residual de E4C3-09: falla cada paso del guardado privado (escribir, `fsync` del archivo, cambio de nombre y `fsync` del directorio). Tras la ejecución y dos reinicios no se ha publicado ningún desafío ni acuse, y la primera ejecución con el disco sano confirma el estado y solo entonces lo anuncia;
    - E4C3-13: 1.000 recibos y el del punto: la primera ejecución lee los 1.000, la segunda el del punto, y la tercera no lee nada, sin que el PC borre nada entre medias;
    - lo procesado se olvida a los 30 días, y lo que aún se está enviando (`.subiendo-*`) no se lee;
    - la petición de resincronización: su nonce vuelve en `discos.json` junto al contador; una observación aceptada después, o una petición cerrada, lo retira; la petición de un disco en el archivo de otro se rechaza;
    - tamaños, tipos, y la elegibilidad nunca insertada;
    - en Linux: enlaces, directorios y archivos de otro dueño, y el candado.
  - **`tests/unit/pc/test_sincronizar.py`:** el guion entero contra el verificador real, a través de un `sftp` simulado (`fake_sftp.py`) que sirve las cuatro carpetas del chroot desde carpetas locales. Corre con **Windows PowerShell 5.1** en el PC y con `pwsh` en la CI. Las visitas, en orden:
    - la primera da `replicado_fuera` y el punto;
    - la segunda, con el caché y el estado cortados (E4C3-11), responde el desafío;
    - dos se cortan, antes del primer borrado y después de él, y la siguiente borra solo lo que el servidor procesó tal como lo subió ese disco (E4C3-09 y E4C3-17);
    - **E4C3-16:** el `discos.json` del servidor va atrasado y el disco pierde su estado y su única caliente completa, que tampoco está ya en el servidor. La visita toma el número siguiente al de su última observación, leída de vuelta del servidor, no del reloj. El servidor se queda sin punto, y la observación antigua reenviada se rechaza;
    - **segundo residual de E4C3-16:**
      - antes de eso, una visita se corta dentro del `put` de su observación, después de vaciar el destino. El archivo de la observación sigue entero, y el `.subiendo-` que queda lo borra la visita siguiente;
    - **tercer residual de E4C3-16,** con los dos casos del revisor. El disco recupera un punto con una caliente nueva. Desde entonces el servidor no puede publicar `discos.json`: acepta una observación más en privado y la publicada se queda atrás. El disco pierde su estado y su caliente, que tampoco sigue en el servidor. Su archivo de observación en el servidor primero desaparece y después vuelve como bytes vacíos:
      - en las dos visitas siguientes, el guion no envía observación, pide la resincronización y termina con error; el servidor no puede contestar y conserva el punto obsoleto;
      - cuando la publicación vuelve, llega la respuesta, la visita siguiente envía un número mayor que el aceptado y cierra la petición, y el servidor se queda sin punto;
      - la observación antigua reenviada se rechaza;
  - **`tests/ci/test_deploy.py`:**
    - `sshd -T` con el bloque solo, y dentro de la configuración entera de Ubuntu 24.04 (la del ejecutor de la CI), para `kbot_sync` y para el administrador;
    - las unidades de los montajes, del verificador y del archivo;
    - el orden de `06_externo.sh`;
    - E4C3-15, como root: una creación de la imagen cortada antes de `mkfs.ext4` se rehace; una imagen con recibos los conserva; una imagen que no es un sistema de archivos detiene la instalación y se queda como estaba;
    - `shellcheck` del guion de instalación.
  - **`tests/db/test_externo_e2e.py`, como root en la CI:**
    - **el montaje:** el `sshd` de prueba en 127.0.0.1:2222 con el drop-in real, el chroot, los tres montajes `bind,ro` y la imagen de 256 MiB, y un día sellado y archivado en PostgreSQL;
    - **las visitas de `sincronizar.ps1` con `pwsh`:**
      - sin el disco, o con una carpeta sin `.kbot-disco`, se detiene sin escribir nada;
      - la primera, con `-Inicializar`, da `replicado_fuera` y el punto recuperable;
      - la segunda, con el caché y el estado del disco cortados (E4C3-11), los descarta con un aviso, lo comprueba todo, responde los desafíos y da `verificado_fuera`, nunca la elegibilidad;
    - **E4C3-02 en el guion:** una caliente más nueva que fija S1 (en el cubo) mientras el servidor y el disco tienen S2 no mueve el punto, y el resumen dice qué falta;
    - **E4C3-03:** en una tercera visita, una caliente que no coincide con su registro, como una descarga a medias, no se guarda. La visita termina con error y no mueve ni el punto ni la última sincronización sin errores;
    - **E4C3-10:** en una cuarta visita, el disco ya no tiene ninguna caliente completa, y el servidor se queda sin punto;
    - **E4C3-07:** como `kbot_archive` con el grupo `kbot_upload`, valida un punto leyendo el registro y el manifiesto, y no puede modificar ninguna copia ni leer `/srv/kbot/subidas`. Sin el grupo, no lee el registro;
    - **lo que `kbot_sync` no puede hacer, por el SFTP real:** escribir en `copias/`, `archivo/` o `desafios/`, pasar de 256 MiB en `recibos/`, crear un enlace simbólico o duro, leer `privado`, salir del chroot, abrir una shell o reenviar un puerto.
- **Lo que queda para la instalación,** con el permiso del propietario:
  - la clave pública de `kbot_sync` y `06_externo.sh`;
  - la primera sincronización real y la siguiente, con los desafíos;
  - las comprobaciones de aislamiento desde la sesión SFTP real, con su informe en `docs/operacion/`.

**Revisión del diseño (11 nov 2026): APROBABLE CON CORRECCIONES.** El revisor confirmó el orden instantánea → comprobaciones → `pg_dump --snapshot` y lo contrastó en PostgreSQL 18.6 (una inserción concurrente no aparece ni en el volcado ni en el manifiesto si van en la misma instantánea). Hallazgos, todos incorporados arriba: **E4-D01** (ALTO) un recibo podía acreditar una copia externa inexistente → recibo completo, desafío en una sincronización posterior y parte confiable con garantía acotada; **E4-D02** (MEDIO) `pg_restore` reponía el sello antes de cargar el Parquet (`23514`) → restauración por fases; **E4-D03** (MEDIO) el perfil SFTP permitía reenvíos y `AllowUsers` excluía a `kbot_sync` → bloque `Match` completo con `DisableForwarding`; **E4-D04** (MEDIO) `IPAddressDeny` no acreditaba el aislamiento de metadatos → cortafuegos por usuario, comprobación desde cada contexto real y parada de la instalación si no funciona; **E4-D05** (MEDIO) faltaba el descifrado para la restauración mensual → ensayo automático con clave de prueba y restauración integral descifrada en el PC; **E4-D06** (MEDIO) el bloqueo REST no era escribible con `ProtectSystem=strict` → precreado por `tmpfiles.d` y `ReadWritePaths`; **E4-D07** (MEDIO) la cadencia semanal no garantizaba copias de 3-7 días → copia de cada día sellado una sola vez, medición del presupuesto y antigüedad de la última subida como RPO efectivo.

**Segunda revisión del diseño (11 nov 2026): APROBABLE CON CORRECCIONES.** E4-D01, D02, D03, D05 y D06 resueltos como diseño; D04 y D07 parciales. Hallazgos nuevos, incorporados arriba: **R-E4-01** (ALTO) una copia de día sellado podía caducar a los 120 días mientras una copia caliente aún la necesitaba → nada caduca por edad, el manifiesto fija la dependencia exacta, el servicio comprueba que existe y, si no, el día vuelve a las copias calientes; las copias previas a actualización son autocontenidas; **R-E4-02** (MEDIO) el ciclo de vida por edad daba 24/8/3 copias y unas 120 de días sellados, no las 3/7/2 de D25 → la retención la aplica el servicio, exactamente D25, con `OBJECT_DELETE` pero sin `OBJECT_VERSION_DELETE` ni `OBJECT_OVERWRITE`, versionado del cubo y una ventana de un día de versiones anteriores (una ventana de recuperación limitada: después el ciclo de vida borra esas versiones); la ampliación de D25 se somete al propietario (P-E4-4) y una simulación de 120 días comprueba el presupuesto; **R-E4-03** (MEDIO) el perfil de red de `svc-backup` bloqueaba el servicio de autenticación regional → sin lista blanca por IP para Oracle, los destinos los acota el SDK con sus dos puntos de acceso explícitos, y la prueba crea el firmante, sube, lee y renueva el testigo; **R-E4-04** (MEDIO) faltaba autenticar el volcado recibido y aislar el restaurador → SHA-256 en claro registrado al crear la copia y comprobado sobre una copia privada antes de `pg_restore`, y clúster de restauración de un usuario del sistema propio sin acceso a producción; **R-E4-05** (BAJO) el desafío se describía como prueba de persistencia → es una lectura diferida por un cliente confiable, con el límite documentado y el guion que falla sin el disco.

**Tercera revisión del diseño (11 nov 2026): APROBABLE CON CORRECCIONES.** R-E4-02 a R-E4-05 resueltos como diseño. **R-E4-01, residual (MEDIO):** la elegibilidad del día no bastaba para borrar una copia de día sellado que fija una copia retenida con otra generación → antes de borrarla se comprueba, para cada copia retenida que la fija, que la generación de su propio manifiesto está verificada fuera; si no, se conserva hasta que esa copia expire. Ajustes menores: el apartado de riesgos ya no dice «120 días» sino la conservación por dependencias y elegibilidad, y la simulación del presupuesto modela las dos visitas del desafío sin cota fija. P-E4-4 sigue pendiente del propietario.

**Cuarta revisión del diseño (11 nov 2026): APROBADO.** R-E4-01 resuelto: el revisor contrastó la condición de borrado con un modelo lógico de 32 combinaciones, que conserva la capacidad de restaurar todas las copias retenidas. Sin reaperturas ni defectos nuevos. La aprobación cubre el diseño; **no** aprueba la ampliación de D25 (P-E4-4, del propietario) ni da por superadas las pruebas de despliegue, restauración o capacidad, que llegan con la implementación (E4a a E4d).

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
| 9 nov 2026 | **Revisión del diseño de E3, en dos rondas** (la primera, con el modelo anterior del revisor; la segunda, con el auditor actual): **APROBABLE CON CORRECCIONES DE DISEÑO**. Hallazgos:<br>· **E3-D01 (ALTO):** archivar no es tener una copia recuperable, así que no se excluye ni se retira nada sin un Parquet verificado fuera del servidor.<br>· **E3-D02:** estados persistentes, una instantánea coherente, recuperación idempotente y una huella canónica.<br>· **E3-D03:** pruebas reales de las claves foráneas entre días, y nunca `CASCADE`.<br>· **E3-D04:** no modificar la `0002`.<br>Condiciones de la segunda ronda:<br>· la evidencia tardía puede cambiar un día archivado, y por eso se sella el día;<br>· retirar el día D mejoraba los estados de D+1, y por eso la evidencia ya no cruza de día;<br>· generaciones completas con referencias fijas;<br>· bloquear las doce tablas antes de comprobar que están vacías, y alinear los minutos a UTC;<br>· preparar el rango de recuperación y fijar UTC.<br>**Decisiones del propietario:** funciones `SECURITY DEFINER` acotadas con el rol `kbot_archive` (opción A) y una migración nueva, la `0003` (opción B). §3.9 reescrito con todo ello. Se añade una tercera función, la de sellar, dentro de la opción A | Revisor y propietario; **función de sellar confirmada por el propietario el 10 nov 2026; implementación en la fila siguiente** |
| 10 nov 2026 | **E3a implementada** (3.9):<br>· la migración `0003`, que rehace las doce tablas particionadas por día UTC si están vacías;<br>· las tablas del archivo;<br>· `prepare_partitions()`, `seal_day(date)` y `retire_day(date)`, esta última sin `EXECUTE` para nadie;<br>· el rol `kbot_archive`;<br>· `sql_guard`, que analiza el cuerpo PL/pgSQL de las funciones con cinco plantillas;<br>· las migraciones de `main` congeladas por SHA-256.<br>Comprobado en la CI: PostgreSQL impide separar una partición referenciada desde otro día, y hacen falta más bloqueos por transacción (`max_locks_per_transaction = 256`) | Claude; **revisada el 10 nov 2026 (fila siguiente)** |
| 10 nov 2026 | **Revisión de E3a (commit `bc9af63`), del auditor actual: APROBABLE CON CORRECCIONES.** Confirma las 960 pruebas y, en su entorno con PostgreSQL 18, 958 correctas (las dos restantes necesitan `sudo`).<br>· **E3a-01 (ALTO):** `sql_guard` aceptaba, dentro de una función, un `DELETE` en un `WITH`, una plantilla aprobada aplicada a otra tabla, un `PERFORM` con efectos y un `REVOKE GRANT OPTION FOR`. Corregido con la validación cerrada de los cuerpos aprobados.<br>· **E3a-02:** `seal_day` sellaba días incompletos. Ahora exige 1.440 estados por instrumento del día.<br>· **E3a-03:** una escritura podía colarse entre la comprobación y las barreras. Ahora las barreras van primero y se exige READ COMMITTED.<br>· **E3a-04 (BAJO):** el catálogo no miraba propietario ni permisos. Ahora sí.<br>Además, se precisan los `SELECT` de `kbot_archive` (3.9) | Revisor; correcciones de Claude **revisadas el 10 nov 2026 (fila siguiente)** |
| 10 nov 2026 | **Segunda revisión de E3a (commit `7654ec1`): APROBABLE CON CORRECCIONES MENORES.** E3a-01, E3a-02 y E3a-03 RESUELTOS; la ampliación de la lista blanca queda conforme para los tres cuerpos aprobados. El revisor repitió la batería sobre una base limpia: 971 correctas (las dos restantes necesitan `sudo`). **E3a-04, parcial (BAJO):** faltaban los permisos de columna (`pg_attribute.attacl`). Corregido: el catálogo los comprueba también, para cualquier otro rol y para `PUBLIC` (3.9) | Revisor; corrección de Claude **confirmada el 10 nov 2026 (fila siguiente)** |
| 10 nov 2026 | **Tercera revisión de E3a (commit `47630aa`): E3a-04 RESUELTO. Dictamen final: APROBADO.** Los cuatro hallazgos E3a-01 a E3a-04 cerrados, sin reaperturas ni defectos nuevos; la ampliación de la lista blanca, conforme para los tres cuerpos aprobados. El revisor ejecutó 184 pruebas y contrastó la CI (973 pruebas, gitleaks sin hallazgos). La aprobación cubre E3a; E3b y E3c se revisan aparte | Revisor |
| 10 nov 2026 | **E3b implementada** (3.9):<br>· `depth_late_books` parte por días la evidencia (`first_slot_start`), y retirar un día ya no cambia el siguiente;<br>· el grabador no escribe evidencia de días sellados;<br>· el archivador `kbot.archive` (`prepare`, `seal`, `archive` y `verify`) escribe generaciones de doce archivos Parquet desde una sola instantánea, verificadas por filas y huella canónica, publicadas en una transacción, con limpieza de lo no publicado y un solo archivador a la vez;<br>· dependencia `pyarrow` 26 | Claude; **revisada el 10 nov 2026 (fila siguiente)** |
| 10 nov 2026 | **Revisión de E3b (commit `33dfcb2`): APROBABLE CON CORRECCIONES**, con tres hallazgos MEDIOS. El reparto de la evidencia por días resuelve el caso de D a D+1, sin reaperturas de E3a. El revisor confirma 999 pruebas; en su entorno, 997 correctas (las otras dos necesitan `sudo`).<br>· **E3b-01:** si el día se sellaba entre la comprobación y la inserción, el tick entero se deshacía. Ahora cada inserción va en su *savepoint* y solo se descarta la fila que viola el sello.<br>· **E3b-02:** el directorio de la generación y sus padres no se sincronizaban, y un error de E/S de un directorio se silenciaba. Ahora la secuencia está completa, los errores se propagan y nada se publica si algo falla.<br>· **E3b-03:** el `null` de JSON se archivaba como un valor que falta, sin que la huella lo notara. Ahora el `jsonb` se lee como texto | Revisor; correcciones de Claude **revisadas el 10 nov 2026 (fila siguiente)** |
| 10 nov 2026 | **Segunda revisión de E3b (commit `56da65f`): APROBABLE CON CORRECCIONES.** E3b-01 y E3b-03 RESUELTOS: el revisor reprodujo el sellado concurrente con dos conexiones y comprobó el `null` de JSON. Ejecutó 1.001 pruebas con PostgreSQL 18.6 (las otras dos necesitan `sudo`). **E3b-02, parcial (BAJO):** si la raíz del archivo no existía, se creaba con sus ancestros sin sincronizar los directorios que los contienen. Corregido: la raíz debe existir de antemano, el archivador se niega sin ella antes de escribir nada, y dentro de ella crea los directorios nivel a nivel | Revisor; corrección de Claude **confirmada el 10 nov 2026 (fila siguiente)** |
| 10 nov 2026 | **Tercera revisión de E3b (commit `4fe6d06`): E3b-02 RESUELTO. Dictamen final: APROBADO.** Los tres hallazgos E3b-01 a E3b-03 cerrados, sin reaperturas de E3a ni defectos nuevos. El revisor ejecutó 1.006 pruebas con PostgreSQL 18.6 (las otras dos necesitan `sudo`) y contrastó la CI (1.008 pruebas, gitleaks sin hallazgos). La aprobación cubre E3b; E3c se revisa aparte | Revisor |
| 11 nov 2026 | **E3c implementada** (commits `7daff5f` a `54f5b76`, y el de la documentación): `--sink rows` con su prueba de equivalencia en la CI; grabación de 119 minutos en el PC con el universo de referencia; medición del Parquet y de DuckDB (`tools/measure_archive.py`); y medición en PostgreSQL 18 de bytes por fila, WAL, catálogo y planificación con 1.260 particiones. Resultado (§3.9, «Implementado en E3c»): con margen, la base de datos con 90 días calientes ocupa ≈ 54,8 GB, frente a ≈ 28 GB estimados, porque la estimación no contaba los índices. El año 1 cabe en los 110 GB y el año 2 no. Decisión pendiente del propietario; **pendiente de revisión** | Claude |
| 11 nov 2026 | **Revisión de E3c (commit `41963f3`): APROBABLE CON CORRECCIONES**, con tres hallazgos MEDIOS y tres BAJOS. El revisor comprobó los SHA-256 de la muestra y reprodujo exactamente los tamaños de `measure.json` y la proyección, y también los 487 B de WAL por fila. Hallazgos: **E3c-01** (MEDIO), el recorte incluía un minuto incompleto y una tabla de filas escasas podía fijarlo; **E3c-02** (MEDIO), la prueba de la CI usaba un 23 % de libros de nivel 50 en lugar del 33 % de referencia (1.019 frente a 872 B por libro); **E3c-03** (MEDIO), `data_gaps` se proyectaba con 90 días, aunque no tiene retención; **E3c-04** (BAJO), `RowSink` escribía dos veces una fila que PostgreSQL descarta; **E3c-05** (BAJO), reutilizar `--out` mezclaba mediciones; **E3c-06** (BAJO), `max_wal_size` se presentaba como cota. Corregidos en `dafe12c`, `420a532`, `72c5a55` y `d16d527` (§3.9, «Implementado en E3c»). Con 117 minutos completos, la proyección queda en 90,1 GB el año 1 y 124,8 GB el año 2 | Revisor; corrección de Claude **revisada en la fila siguiente** |
| 11 nov 2026 | **Segunda revisión de E3c (commit `18fd14b`): APROBABLE CON CORRECCIONES.** E3c-02 a E3c-06 RESUELTOS. **E3c-01 PARCIAL (MEDIO):** los dos archivos de control no acreditan que los demás estén completos. Cada archivo tiene su búfer, y el revisor demostró que, con los de control íntegros y las métricas del último minuto truncadas, se aceptaba ese minuto (73 métricas, 3 × 24 + 1). En la muestra entregada no hay pérdida: confirmó los 117 minutos, los 506 huecos, que los 41.190 intervalos válidos tienen sus 41.190 métricas, y los 90,1076 y 124,7648 GB. Corregido: marcas de progreso en `RowSink`, uso de la última marca que alcanzan todos los archivos, comprobación de métricas frente a intervalos válidos, y rechazo de una grabación sin marcas salvo con `--unmarked` (§3.9). El revisor ejecutó 1.023 pruebas con PostgreSQL 18.6 y contrastó la CI (1.025 pruebas, gitleaks sin hallazgos en 83 commits) | Revisor; corrección de Claude **confirmada en la fila siguiente** |
| 11 nov 2026 | **Tercera revisión de E3c (commit `820c361`): E3c-01 RESUELTO; E3c-01 a E3c-06 cerrados. Dictamen: APROBABLE CON CORRECCIONES**, por un hallazgo nuevo. **E3c-07 (BAJO):** reutilizar la carpeta de una grabación invalidaba las marcas, porque los contadores vuelven a cero en cada ejecución mientras los archivos se abren para añadir. El revisor lo demostró con dos ejecuciones de dos minutos: la medición tomaba solo la primera. Corregido: la carpeta debe ser nueva o estar vacía, y se comprueba antes de abrir nada (§3.9). La muestra antigua reproduce los 117 minutos y los 90,1 y 124,8 GB. El revisor ejecutó 1.027 pruebas con PostgreSQL 18.6 y contrastó la CI (1.029 pruebas, gitleaks sin hallazgos en 84 commits) | Revisor; corrección de Claude **confirmada en la fila siguiente** |
| 11 nov 2026 | **Cuarta revisión de E3c (commit `fc68cca`): E3c-07 RESUELTO. Dictamen final: APROBADO.** Los siete hallazgos E3c-01 a E3c-07 cerrados, sin reaperturas ni defectos nuevos. El revisor ejecutó 110 pruebas, incluidas las de equivalencia con PostgreSQL 18.6, y contrastó la CI (1.030 pruebas, gitleaks sin hallazgos en 85 commits). La aprobación técnica cubre E3c; **la decisión de capacidad del año 2 sigue pendiente del propietario** | Revisor |
| 11 nov 2026 | **E3 integrada en `main`** (pull request #5, `c8efe43`) y **propuesta de diseño de E4** en §13.1, pendiente de revisión: instancia, endurecimiento, servicios, PostgreSQL de producción, copias con exclusión de las particiones verificadas fuera (lo exige el tamaño medido en E3c), Object Storage por *instance principals*, copia al disco externo del propietario por SFTP con recibos, restauración en 10 pasos y medición. Tres preguntas al propietario (P-E4-1 a P-E4-3) | Claude |
| 11 nov 2026 | **Revisión del diseño de E4 (commit `71d6cec`): APROBABLE CON CORRECCIONES**, con un hallazgo ALTO (E4-D01: un recibo podía acreditar una copia externa inexistente) y seis MEDIOS (E4-D02: el sello impedía cargar el Parquet al restaurar; E4-D03: perfil SFTP incompleto; E4-D04: `IPAddressDeny` no acreditaba el aislamiento de metadatos; E4-D05: faltaba el descifrado para la restauración mensual; E4-D06: bloqueo REST no escribible; E4-D07: la cadencia semanal no acotaba el tamaño de las copias). Además: la cuota de A1 difiere entre páginas oficiales (2/12 frente a 4/24) y el propietario la confirma en la consola; `MemoryUtilization` sigue [P] hasta E5; PGDG y `uv.lock` tienen arm64. Todo incorporado en §13.1, pendiente de confirmación | Revisor; corrección de Claude **revisada en la fila siguiente** |
| 11 nov 2026 | **Segunda revisión del diseño de E4 (commit `e22ea18`): APROBABLE CON CORRECCIONES.** E4-D01, D02, D03, D05 y D06 resueltos; D04 y D07 parciales. Nuevos: **R-E4-01** (ALTO) una dependencia podía caducar mientras una copia la necesitaba; **R-E4-02** (MEDIO) el ciclo de vida por edad no daba las cantidades de D25 ni las presupuestadas; **R-E4-03** (MEDIO) el perfil de red de `svc-backup` omitía el servicio de autenticación; **R-E4-04** (MEDIO) faltaba autenticar el volcado recibido y aislar el restaurador; **R-E4-05** (BAJO) el desafío prometía más de lo que da. Todo incorporado en §13.1 y §3.9 (punto 4); nueva pregunta P-E4-4 al propietario sobre ampliar D25 | Revisor; corrección de Claude **revisada en la fila siguiente** |
| 11 nov 2026 | **Tercera revisión del diseño de E4 (commit `595a39e`): APROBABLE CON CORRECCIONES.** R-E4-02 a R-E4-05 resueltos. **R-E4-01 residual (MEDIO):** borrar una copia de día sellado cuando el día es elegible podía dejar sin dependencia a una copia retenida que fija otra generación. Corregido: se borra solo si cada copia retenida que la fija tiene verificada fuera la generación de su propio manifiesto. Ajustes menores (riesgos sin «120 días»; dos visitas del desafío en la simulación) incorporados. P-E4-4 pendiente del propietario | Revisor; corrección de Claude **confirmada en la fila siguiente** |
| 11 nov 2026 | **Cuarta revisión del diseño de E4 (commit `2a71829`): APROBADO.** R-E4-01 resuelto (modelo lógico de 32 combinaciones del revisor), sin reaperturas ni defectos nuevos. Pendiente: P-E4-4 (propietario) y la implementación con sus pruebas | Revisor |
| 11 nov 2026 | **E4a implementada en el repositorio** (commits `ca69fd1` a `95158ca`): endurecimiento, unidades de systemd, guiones de instalación, `kbot.ops` (comprobación F2-A10 y prueba de aislamiento) y pruebas en la CI de `deploy/` (1.045 pruebas). Queda ejecutarla en el servidor cuando el propietario cree la instancia. Cuota de A1 confirmada por el propietario en la consola: 16 núcleos y 96 GB (límite de la cuenta; se usa 2/12). **Revisada en la fila siguiente** | Claude |
| 12 nov 2026 | **Revisión de E4a (commit `061b1ab`): APROBABLE CON CORRECCIONES**, con un hallazgo ALTO y seis más: **E4a-01** (ALTO) incluir la guarda en `/etc/nftables.conf` habría cargado el `flush ruleset` por defecto de Ubuntu y vaciado `ufw`; **E4a-02** (MEDIO) `01` verificaba unidades cuyo intérprete aún no existía; **E4a-03** (MEDIO) sin volumen de datos seguía en el disco de arranque; **E4a-04** (MEDIO) la prueba de metadatos pasaba con `IPAddressDeny=any` aunque la guarda no actuara, la comprobación de la guarda aceptaba palabras sueltas y a `kbot_sync` se le exigía una conexión SQL que no tiene; **E4a-05** (MEDIO) `ufw` ignoraba las filas IPv6; **E4a-06** (MEDIO) un comando fallido podía leerse como «sin puertos»; **E4a-07** (BAJO) faltaba la procedencia de la huella de `uv`. Corregidos en `8108294` (CI en verde, 1.054 pruebas) | Revisor; corrección de Claude **revisada en la fila siguiente** |
| 12 nov 2026 | **Segunda revisión de E4a (commit `baab546`): APROBABLE CON CORRECCIONES.** E4a-02, 03, 05, 06 y 07 RESUELTOS. Residuales MEDIOS: **E4a-01**, parar `nftables.service` después de activar `ufw` ejecutaba su `flush ruleset` de parada; **E4a-04**, la comprobación estructural aceptaba la regla en una cadena sin gancho (el revisor lo demostró) y `netcheck` eximía ya a `kbot_backup`. Corregidos en `717c6d4` (CI en verde, 1.055 pruebas): retirada del servicio genérico antes de `ufw` con comprobación posterior, y análisis del JSON de `nft` con alcanzabilidad de la regla; solo `root` exento hasta E4c | Revisor; corrección de Claude **revisada en la fila siguiente** |
| 12 nov 2026 | **Tercera revisión de E4a (commit `faa219d`): APROBABLE CON CORRECCIONES.** E4a-01 RESUELTO. **E4a-04 residual (MEDIO):** el analizador JSON descartaba los elementos de un conjunto que no interpretaba, así que `{0, rango 100-65535}` pasaba como «solo root». Corregido en `4714d47` (CI en verde, 1.055 pruebas): cualquier elemento no interpretado hace fallar la comprobación | Revisor; corrección de Claude **confirmada en la fila siguiente** |
| 12 nov 2026 | **Cuarta revisión de E4a (commit `d796ba6`): APROBADO** para la parte que no necesita servidor. E4a-04 residual RESUELTO; E4a-01 a E4a-07 cerrados sin regresiones. Quedan para el servidor real: los instaladores, F2-A10, las comprobaciones por usuario, F1-A6 y el bloqueo REST compartido | Revisor |
| 12 nov 2026 | **E4b implementada en el repositorio** (commit `1e4949f`, CI en verde con 1.063 pruebas): configuración de producción de PostgreSQL 18 (sin TCP, `peer` sin mapa, bloqueos de §3.9), `02_postgresql.sh`, `kbot.ops.pgcheck` (las comprobaciones de §9, ejecutadas también por la CI contra su clúster) y `f1a6_servidor.sh`. Queda ejecutarla en el servidor. **Revisada en la fila siguiente** | Claude |
| 16 nov 2026 | **Revisión de E4b (commit `6b95d54`): APROBABLE CON CORRECCIONES**, con cinco hallazgos MEDIOS y uno BAJO: **E4b-01** F1-A6 esperaba el código 3 de `psql` en los rechazos de SQL (es 1); **E4b-02** `02` exigía `03` antes, y `03` verificaba unidades que dependen de PostgreSQL; **E4b-03** una ejecución cortada de `roles.sql` dejaba `kbot_owner` sin la base y el reintento se la saltaba; **E4b-04** faltaban las matrices de pertenencias y privilegios efectivos en el servidor; **E4b-05** un ajuste de rol en la base cambiaba el `statement_timeout` real sin detectarse; **E4b-06** la migración solo se contaba. Corregidos en `2804d45` (CI en verde) | Revisor; corrección de Claude **confirmada más abajo (segunda revisión de E4b)** |
| 16 nov 2026 | **Instancia creada y decisión del propietario: el cortafuegos de la imagen de Oracle en lugar de `ufw`.** La instancia `kbot` (`VM.Standard.A1.Flex`, 2 OCPU, 12 GB, Ubuntu 24.04, AD-3, *Always Free-eligible*) se creó al fin en AD-3, con la red `kbot-vcn` hecha con el asistente de Oracle y el volumen `kbot-datos` de 110 GB conectado (aparece como `/dev/sdb`). Antes de ejecutar nada que cambie el servidor, Claude comprobó que la imagen trae `iptables` con `iptables-persistent`, con entrada solo para SSH y las reglas de Oracle para el disco de arranque (iSCSI) y sus servicios, y que Oracle advierte de no usar `ufw` en sus imágenes de Ubuntu. El propietario aprobó usar ese cortafuegos: `01_sistema.sh` solo limita la regla de SSH a su IP (y se niega si no es la de la sesión), conserva las de Oracle y cierra IPv6; `kbot.ops` comprueba las reglas cargadas en lugar de `ufw`; F2-A10 (`07`) cambia en consecuencia. Revisado el 17 nov (filas siguientes) y **aprobado** tras FW-01 a FW-03 y la decisión del propietario sobre la IP | Propietario y Claude |
| 16 nov 2026 | **Segunda revisión de E4b (commit `ba58857`): APROBADO** para la parte que no necesita servidor. E4b-01 a E4b-06 RESUELTOS sin regresiones: clasificación por SQLSTATE, orden `01 → 02 → 03 → 04`, arranque reanudable (el revisor lo contrastó con un modelo de 27 puntos de interrupción y reanudación, simulado, no contra PostgreSQL), matrices de privilegios, ajustes ligados a la base de datos y head de Alembic. El cambio posterior del cortafuegos (fila anterior) no toca ningún archivo de E4b. Quedan para el servidor real la instalación y sus comprobaciones | Revisor |
| 17 nov 2026 | **Tercera revisión del alcance de la Fase 3 y revisión del cambio de cortafuegos (commit `26d4fbe`).** **Fase 3: APROBADO** como revisión técnica del alcance (F3-AL-01 a F3-AL-04 cerrados; P-F3-1 a P-F3-5 quedaban para el propietario). E4b conserva su aprobación. **Cortafuegos: APROBABLE CON CORRECCIONES.** La sustitución de `ufw` está justificada [H: [Oracle](https://docs.oracle.com/en-us/iaas/Content/Compute/known-issues.htm#ufw)]. **FW-01** (MEDIO): `firewall_status()` aprobaba `-A f2b-sshd -j ACCEPT` (SSH abierto desde cualquier origen, porque `INPUT` salta antes a esa cadena) y un `DROP` delante del permiso de SSH o del de iSCSI; **FW-02** (MEDIO): con la IP también limitada en el servidor, un cambio de IP doméstica dejaba fuera al propietario, y la recuperación documentada desde la consola serie abortaba, porque `01_sistema.sh` exigía una sesión SSH desde la IP nueva; **FW-03** (BAJO): «Implementado en E4a» seguía describiendo `ufw` | Revisor |
| 17 nov 2026 | **Decisiones del propietario.** (1) **Su IP se limita solo en la lista de seguridad de Oracle**, no en el servidor («Solo la consola Oracle»): un cambio de IP se corrige desde la consola web, sin entrar en el servidor, que sigue con SSH solo con clave, `fail2ban` y todo lo demás cerrado. (2) **Alcance de la Fase 3 aprobado** («Sí, lo apruebo»), con P-F3-1 a P-F3-5 tal como se recomendaban; P-F3-1 enmienda D18 (`03`): los backtests de la F3 corren en Windows nativo, y WSL2 vuelve cuando un backtest necesite PostgreSQL. Actualizados §2, §9 y §13.1, `07` y `08` | Propietario |
| 17 nov 2026 | **Correcciones de FW-01 a FW-03**, con la decisión anterior. `01_sistema.sh` deja sin cambios el `rules.v4` de la imagen (comprueba que lo es y se detiene si no) y solo instala `rules.v6`; desaparecen el argumento de la IP, la exigencia de una sesión SSH y la derivación del archivo, y con ellos el bloqueo de FW-02. `firewall_status()` compara las reglas cargadas con las esperadas cadena a cadena y en orden, en las dos familias, con lo que añade `fail2ban` como única parte variable (FW-01); la CI lo contrasta con el `iptables` real de Ubuntu 24.04 en un espacio de red desechable, partiendo del archivo de la imagen grabado en la instancia. `fail2ban` fija `banaction = iptables-multiport`. «Implementado en E4a» actualizado (FW-03). **Aprobado** más abajo (revisión de `5d7e344`) | Claude |
| 17 nov 2026 | **Hallazgo en la instancia, antes de cambiar nada: el agente de Oracle no corre como `root`.** Con órdenes de solo lectura, Claude comprobó que el agente (`/snap/oracle-cloud-agent/.../agent`), su actualizador y el complemento de monitorización `gomon` corren como `snap_daemon` (uid 584788), y que el actualizador tenía una conexión establecida con `169.254.169.254:80`. La guarda de E4-D04, que solo eximía a `root` porque se suponía que el agente corría como él, lo habría dejado sin metadatos y sin métricas (B30 y F2-A13). Corrección: la guarda exime a `root` y a `snap_daemon` por su número (`meta skuid != { 0, 584788 }`); `kbot.ops.check` y `netcheck` comparten la lista (`METADATA_ALLOWED`) y entienden el usuario por nombre o por número; `kbot-guardcheck@snap_daemon` lo comprueba en el servidor. **Aprobado** en la fila siguiente | Claude |
| 17 nov 2026 | **Revisión de las correcciones del cortafuegos y de la exención del agente (commit `5d7e344`): APROBADO** en su parte sin servidor. **FW-01 RESUELTO**: el revisor reprodujo los casos anteriores (un `ACCEPT` en la cadena de `fail2ban`, un `DROP` antes de SSH, del iSCSI o del salto de `OUTPUT`), que ahora fallan, y comprobó que las reglas originales y los bloqueos legítimos en IPv4 e IPv6 pasan. **FW-02 RESUELTO** con la decisión del propietario: el servidor deja de guardar la IP y desaparece la dependencia circular de la recuperación; el diseño es técnicamente aceptable, porque las listas de seguridad filtran en la VNIC [H: [Oracle](https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/securitylists.htm)]. **FW-03 RESUELTO.** La exención de `snap_daemon` es técnicamente aceptable: el uid `584788` es fijo y compartido por los *snaps* que lo declaran, y los usuarios `kbot_*` siguen excluidos [H: [Snapcraft](https://snapcraft.io/docs/explanation/snap-development/system-usernames/)]. Sin residuales ni hallazgos nuevos. Queda la aceptación en el servidor: reglas efectivas, acceso del agente a los metadatos, rechazo de los usuarios `kbot_*`, continuidad de la monitorización y las demás pruebas del despliegue | Revisor |
| 17 nov 2026 | **Instalación en la instancia, primer intento (etiqueta `v0.2.0-e4b`, sobre `5d7e344`).** Con permiso del propietario, Claude clonó el repositorio en `/opt/kbot` y ejecutó `01_sistema.sh /dev/sdb`. Hizo todos sus pasos hasta el cortafuegos, la guarda y `fail2ban` incluidos (volumen `kbot-data` formateado y montado en `/srv/kbot`, paquetes, usuarios, `sshd`, `rules.v6`). Se detuvo, **sin dejar nada abierto**, en su comprobación final, por dos fallos. (1) De Claude: `iptables -S | grep -q` falla con `pipefail` cuando `grep` deja de leer pronto e `iptables` recibe una tubería rota, aunque la regla estaba cargada; la versión anterior leía solo `INPUT`, que cabe en una escritura. (2) De la imagen: `netfilter-persistent reload` usa `--noflush` (`IPTABLES_RESTORE_NOFLUSH=yes` en `/etc/default/netfilter-persistent`) y duplicó todas las reglas de `INPUT`, `FORWARD` y `OUTPUT`, aunque no las de `InstanceServices`; sin efecto práctico, porque la segunda copia nunca se alcanza, pero la comprobación estricta habría fallado y cada recarga añadiría otra copia. Corrección: `01` carga los archivos con `iptables-restore` sin `--noflush`, lee las reglas enteras antes de buscar en ellas y exige la regla de SSH una sola vez; la CI reproduce en el espacio de red desechable las dos formas de carga. Una conexión SSH nueva entró sin problema después del fallo. **Aprobado** más abajo (revisión de `7409ca6`) | Claude |
| 17 nov 2026 | **Instalación en la instancia terminada: todo en verde** (etiqueta `v0.2.3-e4b`, `132b91b`; informe en `docs/operacion/instalacion_2026-11-17.md`). Con el arreglo anterior (`v0.2.1-e4b`), `01` y `02` fueron correctos. `03` instaló PostgreSQL 18.7 con el clúster en `/srv/kbot/postgresql/18/main`, la configuración, los roles y la base, pero se detuvo en las migraciones: Alembic 1.20 busca `pyproject.toml` en el directorio actual, el del administrador, que `postgres` no puede leer. `03` y `04` entran ahora en `/opt/kbot`, como en la CI (`6532ff7`, `v0.2.2-e4b`), y `03` se reanudó hasta `0003 (head)`. El primer `04` dio todo en verde salvo los puertos a la escucha: `rpcbind`, que la imagen trae con `nfs-common`, escuchaba en el 111. `01` lo para y lo enmascara (`132b91b`, `v0.2.3-e4b`). **Resultado final de `04_verificar.sh`: «All checks passed.»** Unidades, F2-A10 con diez de diez (`docs/operacion/comprobacion_2026-11-17.md`), PostgreSQL contra §9 y F1-A6 con los usuarios reales. Desde el contexto real de cada usuario: la guarda rechaza a los cinco `kbot_*` y deja pasar a `snap_daemon` y `root`; B19 correcto para los cinco; el bloqueo REST entre `kbot_ingest` y `kbot_archive` se comporta como dice E4-D06; y el agente de Oracle sigue conectado a los metadatos y sin errores con la guarda activa. Ningún servicio del bot arrancado todavía. **Aprobado** más abajo (revisión de `7409ca6`) | Claude |
| 17 nov 2026 | **Decisión del propietario: el grabador arranca ya** («Sí, ahora»), sin esperar a la revisión de la instalación y sabiendo que las copias llegan en E4c. `systemctl enable --now kbot-archive-daily.timer svc-ingest` a las 10:56 UTC: `kbot-archive-prepare` creó 540 particiones diarias y terminó; `svc-ingest` arrancó con 40 instrumentos (20 + 20; 10 + 10 con profundidad de nivel 50, como en P3); el primer archivo diario corre el 18 nov a las 00:20 UTC. A los dos minutos ya había velas REST y del WebSocket de los 40, calidad por minuto, instantáneas y métricas de profundidad, sin reinicios ni errores en su registro | Propietario y Claude |
| 17 nov 2026 | **Revisión de la instalación (commit `7409ca6`): APROBADO.** Las cuatro correcciones son conformes: la carga directa del cortafuegos reemplaza las tablas y elimina la duplicación de `--noflush` [H: [manual de Ubuntu](https://manpages.ubuntu.com/manpages/noble/man8/iptables-restore.8.html)], la lectura completa de las reglas antes de `grep` evita la tubería rota, `03` y `04` entran en el repositorio antes de Alembic y de las comprobaciones, y se detienen y enmascaran `rpcbind.socket` y `rpcbind.service`. El informe y la comprobación aportan evidencia coherente de la aceptación en el servidor: **F2-A10 conforme**, con PostgreSQL contra §9, F1-A6, la guarda por usuario, B19 y el bloqueo REST entre usuarios. El código coincide con la etiqueta instalada `v0.2.3-e4b`; los commits posteriores solo añaden documentación. Sin hallazgos nuevos. Quedan la confirmación de las métricas en la consola de Oracle, E4c, E4d y las mediciones de E5. El arranque del grabador queda registrado como decisión expresa del propietario | Revisor |
| 17 nov 2026 | **Contrato de datos de la Fase 3** (§12.1; F3-AL-01), en la rama `fase-3-e1`: presencia de cada minuto (`presente`, `ausente` o `no_descargado`, nunca «sin operaciones»), tiempo sin comprimir, temporalidades con velas completas o incompletas, calidad heredada por la peor, ventanas con recalentamiento, volumen limitado mientras B8 siga abierta y doce casos obligatorios (CD-01 a CD-12). **Revisado más abajo** | Claude |
| 17 nov 2026 | **E4a y E4b integradas en `main`** (pull request #6, `2e5889a`), por decisión del propietario («pasamos e4a y e4b a main»), sin esperar a E4c y E4d: el servidor ya funciona con ese código. Es una excepción a una pull request por etapa (P1). E4c, E4d y E5 siguen en ramas nuevas desde `main`, y la rama de la Fase 3 (`fase-3-e1`) ya contiene todo lo integrado | Propietario |
| 17 nov 2026 | **Revisión del contrato de datos (commit `00f2394`): APROBABLE CON CORRECCIONES.** Coherentes la contigüidad, el UTC, la deduplicación, los conflictos, la incompletitud y las restricciones de volumen. **CD-R01** (MEDIO): la primera y la última vela no acreditan la elegibilidad. Una respuesta con huecos en los extremos convertía esos minutos en `no_elegible` y daba una cobertura de 3/3 en vez de 3/5 [H: KuCoin advierte de que sus velas pueden estar incompletas]. **CD-R02** (MEDIO): «minutos terminales» confundía la disponibilidad intradía con el cierre del día, y un agregado de 5 m habría esperado a medianoche. **CD-R03** (MEDIO): faltaba la unidad del porcentaje solo REST y qué entradas cuenta, también la procedencia que conserva un indicador recursivo y su reinicio | Revisor |
| 17 nov 2026 | **Correcciones del contrato:** ventana solicitada, elegibilidad conocida y velas obtenidas separadas, con `no_elegible` solo con evidencia independiente (CD-R01). Disponibilidad intradía de cada minuto y del agregado, distinta del estado terminal del día (CD-R02). Porcentaje solo REST en minutos originales, con la procedencia completa de los indicadores recursivos desde su último reinicio (CD-R03). Seis casos nuevos, de CD-13 a CD-18. **Pendiente de confirmación** | Claude |
| 18 nov 2026 | **Segunda revisión del contrato de datos (commit `76ac5ce`): APROBABLE CON CORRECCIONES.** CD-R02 y CD-R03 RESUELTOS: la disponibilidad intradía y su separación del cierre del día, y el porcentaje solo REST en minutos originales con la semilla recursiva y su reinicio. **CD-R01, residual MEDIO:** un minuto podía ser `presente` y `no_elegible` a la vez, pero solo se excluía del denominador. Con cuatro minutos elegibles presentes y una vela contradictoria, la lectura literal daba 5/4 = 125 %, y no se definía la cobertura de una ventana entera `no_elegible`. Sin más hallazgos | Revisor |
| 18 nov 2026 | **Corrección del residual de CD-R01:** la cobertura es la de los minutos presentes dentro del conjunto $E$ de minutos no `no_elegible`, entre el tamaño de $E$. Las contradicciones se informan aparte, fuera del numerador y del denominador, y con $E$ vacío la cobertura es «no aplicable». CD-16 se amplía con los dos casos. **Aprobado** en la fila siguiente | Claude |
| 18 nov 2026 | **Tercera revisión del contrato de datos (commit `7e0307b`): APROBADO.** CD-R01 RESUELTO: la fórmula usa el mismo conjunto $E$ en el numerador y en el denominador, separa las contradicciones y da «no aplicable» con $E$ vacío, y CD-16 recoge los dos casos. CD-R02 y CD-R03 siguen resueltos. Sin hallazgos nuevos. **Con el contrato aprobado, F3-E1 puede programarse** | Revisor |
| 17 nov 2026 | **Decisión del propietario: las claves privadas de las copias, en una tarjeta SD y en papel**, en lugar del disco externo, que no tiene a mano. Genera las dos claves (principal y recuperación) con `age` en su PC (`docs/operacion/copias_claves_age.md`) y pasa solo las públicas, comprobadas al cifrar un texto de prueba para las dos. El histórico de la Fase 3 (unos 2 GB) va en el disco del PC. La copia en casa de las copias (E4c-3, unos 32 GB al año) no cabe en ninguno de los dos y se decide al llegar a esa parte | Propietario |
| 17 nov 2026 | **E4c-1 implementada** (commit `a7ce81c`, rama `fase-2-e4c`): copias locales cifradas con `age` (la caliente cada hora desde una sola instantánea, la de cada día sellado con las huellas de E3 y los objetos globales cada semana), retención local, unidades sin red y `05_copias.sh`. Ver «Implementado en E4c-1» en §13.1. **Pendiente de revisión** | Claude |
| 18 nov 2026 | **Revisión de E4c-1 (commit `8080251`): APROBABLE CON CORRECCIONES.** Coherentes la instantánea exportada con `pg_dump --snapshot`, la inclusión de toda la base mientras no haya alternativas externas verificadas y la retención local. **E4c1-01** (ALTO): el servicio de los objetos globales, como `postgres`, no podía atravesar `/srv/kbot/backups` (`kbot_backup`, `0750`) para llegar a su directorio. **E4c1-02** (MEDIO): el manifiesto caliente no llevaba la huella del contenido de cada tabla ni las versiones de §10.1. **E4c1-03** (MEDIO): la huella del catálogo no incluía el particionado (padre, clave y límites). **E4c1-04** (MEDIO): el renombrado precedía al registro, y un fallo del registro dejaba una copia publicada sin él, que la ejecución siguiente daba por hecha | Revisor |
| 18 nov 2026 | **Correcciones de E4c1-01 a E4c1-04.** La unidad de los objetos globales lleva `SupplementaryGroups=kbot_backup`, y la CI lo prueba como root con los usuarios y modos reales. Cada tabla y cada partición, con su modo, sus filas y su huella, y las versiones de §10.1; las particiones selladas toman la huella de su copia tras contarlas de nuevo. El particionado entra en la huella del catálogo. Cada copia se registra antes de publicarse, y toda copia publicada que el registro no avala se borra y vuelve a quedar pendiente. Ver «Implementado en E4c-1» en §13.1. **Revisado** en la fila siguiente | Claude |
| 18 nov 2026 | **Segunda revisión de E4c-1 (commit `d55a93f`): APROBABLE CON CORRECCIONES.** E4c1-01, E4c1-03 y E4c1-04 RESUELTOS. **E4c1-02, residual MEDIO:** la caché reutilizaba huellas de un manifiesto cuya integridad entera no se comprobaba. El revisor cambió un dígito hexadecimal de la huella de una partición, manteniendo las filas y el volcado: el registro la seguía aceptando, `reconcile()` no retiraba nada y la copia caliente adoptaba la huella alterada. La reutilización de huellas selladas es compatible con su inmutabilidad si se comprueba la integridad del manifiesto de origen | Revisor |
| 18 nov 2026 | **Corrección del residual de E4c1-02:** el registro guarda también el SHA-256 del manifiesto entero, y una copia solo se acepta, y sus huellas solo se reutilizan, si coinciden su nombre, sus dos volcados y los bytes de su manifiesto. Una copia alterada deja de ser fuente de huellas: la copia caliente las toma en su instantánea, y la ejecución siguiente la borra y la rehace, tras lo cual se reutiliza otra vez. El recuento queda como salvaguarda frente a un sello roto (una partición sellada que pierde una fila detiene la copia). **Aprobado** en la fila siguiente | Claude |
| 18 nov 2026 | **Tercera revisión de E4c-1 (commit `8560778`): APROBADO.** El residual de E4c1-02 queda RESUELTO: el registro incorpora el SHA-256 de los bytes del manifiesto, calculado antes de publicar. Un manifiesto alterado deja de ser fuente de huellas, la copia caliente las toma de su instantánea y la reconciliación retira y rehace la copia. E4c1-01 a E4c1-04, cerrados, sin hallazgos nuevos. La aprobación cubre el código; **falta su validación en el servidor** | Revisor |
| 18 nov 2026 | **E4c-1 instalada en el servidor** (etiqueta `v0.2.4-e4c1`, sobre `8560778`; informe en `docs/operacion/copias_2026-11-18.md`), con permiso del propietario. `02_codigo.sh` y `05_copias.sh`, correctos; `04_verificar.sh`, «All checks passed.». La primera copia caliente tardó 43 s y ocupa 28,8 MB cifrada (552 tablas y particiones, 696.247 filas: unas 13 horas de grabación, unos 50 MB de volcado por día); los objetos globales se escribieron en su directorio con `SupplementaryGroups=kbot_backup`; los permisos son los previstos y no queda ningún volcado en claro. Hasta E4c-2 la copia caliente lleva toda la base cada hora, así que crece con los días: **E4c-2 debe llegar en las próximas semanas**. Pendiente de revisión | Claude |
| 18 nov 2026 | **Corrección del grabador tras su primera noche en el servidor** (rama `fase-2-grabador`, desde `fase-2-e4c`; informe en `docs/operacion/incidencia_2026-11-17_grabador.md`). A las 17:45 del 17 nov KuCoin rechazó con HTTP 400 una reconexión de Spot. Esa excepción (`InvalidStatus`) no se trataba como desconexión y terminó el stream sin aviso: Spot no se grabó por WebSocket durante 6 h 16 min (libros, operaciones y velas del WebSocket; las velas se cerraron con REST). A medianoche, el cambio de universo cerró los streams, encontró el fallido y el grabador cayó; systemd lo arrancó de nuevo a los 10 s. La caída perdió el hueco abierto desde las 17:45 en 67 de los 70 canales de Spot, y el último minuto de 3 instrumentos de Futures que salían del universo: 13 minutos quedaron `sin_explicar` en lugar de `hueco` (no se corrige a mano: lo escrito es terminal y es cierto con lo que quedó). Además, Spot se cortaba por falta de pong a los 3 s unas 5 veces por hora (el 7 % del tiempo el 18 nov). Corrección: (1) un rechazo del handshake y cualquier fallo de la conexión son desconexiones, con reintentos de espera creciente entre 5 y 60 s; (2) el grabador vigila sus streams y su vigilante, y si uno termina se detiene con error tras escribir lo abierto; (3) cerrar los streams nunca falla por uno que ya había fallado; (4) al pararse escribe solo huecos (los cerrados sin escribir, los abiertos, cerrados en ese instante, y lo que los instrumentos retirados ya no cerrarán), y al arrancar el hueco de la parada empieza donde los huecos escritos dejan de cubrir el punto de reanudación, sin pasar nunca por un instante sin explicar; (5) el pong se espera 10 s, o el `pingTimeout` del welcome si es mayor, y los pongs lentos se miden; (6) se acepta el welcome que KuCoin anunció; (7) desconexiones, reconexiones y paradas, en el diario. **Pendiente de revisión** | Claude |
| 18 nov 2026 | **La corrección del grabador, instalada antes de su revisión**, por decisión del propietario: etiqueta `v0.2.5-grabador` (`db5594f`) a las 11:03 UTC; `02_codigo.sh`, el reinicio del grabador (unos 27 s sin grabar) y `04_verificar.sh`, con «All checks passed.». En sus primeros 21 minutos, ningún corte (2 en los 20 anteriores): 12 pongs de Spot tardaron de 1,1 a 6,4 s; el pong se lee siempre unos 126 ms después de su `ts`, de lo que se deduce, sin demostrarlo, que el retraso es anterior (en el camino de ida o en KuCoin) | Claude |
| 18 nov 2026 | **Revisión de la corrección del grabador (commit `db5594f`): APROBABLE CON CORRECCIONES.** El revisor ve adecuados el pong de 10 s (o el anunciado, si es mayor), detener el grabador para que systemd lo vuelva a arrancar y la regla `covered_until`. Hallazgos: **GR-01** (MEDIO), la parada perdía los huecos de un ciclo cuya escritura fallaba; **GR-02** (MEDIO), la espera creciente desbordaba a partir del intento 1.025 y terminaba el stream; **GR-03** (MEDIO), la vigilancia esperaba a que acabase todo el ciclo, y una espera de REST o de la base de datos podía alargarlo; **GR-04** (BAJO), un hueco de un instrumento retirado con el mismo inicio que otro se descartaba por la clave primaria y perdía su cola; **GR-05** (BAJO), una parada pedida durante un ciclo se tomaba por un fallo del vigilante | Revisor |
| 18 nov 2026 | **GR-01 a GR-05 corregidos.** GR-01: el núcleo guarda los huecos que entrega hasta que el servicio confirma su escritura (`written`), y la parada los incluye. GR-02: las duplicaciones dejan de contar en 30. GR-03: cada ciclo corre como tarea propia, vigilada cada segundo; si un stream termina o se pide la parada, el ciclo se cancela en su espera, y la llamada a la base de datos ya empezada se protege (`asyncio.shield`) y la parada la espera antes de escribir. GR-04: `write_gap` escribe la parte no cubierta desde donde acaba el hueco que ocupa su inicio, sin cambiar nada escrito. GR-05: tras pedir la parada no se vigila nada. Pruebas de aceptación de cada uno, dos de ellas contra PostgreSQL. **Pendiente de confirmación** | Claude |
| 18 nov 2026 | **Segunda revisión de la corrección del grabador (commit `f74020e`): APROBABLE CON CORRECCIONES.** GR-02, GR-03, GR-04 (en PostgreSQL) y GR-05, RESUELTOS. **GR-01, residual MEDIO:** si la escritura del último ciclo de un instrumento retirado fallaba, la parada no cubría sus intervalos (sus cursores ya habían avanzado, y ese ciclo lo daba por terminado). **GR-06** (BAJO): `--sink rows` perdía las colas que ahora conserva PostgreSQL, porque su conexión respondía siempre sin filas. **GR-07** (BAJO): el informe daba por demostrado que el retraso del pong estaba en KuCoin; con relojes distintos solo se puede deducir | Revisor |
| 18 nov 2026 | **Residual de GR-01, GR-06 y GR-07, corregidos.** GR-01: el núcleo guarda, por cada instrumento retirado para el que cierra un ciclo aún no confirmado, dónde había cerrado antes (`_retired_unwritten`), aunque ese ciclo lo termine; si la escritura falla, la parada lo cubre desde ahí hasta su salida; confirmado, nada más. GR-06: la conexión grabadora responde a `store.write_gap` con los huecos escritos (si una inserción `RETURNING` se escribió, y dónde acaba el hueco que ocupa un inicio); `test_rows_equivalence.py` compara con PostgreSQL el caso original, los inicios ocupados en cadena y las repeticiones, filas y cobertura. GR-07: el informe y §3.1 separan lo observado de lo deducido. **Pendiente de confirmación** | Claude |
| 18 nov 2026 | **La CI de `a01184a` falló** en las dos pruebas de equivalencia de `--sink rows`: su conexión respondía también a los `RETURNING 1` de la evidencia tardía y buscaba una columna «1». Corregido en `8f304f5`: solo responde a las preguntas de `store.write_gap`, y una prueba unitaria escribe un libro tardío por `--sink rows` | Claude |
| 18 nov 2026 | **Tercera revisión de la corrección del grabador (commit `8f304f5`): APROBABLE CON CORRECCIONES.** GR-06 y GR-07, RESUELTOS. **GR-01, residual MEDIO:** si se pedía la parada mientras se escribía un ciclo, la escritura podía terminar bien después de cancelar el ciclo, pero el núcleo no se enteraba y la parada añadía huecos `proceso_detenido` sobre lo escrito (flujos S1 e intervalos válidos de un instrumento retirado). **GR-08** (BAJO): la prueba de la parada con Spot caído aún podía fallar si el vigilante detectaba un bloqueo y cerraba un hueco `Stall` de Futures en el mismo instante que el hueco abierto de Spot | Revisor |
| 18 nov 2026 | **Residual de GR-01 y GR-08, corregidos.** GR-01: `Recorder._db` acepta `on_success`, que se ejecuta en el bucle en cuanto la llamada termina sin error, aunque nadie la espere ya; la escritura de un ciclo lo usa para confirmarlo al núcleo (`written`), y como su retrollamada va antes que la de `_settle_db`, la parada ya lo sabe al calcular sus huecos. Prueba: se cancela el ciclo durante la escritura del último ciclo de un instrumento retirado; si termina bien, la parada no añade nada (con el código anterior añadía sus cuatro canales); si falla, cubre lo no escrito hasta su salida. GR-08: la prueba distingue el hueco `Stall` por lo que es, no por cuándo acaba, y fuerza un bloqueo de 10 s justo antes de la parada (un reloj que salta). **Pendiente de confirmación** | Claude |
| 18 nov 2026 | **Cuarta revisión de la corrección del grabador (commit `cb4c275`): APROBABLE CON CORRECCIONES.** GR-08, RESUELTO, sin hallazgos nuevos. **GR-01, residual MEDIO:** la confirmación aún podía llegar después de calcular los huecos de la parada. Una retrollamada de la tarea solo se programa cuando esta termina, así que `_settle_db` podía verla terminada, volver sin esperar y dejar que `core.stop` calculase antes de que se confirmara el ciclo (escritura terminada → `_settle_db` la ve terminada → `core.stop` → confirmación): flujos S1 escritos y, encima, tres huecos falsos | Revisor |
| 18 nov 2026 | **Residual de GR-01, corregido.** `Recorder._db` ejecuta `on_success` dentro de la propia tarea protegida, tras `asyncio.to_thread` y antes de que termine: quien la ve terminada sabe que ya confirmó. Prueba: el caso nuevo de la prueba del retirado llama a la parada en el mismo turno del bucle en que termina la escritura y la encuentra terminada; con éxito, ningún hueco (con el código anterior, cuatro); con error, la cobertura de lo no escrito. **Aprobado** en la fila siguiente | Claude |
| 18 nov 2026 | **Quinta revisión de la corrección del grabador (commit `2ffd88b`): APROBADO.** El residual de GR-01 queda RESUELTO: `call_and_confirm` confirma la escritura dentro de la tarea protegida, antes de que termine. Comprobados los cuatro caminos (escritura correcta o fallida, pendiente o ya terminada). GR-01 a GR-08, cerrados, sin hallazgos nuevos. La aprobación cubre el código; **falta instalarlo** | Revisor |
| 18 nov 2026 | **Formato 2 del archivo: `numeric` como `decimal256(76, 24)`** (rama `fase-2-decimales`). La prueba real de la descarga de F3-E1 falló al escribir un Parquet: KuCoin envía volúmenes y turnover de Spot con más de 18 decimales. En el servidor, 2 velas y una vela del WebSocket del 17 nov tienen 20 o 21 decimales (NEAR-USDT, ZEC-USDT), así que el archivo del 17 nov, que toca el 21 nov a las 00:20 UTC, habría fallado; no se habría perdido nada, porque nada se borra de la base antes de archivarlo, pero el archivo se habría detenido. Medido con datos reales (zstd 3, bytes por fila): velas, `decimal128(38, 24)` 67,3, `decimal256(76, 24)` 66,9, `decimal256(76, 38)` 88,3 y texto 39,0; métricas del libro, `decimal128(38, 18)` 103,4, `decimal128(38, 24)` 118,2, `decimal256(76, 24)` 117,7, `decimal256(76, 38)` 153,1 y texto 76,3. Se elige `decimal256(76, 24)`: mismo tamaño que `decimal128(38, 24)` y 52 dígitos enteros en lugar de 14 (el máximo visto es 11). **Revisado** en la fila siguiente | Claude |
| 18 nov 2026 | **Revisión del formato 2 (commit `4719264`): APROBABLE CON CORRECCIONES.** **DC-01** (MEDIO): DuckDB pierde decimales al leer el formato nuevo. DuckDB 2.0, el lector del archivo (§2 y §3.9; hoy, la medición de `tools/measure_archive.py`), lee una precisión mayor que 38 como `DOUBLE`, sin error: `7685.59829732743707761776` vuelve como `7685.598297327437` y `1.000000000000000000000001` como `1.0`, en contra de §0, principio 5. Propuesta: `decimal128(38, 24)`, que conserva los valores reales y DuckDB lee exactos, con la documentación y las pruebas al límite de 14 dígitos enteros. Prueba de aceptación: escribir esos dos valores y leerlos con el consumidor efectivo, exigiendo tipo decimal y valores exactos; añadir los límites del formato elegido y exigir que las rutas incompatibles fallen antes de calcular. La escritura y la verificación con PyArrow sí conservan la exactitud, y el lector actual conserva las huellas del formato 1 | Revisor |
| 18 nov 2026 | **DC-01, corregido.** `numeric` pasa a `decimal128(38, 24)` (sigue siendo el formato 2, aún sin instalar): hasta 24 decimales y 14 dígitos enteros, unas mil veces el máximo visto. `parquet.check_duckdb` comprueba, antes de calcular, que cada decimal de los archivos, también dentro de listas y estructuras, llega a DuckDB como el mismo decimal; si no, falla. La medición la usa antes de su primera consulta y toma del formato el tipo de los libros tipados. Además, como un `CAST` de texto a decimal de DuckDB redondea en silencio (`1.0000000000000000000000009` da `1.000000000000000000000001`), solo convierte el texto de un libro si es un decimal que cabe, y falla con cualquier otro. Pruebas: los valores reales y los dos de DC-01, leídos con DuckDB como `DECIMAL(38,24)` exactos; los límites (24 decimales y 14 dígitos enteros pasan; 25 y 15 se rechazan); un `decimal256`, como columna o dentro de una lista de estructuras, rechazado antes de calcular, y los formatos 1 y 2, aceptados; la medición, que no consulta nada si un archivo no pasa. Y contra PostgreSQL: un día con el turnover de NEAR-USDT y `1.000000000000000000000001` se archiva y DuckDB lee sus doce archivos exactos; otro con 25 decimales no se publica, falla igual en cada ejecución y su fila sigue exacta en la base. **Confirmado** en la fila siguiente | Claude |
| 19 nov 2026 | **Segunda revisión del formato 2 (commit `f57a3a1`): APROBABLE CON CORRECCIONES.** DC-01, RESUELTO: el formato 2 conserva los valores exactos en PyArrow y DuckDB, también en los extremos de 14 dígitos enteros y 24 decimales. **DC-02** (MEDIO): la comprobación permite redondear al consultar juntos los formatos 1 y 2 (§3.9 y §0, principio 5). `check_duckdb` mira cada archivo por separado, pero la consulta combina sus esquemas, y DuckDB toma por defecto el del primer archivo: con un archivo vacío del formato 1 delante de otro del formato 2, la comprobación pasa y `read_parquet` devuelve `7685.59829732743707761776` como `7685.598297327437077618` y `1.000000000000000000000001` como `1.000000000000000000`. `union_by_name=true` tampoco lo evita. Corrección: comprobar el esquema efectivo de cada conjunto consultado y rechazar cualquier conversión con pérdida, o normalizarlo con una conversión segura explícita; los archivos antiguos comprobados vacíos pueden quedar fuera de esa lectura. Prueba de aceptación: consultar juntos un archivo vacío del formato 1 y otro del formato 2, en los dos órdenes, y recuperar exactos los dos valores o rechazar la consulta antes de calcular. La aprobación completa queda pendiente solo de DC-02 | Revisor |
| 19 nov 2026 | **DC-02, corregido.** `check_duckdb` recibe los archivos que una consulta lee juntos y los lee como ella: `read_parquet` con la misma lista, en su orden y sin opciones. Compara el esquema de cada archivo con el de esa lectura: cada decimal, también dentro de listas y estructuras, debe llegar a DuckDB con su mismo tipo, y nada que no sea decimal puede convertirse en uno; si no, falla antes de calcular y nombra el archivo cuyo tipo cambiaría. Así, un archivo del formato 1 y otro del formato 2 se rechazan juntos en los dos órdenes: en el inverso, DuckDB convertiría el `decimal128(38, 18)` del formato 1 en `decimal128(38, 24)`, y un valor de más de 14 dígitos enteros fallaría al leerse. Con `union_by_name`, DuckDB redondea en los dos órdenes; por eso la lectura comprobada es sin opciones, como la de la medición. Al probarlo apareció otra conversión: detrás de un archivo con un decimal, DuckDB convierte el texto o el `DOUBLE` de la misma columna a ese decimal y lo redondea en silencio; también se rechaza. La medición comprueba cada lista de archivos que leen sus consultas, antes de la primera. Los 27 días del formato 1 están vacíos: quien los lea junto con días del formato 2 debe dejarlos fuera o leerlos aparte. Pruebas: un archivo vacío del formato 1 y otro del formato 2 con los dos valores de DC-01, juntos y en los dos órdenes, rechazados antes de calcular; un archivo vacío del formato 2 con el lleno, en los dos órdenes, leídos como `DECIMAL(38,24)` exactos; un texto y un `DOUBLE` detrás de un decimal, rechazados; y la medición, que no ejecuta ninguna consulta si una de sus listas empieza por un día vacío del formato 1. Con la comprobación anterior, esas cinco pruebas fallan. La prueba contra PostgreSQL comprueba cada uno de los doce archivos del día como su propia lectura. **Confirmado** en la fila siguiente | Claude |
| 19 nov 2026 | **Tercera revisión del formato 2 (commit `89095b3`): APROBADO.** DC-02, RESUELTO: `check_duckdb` comprueba cada conjunto de archivos junto y en el orden de la consulta, y la medición lo aplica antes de calcular. Confirmado: las mezclas de los formatos 1 y 2 se rechazan en los dos órdenes, también dentro de listas y estructuras; las conversiones de texto o `DOUBLE` a decimal se rechazan; los conjuntos homogéneos conservan exactos sus valores y tipos; y las cinco pruebas nuevas fallan con la comprobación anterior y pasan con la corregida. DC-01 y DC-02, cerrados, sin hallazgos nuevos: **formato 2 aprobado** como `decimal128(38, 24)`. Queda instalarlo antes del 21 nov a las 00:20 UTC | Revisor |
| 19 nov 2026 | **Formato 2 instalado en el servidor** (etiqueta `v0.2.7-decimales`, sobre `89095b3`), con el visto bueno del propietario, a las 00:21 UTC, después del archivo diario de las 00:20: `02_codigo.sh`, correcto, y `04_verificar.sh`, «All checks passed.». El grabador siguió funcionando sin reinicios: su código no cambia. Desde ahora el archivo escribe `numeric` como `decimal128(38, 24)`; el 17 nov, el primer día con datos, se archivará con él el 21 nov a las 00:20 UTC. Los días vacíos del 19 oct al 15 nov quedan en el formato 1 | Claude |
| 19 nov 2026 | **F3-E1: KuCoin da dos velas distintas para un mismo minuto** (enmienda de §12.1, **aprobada** en la revisión del 20 nov). La descarga real del histórico se detuvo a las 08:36 UTC, con 16 instrumentos de Spot completos, en DOT-USDT del 18 nov. La respuesta de KuCoin para ese día trae dos filas para las 09:03 UTC: la vela completa (mínimo 0,7309, cierre 0,7314, volumen 5.281,1148) y otra parcial (0,7316, 0,7316, 108,8588). Pedida de 08:58 a 09:08 trae también las dos, y de 09:02 a 09:04, solo la completa. Entre las 09:03:25 y las 09:03:28 de ese día, las velas REST de KuCoin respondieron al grabador «HTTP 503». El código detenía toda la descarga: cumplía el contrato, que nunca elige una vela, pero así la descarga no podía terminar. **Corrección:** ese minuto queda `en_conflicto` (punto 2 y CD-19): sin vela en el archivo, con las dos velas exactas en el manifiesto del día, en el denominador y nunca en el numerador. El resto de la respuesta vale, el día se cierra y la descarga sigue. La respuesta no se trata como fallida, para no perder los otros 1.439 minutos ni pedirla una y otra vez con el mismo resultado. Tampoco se estrecha la petición hasta que KuCoin dé una sola vela, porque eso sería elegir. Los manifiestos ya escritos no tienen ese campo ni conflictos, porque la descarga se detenía en el primero. **Observación sobre el grabador** (E2, instalado; no cambia en esta ronda). En el servidor, ese minuto quedó `discrepante` (`vela_cruzada_discrepante`): su vela del WebSocket coincide con la completa, pero como vela REST guardó la parcial. `apply_rest_candles` indexa la respuesta por minuto y, si un minuto trae dos filas distintas, se queda en silencio con la última. No se sabe si aquella respuesta traía las dos filas o solo la parcial (KuCoin aún sin la vela final 15 s después del minuto). La vela no se usa, porque es discrepante, pero el defecto existe; se propone corregirlo en una ronda propia del grabador | Claude |
| 19 nov 2026 | **F3-E1: un día solo se cierra 48 h después de su fin** (enmienda de §12.1, punto 1, y CD-20, **aprobada** en la revisión del 20 nov). Al repetir la descarga a las 22:10 UTC para completar DOT-USDT, KuCoin ya no daba dos velas para el 18 nov a las 09:03: solo la completa (mínimo 0,7309, cierre 0,7314, volumen 5.281,1148), la que coincide con la vela del WebSocket del grabador. Las dos filas estuvieron al menos de las 08:36 a las 09:39 UTC. Con la regla anterior, un día con todas sus peticiones respondidas quedaba cerrado aunque KuCoin lo corrigiera después, y ese minuto habría quedado `en_conflicto` para siempre. **Corrección:** un día solo se cierra si se descargó al menos 48 h después de su fin, el plazo durante el que el grabador sigue pidiendo las velas REST (§3.8). Antes, cada descarga lo vuelve a pedir entero y lo reescribe. No cambia el formato: el manifiesto ya guardaba la hora de la descarga. CD-19 se mantiene: si KuCoin sigue dando dos velas cuando el día se cierra, el minuto queda `en_conflicto`. Pruebas: CD-20 con la secuencia real (el día queda abierto con el conflicto, toma la corrección y se cierra a las 48 h, sin más peticiones después) y el límite exacto de las 48 h | Claude |
| 19 nov 2026 | **F3-E1: el minuto en que un instrumento empieza a cotizar es elegible** (precisión de §12.1, punto 2, y CD-16, **aprobada** en la revisión del 20 nov). El primer informe de cobertura de la descarga real dio una contradicción en ASTERUSDTM: el minuto de las 12:19 del 19 sep 2025 tenía vela, pero salía `no_elegible`. KuCoin da como apertura del contrato las 12:19:20 (`firstOpenDate`), y la elegibilidad se miraba en el inicio del minuto. Ese minuto no está fuera de las fechas, como pide CD-16. **Corrección:** la evidencia de que un minuto no era elegible tiene que cubrirlo entero, y la de que lo era basta con que toque parte de él. Prueba con ese caso real y con una retirada a las 12:07:30; con el código anterior falla | Claude |
| 19 nov 2026 | **F3-E1, para revisión: el histórico descargado y su cobertura** (rama `fase-3-e1`). Programado: el universo (`docs/f3/universo_2026-11-18.json`), las temporalidades superiores (`timeframes`), la presencia, la elegibilidad y la cobertura de cada minuto (`coverage`), los archivos diarios (`store`), la descarga (`download`) y el informe (`report`), con `python -m kbot.history descargar` e `informe`. **Descarga real** en el PC del propietario (P-F3-2), de 07:11 a 22:27 UTC: 40 instrumentos y 28.380 días (Spot, 731 desde el 19 nov 2024; Futures, 688 desde el 1 ene 2025), 34.500.732 velas, 1,88 GB en Parquet (54,6 bytes por vela) y 24 MB de manifiestos. Se estimaban unos 2 GB y 42 millones de filas. Spot tardó 1,7 h (una petición por día) y Futures 13,2 h (ocho por día). Ninguna petición fallida. **Cobertura** (`docs/f3/cobertura_2026-11-19.md`): Spot 89,44 % y Futures 92,87 %, sin minutos `no_descargado` ni en conflicto, y sin contradicciones. En Spot, 13 instrumentos están al 100 % y 4 por encima del 99,99 %. Los otros tres empezaron a cotizar dentro de la ventana: EVAA-USDT (6,2 %), PIEVERSE-USDT (24,8 %) y ASTER-USDT (57,7 %). Sin `firstOpenDate` ni anuncio, sus minutos anteriores son ausentes de elegibilidad desconocida. En Futures, la cobertura va del 79,9 % (PIEVERSEUSDTM) al 99,7 % (PEPEUSDTM). KuCoin no publica velas de minutos sin operaciones (§3.2), y esas ausencias no se interpretan. Con la regla de las 48 h, los días del 17 al 19 nov siguen abiertos | Claude |
| 20 nov 2026 | **Revisión de F3-E1 (commit `663c59c`): APROBABLE CON CORRECCIONES.** La **enmienda del contrato queda APROBADA** técnicamente: CD-19, CD-20 y la precisión de CD-16. Las 48 h quedan como política de congelación de la copia local. El revisor recalculó la cobertura de los 40 instrumentos, verificando las huellas de sus archivos, y coincide: Spot 89,4362 % y Futures 92,8669 %, con 28.380 días y 34.500.732 velas. No ejecutó `descargar` ni modificó el histórico. Pasaron las 47 pruebas de F3-E1. **F3E1-01** (MEDIO): las sumas de volumen y turnover usan el contexto decimal del entorno, de precisión 28, y redondean. El agregado diario de ZEC-USDT del 7 nov 2025 da de turnover `287386854.5583713212601414940`, y el exacto es `287386854.558371321260141494040000` (§12.1, puntos 4 y 6, y §0.5). Corrección: sumar con enteros escalados o con un contexto local de precisión suficiente, sin redondeos silenciosos. Prueba: ese caso y hasta 1.440 valores en los extremos admitidos, con resultados exactos e independientes del contexto. **F3E1-02** (MEDIO): el informe recorre los archivos y no la ventana solicitada. Con un día completo y otro fallido da 1.440/2.880; al borrar solo el Parquet del segundo, conservando su manifiesto, da 1.440/1.440, sin señalar el archivo ausente ni el tramo fallido. Una huella incorrecta también saca el día del denominador (§12.1, punto 2, y F3-A1). Corrección: conservar la ventana solicitada y recorrerla, con los días pendientes como `no_descargado`; una incoherencia de integridad bloquea el informe o queda invalidada de forma explícita. Prueba: interrumpir una descarga y probar archivos ausentes y huellas incorrectas. De acuerdo con revisar en una ronda propia el defecto de los duplicados REST del grabador | Revisor |
| 20 nov 2026 | **F3E1-01 y F3E1-02, corregidos.** **F3E1-01:** `timeframes.exact_sum` suma en el contexto contable de §5.7 (precisión 80 e `Inexact` atrapado). El formato 2 tiene como mucho 14 dígitos enteros y 24 decimales, así que los 1.440 minutos de un día suman como mucho 42 dígitos; una suma que no cabe en 80 falla. Pruebas: el día real de ZEC-USDT, con sus 1.440 volúmenes y turnovers en `tests/fixtures/history/`, exacto con contextos del entorno de 3, 28 y 200 dígitos; 1.440 valores en los extremos del formato 2; y una suma de 85 dígitos que lanza `Inexact`. Con el código anterior, cuatro de ellas fallan. **F3E1-02:** la descarga guarda la ventana pedida de cada instrumento en `solicitado.json`, unida a las anteriores, antes de pedir nada. El informe recorre esas ventanas día a día. Un día sin archivos cuenta sus minutos como `no_descargado`. Uno cuyos archivos no forman un par válido (sin registro, sin Parquet, con otra huella o con una ventana fuera de lo pedido) también, y se nombra con su motivo en la columna nueva «Días sin registro válido». Los días con archivos fuera de lo pedido se listan aparte. Se invalida el día en lugar de bloquear el informe porque una escritura interrumpida al reescribir un día abierto deja otra huella de forma normal, y la descarga siguiente lo repara. Pruebas: el caso del revisor (1.440/2.880 sin el Parquet, con otra huella, sin registro y sin archivos), una descarga interrumpida en el segundo de tres días (1.440/4.320 y, al repetirla, 4.320/4.320), la unión de ventanas y los motivos. **Histórico del 19 nov:** sus 40 `solicitado.json` llevan la ventana de su última descarga, la de las 22:10:54 UTC, que contiene las anteriores: Spot desde el 19 nov 2024, Futures desde el 1 ene 2025, hasta las 22:10. Antes de escribirlos se comprobó que cada día de esa ventana tiene un registro válido dentro de ella y que no hay archivos fuera. El informe rehecho con el código nuevo (`docs/f3/cobertura_2026-11-19.md`, generado a las 23:48 UTC) da los mismos números día a día, sin días sin registro válido ni archivos fuera de lo pedido. **F3E1-01, confirmado; F3E1-02, parcial** en la fila siguiente | Claude |
| 20 nov 2026 | **Segunda revisión de F3-E1 (commit `7fd094a`): APROBABLE CON CORRECCIONES.** **F3E1-01, RESUELTO:** las sumas usan el contexto contable de precisión 80 con `Inexact` atrapado. El revisor confirmó la fixture contra el histórico real de ZEC y la exactitud con distintos contextos del entorno. **F3E1-02, parcial, residual MEDIO:** la ventana se conserva dentro de cada instrumento, pero no para toda la selección de la orden (§12.1, puntos 1 y 2, y F3-A1). Con BTC y ETH y `--dias 1`, al interrumpir la primera petición de velas, ETH quedaba con denominador 0 y sin tramo pendiente, porque aún no tenía `solicitado.json`. Al interrumpir la consulta de contratos, quedaban así los dos. Corrección: escribir las ventanas de todos los instrumentos seleccionados antes de la primera petición REST, también la de contratos, y después descargar. Prueba: con dos instrumentos, interrumpir en contratos y en la primera petición de velas; en los dos casos el informe conserva las dos ventanas completas, con 1.440 minutos `no_descargado` cada una. El revisor pasó las 60 pruebas locales, verificó los 40 `solicitado.json`, contrastó las cifras diarias con la reconstrucción anterior y recalculó BTC-USDT y ASTERUSDTM: coinciden, sin registros inválidos ni archivos fuera de lo pedido. Sin hallazgos nuevos | Revisor |
| 20 nov 2026 | **Residual de F3E1-02, corregido.** `descargar` calcula las ventanas de todos los instrumentos de la orden y las escribe en sus `solicitado.json` antes de su primera petición, la de los contratos; después descarga. `Downloader.instrument` sigue escribiendo la suya, por si se llama directamente. Prueba: dos instrumentos y un día, con la orden interrumpida en los contratos y en la primera petición de velas. En los dos casos, cada instrumento conserva su ventana y el informe da 0/1.440, con 1.440 minutos `no_descargado`. Con el código anterior, los dos casos fallan. El histórico y su informe no cambian. **Confirmado** en la fila siguiente | Claude |
| 20 nov 2026 | **Tercera revisión de F3-E1 (commit `65d450d`): APROBADO.** **F3E1-02, RESUELTO:** la orden guarda las ventanas de todos los instrumentos seleccionados antes de crear el cliente REST y de consultar los contratos. Interrumpirla en los contratos o en la primera petición de velas conserva las ventanas completas y sus minutos `no_descargado`. El revisor confirmó además que un fallo al guardar las ventanas impide empezar las peticiones, y que las dos pruebas nuevas detectan el defecto de la versión anterior. F3E1-01 sigue resuelto: los dos hallazgos quedan cerrados, sin hallazgos nuevos. La aprobación incluye F3-A1 y F3-A2, y se mantiene la del contrato de datos y sus enmiendas. Pasaron 62 pruebas locales; las 1.236 de la CI, con las de PostgreSQL, constan como evidencia aportada. El informe de cobertura no cambia | Revisor |
| 20 nov 2026 | **El grabador ya no elige entre dos velas REST distintas de un minuto, y una parada lenta deja de figurar como bloqueo** (rama `fase-2-grabador-rest`, desde `main` `660ffdb`; **aprobada** en `687cd43`). Es la ronda propia que el revisor aceptó en la revisión de F3-E1. (1) `apply_rest_candles` indexaba la respuesta por minuto y, si un minuto traía dos filas distintas, se quedaba en silencio con la última. Así, el minuto de las 09:03 del 18 nov de DOT-USDT quedó en el servidor como `vela_cruzada_discrepante`, con la vela parcial como REST, y sin vela del WebSocket habría quedado `cerrada_rest` con la parcial. Ahora `core.candles_by_minute` agrupa las velas distintas de cada minuto. Un minuto con dos o más no es una respuesta: se vuelve a pedir como tras un fallo, sin gastar intentos, durante 48 h, y después se cierra tal como esté. `_rest_candles` lo anota (`rest_candle_conflict`). (2) Al parar, el bloqueo se buscaba después de cerrar las conexiones y la base, así que una parada de más de 5 s se anotaba como «Stall» (visto al parar el grabador el 18 nov). Ahora se busca en el instante en que empieza la parada; un bloqueo anterior se sigue anotando (la prueba de GR-08). Pruebas: en el núcleo, la respuesta con las dos velas, siete respuestas así sin decidir el minuto y la decisión con la vela única, y el abandono a las 48 h; en el servicio, el aviso, ninguna vela escrita para ese minuto y la decisión `cerrada_cruzada` cuando KuCoin da una sola; y una parada que tarda 6 s sin ningún hueco «Stall». Con el código anterior, las cuatro nuevas fallan. **Revisado** en la fila siguiente | Claude |
| 20 nov 2026 | **Revisión de la segunda ronda del grabador (commit `2181aed`): APROBABLE CON CORRECCIONES.** El tratamiento de las velas REST contradictorias es conforme. **GR2-01** (MEDIO): el inicio de la parada se tomaba después de cancelar el ciclo. El revisor ejecutó el servicio y el cliente HTTPX reales con un transporte simulado y pidió la parada mientras se esperaba una respuesta REST. El cierre asíncrono de esa petición tardó 6,1 s, y `_finish(step)` lo esperaba antes de tomar `stopped_at`: quedaron un aviso `recorder_stalled` y cinco huecos «Stall» de Futures, aunque el bucle de eventos siguió funcionando. HTTPX puede esperar el cierre de la respuesta al propagarse una cancelación. Corrección: tomar el instante al detectar la parada o al empezar la cancelación del ciclo, antes de esperar `_finish(step)`, y llevarlo al registro final. Se conservan la espera de las escrituras pendientes y un instante posterior para cerrar los huecos. Prueba: cancelar una petición REST cuyo cierre tarde más de 5 s, con el bucle activo, sin «Stall»; un bloqueo real anterior a la parada se sigue anotando. Pasaron las 104 pruebas locales, y las cuatro nuevas detectan los defectos anteriores. Las garantías de GR-01 siguen conformes | Revisor |
| 20 nov 2026 | **GR2-01, corregido.** `_supervised_step` recibe el reloj. Cuando tiene que cancelar un ciclo que aún corre, por una parada pedida o por un stream terminado, anota ese instante como inicio de la parada (`_stop_began`), antes de esperar el ciclo cancelado. `run` busca el bloqueo en ese instante, si existe, o en el de su `finally`. Los huecos se siguen cerrando con un instante posterior, tras esperar las escrituras y cerrar los streams. Prueba: un KuCoin simulado no responde al funding, y su petición cancelada tarda 6 s en cerrarse con el bucle activo; no queda ningún «Stall» ni `recorder_stalled`. Con el código anterior falla. El bloqueo anterior a la parada se sigue anotando (la prueba de GR-08). **Confirmado** en la fila siguiente | Claude |
| 20 nov 2026 | **Segunda revisión de la ronda del grabador (commit `687cd43`): APROBADO.** GR2-01, RESUELTO: el inicio de la parada se registra antes de esperar la cancelación del ciclo. El revisor lo confirmó con el servicio y HTTPX reales y un transporte simulado. Un cierre asíncrono de 6,1 s no genera «Stall» falsos. Un bloqueo previo de 10 s sigue registrándose, sin añadirle el tiempo de cierre. Se mantienen la confirmación de escrituras pendientes y las garantías de GR-01, y la prueba nueva detecta el defecto anterior. El tratamiento de las velas REST contradictorias sigue conforme. Pasaron 105 pruebas locales; las 1.241 de la CI, con las de PostgreSQL, constan como evidencia aportada. Sin hallazgos nuevos. La aprobación cubre el código; quedan su instalación y su verificación en el servidor | Revisor |
| 20 nov 2026 | **Segunda ronda del grabador instalada en el servidor** (etiqueta `v0.2.8-grabador`, sobre `687cd43`), con el visto bueno del propietario («sí, intégralo e instálalo»), a las 07:56 UTC. `02_codigo.sh` terminó bien. El reinicio de `svc-ingest` lo dejó unos 6 s parado. `04_verificar.sh` dio «All checks passed.». El grabador nuevo conectó los dos mercados, sin errores ni reinicios. La parada del proceso anterior, que aún llevaba el código de `v0.2.7-decimales`, tardó 5,8 s en cerrar y anotó 160 huecos «Stall: did not run for 6 s», de 07:56:17 a 07:56:23. Es el defecto que corrige esta ronda: la etiqueta es falsa, pero el periodo es correcto, porque el grabador no grababa. La próxima parada ya usa el código nuevo | Claude |
| 20 nov 2026 | **Diseño de los indicadores de F3-E2** (§12.2, rama `fase-3-e2`, **pendiente de revisión**, antes de programarlos). Recoge lo común a todos: la vela utilizable, el valor y el reinicio, el calentamiento, la disponibilidad, el cálculo en `float`, la procedencia y la regla sin anticipación. Define los diez indicadores del DM §5, salvo la profundidad, con su fórmula, parámetros de partida, calentamiento, dimensiones, uso del volumen y referencia. Los siete de TA-Lib reproducen su cálculo, medido con TA-Lib 0.8.3 en un entorno aparte, con diferencias de $10^{-13}$: por ejemplo, el MACD de TA-Lib empieza sus dos EMA en la misma vela, y con una serie plana el RSI da 50. VWAP, volumen relativo y soportes y resistencias llevan definición y casos propios. Las decisiones abiertas para el revisor están en el punto 6. **Revisado** en la fila siguiente | Claude |
| 20 nov 2026 | **Revisión del diseño de F3-E2 (commit `a249a70`): APROBABLE CON CORRECCIONES.** Cinco hallazgos MEDIOS y uno BAJO. **F3E2-01:** la tabla omitía T en todos los indicadores y P en el volumen relativo, frente a §3.4; una serie contigua con T discrepante podría alimentar una SMA. Prueba: T no válida impide el valor y reinicia; P no válida impide el volumen relativo; V no válida, sola, permite la SMA. **F3E2-02:** la procedencia desde el último inicio es errónea en los de ventana: una SMA(2) con la primera vela solo REST y las dos siguientes contrastadas da 0 % en la tercera, no 33,33 %. Hay que distinguir ventanas, recursivos con su semilla y la acumulación del VWAP. **F3E2-03:** la disponibilidad debe ser el máximo de las de las entradas necesarias y del estado previo; retrasar una predecesora retrasa todo lo que depende de ella. **F3E2-04:** con $H = 10, 10{,}000000000000002, \dots$, $L = 1$ y $C = 5$, +DI vale unos $1{,}4 \cdot 10^{-15}$ y −DI 0: la fórmula propuesta daba DX 100, y TA-Lib da 0, por su umbral `|sumaDI| < 1e-14` y porque conserva el estado. **F3E2-05:** la tolerancia común $10^{-9}\max(1, |b|)$ admite devolver 0 frente a $10^{-10}$; hacen falta tolerancias por indicador y escala, con pruebas reescaladas. **F3E2-06** (BAJO): TA-Lib 0.8.3 sí tiene `VWAP` y `RVOL`. Admisibles el VWAP de sesión UTC y los soportes y resistencias, contando $m$ desde la vela del pivote y con una prueba de caducidad; conforme el RSI de 50 en una serie plana | Revisor |
| 20 nov 2026 | **F3E2-01 a F3E2-06, corregidos en §12.2.** (1) Dimensiones según §3.4: P + T en los de precio y P + V + T en VWAP y volumen relativo; una dimensión no necesaria no afecta. (2) y (3) Cada valor tiene sus **entradas**: la ventana, todo desde el último inicio (los recursivos) o el día (VWAP). Su procedencia son los minutos originales de esas entradas, sin repetir, y su disponibilidad, el máximo de las de ellas. (4) ±DI, DX y ADX exactamente como TA-Lib 0.8.3, leído en su código: ±DI con $S_{TR} > 0$ exacto; DX definido solo si además $|{+DI} + {-DI}| \ge 10^{-14}$, y si no repite el anterior; el ADX suma 0 por cada DX no definido de su semilla y después se queda quieto. Comprobados con TA-Lib el caso del revisor y una degeneración tardía en la vela 505. (5) Tolerancias por unidades: en precio, $10^{-9}|b| + 10^{-12}S$, con $S$ la escala de precios de la serie; de 0 a 100, $10^{-9}$ absoluta; en el volumen relativo, $10^{-9}|b| + 10^{-12}$. Pruebas reescaladas por $10^{-8}$ y $10^{4}$, y una implementación que devuelve 0 que tiene que fallar. (6) `VWAP` (por día UTC) y `RVOL` de TA-Lib como referencias, con tres diferencias declaradas: $\sum V = 0$, los huecos y la media nula. Soportes y resistencias: caducidad a las $m$ velas desde el pivote y su caso. **Confirmado** en la fila siguiente | Claude |
| 20 nov 2026 | **Segunda revisión del diseño de F3-E2 (commit `63822c4`): APROBADO.** F3E2-01 a F3E2-06, RESUELTOS. Las dimensiones están alineadas con §3.4. La procedencia distingue ventanas, recursivos y sesión, y la disponibilidad es la de la entrada necesaria más tardía. El umbral, la inicialización y la conservación de DX y ADX son conformes con TA-Lib 0.8.3. Las tolerancias van por unidades y escala, y el cero incorrecto del ejemplo falla también al reescalar. VWAP y RVOL quedan como referencias, con sus diferencias explícitas. El revisor confirmó con TA-Lib 0.8.3 los casos de DX y ADX, VWAP y RVOL. La caducidad de los pivotes queda definida por $t - j < m$; las $m + k$ velas se entienden como memoria máxima, y el calentamiento sigue siendo $2k+1$. Sin hallazgos nuevos: el diseño permite pasar a la implementación. La validación completa en x86-64 y aarch64 es de esa etapa. D16, D26 y D29 siguen pendientes | Revisor |
| 20 nov 2026 | **F3-E2: los indicadores, programados** (rama `fase-3-e2`, **pendiente de revisión**), según §12.2. El paquete `kbot.indicators` tiene cuatro módulos. `series` define el punto, el valor y `compute`, la única forma de ejecutar un indicador: velas utilizables, reinicios, entradas, procedencia, disponibilidad, usos del volumen (CD-05) y multiplicador (CD-12). `price` tiene SMA, EMA, RSI, MACD, Bollinger, ATR, DMI (±DI y DX) y ADX, con la aritmética del código de TA-Lib 0.8.3; no usa `fma`, que Python 3.12 no tiene. `volume` tiene el VWAP del día UTC y el volumen relativo, con sus tres diferencias declaradas. `levels` tiene los soportes y resistencias. Las velas superiores guardan cuántos de sus minutos son solo REST (`Bar.rest_only_minutes`). TA-Lib 0.8.3 entra solo como dependencia de desarrollo (BSD; trae NumPy 2.5.4), y el servidor no lo instala. Pruebas nuevas: 222. Cada indicador que tiene TA-Lib se compara en tres paseos aleatorios y tres escalas (×1, ×$10^{-8}$ y ×$10^{4}$), en un día real sin huecos (BTC-USDT del 10 nov 2026) y en otro con huecos, tramo a tramo (ZECUSDTM del 2 mar 2025, 888 de 1.440 minutos); además, el VWAP día a día y un cero que debe fallar. Se prueban también los DX y ADX degenerados, las series planas, el envenenamiento, la calidad, el recalentamiento, la procedencia, la disponibilidad, el volumen y los soportes y resistencias. La CI añade un trabajo en `ubuntu-24.04-arm` con las pruebas de los indicadores (F3-A3), y su prueba de política admite ese ejecutor estándar. **Revisado** en la fila siguiente | Claude |
| 20 nov 2026 | **Revisión de los indicadores de F3-E2 (commit `7b1c670`): APROBADO**, conforme al diseño de §12.2 y sin defectos bloqueantes. El revisor comprobó los cálculos, los calentamientos, los reinicios, la calidad, la causalidad, la disponibilidad y el porcentaje solo REST. Pasó 243 pruebas locales de indicadores y temporalidades, en Windows x86-64, y confirmó que las dos fixtures coinciden exactamente con sus Parquet, SHA-256 incluidos. Las 1.464 pruebas de x86-64 y las 222 de aarch64 de la CI constan como evidencia aportada. TA-Lib queda solo en desarrollo, y el servidor instala con `--no-dev`. **F3E2-07** (BAJO, no bloquea ni exige otra ronda): con volúmenes 10¹², 10⁻⁶, 10⁻⁶, 10⁻⁶ y 10⁻⁶, RVOL(3) da 3 en la quinta vela, como TA-Lib 0.8.3, aunque la fórmula sobre su ventana da 1: el acumulado pierde precisión. Recomienda documentar la limitación y conservar el ejemplo como prueba, mostrando los dos resultados y la media recalculada | Revisor |
| 20 nov 2026 | **F3E2-07, atendido.** La limitación queda declarada en §12.2, punto 4, y en `RelativeVolume`. `test_volume.py` conserva el ejemplo: el resultado propio y el de TA-Lib, los dos 3, frente a la media recalculada de la ventana, que da 1. El código no cambia | Claude |
| 20 nov 2026 | **F3-E2 integrada en `main`** (`13d5a3c`) por decisión del propietario («sí»), con F3-A3, F3-A4 y F3-A5. La CI de `main` pasa | Claude |
| 20 nov 2026 | **Diseño de F3-E3: régimen y fichas de tendencia, breakout y reversión a la media** (§12.3, rama `fase-3-e3`, **pendiente de revisión**; después, cada ficha necesita la aprobación del propietario). **Común:** contexto, señal y disparo en tres temporalidades; lo que se ve en cada instante, sin usar nunca una vela de contexto anterior a la última terminada; caducidad de la señal; stop obligatorio a $m_s$ ATR del cierre de disparo, que solo se acerca; ejecución con la regla causal de F3-E4; salidas por nivel, por cierre y por tiempo; en Spot, solo largos. **Régimen:** dirección por el ADX (25 y 20) y volatilidad por el ATR relativo y la anchura de Bollinger frente a sus 100 últimos valores, sin que los empates hagan un extremo. **Versión:** commit más el SHA-256 del JSON canónico de los parámetros. **Fichas:** tendencia (4 h / 1 h / 15 m, con arrastre del stop), breakout (4 h / 1 h / 15 m, tras compresión y con volumen relativo, objetivo de 2 R) y reversión a la media (1 h / 15 m / 5 m, objetivo en la media, salida a las 4 h), cada una con sus parámetros de partida, su hash, los datos que exige y un ejemplo resuelto con variantes. No decide D13, D15, D20, D30 ni ninguna de la F8 | Claude |
| 20 nov 2026 | **Revisión del diseño de F3-E3 (commit `db0c8db`): APROBABLE CON CORRECCIONES.** El régimen, sus fronteras y sus calentamientos son coherentes. Los tres hashes coinciden al recalcularlos, y la aritmética de los ejemplos es correcta. Dos hallazgos MEDIOS. **F3E3-01:** el recorrido de los disparos podía retroceder en el tiempo. Con una primera vela de ejecución disponible a las 15:50 y una segunda disponible a las 15:30:15, se decidía con la segunda a las 15:30:15 sin saber aún qué hacía la primera. **F3E3-02:** se admitían stops y objetivos no positivos, que ningún precio puede tocar: un largo con el stop en −1, o un breakout corto con el objetivo en −3. La revisión es documental y matemática: F3-A6 y F3-A7 se acreditarán con las pruebas del código. Las aprobaciones de las fichas y D13, D15, D20 y D30 siguen siendo del propietario | Revisor |
| 20 nov 2026 | **F3E3-01 y F3E3-02, corregidos en §12.3.1**, con sus pruebas en §12.3.7. (1) Los instantes del disparo nunca retroceden: $D_0 = D_s$, y $D_i = \max(D_s, D_{i-1}, \text{resolución de } e_i)$. Una vela se resuelve cuando está disponible o cuando se sabe que no será utilizable, y el contexto se mira en ese instante. En el caso del revisor, la decisión es a las 15:50:00 y la entrada a las 15:51, nunca a las 15:31. (2) No se entra con un stop o un objetivo que no sea positivo, también en el objetivo del breakout (§12.3.5), y los niveles nunca se recortan. No cambian los parámetros, sus hashes ni los ejemplos de las fichas | Claude |
| 20 nov 2026 | **Segunda revisión del diseño de F3-E3 (commit `1974a25`): APROBADO.** F3E3-01, RESUELTO: los instantes incorporan $D_{i-1}$ y la resolución de cada vela, también cuando no es utilizable, y el contexto se mira en el instante efectivo; el caso del retraso ya no permite decidir antes de las 15:50, y sus variantes quedan contempladas. F3E3-02, RESUELTO: se rechazan los stops y los objetivos cero o negativos, también el objetivo del breakout calculado al entrar, y los niveles no se recortan; las pruebas incluyen las fronteras en cero y conservan los ejemplos válidos. El diff y el ZIP coinciden y solo cambian documentación. Sin residuales ni hallazgos nuevos. **Es una aprobación técnica del diseño:** cada ficha necesita la aprobación del propietario antes de programarse, y F3-A6 y F3-A7 se acreditarán con las pruebas de la implementación | Revisor |
| 20 nov 2026 | **Decisión del propietario: aprueba las tres fichas de F3-E3** (tendencia, breakout y reversión a la media; §12.3.4 a §12.3.6) con sus parámetros de partida y sus hashes, tras la aprobación técnica del revisor (respuesta: «apruebo las tres»). Se le explicaron antes, en palabras sencillas, con los ejemplos de cada ficha. Con esto se programan el régimen, la versión y las reglas de las tres fichas | Propietario |
| 20 nov 2026 | **F3-E3: el régimen, la versión y las tres fichas, programados** (rama `fase-3-e3`, **pendiente de revisión**), según §12.3. El paquete `kbot.strategies` tiene ocho módulos. `regime` da la dirección y la volatilidad de cada vela, con su disponibilidad y el inicio de sus entradas. `version` forma `strategy_version` con `kbot.core.jcs`, y no da versión si un archivo seguido por git tiene cambios sin confirmar. `rules` reúne lo común como funciones puras: las direcciones por mercado, la vela de contexto, el recorrido del disparo (F3E3-01), el stop inicial, los controles de la entrada (F3E3-02), las salidas por nivel y el arrastre. `frames` lee cada temporalidad de un instrumento solo con lo disponible en cada instante, resuelve las velas de ejecución no utilizables y cuenta la procedencia en minutos originales. `ficha` busca las señales y recorre su disparo, igual para las tres. `trend`, `breakout` y `mean_reversion` son las fichas aprobadas: sus reglas sobre valores sueltos, su lectura de los marcos y sus invalidaciones, y en la tendencia, también el arrastre. **Pruebas nuevas: 68.** Reproducen los tres ejemplos con sus variantes, los tres hashes y los casos de F3E3-01 y F3E3-02 (F3-A6), y las fronteras, los empates, la precedencia, el calentamiento de 119 velas y el orden de carga del régimen (F3-A7). Sobre series de minutos con periodos cortos, envenenar lo posterior a $D_s$ no cambia ninguna señal, y lo posterior a una decisión no cambia ni la decisión ni su stop (F3-A4 en las señales). **Dos precisiones de la implementación**, en §12.3.1 y §12.3.3: en el histórico, un minuto ausente se sabe al terminar más el margen; y los archivos que git no sigue no cuentan como cambios sin confirmar. Guardar las señales, el combinador y el motor de backtest siguen siendo de F3-E4 | Claude |
| 20 nov 2026 | **Revisión del código de F3-E3 (commit `49b9987`): APROBABLE CON CORRECCIONES.** Pasan las 68 pruebas locales. Las reglas de las tres fichas, sus parámetros y el régimen coinciden con el diseño aprobado; el reloj de `walk` mantiene los instantes no decrecientes, y `may_enter` rechaza los niveles no positivos. Las dos precisiones documentales son admisibles en su alcance. **F3E3-03** (MEDIO): la procedencia de la decisión omitía las velas de ejecución examinadas antes del disparo. Con la tendencia de `test_scan`, la señal de las 07:00 mira [07:15, 07:20), que no dispara, y decide con [07:20, 07:25). Dejando solo REST los cinco minutos de la primera, la decisión daba 440 minutos y 0 solo REST, en vez de 445 y 5. **F3E3-04** (BAJO): una vela no utilizable se resolvía en el primer minuto que falla por su apertura, no en el primer instante conocido. Con 00:01 discrepante y disponible a la 01:00 y 00:05 ausente, se resolvía a la 01:00, en vez de a las 00:06:15 | Revisor |
| 20 nov 2026 | **F3E3-03 y F3E3-04, corregidos.** (1) `walk` devuelve las velas cuyo cierre miró, cada una con su instante. La decisión une a la procedencia de la señal esas velas y el contexto de cada uno de sus instantes, cada minuto una sola vez. En el caso del revisor da 445 minutos y 5 solo REST, y ninguna vela posterior al disparo entra. (2) `known_unusable` toma el menor de los instantes conocidos de los minutos que hacen inutilizable la vela: el caso del revisor se resuelve a las 00:06:15. Seis pruebas nuevas, 74 en `tests/unit/strategies/` | Claude |
| 20 nov 2026 | **Segunda revisión del código de F3-E3 (commit `67e0f9f`): APROBADO.** F3E3-03, RESUELTO: la procedencia incorpora las velas examinadas y el contexto de cada instante, fusionando los intervalos sin duplicados; el caso exacto da 445 minutos y 5 solo REST, y excluye las velas posteriores al disparo. F3E3-04, RESUELTO: la resolución toma el primer instante conocido; el caso exacto da las 00:06:15, y sus variantes quedan cubiertas. Pasan las 74 pruebas locales de estrategias, y las dos pruebas de regresión fallan con `49b9987` y pasan con la corrección. El diff y el ZIP coinciden. Sin residuales ni hallazgos nuevos. Queda aprobada la implementación del régimen, la versión y las tres fichas en el alcance de F3-E3; el combinador, la persistencia y el motor de backtest son de F3-E4 | Revisor |
| 20 nov 2026 | **F3-E3 integrada en `main`** (`cd16a6b`) por decisión del propietario («sí»), con F3-A6, F3-A7 y F3-A4 en las señales | Claude |
| 20 nov 2026 | **Diseño de F3-E4: señales, combinador y motor de backtest** (§12.4, rama `fase-3-e4`, **pendiente de revisión**, antes del código). **La señal:** única por su clave, con sus cuatro instantes (fin, disponibilidad, decisión y ejecución) y la referencia idealizada aparte. **Posiciones:** ocupan su instrumento desde la decisión hasta la disponibilidad del minuto en que se resuelven. **Combinador:** una posición neta por instrumento; con el instrumento ocupado no se ejecuta ninguna decisión; con decisiones opuestas en el mismo instante, ninguna; del mismo sentido, una, por un orden fijo de familias; y un modo por familia para el informe exploratorio. **Ejecución de la F3**, simple y declarada: entrada a mercado en la apertura; stop a mercado, a la apertura si hay hueco; objetivo limitado a su nivel; salidas por cierre y por tiempo a mercado; sin precio observado no hay ejecución; comisión y deslizamiento fijos, con sus valores en F3-E5; sin funding; resultados en R, brutos y netos; ventanas sin datos de la siguiente. **Motor:** por instrumento, con un orden total de sucesos y la misma lógica que el paper trading. Cinco ejemplos y las pruebas de F3-A8, F3-A9 y F3-A4 del motor entero. No decide D12, D13, D15, D16, D20, D22, D28 ni D30 | Claude |
| 20 nov 2026 | **Revisión del diseño de F3-E4 (commit `8d6932b`): APROBABLE CON CORRECCIONES.** El diff y el ZIP coinciden y solo cambian documentación. Los ejemplos A y B son correctos: +0,15294 R bruto, −0,11029 R neto y 99,1504 tras el deslizamiento del hueco. Tres hallazgos MEDIOS. **F3E4-01:** faltaba ordenar las liberaciones y las decisiones de un mismo instante. Con un breakout liberado a las 12:00:15 y una decisión de la tendencia en ese instante, el orden por familia podía rechazarla como `ocupado`; y las decisiones simultáneas debían reunirse antes de ver si son opuestas. **F3E4-02:** el fin de ventana admitía una decisión a las 23:59:15 que entraba a las 00:00, fuera de la ventana. Además, la liquidación al cierre del último minuto presente se atribuía a ese minuto, aunque elegirlo exige conocer la ventana entera. **F3E4-03:** el porcentaje solo REST de la operación era el de la decisión, y omitía los datos que deciden la salida. La revisión es documental y matemática: F3-A8, F3-A9 y el envenenamiento del motor se acreditarán con el código | Revisor |
| 20 nov 2026 | **F3E4-01 a F3E4-03, corregidos en §12.4.** (1) La ocupación es [decisión, liberación). En cada instante se aplican primero las resoluciones y liberaciones, y después todas las decisiones juntas; el orden de familias solo desempata las del mismo sentido, y el registro no depende del orden de llegada (ejemplo F). (2) El motor solo recibe los minutos anteriores a $fin$. Una decisión cuyo minuto de ejecución empieza en $fin$ o después no se ejecuta (`fin_de_ventana`), y no hay salidas desde $fin$. Una posición abierta cuando se resuelve la ventana se liquida al cierre de su último minuto presente, rotulada, y su instante de conocimiento es la resolución de la ventana (ejemplo E). (3) Cada operación lleva dos porcentajes solo REST, con su procedencia por separado: el de la decisión, y el del resultado, que añade todo lo que usó hasta la salida, cada minuto una vez. Las pruebas de §12.4.7 cubren los tres | Claude |
| 20 nov 2026 | **Segunda revisión del diseño de F3-E4 (commit `5b5491a`): APROBABLE CON CORRECCIONES.** F3E4-01, RESUELTO: la ocupación es [decisión, liberación), las liberaciones van antes que las decisiones y estas se combinan juntas. F3E4-03, RESUELTO: se separan la procedencia y el porcentaje de la decisión y del resultado. **F3E4-02, PARCIAL, con un residual MEDIO:** la resolución de la ventana suponía las disponibilidades en orden. Con $fin$ = 00:00, el minuto de las 23:59 ausente se resuelve a las 00:00:15, pero el último presente, el de las 23:58, puede no estar disponible hasta las 00:10. La regla resolvía la ventana a las 00:00:15 y liquidaba con un cierre aún desconocido, contra la regla general del instante de conocimiento. El rechazo de las entradas fuera de la ventana y la separación de la liquidación están corregidos. Sin hallazgos nuevos | Revisor |
| 20 nov 2026 | **Residual de F3E4-02, corregido en §12.4.4.** La ventana se resuelve, para una posición abierta, cuando están resueltos todos sus minutos desde el de la entrada (disponibles o sabidos ausentes): el máximo de sus resoluciones. Con disponibilidades en orden es $fin$ más el margen. La liquidación se conoce en esa resolución. El ejemplo E añade el caso del revisor (liquidación conocida a las 00:10), y §12.4.7, su prueba con el corte a las 00:05 | Claude |
| 20 nov 2026 | **Tercera revisión del diseño de F3-E4 (commit `0c5d098`): APROBADO.** F3E4-02, RESUELTO: la resolución toma el máximo de las resoluciones de todos los minutos desde la entrada, y la liquidación se conoce en ese instante, conforme a §12.4.4 y §12.4.5. El ejemplo E sitúa el caso retrasado a las 00:10, y §12.4.7 exige que el corte de las 00:05 y el envenenamiento del dato tardío conserven lo conocido antes. F3E4-01 y F3E4-03 siguen resueltos. Sin residuales ni hallazgos nuevos. La aprobación es del diseño: F3-A8, F3-A9 y el envenenamiento del motor se validarán con la implementación | Revisor |
| 20 nov 2026 | **F3-E4: señales, combinador y motor, programados** (rama `fase-3-e4`, **pendiente de revisión**), según §12.4. En `kbot.strategies`: `combiner` (`combine` y `allocate`: liberaciones antes que decisiones, y las decisiones de un instante juntas), y una interfaz común de salidas en las fichas (`invalidation`, `trailing`, `entry_target` y `time_limit`), con la procedencia de cada comprobación. En `kbot.backtest`: `execution` (costes, precios y R), `position` (una posición minuto a minuto, con sus instantes de conocimiento y sus dos procedencias) y `engine` (una ejecución por instrumento y ventana, con solo los minutos anteriores a su fin, la unicidad de las señales y el registro). **Pruebas nuevas: 36.** Cubren los ejemplos A, B, C, E y F, el modelo de ejecución, las entradas fallidas (CD-03), la liquidación y su resolución con un minuto tardío, los dos porcentajes, los cuatro instantes de cada operación (F3-A9), el retraso de una vela, la repetición y el orden de las fichas, la permutación de las decisiones (F3-A8) y el envenenamiento del motor entero en doce cortes (F3-A4). **Dos precisiones de la implementación** en §12.4.5: una salida no se conoce antes de que se resuelvan todos los minutos desde la entrada, y un cambio del stop, antes de que se resuelvan los minutos hasta el que contiene su instante. Con las disponibilidades en orden no cambian nada | Claude |
| 20 nov 2026 | **Revisión del código de F3-E4 (commit `535652a`): APROBABLE CON CORRECCIONES.** Pasan las 110 pruebas locales de backtest y de estrategias. El combinador, las reglas de ejecución y la interfaz de salidas coinciden con el diseño, y las dos precisiones sobre el conocimiento retrasado son coherentes. Dos hallazgos MEDIOS en el registro. **F3E4-04:** se perdía el instante de conocimiento de una entrada fallida. La misma decisión y el mismo `no_entra`, con el minuto de entrada disponible a las 10:22:15 o a las 12:00, daban un `Run` idéntico, aunque a las 11:00 el rechazo solo se conoce en el primero. **F3E4-05:** el registro no conservaba los dos porcentajes solo REST. La misma serie, con todos sus minutos solo REST o todos contrastados, daba `Run` idénticos: se guardaban los intervalos, pero no los recuentos | Revisor |
| 20 nov 2026 | **F3E4-04 y F3E4-05, corregidos.** (1) `DecisionRecord` separa lo que se sabe en $D$ (la caducidad, el rechazo o «elegida») del resultado de la entrada de una elegida (`ejecutada`, `caduca_sin_minuto` o `no_entra`), con su instante de conocimiento. Los dos casos del revisor dan registros distintos, y a las 11:00 solo el primero conoce el rechazo. (2) Cada operación guarda los recuentos, total y solo REST, de la procedencia de la decisión y de la del resultado. Desde el `Run` se leen el 100 %, el 0 % y el caso mixto, (15, 0) y (41, 20). Dos pruebas nuevas, 38 en `tests/unit/backtest/` | Claude |
| 21 nov 2026 | **Segunda revisión del código de F3-E4 (commit `676711b`): APROBADO.** F3E4-04, RESUELTO: `DecisionRecord` separa la elección en $D$ del resultado de la entrada y su conocimiento; los casos de las 10:22:15 y de las 12:00 dan registros distintos, y a las 11:00 solo el primero conoce el rechazo. F3E4-05, RESUELTO: cada operación conserva los dos recuentos solo REST; el revisor confirmó desde `Run` el 100 %, el 0 % y el caso mixto (15, 0) y (41, 20). Pasan las 112 pruebas locales de backtest y de estrategias. El diff y el ZIP coinciden. Sin residuales ni hallazgos nuevos. Queda aprobada la implementación del combinador, el modelo de ejecución, las posiciones y el motor de backtest en el alcance de F3-E4 | Revisor |
| 21 nov 2026 | **F3-E4 integrada en `main`** (`350f8e0`) por decisión del propietario («integra»), con F3-A8, F3-A9 y F3-A4 del motor entero | Claude |
| 21 nov 2026 | **Diseño de F3-E5a: la evaluación exploratoria** (§12.5, rama `fase-3-e5`, **pendiente de revisión**, antes del código). **Particiones** de calendario UTC, iguales para todos los instrumentos, en la medianoche más cercana al 60 % y al 80 % del histórico: desarrollo [19 nov 2024, 1 feb 2026), validación [1 feb 2026, 27 jun 2026) y prueba [27 jun 2026, 19 nov 2026 22:10). La validación, una vez por familia y versión, anotada; la prueba, sellada en el código. ***Walk-forward*** en cuatro tramos de unos 110 días del desarrollo, con 30 días de calentamiento, que mide la estabilidad porque la F3 no ajusta parámetros. **Costes:** la comisión de tomador de cada instrumento leída de KuCoin, 0,05 % de deslizamiento por ejecución y una prueba con costes dobles; sin funding. **Informe** por familia y versión, con todo lo que exige `08` §3 y sus cifras por instrumento, tramo y régimen. **Herramienta** `kbot.backtest.explore`. La migración `0004` y `svc-strategy` quedan para F3-E5b. No decide D12, D15, D16, D22, D28 ni D30 | Claude |
| 21 nov 2026 | **Revisión del diseño de F3-E5a (commit `00390b7`): APROBABLE CON CORRECCIONES.** Los cortes de las particiones y los cuatro tramos son correctos, la separación entre E5a y E5b es coherente, y el modelo de Spot por clase y coeficiente tiene respaldo en la página de tarifas de KuCoin y en su SDK oficial. Dos hallazgos MEDIOS y dos BAJOS. **F3E5-01:** anotar la validación al terminar permitía repetirla tras un fallo, o a dos procesos a la vez. **F3E5-02:** la caída máxima total dependía del orden de las salidas simultáneas: tras −1 R, salir con +2 R y −1 R a la vez daba 1 R o 2 R según el orden. **F3E5-03:** el ejemplo del calentamiento contaba las 50 velas de la semilla como actualizaciones; son 130, y la influencia es de un 0,55 %. **F3E5-04:** lo grabado desde el 17 nov se solapa con la prueba, que acaba el 19 nov a las 22:10 UTC. La revisión es documental y matemática | Revisor |
| 21 nov 2026 | **F3E5-01 a F3E5-04, corregidos en §12.5.** (1) La consulta de la validación se reserva antes de leerla, de forma atómica y persistente: un archivo por familia y versión creado solo si no existe, con los estados `iniciada`, `completada` y `fallida`; un fallo no la borra y cualquier reserva impide otra. (2) La curva de R acumulado empieza en 0 y avanza por el minuto de salida, con las salidas de un mismo minuto sumadas en un paso: la caída máxima no depende del orden. (3) $(49/51)^{130} \approx 0{,}0055$. (4) La muestra temporal adicional empieza el 19 nov 2026 a las 22:10 UTC; lo grabado antes queda sellado con la prueba. Sus pruebas, en §12.5.6 | Claude |
| 21 nov 2026 | **El 17 nov, el primer día con datos reales, archivado en el servidor en el formato 2.** El temporizador diario corrió a las 00:23 UTC: selló el 17 nov a las 00:23:01 y lo archivó a las 00:24:36 (generación `83b9274d-ce92-43a0-bed4-3b109165c45a`, esquema `0003`, formato 2). La comprobación de las 00:25:19 dio «archived days checked: all match». El día tiene 62.496 velas, 23.562 velas del WebSocket, 206.730 métricas de profundidad y 35.010 instantáneas del libro: unos 40 MB en total. El servicio terminó bien, con un pico de 495 MB de memoria y 2 min 15 s de CPU, y el grabador siguió sin reinicios. El 18 nov no se selló, porque aún no habían pasado 72 h desde su final | Claude |
| 23 nov 2026 | **Segunda revisión del diseño de F3-E5a (commit `7eab2b4`): APROBADO.** F3E5-01, RESUELTO: la reserva es atómica, persistente y anterior a la lectura; cualquier estado impide repetir la consulta, y hay pruebas de fallo y de concurrencia. F3E5-02, RESUELTO: la curva parte de cero y agrupa las salidas por minuto, y el ejemplo da 1 R de caída máxima en cualquier orden. F3E5-03, RESUELTO: las 130 actualizaciones y $(49/51)^{130} \approx 0{,}00551274$ son correctas. F3E5-04, RESUELTO: la muestra adicional empieza el 19 nov 2026 a las 22:10 UTC y excluye el solapamiento con las particiones. El diff, el ZIP y el paquete coinciden. Sin residuales ni hallazgos nuevos. La aprobación es del diseño: las pruebas de F3-A11 se acreditarán con la implementación, y F3-E5b tiene su propia revisión | Revisor |
| 23 nov 2026 | **F3-E5a programada** (rama `fase-3-e5`, **pendiente de la revisión del código**). Los módulos están en `src/kbot/backtest/`: `partitions` (las particiones, los cuatro tramos con su calentamiento y la muestra adicional); `reader` (los minutos del histórico, sin abrir nunca un día de la prueba); `reservation` (la reserva atómica de la validación, con sus tres estados y su copia); `fees` (la comisión de cada instrumento leída de KuCoin, el deslizamiento y la prueba de esfuerzo); `report` (las cifras, la caída máxima por minuto de salida y el informe en Markdown y JSON); y `explore`, la herramienta. Hay 37 pruebas nuevas en `tests/unit/backtest/` para §12.5.6, entre ellas cuatro procesos que piden la misma reserva a la vez, el fallo provocado tras leer la validación, la caída máxima en cualquier orden y la EMA(50) tras 130 actualizaciones. En local pasan 1.306. La tarifa de Spot de cada clase se leyó de la tabla VIP de KuCoin y ya es [H] (§12.5.3), y §12.5.5 anota siete precisiones de la implementación. Medido con un instrumento y un tramo reales del desarrollo: unos 80 s con las tres familias, y menos de 0,1 MB de registro por ejecución | Claude |
| 23 nov 2026 | **El 18 y el 19 nov, archivados en el servidor en el formato 2:** el 22 nov a las 00:26 UTC (generación `25ec0499-17fd-4bd6-8212-497b39c0ce9b`) y el 23 nov a las 00:23 UTC (`bf66eb50-3de0-42f3-a261-c0cc168fa009`). Las dos comprobaciones dieron «archived days checked: all match». Son los primeros días grabados enteros: unos 94 MB cada uno, con unas 115.000 velas y 505.000 métricas de profundidad. El grabador sigue sin reinicios desde el 20 nov, y la copia de las 10:45 UTC terminó bien. Ese día el acceso SSH desde el PC falló por dos motivos del PC, que el propietario resolvió: su IP de casa había cambiado (la regla de la lista de seguridad de Oracle) y el servicio `ssh-agent` de Windows estaba parado | Claude |
| 23 nov 2026 | **Primeros informes del desarrollo** (§12.5.8), hechos con `python -m kbot.backtest.explore --familia todas --particion desarrollo` sobre el árbol limpio de `6f08bb8`. Tardó 1 h 14 min con los 40 instrumentos, los cuatro tramos y las tres familias. Las comisiones leídas de KuCoin son: en Futures, 0,06 % todas; en Spot, la clase A con coeficiente 1,00 (0,1 %), salvo EVAA-USDT (clase C, 0,3 %) y PIEVERSE-USDT (clase B, 0,2 %). Los hashes coinciden con los aprobados. Resultados, en R por operación: **tendencia**, 318 operaciones, bruto −0,084 y neto −0,215, sin ningún tramo con R neto positivo; **breakout**, 259 operaciones, bruto +0,075 y neto −0,079, y solo gana en el tramo 4; **reversión a la media**, 832 operaciones, bruto +0,057 y neto −0,240, sin ningún tramo positivo. **Observación:** muchos contratos de Futures no dan ninguna señal, por la regla de las velas completas con los minutos sin operaciones que KuCoin no publica. Queda como pregunta abierta en §12.5.8, sin decidir. No se ha consultado la validación | Claude |
| 23 nov 2026 | **Revisión del código de F3-E5a (commit `a6c64c0`): APROBABLE CON CORRECCIONES.** Pasan las 75 pruebas locales de backtest. La recomposición de los 480 registros guardados del desarrollo confirma las cifras de los tres informes: operaciones, R bruto y neto, costes dobles, regímenes y caída máxima. El revisor no volvió a ejecutar las estrategias sobre el histórico ni consultó velas de la validación o de la prueba. Un hallazgo MEDIO y dos BAJOS. **F3E5-05** (MEDIO): la cobertura ignoraba la elegibilidad. SOXLUSDTM salía con 0 de 632.160 minutos, un 0 %, pero abrió el 15 may 2026, después del desarrollo: su cobertura del contrato es «no aplicable». **F3E5-06** (BAJO): la copia de auditoría perdía consultas concurrentes. Con dos familias a la vez, las dos reservas quedaban `completada`, pero la copia guardaba una sola entrada. **F3E5-07** (BAJO): los parámetros del informe podían perder su texto canónico. `Decimal("2.00")` da el hash aprobado, pero el informe guardaba `"2.00"`, y el hash rehecho desde él era otro; los tres informes actuales no lo tienen. **Sobre Futures,** el revisor mantendría el contrato como referencia. KuCoin documenta que no publica los intervalos sin operaciones, pero también avisa de que las velas pueden estar incompletas: una ausencia no demuestra por sí sola que no hubiera operaciones. Antes de atribuir el silencio a los huecos, separaría tres causas: contratos sin periodo elegible, contexto sin calentamiento suficiente y condiciones de la estrategia no cumplidas. Rellenar con velas planas introduce datos imputados, y tolerar minutos ausentes deja desconocidos los máximos, los mínimos y los volúmenes. Las dos opciones requieren una enmienda versionada, una comparación solo en el desarrollo y reglas explícitas de calidad y procedencia, y una vela sintética nunca debe convertirse en un precio observado para ejecutar. El dictamen no aprueba ninguna modificación del contrato | Revisor |
| 23 nov 2026 | **F3E5-05 a F3E5-07, corregidos** (§12.5.1, §12.5.4 a §12.5.6). (5) El lector da también la presencia de F3-E1 de cada minuto pedido, sacada de `solicitado.json` y de las peticiones de cada día. Con ella y con la evidencia de elegibilidad de F3-E1, el informe calcula la cobertura del contrato y da aparte E, los presentes en E, los ausentes, lo no descargado, lo que está en conflicto, las contradicciones, los no elegibles y lo que nunca se pidió. La de los presentes sobre el calendario queda rotulada como otra medida. (6) La copia se lee, se mezcla y se publica bajo un cierre exclusivo del sistema operativo (`copia.lock`, en la carpeta de las reservas, fuera del repositorio), y se reemplaza con un archivo temporal. (7) Los decimales de los parámetros van en el informe con `to_canonical`. Hay cuatro pruebas nuevas, las de aceptación del revisor, y en local pasan 1.310 | Claude |
| 23 nov 2026 | **Informes del desarrollo rehechos con `3490a1c` (en `5829cf1`), y §12.5.8 ampliado.** El primer intento, con `2fd382e`, se detuvo en el primer día de Futures anterior a lo solicitado (antes del 1 ene 2025). El lector construía una ventana invertida para un día que no toca ninguna ventana pedida. `3490a1c` lo salta, con dos pruebas que fallaban con el lector anterior; su CI, en verde, pasa 1.619 pruebas y 223 en aarch64. La nueva ejecución tardó 1 h 08 min. **Todas las cifras coinciden con las de `6f08bb8`:** solo cambian la cobertura, el commit y la hora de lectura de las comisiones, que son las mismas. La cobertura del contrato da «no aplicable» a SOXLUSDTM y 99,37 % a XBTUSDTM, y deja aparte los 61.920 minutos de Futures anteriores al 1 ene 2025, que nunca se pidieron. EVAA-USDT y PIEVERSE-USDT de Spot dan 0 %: no hay evidencia de cuándo empezaron a cotizar, así que sus minutos son de elegibilidad desconocida y cuentan en E, como en el informe de cobertura de F3-E1. Los informes de `6f08bb8` se retiran del repositorio, y siguen en su historia. §12.5.8 añade la opinión del revisor y por qué calla cada contrato: sin periodo elegible (SOXL), el contexto nunca se calienta (8 contratos en la tendencia y en el breakout, 1 en la reversión) o valores a ratos sin señal | Claude |
| 23 nov 2026 | **Segunda revisión del código de F3-E5a (commit `2ba8e2f`): APROBADO**, con el código y los tres informes del desarrollo. **F3E5-05, RESUELTO:** la cobertura usa la presencia y la elegibilidad de F3-E1 y deja lo no solicitado fuera del denominador. SOXLUSDTM figura como «no aplicable» y XBTUSDTM con 566.630 de 570.240, un 99,37 %, y la ventana invertida de los días anteriores a lo solicitado queda corregida. **F3E5-06, RESUELTO:** el cierre del sistema operativo protege a la vez la lectura, la mezcla y la publicación, y la prueba concurrente conserva las 100 anotaciones, con reemplazo atómico. **F3E5-07, RESUELTO:** los parámetros usan el texto canónico; `2` y `2.00` dan el mismo contenido y permiten rehacer el hash aprobado. **Verificación del revisor:** pasan las 80 pruebas de backtest, las cifras de operaciones y resultados de los 480 registros del desarrollo coinciden exactamente con las anteriores, y los informes en Markdown concuerdan con sus JSON. La separación de causas de Futures es coherente con mantener el contrato vigente; los recuentos de calentamiento son mediciones aportadas, que no recalculó. Sin hallazgos nuevos. El revisor no repitió la ejecución completa sobre el histórico ni consultó datos de la validación o de la prueba. La aprobación es la de la evaluación exploratoria: siguen pendientes F3-E5b y la validación definitiva | Revisor |
| 23 nov 2026 | **Diseño de F3-E5b: las señales en la base de datos y `svc-strategy`** (§12.6, rama `fase-3-e5b`, **pendiente de revisión**, antes del código). **Migración `0004`:** `trading.signals`, una fila por señal, única por su clave de §1.3, con el commit y el hash de su versión, sus instantes, su régimen, sus niveles, su procedencia y sus recuentos solo REST, y una clave foránea a la versión aprobada; y `config.strategy_versions`, que aprueba los parámetros de una ficha, la escriben solo las migraciones y nace vacía, porque qué familias siguen lo decide el propietario (§12.5.7). **Permisos de `kbot_strategy` (F3-A10):** lee `market.candles`, `market.universe_snapshots` y `config.strategy_versions`, e inserta señales, y nada más. **`svc-strategy`:** calcula cada 15 minutos las señales de cada día con los minutos del grabador desde 30 días antes, con un corte de 60 s, la conversión de cada fila a un minuto del contrato y la disponibilidad en `recorded_at`. Con los mismos minutos da exactamente las señales del motor. Sin red, idempotente y sin desplegar | Claude |
| 23 nov 2026 | **Revisión del diseño de F3-E5b (commit `6e50ef2`): APROBABLE CON CORRECCIONES**, con cuatro hallazgos MEDIOS. **F3E5B-01:** el universo podía aplicarse hacia atrás: con la última evaluación del día, una exclusión de las 00:30 quitaba al recalcular una señal de las 00:15:15 que el ciclo de las 00:16 podía escribir. **F3E5B-02:** la igualdad con el recálculo del día entero no está garantizada. El revisor lo reprodujo con la tendencia de periodos cortos: con un minuto del 5 de enero de las 01:10 disponible el día 6 a las 12:00, el corte de las 11:00:30 del día 6 da 9 señales, y el motor con todos los datos, 0 con $D_s$ ≤ ese corte; pasa también con `recorded_at` posterior al corte, no solo con la confirmación tardía. **F3E5B-03:** si fallan los ciclos de la primera hora del día, la señal de ayer con $D_s$ = 23:45:15 queda sin escribir, aunque se prometía reintentar. **F3E5B-04:** falta el multiplicador para cumplir CD-12. `market.candles` no lo tiene y el servicio no podía leer `market.instruments`: con 20 velas de volumen 100 y multiplicador 0,01 seguidas de volumen 1.000 y multiplicador 0,001, el revisor reprodujo un volumen relativo de 10. La tabla de aprobaciones vacía respeta las decisiones pendientes del propietario, y la reinserción sin `SELECT` es viable con `ON CONFLICT DO NOTHING` sin objetivo ni `RETURNING` | Revisor |
| 23 nov 2026 | **F3E5B-01 a F3E5B-04, corregidos en §12.6.3 a §12.6.5.** (1) Un instrumento cuenta en $D_s$ según la última evaluación visible con `evaluated_at` ≤ $D_s$. (2) La equivalencia con el motor se define en cada corte, con los mismos minutos visibles, y se separa de la divergencia entre lo escrito y el recálculo final, que es un límite declarado que mide la F4. (3) Cada ciclo mira una ventana móvil de 48 h y recalcula, sin reiniciar, el día sin un cálculo bueno posterior a su fin o cuyo número de filas visibles cambió. (4) El servicio lee `market.instruments`: cada minuto lleva el multiplicador de la última observación anterior, y es desconocido, con la V no válida, en las 24 h antes de un cambio; así VWAP y volumen relativo se reinician en el cambio (CD-12). Cada corrección tiene su prueba en §12.6.5 | Claude |
| 23 nov 2026 | **Segunda revisión del diseño de F3-E5b (commit `65aadae`): APROBABLE CON CORRECCIONES.** F3E5B-01, RESUELTO: el universo se determina en cada $D_s$. F3E5B-02, RESUELTO: la equivalencia usa los mismos datos visibles en cada corte y se separa del recálculo final. F3E5B-03, RESUELTO: la ventana móvil y los días pendientes cubren la recuperación sin reiniciar. **F3E5B-04, PARCIAL, con un residual MEDIO:** revisar a diario no garantiza una observación válida cada 24 h. El grabador conserva el universo, reintenta cuando faltan los listados y solo guarda los instrumentos que cambian. El revisor lo reprodujo: con una comprobación correcta el 1 nov a las 23:59, el cambio el día 2 a las 00:00 y la detección el día 3 a las 12:00 tras una evaluación fallida, la regla solo invalidaba desde el día 2 a las 12:00 y dejaba doce horas con el volumen utilizable y el multiplicador antiguo, así que aún era posible el volumen relativo artificial de 10. Sin hallazgos nuevos | Revisor |
| 23 nov 2026 | **Residual de F3E5B-04, corregido en §12.6.4 y §12.6.5.** Sin ninguna cota fija: con las observaciones visibles en el corte, el multiplicador es desconocido en todo el intervalo entre dos observaciones seguidas que lo cambian, desde la anterior hasta la que detecta el cambio, y antes de la primera. Entre dos con el mismo, el de las dos, y desde la última, el suyo. Hasta la detección, los minutos llevan el multiplicador antiguo: es un límite declarado del tiempo real, y las señales escritas así se quedan (F3E5B-02). Acortarlo exigiría que el grabador guardara las comprobaciones sin cambios, y se propondrá aparte si el propietario lo pide. La prueba sigue el caso del revisor, con la detección más de 24 h después | Claude |
| 23 nov 2026 | **Tercera revisión del diseño de F3-E5b (commit `e2fa97d`): APROBADO.** F3E5B-04, RESUELTO: la regla elimina la cota de 24 h y marca como desconocido todo el intervalo entre observaciones consecutivas con distinto multiplicador, así que quedan cubiertas las doce horas que escapaban antes. La prueba de §12.6.5 contempla la detección retrasada, la propagación a los agregados, el reinicio de VWAP y del volumen relativo y la conservación del precio, y distingue el corte anterior a la detección, cuando solo se conocía el multiplicador antiguo. F3E5B-01 a F3E5B-03 siguen resueltos. Sin residuales ni hallazgos nuevos. La aprobación es del diseño: las pruebas de F3-A10 se verificarán con la implementación, y las versiones autorizadas y el despliegue siguen pendientes de decisión del propietario | Revisor |
| 26 nov 2026 | **F3-E5b programada** (rama `fase-3-e5b`, **pendiente de la revisión del código**). **La migración `0004`** crea `config.strategy_versions` (vacía) y `trading.signals`, con sus restricciones y los permisos de `kbot_strategy`, y la política de permisos añade su regla de §9. **`kbot.strategies.recorded`** convierte las filas del grabador en minutos del contrato, con el multiplicador y el universo en cada instante. **`kbot.strategies.service` y `python -m kbot.strategies`** calculan las señales de cada día en cada corte, con la ventana de recuperación, la escritura idempotente y la lectura en una instantánea. **Las pruebas de §12.6.5:** 26 sin base, entre ellas la equivalencia con el motor en cada corte y los casos de F3E5B-01 a F3E5B-04; y 40 con PostgreSQL, entre ellas los permisos, la clave, cada restricción en su frontera y el servicio sobre velas escritas como el grabador, también en un proceso sin red. En local pasan 1.337; la CI de `18eeeae`, en verde, pasa 1.685, y 223 en aarch64. §12.6.4 anota el coste medido y seis precisiones de la implementación | Claude |
| 26 nov 2026 | **Revisión del código de F3-E5b (commit `e8f9502`): APROBABLE CON CORRECCIONES**, con dos hallazgos MEDIOS y uno BAJO. **F3E5B-05** (MEDIO): un minuto parcialmente incierto conservaba la V válida. Con 0,01 a las 00:00 y a las 11:59:30 y 0,001 a las 14:00, la incertidumbre empieza a las 11:59:30, pero el código solo miraba la apertura: el minuto de las 11:59 y el agregado de las 11:45 quedaban válidos y alimentaban el VWAP. **F3E5B-06** (BAJO): una vela de Spot sin observaciones, o anterior a la primera dentro de su minuto, recibía multiplicador nulo y V no válida, aunque en Spot es siempre 1. **F3E5B-07** (MEDIO): la prueba «sin red» no bloqueaba las conexiones nativas. Bajo el mismo gancho de auditoría, `psycopg.connect(hostaddr='127.0.0.1')` alcanzó un listener TCP local, y Python no ofrece esos ganchos como aislamiento. No demuestra tráfico indebido del servicio. **Verificación del revisor:** pasan 465 pruebas locales, y el ZIP corresponde al commit y al diff. No ejecutó las de PostgreSQL ni las tres que necesitan NumPy y TA-Lib | Revisor |
| 26 nov 2026 | **F3E5B-05 a F3E5B-07, corregidos** (§12.6.4 y §12.6.5). (5) Un minuto tiene multiplicador solo si es conocido e igual en todo él: `multiplier_at` mira también las observaciones dentro del minuto y la siguiente. (6) En Spot, el multiplicador es siempre 1, sin depender de las observaciones. (7) La prueba sin red corre el ciclo en un espacio de red propio (`sudo -n unshare --net` y `setpriv` para volver al usuario de las pruebas). Dentro, una conexión TCP de psycopg a un puerto que escucha la prueba se rechaza, mientras fuera ese puerto contesta, y el ciclo funciona por el socket Unix. Cada corrección tiene su prueba de aceptación | Claude |
| 26 nov 2026 | **Segunda revisión del código de F3-E5b (commit `d169f17`): APROBADO.** F3E5B-05, RESUELTO: se comprueba el minuto entero, con los dos límites dentro del minuto, y la incertidumbre invalida la V y sus agregados, con P y T intactas. F3E5B-06, RESUELTO: Spot recibe siempre el multiplicador 1, sin depender de las observaciones. F3E5B-07, RESUELTO por revisión del código: la prueba usa un aislamiento del sistema operativo y comprueba el rechazo del TCP nativo, el funcionamiento por el socket Unix y que el puerto de fuera no recibió ninguna conexión. **Verificación del revisor:** pasan 136 pruebas locales, y las tres pruebas nuevas fallan con el código anterior y pasan con el corregido. Una comparación adicional de 6.843 casos de intervalos no encontró discrepancias, y el ZIP, el commit, el diff y el paquete coinciden. No repitió la prueba con Linux y PostgreSQL desde Windows: para ella, la CI en verde sigue siendo la evidencia aportada. Sin residuales ni hallazgos nuevos. El despliegue y la elección de las versiones siguen pendientes del propietario | Revisor |
| 26 nov 2026 | **Un reinicio del grabador por una actualización automática.** A las 06:38-06:39 UTC, `unattended-upgrade` instaló bibliotecas de Kerberos nuevas (`libgssapi-krb5-2`, `libkrb5-3` y otras) y el sistema reinició `postgresql@18-main` a las 06:39:14. El grabador recibió `AdminShutdown` al escribir sus velas, terminó con error y `systemd` lo arrancó de nuevo a las 06:39:31, como está previsto (§0, regla 1). Quedó un hueco `proceso_detenido` de 06:39:00 a 06:40:10 en los dos mercados: las velas se recuperan por REST, y el libro de ese minuto se pierde. La copia de las 06:40 terminó bien. **Pendiente de proponer:** fijar la hora de las actualizaciones automáticas y cómo reinician PostgreSQL | Claude |
| 26 nov 2026 | **SSH: el bloque del proveedor** (decisión del propietario, provisional mientras se desarrolla). La regla de entrada de la lista de seguridad admite `79.112.0.0/12` en vez de una sola IP: la IP de casa cambió tres veces en nueve días, siempre dentro de ese bloque (§9 y `docs/operacion/oracle_instancia.md` §4). El acceso sigue siendo solo con clave | Propietario |
| 26 nov 2026 | **Diseño de detalle de E4c-2: la subida a Object Storage** (§13.1, punto 4; rama `fase-2-e4c2`, **pendiente de revisión**, antes del código). Con lo medido en el servidor (la copia caliente, unos 577 MB; cada día sellado, unos 63 MB) y los límites gratuitos leídos en la documentación de Oracle (20 GB y 50.000 peticiones al mes). **Dos procesos:** `kbot-backup` sigue sin red, y un `kbot-upload` nuevo, sin acceso a la base, sube solo copias cifradas, y es el único exento del aislamiento de metadatos. El cubo, versionado y sin sobrescritura, con un grupo dinámico de una sola instancia y la regla de 1 día para las versiones anteriores. La subida, la relectura con SHA-256, la retención D25 con sus clases concretas, las dependencias del residual de R-E4-01, la omisión de días en la copia caliente solo con un estado verificado de menos de 2 h, las peticiones del mes (unas 9.000) y la simulación de 120 días. Guía del propietario: `docs/operacion/copias_object_storage.md` | Claude |
| 26 nov 2026 | **Revisión del diseño de detalle de E4c-2 (commit `b4c42f6`): APROBABLE CON CORRECCIONES**, con tres hallazgos MEDIOS. **E4C2-01:** Oracle exige `OBJECT_OVERWRITE` para `CreateMultipartUpload`, `UploadPart` y `CommitMultipartUpload` aunque el nombre sea nuevo, así que la subida en partes del volcado no quedaba autorizada; un `PutObject` admite 50 GiB. **E4C2-02:** con la frescura de 2 h, un borrado a las 10:55:01 y un listado fallido a las 11:55 dejaban que las copias de las 11:40 y de las 12:40 omitieran el día, contra el límite declarado de una hora. **E4C2-03:** con 49.999 peticiones, el modo imprescindible podía llegar a 50.002. Las cuatro dudas [P], contrastadas con la documentación de Oracle y de Netfilter. **Verificación del revisor:** el ZIP, el commit, el diff y las copias entregadas; revisión documental y aritmética, sin acciones en Oracle | Revisor |
| 26 nov 2026 | **E4C2-01 a E4C2-03, corregidos** en el diseño de detalle de E4c-2 (§13.1, punto 4). (1) Cada objeto en un único `PutObject` con `If-None-Match: *`, sin `UploadManager` ni subida en partes; un `412` tras una respuesta perdida se resuelve releyendo. (2) Un listado autoriza omisiones solo si es el último intento, tiene menos de 1 hora y no lo usó otra copia caliente: como mucho una copia caliente omite un día borrado a mano. (3) Tope obligatorio de 45.000 peticiones en cualquier ventana de 32 días UTC, reservado en el disco antes de cada petición, con cada página y cada reintento, sin reintentos ocultos del SDK y sin empezar una operación que no quepa entera. Las peticiones normales bajan a unas 7.000 en 31 días. Cada corrección tiene su prueba de aceptación | Claude |
| 26 nov 2026 | **Segunda revisión del diseño de detalle de E4c-2 (commit `79fac56`): APROBABLE CON CORRECCIONES.** E4C2-01, RESUELTO: un único `PutObject` sin subida en partes, la política sin sobrescritura y la relectura tras una respuesta perdida. E4C2-02, RESUELTO: el último intento, menos de una hora y una sola copia caliente cierran el caso anterior. E4C2-03, PARCIAL, con un residual MEDIO: la reserva sincronizaba el archivo temporal y lo renombraba, pero no sincronizaba el directorio, y tras una caída podía reaparecer el contador anterior y permitir una petición de más (`fsync(2)`). Sin hallazgos nuevos. **Verificación del revisor:** el ZIP, el commit, el diff y el paquete | Revisor |
| 26 nov 2026 | **Residual de E4C2-03, corregido** (§13.1, punto 4, puntos 5 y 7). Antes de cada petición: archivo temporal, `fsync` del archivo, cambio de nombre, `fsync` del directorio y solo entonces la petición HTTP. Si falla cualquier paso, no se envía. `estado.json` usa la misma secuencia. La prueba registra el orden de las operaciones e inyecta un fallo en cada paso | Claude |
| 26 nov 2026 | **Tercera revisión del diseño de detalle de E4c-2 (commit `ae0b217`): APROBADO.** E4C2-03, RESUELTO: el archivo se sincroniza y, tras el cambio de nombre, también el directorio antes de la petición HTTP; cualquier fallo de persistencia impide el envío, con la misma garantía para `estado.json`, y la prueba comprueba el orden completo e inyecta un fallo en cada paso. E4C2-01 y E4C2-02 siguen RESUELTOS. Sin residuales ni hallazgos nuevos. La aprobación es del diseño: las pruebas, la simulación y las comprobaciones en Oracle se verifican con el código. **Verificación del revisor:** el ZIP, el commit, el diff y el paquete | Revisor |
| 26 nov 2026 | **E4c-2 programada** (rama `fase-2-e4c2`, **pendiente de la revisión del código**; §13.1, punto 4, «Implementado en E4c-2»). `kbot.upload`: el ciclo de `kbot_upload`, sin acceso a la base, con el listado anotado antes de pedirlo, cada objeto en un único `PutObject` con `If-None-Match: *`, la relectura con SHA-256, la retención D25 y el residual de R-E4-01 con su índice de dependencias, el contador con el tope de 45.000 peticiones en 32 días reservado en el disco antes de cada petición, y el principal de instancia sin reintentos del SDK. `kbot.backup`: la omisión de los días verificados en el cubo, atada a un listado de un solo uso, y la copia previa autocontenida. Las unidades `kbot-upload` y `kbot-backup-previa`, `UMask=0027` y el grupo `kbot_upload` en las copias, el guardián que exime a `kbot_upload` por nombre, `01`, `02` y `05`. Dependencia nueva: `oci` 2.189.0 | Claude |
| 26 nov 2026 | **La simulación del presupuesto del cubo** (`tools/simular_copias.py`, con lo medido ese día: la copia caliente, 580 MB con todos los días; un día sellado, 63 MB). **Con la copia caliente al cubo cada hora, lo aprobado, no cabe en 19 GB:** cada copia que sale de la retención deja una versión anterior de unos 240 MB durante uno a tres días, unos 14 GB en total, y la primera subida que no cabe llega el día 4. Cada 6 horas, con el disco externo funcionando, el máximo es de 10,3 GB; cada 3 horas, 13,1 GB. Sin E4c-3 y E4c-4 no cabe nunca: con cada 6 horas, el día 50. **Pendiente del propietario** (D25): la cadencia de la copia caliente al cubo (`HOT_UPLOAD_HOURS`, hoy 1; recomendación: 6). En el servidor la copia sigue siendo horaria | Claude |
| 26 nov 2026 | **D25 enmendada: la copia caliente sube al cubo cada 6 horas** (decisión del propietario, «elijo tu recomendacion», a la vista de la simulación). Suben las copias de las 00:40, 06:40, 12:40 y 18:40 UTC (`HOT_UPLOAD_HOURS = 6`), y las «horarias» del cubo son las 3 últimas de esas subidas; las 7 diarias, las 2 semanales y las 2 previas no cambian. En el servidor la copia sigue siendo horaria: frente a un fallo de la base, el RPO sigue siendo 1 h; frente a la pérdida del servidor pasa a ser de como mucho unas 6 h. Con el disco externo, el máximo del cubo es de 10,3 GB | Propietario |
| 26 nov 2026 | **Revisión del código de E4c-2 (commit `cc79eea`): APROBABLE CON CORRECCIONES**, con cinco hallazgos MEDIOS. **E4C2-04:** con un listado vigente, tres copias seguidas daban omite, incluye, omite: solo se miraba el último manifiesto. **E4C2-05:** la simulación quitaba de la previa los días elegibles, que el código lleva mientras sigan en PostgreSQL; corregido eso, cada 6 horas con el disco externo el primer rechazo llega el día 64, y en 180 días con un 10 % de crecimiento, el día 50. **E4C2-06:** un ciclo sin subidas cambiaba `ultima_subida_completa` y aparentaba menos exposición. **E4C2-07:** `nft -j` imprime el UID numérico, y la comprobación solo conocía los dos fijos. **E4C2-08:** con el reloj atrasado dos segundos al cruzar la medianoche, 45.001 reservas reales y 45.000 contadas. **Verificación del revisor:** 87 pruebas locales y una reproducción de cada hallazgo; sin acciones en Oracle | Revisor |
| 26 nov 2026 | **E4C2-04 a E4C2-08, corregidos** (§13.1, punto 4, «Implementado en E4c-2»). (4) El listado consumido se anota de forma persistente antes de cada copia caliente. (5) La simulación modela la previa del código y la retirada a los 90 días; la tabla se rehízo, y E4c-3 debe traer la omisión por generación también en la previa. (6) `ultima_subida_completa` es la hora de la copia caliente verificada con sus dependencias, y el último ciclo sin errores va aparte. (7) Los UID del guardián se traducen con `/etc/passwd`. (8) Los días posteriores a hoy siguen contando. Además: una copia de día sale del cubo solo si la copia caliente más reciente ya omite el día por su generación, y una versión empatada en hora con una marca de borrado no cuenta como actual. Cada corrección tiene su prueba de aceptación | Claude |
| 26 nov 2026 | **El cubo, el grupo dinámico, la política y la regla de ciclo de vida, creados** por el propietario en la consola de Oracle, siguiendo `docs/operacion/copias_object_storage.md`. La guía se precisó con lo que mostró la consola: la entrada **Policies** buena es la del bloque **Identity**; el editor manual puede llamarse **Show manual editor** o **Customize (Advanced)**; y las reglas de ciclo de vida están en la pestaña **Policies** del cubo. Se comprueban en la subida de prueba, al instalar E4c-2 | Propietario |
| 26 nov 2026 | **El límite gratuito de Object Storage durante la prueba** [H, aviso de la consola y página de Always Free]. Mientras dura la prueba gratuita de la cuenta (hasta hacia el 8 dic 2026), lo gratuito son 10 GB de nivel Standard, y lo que pase se descuenta del crédito de prueba. Después, son 20 GB de los tres niveles juntos, el límite del diseño. Si al terminar la prueba hay más de 20 GB, Oracle borra todos los objetos: los 19 GB del presupuesto lo impiden | Claude |
| 26 nov 2026 | **Segunda revisión del código de E4c-2 (commit `24ba53a`): APROBABLE CON CORRECCIONES.** E4C2-04 a E4C2-08, RESUELTOS en sus casos originales, y los dos ajustes de más, coherentes. **E4C2-09** (MEDIO): el punto recuperable comprobaba el nombre de la dependencia y no su identidad. Con la copia de día rehecha con otro cifrado, vuelta a subir y la versión antigua ya eliminada, el estado seguía dando por recuperable una caliente cuyo manifiesto fija otros SHA-256. **Verificación del revisor:** el ZIP con los 314 archivos del commit y 99 pruebas locales | Revisor |
| 26 nov 2026 | **E4C2-09, corregido** (§13.1, punto 4, «Implementado en E4c-2»). El índice de dependencias guarda la identidad exacta de cada copia de día (objeto y SHA-256 del volcado y del manifiesto), y una caliente cuenta como punto recuperable solo si la copia verificada en el cubo tiene esa identidad. Cada verificación guarda la versión leída (`version-id` de `GetObject`), y si otra versión ocupa su lugar se deshace. La prueba reproduce el caso del revisor y la vuelta de los objetos originales | Claude |
| 26 nov 2026 | **Tercera revisión del código de E4c-2 (commit `d3a4c5b`): APROBABLE CON CORRECCIONES.** E4C2-09, RESUELTO: identidades completas, el índice rehecho y el punto recuperable H06:40 → H00:40 → H06:40. **E4C2-10** (MEDIO): con dos versiones de la misma hora y sin marca de borrado se tomaba la primera del listado, y una verificación antigua podía mantenerse sin ninguna lectura. Reproducción sintética, no observada en Oracle. **Verificación del revisor:** el ZIP con los 314 archivos y 100 pruebas locales | Revisor |
| 26 nov 2026 | **E4C2-10, corregido** (§13.1, punto 4). Un objeto cuya hora más reciente comparten varias versiones nunca es actual: con una marca de borrado se intenta subir; sin ella es ambiguo, no se sube y se vuelve a leer, y deciden la versión y el SHA-256 leídos. Probado en las dos ordenaciones del listado | Claude |
| 27 nov 2026 | **Cuarta revisión del código de E4c-2 (commit `7b4c374`): APROBABLE CON CORRECCIONES.** E4C2-10, RESUELTO. **E4C2-11** (MEDIO): la comprobación de espacio pedía sitio para una copia entera antes de volver a leer una cuyos objetos ya estaban en el cubo, ambiguos. En el límite: «no cabe», ninguna lectura y el RPO en H00:40, cuando releer no añade bytes. Reproducción con almacenamiento simulado y límite reducido. **Verificación del revisor:** el ZIP con los 314 archivos y 59 pruebas de subida | Revisor |
| 27 nov 2026 | **E4C2-11, corregido** (§13.1, punto 4). El presupuesto de espacio cuenta los bytes de los objetos que de verdad se subirían, los que no están en el cubo ni actuales ni ambiguos: releer lo que ya está no añade nada. La prueba lo hace en el límite, y rechaza a la vez una copia nueva que sí añadiría bytes | Claude |
| 27 nov 2026 | **Quinta revisión del código de E4c-2 (commit `8a83cc2`): APROBADO.** E4C2-11, RESUELTO: con el límite igual a lo ocupado, dos lecturas, ninguna subida, ningún aumento de espacio y la vuelta a H06:40; las copias que no caben se siguen rechazando. Sin hallazgos nuevos ni residuales. La aprobación cubre el código; quedan las comprobaciones reales de la instalación en Oracle. **Verificación del revisor:** el ZIP con los 314 archivos y 60 pruebas de subida | Revisor |
| 27 nov 2026 | **E4c-2 integrada en `main`** (`4c906e0`, con el «si» del propietario) e **instalada en el servidor** con `v0.2.9-copias`. `02_codigo.sh` a las 08:46 UTC, sin reiniciar el grabador, cuyo código no cambia. `05_copias.sh`: el usuario `kbot_upload`, los grupos de las copias, el guardián comprobado desde cada usuario y la primera subida de prueba (38 copias verificadas en el cubo, 1,01 GB, 157 peticiones). El ciclo de las 08:55, sin errores. La migración `0004` de F3-E5b, aplicada a las 09:01 sin reiniciar PostgreSQL, por decisión del propietario: solo los pasos de roles y de migración de `03_postgresql.sh`, que reiniciaría el clúster. `04_verificar.sh`: todo en verde. `svc-strategy` sigue sin instalar | Claude |
| 27 nov 2026 | **El revisor, en una conversación nueva de codex** a la que Claude manda las rondas directamente, con la autorización del propietario. Cada mensaje y cada respuesta, enteros, en `revision_actual/CONVERSACION_REVISOR.md`. La conversación anterior escribió su traspaso (`revision_actual/TRASPASO_REVISOR_2026-11-27.md`), y la nueva pasó una prueba de control de ocho preguntas. El propietario decide además que diseño y código se revisan siempre por separado, y que los hallazgos solo teóricos siguen parando la aprobación | Propietario |
| 27 nov 2026 | **Diseño de detalle de E4c-3: el disco externo** (§13.1, punto 4; rama `fase-2-e4c3`, **pendiente de revisión**, antes del código). El SFTP de `kbot_sync` con su chroot, los montajes de solo lectura y un sistema de archivos propio de 256 MiB para los recibos, sin enlaces. Lo que publica `kbot_archive` (el índice de generaciones y los desafíos, con las respuestas esperadas en privado). El guion de Windows, que falla sin el disco correcto. La verificación cada 15 minutos (`replicado_fuera` y `verificado_fuera`, nunca `elegible_para_exclusion_backup`). Medido: unos 90 MB de Parquet por día completo, y de 1,3 a 1,7 GB por visita semanal | Claude |
| 27 nov 2026 | **Revisión del diseño de detalle de E4c-3 (commit `4c8ed9e`): APROBABLE CON CORRECCIONES**, con cuatro hallazgos MEDIOS y dos BAJOS. **E4C3-01:** un desafío anterior podía acreditar una generación cuyo recibo actual detecta corrupción fuera del rango desafiado. **E4C3-02:** la descarga no garantizaba las dependencias exactas de la caliente (S1 en el cubo, S2 en el servidor). **E4C3-03:** el RPO externo se rejuvenecía con la visita. **E4C3-04:** los 256 MiB no acotaban lo que el servidor genera (desafíos y estado). **E4C3-05:** la estimación anual omitía las calientes semanales: de 69 a 87 GB, no 60. **E4C3-06:** F2-A11 seguía diciendo «sin instalar». Revisó la zona de cada hallazgo y no ve más. **Verificación del revisor:** los 314 archivos del ZIP, el diff y el bloque idéntico entre el paquete y el commit; modelos sintéticos | Revisor |
| 27 nov 2026 | **E4C3-01 a E4C3-06, corregidos** en el diseño de detalle de E4c-3 (§13.1, punto 4). (1) Cada desafío se ata al último recibo válido del par generación-disco; un recibo posterior con fallo lo anula, y el guion solo responde si la generación pasa entera la comprobación en esa visita. (2) Una caliente es recuperable desde el disco solo si el disco tiene cada dependencia con su identidad exacta, y las copias de un mismo día con otra identidad se guardan aparte. (3) El punto recuperable del disco, separado de la última visita y de la última sincronización sin errores. (4) Recibos idempotentes, como mucho 2 discos admitidos, un desafío por par y el estado acotado; el guion borra lo ya procesado. (5) De 69 a 87 GB al año, con sus supuestos. (6) F2-A11 corregida | Claude |
| 27 nov 2026 | **Segunda revisión del diseño de detalle de E4c-3 (commit `1c8cfee`): APROBABLE CON CORRECCIONES.** E4C3-02 a E4C3-06, RESUELTOS. **E4C3-01, PARCIAL:** deduplicar contra 30 días de SHA-256 bloqueaba la recuperación tras reparar (A → B inválido → A otra vez). **E4C3-07** (MEDIO): el verificador, como `kbot_archive`, no tenía acceso definido al registro ni a los manifiestos de las copias. **E4C3-08** (MEDIO): la retención podía borrar el manifiesto de la caliente antes de validar su recibo. Revisó la zona de cada uno y no ve más. **Verificación del revisor:** los 314 archivos del ZIP y el diff; modelos sintéticos | Revisor |
| 27 nov 2026 | **Residual de E4C3-01, E4C3-07 y E4C3-08, corregidos** (§13.1, punto 4). La idempotencia se mide contra el último recibo del par, no contra un historial. `kbot-archive-externo` lee las copias con `SupplementaryGroups=kbot_upload` solo en su unidad, sin poder modificarlas ni leer el estado de las subidas. `recibo_punto.json` lleva el manifiesto entero, autenticado contra el SHA-256 del registro, que nunca se borra | Claude |
| 27 nov 2026 | **Tercera revisión del diseño de detalle de E4c-3 (commit `4227572`): APROBADO.** El residual de E4C3-01, RESUELTO: A → A conserva el desafío, y A → B inválido → A reparado se recupera con un único desafío nuevo, también tras reinicios. E4C3-07, RESUELTO: el grupo suplementario, limitado a `kbot-archive-externo.service`. E4C3-08, RESUELTO: el manifiesto en el recibo, autenticado contra el registro permanente. E4C3-02 a E4C3-06 siguen resueltos. Sin hallazgos nuevos ni residuales. La aprobación cubre el diseño: quedan el código, sus pruebas y la instalación; la elegibilidad y las omisiones por generación siguen bloqueadas hasta E4c-4. **Verificación del revisor:** los 314 archivos del ZIP, el diff y modelos de cierre | Revisor |
| 27 nov 2026 | **E4c-3 programada** (rama `fase-2-e4c3`, **pendiente de la revisión del código**; §13.1, punto 4, «Implementado en E4c-3»). `kbot.archive.externo`: el índice de generaciones, los recibos y los desafíos atados al último recibo válido de cada par, como mucho 2 discos, el punto recuperable autenticado con el manifiesto del recibo contra el registro, y nunca la elegibilidad. `tools/pc/sincronizar.ps1`, para PowerShell 5.1 y 7. El bloque `Match` de `kbot_sync`, los montajes de solo lectura y la imagen de 256 MiB de `recibos/`, `kbot-archive-externo` cada 15 minutos, el archivo con `UMask=0027` y `06_externo.sh`. La guía `docs/operacion/disco_externo.md`. Las pruebas de extremo a extremo, en la CI con `sshd`, `pwsh` y PostgreSQL reales, cubren cada punto de la lista del punto 7 | Claude |
| 27 nov 2026 | **Primera revisión del código de E4c-3 (commit `79cbbdc`): APROBABLE CON CORRECCIONES**, con cinco hallazgos MEDIOS y dos BAJOS. **E4C3-09:** se publicaban desafíos y acuses antes de guardar el estado que los justificaba, y podía perderse una invalidación. **E4C3-10:** el servidor conservaba un punto recuperable que el disco ya había perdido. **E4C3-11:** un caché o un estado del PC cortados bloqueaban las visitas siguientes. **E4C3-12:** el recibo de un tercer disco seguía ignorado tras liberar una plaza. **E4C3-13:** lo ya procesado impedía avanzar por una cola de más de 1.000 archivos. **E4C3-14** (BAJO): un punto rechazado adelantaba la última sincronización sin errores. **E4C3-15** (BAJO): un corte al formatear la imagen de `recibos/` impedía reanudar la instalación. Revisó la zona de cada hallazgo y no ve más. **Verificación del revisor:** los 326 archivos del ZIP y el diff; reproducciones sintéticas, también con Windows PowerShell 5.1 | Revisor |
| 27 nov 2026 | **E4C3-09 a E4C3-15, corregidos** (§13.1, punto 4, «Implementado en E4c-3»). (9) Cada ejecución guarda el estado y solo entonces publica los desafíos, sus retiradas, `discos.json` y los acuses, desde el estado guardado; el PC borra solo lo procesado con el SHA-256 que subió. (10) Cada visita envía su observación numerada, también sin ninguna caliente completa, y una más nueva sustituye el punto, aunque vaya hacia atrás; una antigua se rechaza. (11) El estado y el caché del disco se escriben con temporal y sustitución, y uno ilegible se descarta y fuerza la comprobación completa. (12) Lo rechazado por falta de plaza se vuelve a leer cuando cambian los discos admitidos. (13) Lo procesado se salta por su sello sin leerlo, y el límite cuenta solo lo leído. (14) Una observación rechazada no cambia nada del disco. (15) La imagen se prepara aparte y se publica con un cambio de nombre; una existente solo se comprueba con `e2fsck -n`. Cada corrección tiene su prueba de aceptación | Claude |
| 27 nov 2026 | **Segunda revisión del código de E4c-3 (commit `de577c9`): APROBABLE CON CORRECCIONES.** E4C3-10 a E4C3-15, RESUELTOS. **E4C3-09, PARCIAL:** si fallaba el último `fsync` del guardado, la ejecución siguiente anunciaba desde un estado visible pero sin asegurar. **E4C3-16** (MEDIO): perder el estado del disco mientras `discos.json` iba atrasado podía repetir un número de observación, y el servidor conservaba un punto ya perdido. **E4C3-17** (BAJO): un corte de la conexión hacía olvidar archivos pendientes de borrar. Revisó la zona de cada uno y no ve más. **Verificación del revisor:** los 328 archivos del ZIP y el diff; el guion entero con Windows PowerShell 5.1 y `sftp` simulado | Revisor |
| 27 nov 2026 | **El residual de E4C3-09, E4C3-16 y E4C3-17, corregidos** (§13.1, punto 4, «Implementado en E4c-3»). (9) Cada ejecución, y `retirar-disco`, confirma con `fsync` del archivo y del directorio el estado cargado antes de anunciar nada desde él. (16) El número de cada observación nunca es menor que la hora actual en milisegundos, y `discos.json` lleva el SHA-256 de la última aceptada, para que el guion avise si la suya no lo fue. (17) El guion olvida lo pendiente de borrar solo cuando el listado de `recibos/` lo muestra borrado. Cada corrección tiene su prueba de aceptación, y las del guion corren con Windows PowerShell 5.1 en el PC | Claude |
| 27 nov 2026 | **Tercera revisión del código de E4c-3 (commit `bf69fd9`): APROBABLE CON CORRECCIONES.** E4C3-09 y E4C3-17, RESUELTOS; los cierres anteriores se mantienen. **E4C3-16, PARCIAL:** el suelo de la hora UTC no basta. Con el reloj del PC cinco segundos atrasado, el estado del disco perdido y `discos.json` atrasado, la observación nueva llevaba un número menor que el aceptado; el servidor la rechazaba y conservaba H27. Sin hallazgos nuevos. **Verificación del revisor:** los 328 archivos del ZIP y el diff; el guion entero con Windows PowerShell 5.1, cambiando solo la lectura del reloj en una copia aislada | Revisor |
| 27 nov 2026 | **El residual de E4C3-16, corregido** (§13.1, punto 4, «Implementado en E4c-3»). El número de cada observación ya no depende del reloj. Cada disco envía su observación en su propio archivo, `recibo_punto_<disco>.json`, que se sustituye en cada visita y nunca se borra. Al empezar, el guion lo lee de vuelta, y el número nuevo es mayor que el de ese archivo, que el de `discos.json` y que el guardado en el disco. La prueba repite la secuencia del revisor con Windows PowerShell 5.1: el número nuevo es exactamente el anterior más uno | Claude |
| 27 nov 2026 | **Cuarta revisión del código de E4c-3 (commit `e25f1b3`): APROBABLE CON CORRECCIONES.** El reloj ya no interviene, y el caso anterior pasa. **E4C3-16 sigue PARCIAL:** `put` vacía el archivo de destino antes de escribirlo. Un corte en ese momento deja vacío el archivo de la observación, y el guion lo tomaba por un contador inexistente: enviaba el número 1, el servidor lo rechazaba y conservaba el punto obsoleto. E4C3-09 y E4C3-17 siguen RESUELTOS. Sin hallazgos nuevos. **Verificación del revisor:** los 328 archivos del ZIP y el diff; el guion entero con Windows PowerShell 5.1 y `sftp` simulado, y el vaciado comprobado aparte con un servidor SFTP real | Revisor |
| 27 nov 2026 | **El segundo residual de E4C3-16, corregido** (§13.1, punto 4, «Implementado en E4c-3»). El guion sube cada archivo como `.subiendo-<nombre>` y lo pone en su sitio con `rename` (`posix-rename`), así que un corte nunca deja vacío el archivo de la observación. El verificador no lee esos archivos temporales. Si el guion no puede fiarse del número, no envía la observación y lo dice; cuando el servidor ha procesado el archivo ilegible, `discos.json` basta y la visita siguiente continúa sola. Prueba con Windows PowerShell 5.1: el corte dentro del `put`, la pérdida del estado y la reanudación | Claude |
| 27 nov 2026 | **Quinta revisión del código de E4c-3 (commit `d6a20f3`): APROBABLE CON CORRECCIONES.** El corte dentro de `put`, corregido. **E4C3-16 sigue PARCIAL** por dos casos con la publicación de `discos.json` fallando de forma persistente. (1) Una visita detenida guardaba igualmente el número calculado, y la siguiente lo daba por fiable. (2) Que el servidor hubiera procesado un archivo ilegible con ese mismo SHA-256 no garantiza que `discos.json` esté al día. E4C3-09 y E4C3-17 siguen RESUELTOS. Sin hallazgos nuevos. **Verificación del revisor:** los 328 archivos del ZIP y el diff; los dos casos con el guion entero en Windows PowerShell 5.1, `sftp` simulado y el verificador real | Revisor |
| 27 nov 2026 | **El tercer residual de E4C3-16, corregido** (§13.1, punto 4, «Implementado en E4c-3») con una resincronización explícita. Un número solo es fiable si sale de una de cuatro referencias: el guardado por el disco (que solo se guarda si lo era), la última observación leída de vuelta entera, un disco recién inicializado o una resincronización confirmada. Sin ninguna, el guion no envía observación ni guarda número: sube `resincronizar_<disco>.json` con un nonce, y el servidor lo contesta en `discos.json`, junto a su contador, solo desde su estado guardado. Al usarla, el guion cierra la petición. Prueba con Windows PowerShell 5.1 de los dos casos del revisor, con la publicación fallando de forma persistente | Claude |
