Este informe no es la validación de la estrategia (D16, F8): sirve para ver si la familia merece seguir.

# Informe exploratorio de «breakout» en la partición «desarrollo»

## Familia, versión y ficha

- **Familia:** breakout, con la ficha de `02` §12.3.5, aprobada.
- **Versión:** `3490a1cd7318290cbc1978f26fae7988c3ab0c1b+eac620fee86fed9ae4e3f5eeee8ded14986311445ccd71843bf5597d6ae94917`.
  - commit `3490a1cd7318290cbc1978f26fae7988c3ab0c1b`;
  - hash de los parámetros `eac620fee86fed9ae4e3f5eeee8ded14986311445ccd71843bf5597d6ae94917`.
- **Parámetros** (§12.3.3):

```json
{
 "familia": "breakout",
 "temporalidades": {
  "contexto": "4h",
  "senal": "1h",
  "ejecucion": "15m"
 },
 "regimen": {
  "adx": 14,
  "adx_tendencia": "25",
  "adx_rango": "20",
  "atr": 14,
  "bollinger": 20,
  "bollinger_k": "2",
  "ventana": 100,
  "expansion_mayor_que": "80",
  "compresion_menor_que": "80"
 },
 "contexto": {
  "ema_lenta": 50
 },
 "senal": {
  "pivote_k": 5,
  "pivote_m": 100,
  "compresion_velas": 5,
  "volumen_relativo": 20,
  "volumen_relativo_minimo": "1.5",
  "atr": 14
 },
 "ejecucion": {
  "velas": 4
 },
 "stop": {
  "multiplo_atr": "2"
 },
 "objetivo": {
  "multiplo_r": "2"
 }
}
```

## Periodo, particiones y decisiones

| Ventana | Desde | Hasta | Calentamiento desde |
|---|---|---|---|
| tramo 1 | 2024-11-19T00:00:00+00:00 | 2025-03-09T00:00:00+00:00 | 2024-11-19T00:00:00+00:00 |
| tramo 2 | 2025-03-09T00:00:00+00:00 | 2025-06-27T00:00:00+00:00 | 2025-02-07T00:00:00+00:00 |
| tramo 3 | 2025-06-27T00:00:00+00:00 | 2025-10-14T00:00:00+00:00 | 2025-05-28T00:00:00+00:00 |
| tramo 4 | 2025-10-14T00:00:00+00:00 | 2026-02-01T00:00:00+00:00 | 2025-09-14T00:00:00+00:00 |

- **Particiones consultadas:** desarrollo.
- **La prueba no se ha leído:** está sellada hasta la F8.
- **Decisiones tomadas con ellas:** ninguna todavía: el propietario decide qué familias siguen con los informes del desarrollo (§12.5.7).
- Cada ventana empieza sin posiciones, y una posición abierta al final se liquida.

## Universo

`docs/f3/universo_2026-11-18.json`: 20 instrumentos de Spot y 20 de Futures, los del universo del grabador el día de esa lista. **Sesgo de supervivencia:** son instrumentos que seguían cotizando ese día. Los que dejaron de cotizar antes no están, así que los resultados pueden ser mejores que los de una selección hecha en su día.

## Cobertura del histórico

En las ventanas, sin el calentamiento. **Cobertura** es la del contrato (§12.1, punto 2): los minutos presentes de E entre los de E. E son los minutos pedidos a KuCoin cuya elegibilidad no es `no_elegible`; los de elegibilidad desconocida cuentan. Si E está vacío, es «no aplicable». **Fuera de lo solicitado** son minutos que nunca se pidieron, como los de Futures antes del 1 ene 2025, que no tienen velas de un minuto: no cuentan en E. **Presentes sobre el calendario** es otra medida: los presentes entre todos los minutos de las ventanas.

| Instrumento | Minutos | Fuera de lo solicitado | No elegibles | E | Presentes en E | Ausentes | En conflicto | No descargados | Contradicciones | Cobertura | Presentes sobre el calendario | Días no válidos |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 632160 | 61920 | 376579 | 193661 | 182680 | 10981 | 0 | 0 | 0 | 94,33 % | 28,90 % | — |
| FUTURES AVAXUSDTM | 632160 | 61920 | 0 | 570240 | 489033 | 81207 | 0 | 0 | 0 | 85,76 % | 77,36 % | — |
| FUTURES BCHUSDTM | 632160 | 61920 | 0 | 570240 | 465180 | 105060 | 0 | 0 | 0 | 81,58 % | 73,59 % | — |
| FUTURES ENAUSDTM | 632160 | 61920 | 0 | 570240 | 531239 | 39001 | 0 | 0 | 0 | 93,16 % | 84,04 % | — |
| FUTURES ETHUSDTM | 632160 | 61920 | 0 | 570240 | 569038 | 1202 | 0 | 0 | 0 | 99,79 % | 90,01 % | — |
| FUTURES EVAAUSDTM | 632160 | 61920 | 396635 | 173605 | 173150 | 455 | 0 | 0 | 0 | 99,74 % | 27,39 % | — |
| FUTURES FHEUSDTM | 632160 | 61920 | 491520 | 78720 | 75477 | 3243 | 0 | 0 | 0 | 95,88 % | 11,94 % | — |
| FUTURES HYPEUSDTM | 632160 | 61920 | 0 | 570240 | 539115 | 31125 | 0 | 0 | 0 | 94,54 % | 85,28 % | — |
| FUTURES ICPUSDTM | 632160 | 61920 | 0 | 570240 | 453547 | 116693 | 0 | 0 | 0 | 79,54 % | 71,75 % | — |
| FUTURES INJUSDTM | 632160 | 61920 | 0 | 570240 | 509944 | 60296 | 0 | 0 | 0 | 89,43 % | 80,67 % | — |
| FUTURES LINKUSDTM | 632160 | 61920 | 0 | 570240 | 559145 | 11095 | 0 | 0 | 0 | 98,05 % | 88,45 % | — |
| FUTURES NIGHTUSDTM | 632160 | 61920 | 493090 | 77150 | 73280 | 3870 | 0 | 0 | 0 | 94,98 % | 11,59 % | — |
| FUTURES PEPEUSDTM | 632160 | 61920 | 0 | 570240 | 568306 | 1934 | 0 | 0 | 0 | 99,66 % | 89,90 % | — |
| FUTURES PIEVERSEUSDTM | 632160 | 61920 | 457175 | 113065 | 74445 | 38620 | 0 | 0 | 0 | 65,84 % | 11,78 % | — |
| FUTURES SOLUSDTM | 632160 | 61920 | 0 | 570240 | 569832 | 408 | 0 | 0 | 0 | 99,93 % | 90,14 % | — |
| FUTURES SOXLUSDTM | 632160 | 61920 | 570240 | 0 | 0 | 0 | 0 | 0 | 0 | no aplicable | 0,00 % | — |
| FUTURES UNIUSDTM | 632160 | 61920 | 0 | 570240 | 462679 | 107561 | 0 | 0 | 0 | 81,14 % | 73,19 % | — |
| FUTURES XBTUSDTM | 632160 | 61920 | 0 | 570240 | 566630 | 3610 | 0 | 0 | 0 | 99,37 % | 89,63 % | — |
| FUTURES XRPUSDTM | 632160 | 61920 | 0 | 570240 | 568307 | 1933 | 0 | 0 | 0 | 99,66 % | 89,90 % | — |
| FUTURES ZECUSDTM | 632160 | 61920 | 0 | 570240 | 408316 | 161924 | 0 | 0 | 0 | 71,60 % | 64,59 % | — |
| SPOT ASTER-USDT | 632160 | 0 | 0 | 632160 | 186660 | 445500 | 0 | 0 | 0 | 29,53 % | 29,53 % | — |
| SPOT AVAX-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT BNB-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT BTC-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT DOGE-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT DOT-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT ETH-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT EVAA-USDT | 632160 | 0 | 0 | 632160 | 0 | 632160 | 0 | 0 | 0 | 0,00 % | 0,00 % | — |
| SPOT LTC-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT NEAR-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT PEPE-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT PIEVERSE-USDT | 632160 | 0 | 0 | 632160 | 0 | 632160 | 0 | 0 | 0 | 0,00 % | 0,00 % | — |
| SPOT SOL-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT SUI-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT TAO-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT UNI-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT XLM-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT XMR-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT XRP-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |
| SPOT ZEC-USDT | 632160 | 0 | 0 | 632160 | 632160 | 0 | 0 | 0 | 0 | 100,00 % | 100,00 % | — |

## Porcentaje solo REST

Minutos originales de las operaciones, cada uno una vez por operación. En el histórico todos son solo REST.

| Procedencia | Minutos | Solo REST | Porcentaje |
|---|---|---|---|
| decisiones | 24166995 | 24166995 | 100,00 % |
| resultados | 24326092 | 24326092 | 100,00 % |

## Modelo de ejecución y costes

- **Ejecución** (§12.4.4): entrada a mercado en la apertura del primer minuto que empieza después de la decisión; stop a su nivel, o en la apertura si el minuto ya abre más allá; objetivo a su nivel, nunca mejor; salidas por cierre, por tiempo y por el final de la ventana, a mercado. Sin precio observado no hay ejecución.
- **Comisión** por lado, de tomador, la de cada instrumento leída de KuCoin (§12.5.3).
- **Deslizamiento:** 0,05 % por ejecución.
- **Prueba de esfuerzo:** comisión y deslizamiento por 2.
- **Sin funding** (§12.4.4).

| Instrumento | Comisión de tomador | Origen | Leída |
|---|---|---|---|
| FUTURES ASTERUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:21.947230+00:00 |
| FUTURES AVAXUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:24.377337+00:00 |
| FUTURES BCHUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:22.479850+00:00 |
| FUTURES ENAUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:24.898585+00:00 |
| FUTURES ETHUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:19.805981+00:00 |
| FUTURES EVAAUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:20.343720+00:00 |
| FUTURES FHEUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:24.632174+00:00 |
| FUTURES HYPEUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:21.416741+00:00 |
| FUTURES ICPUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:22.758312+00:00 |
| FUTURES INJUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:23.553698+00:00 |
| FUTURES LINKUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:24.099120+00:00 |
| FUTURES NIGHTUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:23.291410+00:00 |
| FUTURES PEPEUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:22.214554+00:00 |
| FUTURES PIEVERSEUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:21.677795+00:00 |
| FUTURES SOLUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:20.600501+00:00 |
| FUTURES SOXLUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:23.018067+00:00 |
| FUTURES UNIUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:23.840592+00:00 |
| FUTURES XBTUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:20.077845+00:00 |
| FUTURES XRPUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:21.142526+00:00 |
| FUTURES ZECUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T13:17:20.880879+00:00 |
| SPOT ASTER-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:16.260223+00:00 |
| SPOT AVAX-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:19.250093+00:00 |
| SPOT BNB-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:17.891037+00:00 |
| SPOT BTC-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:14.321043+00:00 |
| SPOT DOGE-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:18.160291+00:00 |
| SPOT DOT-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:18.700349+00:00 |
| SPOT ETH-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:14.592832+00:00 |
| SPOT EVAA-USDT | 0,3 % | classC 0.003 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:16.809403+00:00 |
| SPOT LTC-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:17.620811+00:00 |
| SPOT NEAR-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:17.077463+00:00 |
| SPOT PEPE-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:16.534319+00:00 |
| SPOT PIEVERSE-USDT | 0,2 % | classB 0.002 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:18.440131+00:00 |
| SPOT SOL-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:15.700459+00:00 |
| SPOT SUI-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:18.988072+00:00 |
| SPOT TAO-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:17.343716+00:00 |
| SPOT UNI-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:19.539136+00:00 |
| SPOT XLM-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:15.146382+00:00 |
| SPOT XMR-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:15.423773+00:00 |
| SPOT XRP-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:14.871379+00:00 |
| SPOT ZEC-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T13:17:15.979482+00:00 |

## Resultados

En R, por operación. **Aciertos:** operaciones con R neto > 0. **Caída máxima:** sobre la curva de R acumulado por minuto de salida, desde 0, con las salidas de un mismo minuto sumadas en un paso.

### En total

**Recuentos**

|  | Señales largas | Señales cortas | Decisiones | Ejecutadas | Operaciones | Caducadas | No ejecutadas |
|---|---|---|---|---|---|---|---|
| Total | 602 | 46 | 283 | 259 | 259 | 365 | 24 |

**Caducadas y no ejecutadas, por motivo**

|  | caduca_vela_no_utilizable | caduca_contexto | caduca_sin_disparo | ocupado | opuestas | fin_de_ventana | posicion_tomada | caduca_sin_minuto | no_entra |
|---|---|---|---|---|---|---|---|---|---|
| Total | 0 | 1 | 364 | 24 | 0 | 0 | 0 | 0 | 0 |

**R neto**

|  | Operaciones | Aciertos | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| Total | 259 | 80 | -0,079 | -0,717 | -20,359 | -1,676 | 1,964 | 28,018 |

**R bruto**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| Total | 259 | 80 | 0,075 | -0,595 | 19,364 | -1,030 | 2,000 | 15,032 |

**R neto con los costes dobles**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| Total | 259 | 80 | -0,232 | -0,857 | -60,082 | -2,353 | 1,927 | 62,516 |

**Salidas, por motivo**

|  | stop | objetivo | invalidacion | tiempo | liquidacion_fin_de_ventana |
|---|---|---|---|---|---|
| Total | 95 | 80 | 84 | 0 | 0 |

**Por régimen de la vela de contexto** (dirección/volatilidad, §12.3.2)

| Régimen | Operaciones | R bruto medio | R neto medio |
|---|---|---|---|
| tendencia/expansion | 17 | -0,329 | -0,443 |
| tendencia/compresion | 10 | -0,199 | -0,382 |
| tendencia/normal | 58 | -0,062 | -0,216 |
| tendencia/sin_etiqueta | 19 | 0,028 | -0,063 |
| rango/compresion | 57 | 0,468 | 0,278 |
| rango/normal | 42 | 0,062 | -0,119 |
| rango/sin_etiqueta | 15 | 0,156 | 0,083 |
| transicion/expansion | 1 | -0,439 | -0,519 |
| transicion/compresion | 8 | -0,522 | -0,660 |
| transicion/normal | 23 | 0,405 | 0,241 |
| transicion/sin_etiqueta | 9 | -0,702 | -0,789 |

**Por régimen de la vela de señal** (dirección/volatilidad, §12.3.2)

| Régimen | Operaciones | R bruto medio | R neto medio |
|---|---|---|---|
| tendencia/expansion | 14 | 0,159 | 0,042 |
| tendencia/compresion | 22 | 0,233 | 0,068 |
| tendencia/normal | 33 | -0,167 | -0,338 |
| rango/expansion | 8 | -0,496 | -0,637 |
| rango/compresion | 46 | -0,042 | -0,203 |
| rango/normal | 80 | 0,195 | 0,056 |
| transicion/expansion | 8 | -0,181 | -0,287 |
| transicion/compresion | 19 | -0,230 | -0,429 |
| transicion/normal | 29 | 0,471 | 0,314 |

### Por ventana

**Aviso: la familia solo gana en un tramo** (tramo 4): R neto sumado > 0.

**Recuentos**

|  | Señales largas | Señales cortas | Decisiones | Ejecutadas | Operaciones | Caducadas | No ejecutadas |
|---|---|---|---|---|---|---|---|
| tramo 1 | 137 | 5 | 62 | 54 | 54 | 80 | 8 |
| tramo 2 | 137 | 8 | 61 | 56 | 56 | 84 | 5 |
| tramo 3 | 212 | 12 | 101 | 92 | 92 | 123 | 9 |
| tramo 4 | 116 | 21 | 59 | 57 | 57 | 78 | 2 |

**Caducadas y no ejecutadas, por motivo**

|  | caduca_vela_no_utilizable | caduca_contexto | caduca_sin_disparo | ocupado | opuestas | fin_de_ventana | posicion_tomada | caduca_sin_minuto | no_entra |
|---|---|---|---|---|---|---|---|---|---|
| tramo 1 | 0 | 0 | 80 | 8 | 0 | 0 | 0 | 0 | 0 |
| tramo 2 | 0 | 1 | 83 | 5 | 0 | 0 | 0 | 0 | 0 |
| tramo 3 | 0 | 0 | 123 | 9 | 0 | 0 | 0 | 0 | 0 |
| tramo 4 | 0 | 0 | 78 | 2 | 0 | 0 | 0 | 0 | 0 |

**R neto**

|  | Operaciones | Aciertos | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| tramo 1 | 54 | 15 | -0,115 | -0,627 | -6,217 | -1,352 | 1,963 | 12,363 |
| tramo 2 | 56 | 16 | -0,154 | -0,778 | -8,648 | -1,310 | 1,945 | 17,047 |
| tramo 3 | 92 | 29 | -0,076 | -0,702 | -7,019 | -1,676 | 1,960 | 25,369 |
| tramo 4 | 57 | 20 | 0,027 | -0,714 | 1,526 | -1,430 | 1,964 | 18,150 |

**R bruto**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| tramo 1 | 54 | 15 | 0,010 | -0,519 | 0,530 | -1,000 | 2,000 | 8,497 |
| tramo 2 | 56 | 16 | 0,005 | -0,608 | 0,282 | -1,000 | 2,000 | 13,620 |
| tramo 3 | 92 | 29 | 0,092 | -0,582 | 8,454 | -1,000 | 2,000 | 14,163 |
| tramo 4 | 57 | 20 | 0,177 | -0,613 | 10,099 | -1,030 | 2,000 | 15,032 |

**R neto con los costes dobles**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| tramo 1 | 54 | 15 | -0,240 | -0,773 | -12,965 | -1,703 | 1,926 | 17,942 |
| tramo 2 | 56 | 16 | -0,314 | -0,990 | -17,578 | -1,620 | 1,890 | 23,443 |
| tramo 3 | 92 | 29 | -0,244 | -0,811 | -22,492 | -2,353 | 1,921 | 36,755 |
| tramo 4 | 57 | 20 | -0,124 | -0,814 | -7,048 | -1,860 | 1,927 | 23,510 |

**Salidas, por motivo**

|  | stop | objetivo | invalidacion | tiempo | liquidacion_fin_de_ventana |
|---|---|---|---|---|---|
| tramo 1 | 19 | 15 | 20 | 0 | 0 |
| tramo 2 | 23 | 16 | 17 | 0 | 0 |
| tramo 3 | 32 | 29 | 31 | 0 | 0 |
| tramo 4 | 21 | 20 | 16 | 0 | 0 |

### Por instrumento

**Recuentos**

|  | Señales largas | Señales cortas | Decisiones | Ejecutadas | Operaciones | Caducadas | No ejecutadas |
|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 2 | 3 | 2 | 2 | 2 | 3 | 0 |
| FUTURES AVAXUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES BCHUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ENAUSDTM | 1 | 0 | 0 | 0 | 0 | 1 | 0 |
| FUTURES ETHUSDTM | 5 | 9 | 5 | 5 | 5 | 9 | 0 |
| FUTURES EVAAUSDTM | 2 | 2 | 0 | 0 | 0 | 4 | 0 |
| FUTURES FHEUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES HYPEUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ICPUSDTM | 0 | 1 | 0 | 0 | 0 | 1 | 0 |
| FUTURES INJUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES LINKUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES NIGHTUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES PEPEUSDTM | 1 | 1 | 1 | 1 | 1 | 1 | 0 |
| FUTURES PIEVERSEUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES SOLUSDTM | 16 | 21 | 19 | 19 | 19 | 18 | 0 |
| FUTURES SOXLUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES UNIUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES XBTUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES XRPUSDTM | 5 | 4 | 5 | 5 | 5 | 4 | 0 |
| FUTURES ZECUSDTM | 3 | 5 | 4 | 4 | 4 | 4 | 0 |
| SPOT ASTER-USDT | 7 | 0 | 2 | 2 | 2 | 5 | 0 |
| SPOT AVAX-USDT | 40 | 0 | 19 | 16 | 16 | 21 | 3 |
| SPOT BNB-USDT | 43 | 0 | 18 | 16 | 16 | 25 | 2 |
| SPOT BTC-USDT | 44 | 0 | 18 | 15 | 15 | 26 | 3 |
| SPOT DOGE-USDT | 28 | 0 | 10 | 10 | 10 | 18 | 0 |
| SPOT DOT-USDT | 32 | 0 | 15 | 12 | 12 | 17 | 3 |
| SPOT ETH-USDT | 50 | 0 | 20 | 19 | 19 | 30 | 1 |
| SPOT EVAA-USDT | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT LTC-USDT | 28 | 0 | 13 | 12 | 12 | 15 | 1 |
| SPOT NEAR-USDT | 15 | 0 | 11 | 10 | 10 | 4 | 1 |
| SPOT PEPE-USDT | 30 | 0 | 11 | 11 | 11 | 19 | 0 |
| SPOT PIEVERSE-USDT | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT SOL-USDT | 34 | 0 | 12 | 12 | 12 | 22 | 0 |
| SPOT SUI-USDT | 29 | 0 | 17 | 16 | 16 | 12 | 1 |
| SPOT TAO-USDT | 23 | 0 | 9 | 7 | 7 | 14 | 2 |
| SPOT UNI-USDT | 27 | 0 | 6 | 6 | 6 | 21 | 0 |
| SPOT XLM-USDT | 30 | 0 | 15 | 12 | 12 | 15 | 3 |
| SPOT XMR-USDT | 33 | 0 | 15 | 14 | 14 | 18 | 1 |
| SPOT XRP-USDT | 34 | 0 | 16 | 14 | 14 | 18 | 2 |
| SPOT ZEC-USDT | 40 | 0 | 20 | 19 | 19 | 20 | 1 |

**Caducadas y no ejecutadas, por motivo**

|  | caduca_vela_no_utilizable | caduca_contexto | caduca_sin_disparo | ocupado | opuestas | fin_de_ventana | posicion_tomada | caduca_sin_minuto | no_entra |
|---|---|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES AVAXUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES BCHUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ENAUSDTM | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ETHUSDTM | 0 | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES EVAAUSDTM | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES FHEUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES HYPEUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ICPUSDTM | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES INJUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES LINKUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES NIGHTUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES PEPEUSDTM | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES PIEVERSEUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES SOLUSDTM | 0 | 0 | 18 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES SOXLUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES UNIUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES XBTUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES XRPUSDTM | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ZECUSDTM | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT ASTER-USDT | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT AVAX-USDT | 0 | 0 | 21 | 3 | 0 | 0 | 0 | 0 | 0 |
| SPOT BNB-USDT | 0 | 0 | 25 | 2 | 0 | 0 | 0 | 0 | 0 |
| SPOT BTC-USDT | 0 | 0 | 26 | 3 | 0 | 0 | 0 | 0 | 0 |
| SPOT DOGE-USDT | 0 | 0 | 18 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT DOT-USDT | 0 | 0 | 17 | 3 | 0 | 0 | 0 | 0 | 0 |
| SPOT ETH-USDT | 0 | 0 | 30 | 1 | 0 | 0 | 0 | 0 | 0 |
| SPOT EVAA-USDT | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT LTC-USDT | 0 | 0 | 15 | 1 | 0 | 0 | 0 | 0 | 0 |
| SPOT NEAR-USDT | 0 | 0 | 4 | 1 | 0 | 0 | 0 | 0 | 0 |
| SPOT PEPE-USDT | 0 | 0 | 19 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT PIEVERSE-USDT | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT SOL-USDT | 0 | 0 | 22 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT SUI-USDT | 0 | 0 | 12 | 1 | 0 | 0 | 0 | 0 | 0 |
| SPOT TAO-USDT | 0 | 0 | 14 | 2 | 0 | 0 | 0 | 0 | 0 |
| SPOT UNI-USDT | 0 | 0 | 21 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT XLM-USDT | 0 | 0 | 15 | 3 | 0 | 0 | 0 | 0 | 0 |
| SPOT XMR-USDT | 0 | 1 | 17 | 1 | 0 | 0 | 0 | 0 | 0 |
| SPOT XRP-USDT | 0 | 0 | 18 | 2 | 0 | 0 | 0 | 0 | 0 |
| SPOT ZEC-USDT | 0 | 0 | 20 | 1 | 0 | 0 | 0 | 0 | 0 |

**R neto**

|  | Operaciones | Aciertos | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 2 | 1 | 0,462 | 0,462 | 0,923 | -1,037 | 1,960 | 1,037 |
| FUTURES AVAXUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES BCHUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ENAUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ETHUSDTM | 5 | 0 | -0,804 | -0,715 | -4,018 | -1,090 | -0,565 | 4,018 |
| FUTURES EVAAUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES FHEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES HYPEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ICPUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES INJUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES LINKUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES NIGHTUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES PEPEUSDTM | 1 | 0 | -0,620 | -0,620 | -0,620 | -0,620 | -0,620 | 0,620 |
| FUTURES PIEVERSEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES SOLUSDTM | 19 | 5 | -0,035 | -0,567 | -0,666 | -1,149 | 1,942 | 5,875 |
| FUTURES SOXLUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES UNIUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES XBTUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES XRPUSDTM | 5 | 1 | -0,306 | -0,896 | -1,530 | -1,092 | 1,865 | 3,395 |
| FUTURES ZECUSDTM | 4 | 2 | 0,631 | 0,803 | 2,525 | -1,045 | 1,964 | 1,045 |
| SPOT ASTER-USDT | 2 | 1 | 0,432 | 0,432 | 0,864 | -1,040 | 1,905 | 1,040 |
| SPOT AVAX-USDT | 16 | 9 | 0,597 | 1,756 | 9,549 | -1,185 | 1,899 | 1,922 |
| SPOT BNB-USDT | 16 | 4 | -0,323 | -0,870 | -5,172 | -1,352 | 1,841 | 6,971 |
| SPOT BTC-USDT | 15 | 2 | -0,892 | -1,283 | -13,376 | -1,676 | 1,658 | 13,604 |
| SPOT DOGE-USDT | 10 | 7 | 0,993 | 1,795 | 9,929 | -1,123 | 1,880 | 1,123 |
| SPOT DOT-USDT | 12 | 3 | -0,251 | -0,899 | -3,017 | -1,225 | 1,885 | 4,534 |
| SPOT ETH-USDT | 19 | 4 | -0,532 | -1,171 | -10,105 | -1,319 | 1,854 | 10,105 |
| SPOT EVAA-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT LTC-USDT | 12 | 4 | -0,007 | -0,715 | -0,087 | -1,180 | 1,837 | 3,936 |
| SPOT NEAR-USDT | 10 | 3 | -0,121 | -0,901 | -1,212 | -1,119 | 1,895 | 3,225 |
| SPOT PEPE-USDT | 11 | 3 | -0,220 | -1,058 | -2,423 | -1,129 | 1,880 | 3,146 |
| SPOT PIEVERSE-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT SOL-USDT | 12 | 3 | -0,199 | -0,724 | -2,392 | -1,205 | 1,909 | 4,959 |
| SPOT SUI-USDT | 16 | 7 | 0,218 | -0,969 | 3,484 | -1,172 | 1,945 | 6,730 |
| SPOT TAO-USDT | 7 | 4 | 0,618 | 1,875 | 4,324 | -1,117 | 1,932 | 2,185 |
| SPOT UNI-USDT | 6 | 1 | -0,263 | -0,539 | -1,580 | -1,090 | 1,841 | 3,421 |
| SPOT XLM-USDT | 12 | 1 | -0,616 | -0,809 | -7,396 | -1,333 | 1,963 | 9,359 |
| SPOT XMR-USDT | 14 | 2 | -0,311 | -0,500 | -4,357 | -1,134 | 1,917 | 7,569 |
| SPOT XRP-USDT | 14 | 3 | -0,391 | -1,018 | -5,470 | -1,270 | 1,926 | 9,310 |
| SPOT ZEC-USDT | 19 | 10 | 0,603 | 1,759 | 11,464 | -1,158 | 1,959 | 2,584 |

**R bruto**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 2 | 1 | 0,500 | 0,500 | 1,000 | -1,000 | 2,000 | 1,000 |
| FUTURES AVAXUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES BCHUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ENAUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ETHUSDTM | 5 | 0 | -0,716 | -0,652 | -3,581 | -1,000 | -0,490 | 3,581 |
| FUTURES EVAAUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES FHEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES HYPEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ICPUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES INJUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES LINKUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES NIGHTUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES PEPEUSDTM | 1 | 0 | -0,569 | -0,569 | -0,569 | -0,569 | -0,569 | 0,569 |
| FUTURES PIEVERSEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES SOLUSDTM | 19 | 5 | 0,066 | -0,510 | 1,247 | -1,000 | 2,000 | 4,705 |
| FUTURES SOXLUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES UNIUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES XBTUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES XRPUSDTM | 5 | 1 | -0,213 | -0,812 | -1,065 | -1,000 | 2,000 | 3,065 |
| FUTURES ZECUSDTM | 4 | 2 | 0,667 | 0,835 | 2,670 | -1,000 | 2,000 | 1,000 |
| SPOT ASTER-USDT | 2 | 1 | 0,500 | 0,500 | 1,000 | -1,000 | 2,000 | 1,000 |
| SPOT AVAX-USDT | 16 | 9 | 0,747 | 2,000 | 11,946 | -1,000 | 2,000 | 1,611 |
| SPOT BNB-USDT | 16 | 4 | -0,069 | -0,618 | -1,107 | -1,000 | 2,000 | 5,120 |
| SPOT BTC-USDT | 15 | 2 | -0,490 | -1,000 | -7,356 | -1,000 | 2,000 | 8,356 |
| SPOT DOGE-USDT | 10 | 7 | 1,149 | 2,000 | 11,488 | -1,000 | 2,000 | 1,000 |
| SPOT DOT-USDT | 12 | 3 | -0,120 | -0,788 | -1,435 | -1,000 | 2,000 | 3,435 |
| SPOT ETH-USDT | 19 | 4 | -0,327 | -1,000 | -6,214 | -1,000 | 2,000 | 6,214 |
| SPOT EVAA-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT LTC-USDT | 12 | 4 | 0,169 | -0,562 | 2,032 | -1,000 | 2,000 | 3,159 |
| SPOT NEAR-USDT | 10 | 3 | 0,004 | -0,766 | 0,038 | -1,000 | 2,000 | 2,908 |
| SPOT PEPE-USDT | 11 | 3 | -0,120 | -1,000 | -1,321 | -1,000 | 2,000 | 2,744 |
| SPOT PIEVERSE-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT SOL-USDT | 12 | 3 | -0,048 | -0,552 | -0,580 | -1,000 | 2,000 | 4,104 |
| SPOT SUI-USDT | 16 | 7 | 0,326 | -0,889 | 5,223 | -1,000 | 2,000 | 6,000 |
| SPOT TAO-USDT | 7 | 4 | 0,714 | 2,000 | 5,000 | -1,000 | 2,000 | 2,000 |
| SPOT UNI-USDT | 6 | 1 | -0,148 | -0,411 | -0,889 | -1,000 | 2,000 | 2,889 |
| SPOT XLM-USDT | 12 | 1 | -0,491 | -0,715 | -5,891 | -1,030 | 2,000 | 7,891 |
| SPOT XMR-USDT | 14 | 2 | -0,181 | -0,382 | -2,535 | -1,000 | 2,000 | 6,096 |
| SPOT XRP-USDT | 14 | 3 | -0,231 | -0,829 | -3,230 | -1,000 | 2,000 | 7,230 |
| SPOT ZEC-USDT | 19 | 10 | 0,710 | 2,000 | 13,494 | -1,000 | 2,000 | 2,220 |

**R neto con los costes dobles**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 2 | 1 | 0,423 | 0,423 | 0,846 | -1,074 | 1,921 | 1,074 |
| FUTURES AVAXUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES BCHUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ENAUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ETHUSDTM | 5 | 0 | -0,891 | -0,778 | -4,454 | -1,179 | -0,640 | 4,454 |
| FUTURES EVAAUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES FHEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES HYPEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ICPUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES INJUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES LINKUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES NIGHTUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES PEPEUSDTM | 1 | 0 | -0,670 | -0,670 | -0,670 | -0,670 | -0,670 | 0,670 |
| FUTURES PIEVERSEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES SOLUSDTM | 19 | 5 | -0,136 | -0,634 | -2,580 | -1,298 | 1,884 | 7,046 |
| FUTURES SOXLUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES UNIUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES XBTUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES XRPUSDTM | 5 | 1 | -0,399 | -0,972 | -1,995 | -1,183 | 1,730 | 3,724 |
| FUTURES ZECUSDTM | 4 | 2 | 0,595 | 0,771 | 2,379 | -1,090 | 1,927 | 1,090 |
| SPOT ASTER-USDT | 2 | 1 | 0,364 | 0,364 | 0,729 | -1,081 | 1,810 | 1,081 |
| SPOT AVAX-USDT | 16 | 9 | 0,447 | 1,513 | 7,152 | -1,371 | 1,798 | 2,242 |
| SPOT BNB-USDT | 16 | 4 | -0,577 | -1,151 | -9,237 | -1,703 | 1,681 | 9,936 |
| SPOT BTC-USDT | 15 | 2 | -1,293 | -1,577 | -19,396 | -2,353 | 1,316 | 19,396 |
| SPOT DOGE-USDT | 10 | 7 | 0,837 | 1,590 | 8,370 | -1,245 | 1,760 | 1,245 |
| SPOT DOT-USDT | 12 | 3 | -0,383 | -1,035 | -4,599 | -1,451 | 1,771 | 5,633 |
| SPOT ETH-USDT | 19 | 4 | -0,737 | -1,341 | -13,995 | -1,638 | 1,709 | 13,995 |
| SPOT EVAA-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT LTC-USDT | 12 | 4 | -0,184 | -0,829 | -2,206 | -1,360 | 1,674 | 5,299 |
| SPOT NEAR-USDT | 10 | 3 | -0,246 | -0,990 | -2,463 | -1,242 | 1,791 | 3,542 |
| SPOT PEPE-USDT | 11 | 3 | -0,321 | -1,116 | -3,526 | -1,258 | 1,760 | 3,714 |
| SPOT PIEVERSE-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT SOL-USDT | 12 | 3 | -0,350 | -0,895 | -4,204 | -1,410 | 1,819 | 5,814 |
| SPOT SUI-USDT | 16 | 7 | 0,109 | -1,049 | 1,746 | -1,344 | 1,890 | 7,461 |
| SPOT TAO-USDT | 7 | 4 | 0,521 | 1,749 | 3,648 | -1,234 | 1,864 | 2,371 |
| SPOT UNI-USDT | 6 | 1 | -0,379 | -0,679 | -2,271 | -1,179 | 1,682 | 3,954 |
| SPOT XLM-USDT | 12 | 1 | -0,742 | -0,903 | -8,901 | -1,636 | 1,926 | 10,827 |
| SPOT XMR-USDT | 14 | 2 | -0,441 | -0,642 | -6,180 | -1,268 | 1,834 | 9,042 |
| SPOT XRP-USDT | 14 | 3 | -0,551 | -1,183 | -7,710 | -1,541 | 1,852 | 11,390 |
| SPOT ZEC-USDT | 19 | 10 | 0,497 | 1,519 | 9,435 | -1,315 | 1,918 | 2,951 |

**Salidas, por motivo**

|  | stop | objetivo | invalidacion | tiempo | liquidacion_fin_de_ventana |
|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 1 | 1 | 0 | 0 | 0 |
| FUTURES AVAXUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES BCHUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES ENAUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES ETHUSDTM | 1 | 0 | 4 | 0 | 0 |
| FUTURES EVAAUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES FHEUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES HYPEUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES ICPUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES INJUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES LINKUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES NIGHTUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES PEPEUSDTM | 0 | 0 | 1 | 0 | 0 |
| FUTURES PIEVERSEUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES SOLUSDTM | 4 | 5 | 10 | 0 | 0 |
| FUTURES SOXLUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES UNIUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES XBTUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES XRPUSDTM | 1 | 1 | 3 | 0 | 0 |
| FUTURES ZECUSDTM | 1 | 2 | 1 | 0 | 0 |
| SPOT ASTER-USDT | 1 | 1 | 0 | 0 | 0 |
| SPOT AVAX-USDT | 4 | 9 | 3 | 0 | 0 |
| SPOT BNB-USDT | 6 | 4 | 6 | 0 | 0 |
| SPOT BTC-USDT | 10 | 2 | 3 | 0 | 0 |
| SPOT DOGE-USDT | 2 | 7 | 1 | 0 | 0 |
| SPOT DOT-USDT | 6 | 3 | 3 | 0 | 0 |
| SPOT ETH-USDT | 13 | 4 | 2 | 0 | 0 |
| SPOT EVAA-USDT | 0 | 0 | 0 | 0 | 0 |
| SPOT LTC-USDT | 3 | 4 | 5 | 0 | 0 |
| SPOT NEAR-USDT | 4 | 3 | 3 | 0 | 0 |
| SPOT PEPE-USDT | 6 | 3 | 2 | 0 | 0 |
| SPOT PIEVERSE-USDT | 0 | 0 | 0 | 0 | 0 |
| SPOT SOL-USDT | 4 | 3 | 5 | 0 | 0 |
| SPOT SUI-USDT | 8 | 7 | 1 | 0 | 0 |
| SPOT TAO-USDT | 3 | 4 | 0 | 0 | 0 |
| SPOT UNI-USDT | 1 | 1 | 4 | 0 | 0 |
| SPOT XLM-USDT | 3 | 1 | 8 | 0 | 0 |
| SPOT XMR-USDT | 3 | 2 | 9 | 0 | 0 |
| SPOT XRP-USDT | 6 | 3 | 5 | 0 | 0 |
| SPOT ZEC-USDT | 4 | 10 | 5 | 0 | 0 |

El régimen por ventana y por instrumento está en el JSON.

## Datos para D15

Los resultados por volatilidad servirán, si procede, para proponer el filtro de volatilidad extrema (D15), que decide el propietario.

## Registro completo

Las señales, las decisiones y las operaciones de cada ejecución están en el PC, fuera de Git: `C:/kbot_historico/informes/breakout_desarrollo_3490a1c_eac620fee86f`.
