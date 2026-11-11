# Histórico público de KuCoin (Fase 2, etapa E3)

**Para qué sirve:** cierra B3 y B13 de `04` y la parte de datos del punto 73 (sesgo de supervivencia): hasta dónde llega el histórico del índice en Classic y qué velas quedan de los pares deslistados. Es la evidencia de F2-A9 en este apartado (`07` §2, E3).

**Cómo se obtuvo:** el 9 de noviembre de 2026, entre las 12:54 y las 12:56 UTC, desde el PC del propietario, con `uv run python -m tools.kucoin_history`. Solo endpoints públicos, sin claves y a través del propio cliente del bot (lista blanca y límite de peso activos). La herramienta busca por bisección, con pocas peticiones ligeras. Supone que el histórico no tiene huecos más largos que su ventana de búsqueda: una hora para el índice y un día para las velas.

Etiquetas: [H] hecho comprobado con datos reales · [I] inferencia · [R] recomendación · [P] pendiente.

## 1. Índice de Classic (B3)

`uv run python -m tools.kucoin_history index .KXBTUSDT .KETHUSDT`

| Índice (contrato) | Valor más antiguo |
|---|---|
| `.KXBTUSDT` (XBTUSDTM) | 31 de julio de 2026, 16:00:00 UTC [H] |
| `.KETHUSDT` (ETHUSDTM) | 31 de julio de 2026, 16:00:00 UTC [H] |

Comprobación aparte, día a día: no hay ningún valor el 25 de julio, el 1 de julio, el 1 de mayo ni el 1 de febrero de 2026, ni el 1 de agosto de 2025 [H].

- **El histórico del índice cubre unos 100 días** [H]: del 31 de julio al 9 de noviembre de 2026.
- El límite cae a las 00:00 del 1 de agosto en hora de Pekín (UTC+8), igual en los dos índices. **Probablemente KuCoin lo borra por meses enteros**, y el más antiguo avanzará de golpe cuando empiece diciembre en esa hora [I, a confirmar repitiendo la medición después del 1 de diciembre de 2026].
- **Consecuencia:** los minutos de index perdidos solo se pueden reconstruir con Classic durante unos 3 meses. Pasado ese plazo, la única fuente es lo que el grabador haya guardado, y el archivo de E3 es lo que lo conserva.

## 2. Velas de pares deslistados (B13, punto 73)

`uv run python -m tools.kucoin_history klines SPOT BLAST-USDT REN-USDT HYVE-USDT OXT-USDT`

| Par | Anuncio oficial | Retirada (UTC) | Cierre de retiradas (UTC) | Velas de 1 minuto en UTA V2, el 9 nov 2026 |
|---|---|---|---|---|
| BLAST/USDT | [KuCoin Will Delist BLAST on 2026-10-16](https://www.kucoin.com/announcement/en-kucoin-will-delist-blast-on-2026-10-16) (13 oct 2026) | 16 oct 2026, 08:00 | 25 oct 2026, 12:00 | **Sí:** todo el histórico, del 26 de junio de 2024, 15:00, al 16 de octubre de 2026, 08:02 [H] |
| OXT/USDT | [KuCoin Will Delist Certain Projects & Their Associated Tokens](https://www.kucoin.com/announcement/en-kucoin-will-delist-certain-projects-their-associated-tokens-20260812) (12 ago 2026; se retira el proyecto Orchid, OXT, con sus pares) | 14 ago 2026, 08:00 | 14 sep 2026, 08:00 | **No:** código 138004, «Unsupported trading pair» [H] |
| REN/USDT | [ST: KuCoin Will Delist Certain Projects and Their Associated Tokens or Trading Pairs](https://www.kucoin.com/announcement/en-st-kucoin-will-delist-certain-projects-and-their-associated-tokens-or-trading-pairs-1226) (26 dic 2025) | 29 dic 2025, 09:00 (hora de retirada de los proyectos) | 29 ene 2026, 09:00 | **No:** código 138004 [H] |
| HYVE/USDT | El mismo anuncio que REN/USDT | 29 dic 2025, 09:00 | 29 ene 2026, 09:00 | **No:** código 138004 [H] |

- **KuCoin conserva las velas de un par deslistado durante un tiempo y después lo quita de la API** [H]. BLAST/USDT tenía todo su histórico 24 días después de su retirada, y 15 días después del cierre de sus retiradas. OXT/USDT ya no existía 87 días después de la suya.
- Son observaciones de pares distintos, no una regla de KuCoin: no se ha seguido ningún par a lo largo del tiempo, y la política general de retención de los pares retirados sigue sin determinar [P].
- **Consecuencia para el backtest (punto 73):** KuCoin no sirve como fuente del histórico de los pares deslistados más allá de unas semanas. Solo habrá velas de los pares que el grabador haya guardado mientras estaban en el universo, o de los que se descarguen poco después de su retirada [I].
- [R] Descargar el histórico completo de velas de 1 minuto de cada par del universo en cuanto KuCoin anuncie su retirada, o como muy tarde al salir del universo, mientras la API aún lo dé. Queda como propuesta para el propietario. Pertenece al diseño futuro de la recarga de históricos (backfill), no a E3.
