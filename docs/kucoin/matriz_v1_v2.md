# Matriz de revisión V1/V2 de KuCoin (Fase 2, etapa E1)

**Para qué sirve:** es la evidencia para que el propietario decida **D04** (qué versión de la API usa el bot) y cierra la parte de E1 de H08, B4, B6, B14 a y B31 (`02` §3.2, `04`, `07` A4).

**Cómo se obtuvo:** el 6 de noviembre de 2026, entre las 23:30 y las 23:33 UTC, desde el PC del propietario, con `uv run python -m tools.record_kucoin`. Solo endpoints públicos, sin claves y a través del propio cliente del bot (lista blanca y límite de peso activos). Las respuestas están en `tests/fixtures/kucoin/` y las comprueban las pruebas de `tests/contract/`. Las listas largas se recortan a 3 elementos; los números conservan el texto exacto que envió KuCoin.

Etiquetas: [H] hecho comprobado con datos reales · [I] inferencia · [R] recomendación · [P] pendiente.

## 1. REST: V1 frente a V2 (UTA, `api.kucoin.com`)

| Dato | Ruta | Peso medido | Diferencias observadas | Recomendación |
|---|---|---|---|---|
| Instrumentos | `/api/ua/{v1,v2}/market/instrument` | 4 y 4 | V1 mezcla números JSON (`orderPriceRange: 0.05`, `sellLimit: 72087.8`, `indexPriceTickSize: 0.01`) con texto, y da los estados como códigos (`"1"`). V2 envía todos los decimales como texto y estados legibles (`TradingEnabled`, `Open`) [H] | V2 |
| Ticker | `/api/ua/{v1,v2}/market/ticker` | 15 y 15 | Mismos campos; V2 añade en Futures `sequence`, `side`, `tradeId` y `ts` [H]. `priceChangePercent` no parece tener la misma escala en Spot y en Futures [P, no se usa] | V2 |
| Velas | `/api/ua/{v1,v2}/market/kline` | 3 y 3 | **Datos idénticos** en el minuto grabado [H]. Las dos devuelven `klineType: "TRADE"` en Futures. V2 acepta el parámetro `klineType`: `TRADE` funciona, **`MARK` e `INDEX` se rechazan** con el código 148035 [H] | V2 (indiferente) |
| Operaciones | `/api/ua/{v1,v2}/market/trade` | 3 y 3 | Misma forma; lado en mayúsculas (`BUY`/`SELL`) [H] | V2 (indiferente) |
| Funding actual | `/api/ua/{v1,v2}/market/funding-rate` | 2 y 2 | V1 devuelve un objeto con el símbolo `.XBTUSDTMFPI8H` y números JSON (`1.0E-4`); V2, una lista con el símbolo del contrato `XBTUSDTM` y texto (`"0.0001"`). Mismo valor y misma hora [H] | **V2**: evita la correspondencia de símbolos de B6 |
| Histórico de funding | `/api/ua/{v1,v2}/market/funding-rate-history` | 5 y 5 | Mismos valores y horas; V1 en números JSON, V2 en texto [H] | V2 |

**B4 (`productType`):** con un símbolo dado, `productType=USDT-FUTURES` y `productType=COIN-FUTURES` devuelven exactamente lo mismo que sin el parámetro [H]. Por tanto, el ejemplo oficial con `COIN-FUTURES` y `XBTUSDTM` no cambia la respuesta: el bot no envía `productType`. **`klineType`:** el único valor válido encontrado es `TRADE` [H]. Sigue faltando la tabla oficial de valores [P].

**B6:** el símbolo de funding de V1 es el campo `fundingRateSymbol` del contrato en Classic (`.XBTUSDTMFPI8H` para `XBTUSDTM`) [H en un contrato]. Si D04 elige V2, B6 deja de hacer falta.

**B5 (retirada de V1):** no se encontró fecha de retirada en la documentación consultada el 6 de noviembre de 2026, que agrupa V1 bajo «Abandoned Endpoints» [I, consulta resumida sin cita literal guardada]. Sigue abierta hasta tener la cita.

## 2. REST: Classic Futures (`api-futures.kucoin.com`)

Sin equivalente en UTA para lo que el bot necesita. Todos devuelven números JSON, que el bot lee como `Decimal` exactos.

| Dato | Ruta | Peso medido | Observado |
|---|---|---|---|
| Contratos activos | `/api/v1/contracts/active` | 3 | 717 contratos. `multiplier` 0,001 en `XBTUSDTM`, igual al `unitSize` de V2; `indexSymbol` `.KXBTUSDT`; `fundingRateSymbol` [H] |
| Tramos de riesgo | `/api/v1/contracts/risk-limit/{symbol}` | 5 | Público. Tramos contiguos (el mínimo de cada uno es el máximo del anterior), con margen de mantenimiento creciente y apalancamiento decreciente [H] |
| Mark price actual | `/api/v1/mark-price/{symbol}/current` | 3 | Valor actual con `granularity` 1000 ms [H] |
| Índice | `/api/v1/index/query` | 2 | Histórico paginado (`hasMore`) del índice `.KXBTUSDT` con su composición por exchanges [H] |

**Pesos:** los 16 pesos de la lista blanca se midieron con la cabecera `gw-ratelimit-remaining` (dos peticiones seguidas por ruta) y coinciden con `src/kbot/kucoin/routes.py` [H]. El pool fue de 2.000 en todas las respuestas [H].

**B14 a:** todos los endpoints públicos de Spot y Futures responden desde la IP del PC [H]. Desde Oracle se comprobará en E4.

## 3. WebSocket UTA (sin versiones V1/V2)

Endpoints `wss://x-push-spot.kucoin.com` y `wss://x-push-futures.kucoin.com`, sin token.

| Mensaje | Observado [H] |
|---|---|
| Bienvenida | `{"sessionId", "message": "welcome", "pingInterval": 18000}` |
| Confirmación | `{"id": "s1", "result": true}` para cada suscripción |
| Ping (B31) | `{"id", "type": "ping"}` recibe `{"type": "pong", "id", "ts"}` cada 18 s, sin desconexiones durante la grabación. **Falta la mitad de B31:** que la conexión aguante 24 h con este formato (E5) [P] |
| `obu` (5 y 50) | Instantáneas completas (`t: "snapshot"`) con `O == C`, niveles ordenados y libro sin cruzar; unas 10 por segundo |
| `trade` | `p`, `q`, `S` (en minúsculas), `M` en ns, `ti` |
| `ticker` | Mejor bid y ask con tamaños, última operación. `S` en mayúsculas en Spot |
| `kline` 1min | Campos etiquetados `o`, `h`, `l`, `c`, `v`, `a` (turnover), `O` y `C` en segundos |
| `mark-price` | `mp`, `ip`, `oi`, `ts` en ms; un mensaje por segundo en las muestras (B2: primera evidencia; la medición de 24 h es de E5) |

**Unidades de tiempo [H]:** velas REST y `O`/`C` del WebSocket en segundos; funding, Classic y `mark-price` en milisegundos; operaciones y ticker REST, `P`, `M` y el `ts` del pong en nanosegundos.

## 4. Hallazgos que afectan a la etapa E2

1. **Columnas de las velas REST [H]:** `[apertura (s), open, high, low, close, volumen, turnover]`. Coinciden campo a campo con la vela etiquetada del WebSocket del mismo minuto, en las tres grabaciones de esta etapa (6 minutos, Spot y Futures). El repositorio guarda la última.
2. **Volumen de la vela = 2 × operaciones del WebSocket [I]:** en esos 6 minutos, el volumen y el turnover de la vela son **exactamente el doble** de la suma de las operaciones recibidas por el canal `trade`. Puede que la vela cuente los dos lados de cada operación o que el canal entregue la mitad. **B8 sigue abierta:** la comparación de 60 minutos de E5 debe incluir también el endpoint REST de operaciones. En Futures, los tamaños son contratos (`multiplier` 0,001 en `XBTUSDTM`) [H].
3. **Velas de mark e index [H]:** la API UTA no las da (`klineType` MARK e INDEX rechazados). Afecta a `02` §1.3 (`candles` de last, mark e index) y a B3. Alternativas para decidir en E2: construir las velas de 1 minuto desde el canal `mark-price` (trae `mp` e `ip` cada segundo) o usar el histórico del índice de Classic. No se encontró un histórico de mark price en Classic [I].
4. **El cierre de una vela no se anuncia [I]:** no llega ningún mensaje de la vela después de su minuto, y el campo `S` no indica el cierre (vale `true` a mitad de minuto en Futures). El cierre se deduce por el tiempo y los valores definitivos se toman por REST.
5. **Reloj del PC [I]:** según el reloj del PC, todas las muestras llegan entre 0,4 y 0,55 s **antes** de la hora `P` que marca KuCoin, así que el reloj del PC va al menos medio segundo por detrás. La latencia (D26) debe medirse en el servidor con el reloj sincronizado.

## 5. D04: decidida

**Aprobada por el propietario el 7 de noviembre de 2026** («d04 v2 mas classic»): **V2 para todos los endpoints REST de UTA, Classic para contratos, tramos de riesgo, mark price e índice, y el WebSocket UTA.** V2 envía todos los decimales como texto y usa el símbolo del contrato en el funding; los datos son los mismos que en V1.

**Efecto en el código:** las rutas V1 salen de la lista blanca. Sus respuestas grabadas se conservan en `tests/fixtures/kucoin/evidencia_d04_v1/` solo como evidencia, y una prueba comprueba que una petición V1 se rechaza.
