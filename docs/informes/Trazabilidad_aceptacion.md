# Trazabilidad de las pruebas de aceptación

Única tabla del proyecto que une cada criterio de aceptación con las pruebas que lo comprueban y su estado. Las definiciones completas están en el alcance de cada fase (`05_Fase1_Alcance.md` §4 y `07_Fase2_Alcance.md` §4, en el paquete de contexto); el detalle de cada revisión, en `07` §10 y en [`Informe_Fase1.md`](Informe_Fase1.md).

**Identificadores por fase** (auditoría de E2, hallazgo E2-05, 8 de noviembre de 2026): **F1-A1 a F1-A10** para la Fase 1 y **F2-A1 a F2-A15** para la Fase 2. Los criterios de la Fase 2 son de toda la fase, no de una etapa: E1 cerró F2-A2, F2-A3 y F2-A4, y E2 responde de F2-A1 y de F2-A5 a F2-A8.

**Registros anteriores al 8 de noviembre de 2026** (registro de cambios de `02` y revisiones de E1 y E2) citan los criterios sin prefijo: «A#» es F1-A# en lo que se refiere a la Fase 1 y F2-A# en lo que se refiere a la Fase 2. Los nombres A2-01, A3-01 y A4-01 son hallazgos de la revisión de E1, no criterios.

## Fase 1: núcleo y base de datos (integrada en `main`)

| ID | Criterio | Pruebas | Estado |
|---|---|---|---|
| F1-A1 | `uv sync --frozen` instala desde `uv.lock` en un entorno limpio | Paso «F1-A1» de `.github/workflows/ci.yml` | Pasa (6 nov 2026) |
| F1-A2 | Toda la CI pasa en la rama de la fase | La CI completa | Pasa (6 nov 2026) |
| F1-A3 | Ningún cálculo fuera de las cuantizaciones lanza `Inexact`; uno que perdería precisión falla en lugar de redondear | `tests/unit/test_decimal.py` | Pasa (6 nov 2026) |
| F1-A4 | `jcs` reproduce los ejemplos de la RFC 8785 y serializa los decimales como texto | `tests/unit/test_jcs.py` | Pasa (6 nov 2026) |
| F1-A5 | Dos implementaciones independientes dan los valores de RC14 | `tests/unit/test_merkle.py` | Pasa (6 nov 2026) |
| F1-A6 | Roles sin contraseña, solo `peer`, sin TCP, identidad no autorizada rechazada, sin `CREATE`, PUBLIC sin derechos | `tests/db/test_roles.py`, `tests/db/test_privileges.py` | Pasa en la CI; se repite en el servidor (F2-A10) |
| F1-A7 | `config.risk_limits` coincide con el DM y ningún servicio puede cambiarla | `tests/db/test_risk_limits.py` | Pasa (6 nov 2026) |
| F1-A8 | La CI rechaza una sentencia fuera de la lista blanca, un secreto de prueba y un límite distinto del DM | `tests/ci/test_sql_guard.py`, `tests/ci/test_grant_policy.py`, `tests/ci/test_gitleaks.py`, `tests/ci/test_limits_check.py` | Pasa (6 nov 2026) |
| F1-A9 | Las claves foráneas toman `FOR KEY SHARE` | `tests/db/test_fk_locks.py` | Pasa (6 nov 2026) |
| F1-A10 | Repositorio público y solo runners estándar de GitHub Actions | `tests/ci/test_workflow.py` | Pasa (6 nov 2026) |

## Fase 2: recopilación y almacenamiento de datos (en curso)

| ID | Criterio | Pruebas | Estado |
|---|---|---|---|
| F2-A1 | Toda la CI pasa, con la lista blanca de SQL y la matriz de roles ampliadas solo con lo que la fase necesita | `tools/sql_guard.py`, `tools/grant_policy.py`, `tests/ci/test_sql_guard.py`, `tests/ci/test_grant_policy.py`, `tests/db/test_privileges.py` | E1: pasa. E2: la `0002` (con `ws_candles` y `exhausted_index_rebuilds`) pasa la lista blanca sin ampliarla y la matriz solo añade `SELECT` e `INSERT` de `kbot_ingest` en `market`. La tercera revisión lo comprobó ejecutando las pruebas (salvo gitleaks y la visibilidad, que necesitan la CI de GitHub) |
| F2-A2 | REST: solo GET de la lista; limitador por debajo del 50 % del pool; esperas correctas ante 429 | `tests/unit/kucoin/test_rest_guard.py`, `tests/unit/kucoin/test_ratelimit.py` | Superada en E1 (aprobada el 7 nov 2026) |
| F2-A3 | WebSocket: nada privado, de órdenes ni fuera de la lista; los canales públicos necesarios funcionan | `tests/unit/kucoin/test_ws_guard.py`, `tests/unit/kucoin/test_ws_stream.py` | Superada en E1 (aprobada el 7 nov 2026) |
| F2-A4 | Matriz V1/V2 y pruebas de contrato con respuestas reales | `docs/kucoin/matriz_v1_v2.md`, `tests/contract/`, `tests/fixtures/kucoin/`, `tools/record_kucoin.py` | Superada en E1 (aprobada el 7 nov 2026) |
| F2-A5 | Cada hueco en `data_gaps` y cada intervalo afectado `desconectado`; tras una parada, el grabador continúa desde la base | `tests/unit/market/test_gaps.py`, `tests/unit/market/test_depth.py`, `tests/unit/ingest/test_core.py`, `tests/unit/ingest/test_service.py`, `tests/db/test_market_recovery.py`, `tests/db/test_ingest.py` | E2: parcial en la tercera revisión (I-E2-03, I-E2-05 e I-E2-06); corregido, pendiente de confirmación. E2-01, E2-02 y R-E2-01 ya están confirmados |
| F2-A6 | Estados de la vela y del minuto según `02` §3.4 y §3.5, sin velas fabricadas, con casos con cero | `tests/unit/market/test_last_candles.py`, `tests/unit/market/test_mark_candles.py`, `tests/db/test_market.py`, `tests/db/test_market_recovery.py`, `tests/db/test_ingest.py` | E2: parcial en la tercera revisión (I-E2-01, I-E2-02, I-E2-05 e I-E2-06); corregido, pendiente de confirmación |
| F2-A7 | `kbot_ingest` solo tiene permisos en `market` y la base coincide con la matriz de roles | `tests/db/test_market.py` (`test_f2_a7_*`, también con las dos tablas nuevas), `tests/db/test_privileges.py` | E2: conforme, validado en PostgreSQL por la tercera revisión |
| F2-A8 | Profundidad: exactamente un estado terminal por intervalo; silencio con conexión sana como libro vigente; bytes por instrumento y día | `tests/unit/market/test_depth.py`, `tests/db/test_market.py`, `tests/db/test_ingest.py` | E2: unicidad y silencio sano conformes; la clasificación temporal (I-E2-04 e I-E2-07) está corregida, pendiente de confirmación. Los bytes por día, en E5 |
| F2-A9 | Parquet con SHA-256 de cada archivo; tamaño y consulta medidos | — | No empezada (E3) |
| F2-A10 | Servidor endurecido; F1-A6 repetida con los usuarios reales | — | No empezada (E4) |
| F2-A11 | Copias cifradas, retención comprobada, restauración y RPO medidos | — | No empezada (E4) |
| F2-A12 | 72 h seguidas sin intervención, dentro de lo gratuito | — | No empezada (E5) |
| F2-A13 | Inactividad de Oracle: 7 días con sus métricas por encima de los umbrales | — | No empezada (E4 y E5) |
| F2-A14 | Informe de la medición con propuestas para D26, D04 y D29 | — | No empezada (E5) |
| F2-A15 | Coste 0 €: todo dentro de Always Free, cuenta sin pasar a pago | — | No empezada (E4 y E5) |

Los resultados de la CI los comunica el propietario o los lee Claude en GitHub; el revisor independiente los contrasta con el código en cada revisión, pero no sustituyen a una auditoría de todos los puntos de parada posibles.
