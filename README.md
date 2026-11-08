# kucoin-trading-bot

Bot de **análisis y paper trading** de criptomonedas en KuCoin (Spot y Futures lineales en USDT), escrito en Python. Empieza con **10.000 USDT ficticios**.

> **No opera con dinero real.** No envía órdenes a KuCoin, no tiene permisos de trading ni de retirada y no usa ninguna clave de KuCoin: todos los datos que necesita son públicos.

## Estado

**Fase 2 (recopilación y almacenamiento de datos), etapa E2.** La Fase 1 dejó la base: el proyecto Python, el núcleo de cálculo, la base de datos con su seguridad y la integración continua. La etapa E1 añadió el cliente de KuCoin de solo lectura (REST y WebSocket públicos, sin claves; API UTA V2 más Classic, según D04). La etapa E2 añade el esquema `market` y el grabador de datos de mercado. Todavía no simula operaciones.

La especificación vigente y el Documento Maestro están en [`docs/especificacion/`](docs/especificacion/). El Documento Maestro es la fuente de verdad: nada puede contradecirlo.

## Reglas que nunca cambian (Documento Maestro)

- Riesgo máximo del **0,5 %** por operación, pérdida diaria máxima del **2 %** y drawdown máximo del **10 %**.
- Futures con apalancamiento máximo **2x** y margen aislado.
- **Stop-loss obligatorio** en todas las operaciones y prohibido aumentar posiciones perdedoras.
- Infraestructura a **0 €/mes**; IA como máximo **25 €/mes**.
- **Ninguna clave, contraseña ni secreto** en el repositorio.

Los límites de riesgo viven en la tabla `config.risk_limits`, que solo puede escribir el rol propietario mediante migraciones. La integración continua los compara con el texto literal del Documento Maestro y falla si no coinciden.

## Estructura

| Carpeta | Contenido |
|---|---|
| `src/kbot/core/` | Núcleo de cálculo: decimales sin redondeos ocultos, JSON canónico (RFC 8785), árbol de Merkle (RFC 6962) y reloj inyectable |
| `src/kbot/limits.py` | Límites de riesgo del Documento Maestro y su huella SHA-256 |
| `src/kbot/kucoin/` | Cliente de KuCoin de solo lectura: REST solo GET con lista blanca de rutas y límite de peso, WebSocket público con lista blanca de mensajes, y contratos de los datos |
| `src/kbot/market/` | Lógica de los datos de mercado: profundidad cada 10 s, velas de last, mark e index, huecos, universo, cierre diario y escritura en PostgreSQL (solo inserciones) |
| `src/kbot/ingest/` | El grabador (`svc-ingest`): une las conexiones de KuCoin, el reloj y la base de datos. `uv run python -m kbot.ingest --sink file --out CARPETA --minutes 15` lo prueba en un PC sin PostgreSQL |
| `sql/roles.sql` | Roles de PostgreSQL y base de datos. Ningún rol tiene contraseña |
| `migrations/` | Migraciones de Alembic. Todo su SQL está en archivos `.sql` revisados |
| `tools/` | Comprobaciones de la integración continua (lista blanca de SQL y límites) y grabador de respuestas públicas de KuCoin (`record_kucoin.py`) |
| `ci/` | Instalación de PostgreSQL 18 y gitleaks en la integración continua |
| `tests/` | Pruebas unitarias, de propiedades, de contrato (respuestas reales de KuCoin en `tests/fixtures/kucoin/`), de la integración continua y de PostgreSQL |
| `docs/especificacion/` | Documento Maestro y especificación vigente |
| `docs/kucoin/` | Matriz de revisión V1/V2 de la API de KuCoin (evidencia de D04, ya decidida) |
| `docs/informes/` | Informe de la Fase 1 y tabla de trazabilidad de las pruebas de aceptación (F1-A# y F2-A#) |

## Cómo ejecutar las pruebas

Hace falta [uv](https://docs.astral.sh/uv/). En Windows se instala con `winget install astral-sh.uv`; uv instala Python 3.12 por su cuenta.

```
uv sync --frozen
uv run ruff check .
uv run pytest
```

Las pruebas de PostgreSQL usan autenticación `peer`, que solo existe en Linux, así que se ejecutan en la integración continua de GitHub. En un PC con Windows aparecen como omitidas. En la integración continua, una prueba que no se puede ejecutar cuenta como fallo, nunca como omitida.

## Integración continua

Cada cambio publicado ejecuta, en una máquina Ubuntu de GitHub:

1. instalación exacta desde `uv.lock`, con las huellas de cada paquete;
2. estilo del código (`ruff`);
3. búsqueda de secretos en todo el historial (`gitleaks`);
4. lista blanca de sentencias SQL, con la gramática real de PostgreSQL 18 (`pglast`);
5. comparación de los límites de riesgo con el Documento Maestro;
6. PostgreSQL 18 instalado en la propia máquina, sin red y solo con `peer`: roles, permisos, migraciones y pruebas con sesiones reales de cada rol;
7. todas las pruebas.

Las acciones de GitHub están fijadas por su commit y gitleaks por su huella SHA-256.
