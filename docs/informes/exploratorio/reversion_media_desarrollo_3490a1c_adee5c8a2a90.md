Este informe no es la validación de la estrategia (D16, F8): sirve para ver si la familia merece seguir.

# Informe exploratorio de «reversion_media» en la partición «desarrollo»

## Familia, versión y ficha

- **Familia:** reversion_media, con la ficha de `02` §12.3.6, aprobada.
- **Versión:** `3490a1cd7318290cbc1978f26fae7988c3ab0c1b+adee5c8a2a900e797bf62abbc22aef3f441de36ffd79e4425f72a7087bad63d1`.
  - commit `3490a1cd7318290cbc1978f26fae7988c3ab0c1b`;
  - hash de los parámetros `adee5c8a2a900e797bf62abbc22aef3f441de36ffd79e4425f72a7087bad63d1`.
- **Parámetros** (§12.3.3):

```json
{
 "familia": "reversion_media",
 "temporalidades": {
  "contexto": "1h",
  "senal": "15m",
  "ejecucion": "5m"
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
  "bollinger": 20,
  "bollinger_k": "2"
 },
 "senal": {
  "bollinger": 20,
  "bollinger_k": "2",
  "rsi": 14,
  "rsi_largo": "30",
  "rsi_corto": "70",
  "atr": 14,
  "distancia_vwap_atr": "1"
 },
 "ejecucion": {
  "velas": 3
 },
 "stop": {
  "multiplo_atr": "2"
 },
 "salida_tiempo_minutos": 240
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
| decisiones | 69529190 | 69529190 | 100,00 % |
| resultados | 69612002 | 69612002 | 100,00 % |

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
| Total | 2657 | 309 | 874 | 832 | 832 | 2092 | 42 |

**Caducadas y no ejecutadas, por motivo**

|  | caduca_vela_no_utilizable | caduca_contexto | caduca_sin_disparo | ocupado | opuestas | fin_de_ventana | posicion_tomada | caduca_sin_minuto | no_entra |
|---|---|---|---|---|---|---|---|---|---|
| Total | 0 | 693 | 1399 | 41 | 0 | 0 | 0 | 0 | 1 |

**R neto**

|  | Operaciones | Aciertos | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| Total | 832 | 316 | -0,240 | -0,320 | -199,889 | -4,853 | 4,327 | 202,762 |

**R bruto**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| Total | 832 | 395 | 0,057 | -0,052 | 47,554 | -1,051 | 6,238 | 44,282 |

**R neto con los costes dobles**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| Total | 832 | 262 | -0,538 | -0,567 | -447,331 | -8,707 | 2,417 | 449,220 |

**Salidas, por motivo**

|  | stop | objetivo | invalidacion | tiempo | liquidacion_fin_de_ventana |
|---|---|---|---|---|---|
| Total | 234 | 250 | 203 | 144 | 1 |

**Por régimen de la vela de contexto** (dirección/volatilidad, §12.3.2)

| Régimen | Operaciones | R bruto medio | R neto medio |
|---|---|---|---|
| rango/expansion | 80 | -0,265 | -0,483 |
| rango/compresion | 222 | 0,092 | -0,285 |
| rango/normal | 422 | 0,098 | -0,203 |
| rango/sin_etiqueta | 108 | 0,063 | -0,113 |

**Por régimen de la vela de señal** (dirección/volatilidad, §12.3.2)

| Régimen | Operaciones | R bruto medio | R neto medio |
|---|---|---|---|
| tendencia/expansion | 123 | 0,206 | -0,016 |
| tendencia/compresion | 17 | -0,069 | -0,372 |
| tendencia/normal | 338 | 0,028 | -0,291 |
| rango/expansion | 29 | 0,003 | -0,178 |
| rango/compresion | 3 | -0,858 | -2,345 |
| rango/normal | 106 | 0,100 | -0,207 |
| rango/sin_etiqueta | 1 | 0,909 | 0,727 |
| transicion/expansion | 52 | 0,037 | -0,189 |
| transicion/compresion | 4 | -0,057 | -0,256 |
| transicion/normal | 159 | 0,020 | -0,308 |

### Por ventana

**La familia no gana en ningún tramo** (R neto sumado ≤ 0 en los cuatro).

**Recuentos**

|  | Señales largas | Señales cortas | Decisiones | Ejecutadas | Operaciones | Caducadas | No ejecutadas |
|---|---|---|---|---|---|---|---|
| tramo 1 | 699 | 44 | 245 | 234 | 234 | 498 | 11 |
| tramo 2 | 557 | 50 | 191 | 177 | 177 | 416 | 14 |
| tramo 3 | 592 | 73 | 190 | 185 | 185 | 475 | 5 |
| tramo 4 | 809 | 142 | 248 | 236 | 236 | 703 | 12 |

**Caducadas y no ejecutadas, por motivo**

|  | caduca_vela_no_utilizable | caduca_contexto | caduca_sin_disparo | ocupado | opuestas | fin_de_ventana | posicion_tomada | caduca_sin_minuto | no_entra |
|---|---|---|---|---|---|---|---|---|---|
| tramo 1 | 0 | 172 | 326 | 11 | 0 | 0 | 0 | 0 | 0 |
| tramo 2 | 0 | 150 | 266 | 14 | 0 | 0 | 0 | 0 | 0 |
| tramo 3 | 0 | 143 | 332 | 5 | 0 | 0 | 0 | 0 | 0 |
| tramo 4 | 0 | 228 | 475 | 11 | 0 | 0 | 0 | 0 | 1 |

**R neto**

|  | Operaciones | Aciertos | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| tramo 1 | 234 | 90 | -0,220 | -0,298 | -51,483 | -1,957 | 4,327 | 55,336 |
| tramo 2 | 177 | 68 | -0,202 | -0,393 | -35,745 | -1,806 | 2,482 | 52,707 |
| tramo 3 | 185 | 57 | -0,430 | -0,564 | -79,532 | -2,145 | 1,586 | 80,606 |
| tramo 4 | 236 | 101 | -0,140 | -0,134 | -33,128 | -4,853 | 2,420 | 33,656 |

**R bruto**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| tramo 1 | 234 | 111 | 0,024 | -0,058 | 5,731 | -1,004 | 6,238 | 26,783 |
| tramo 2 | 177 | 85 | 0,129 | -0,075 | 22,833 | -1,051 | 3,081 | 18,223 |
| tramo 3 | 185 | 69 | -0,103 | -0,204 | -19,058 | -1,004 | 1,860 | 27,973 |
| tramo 4 | 236 | 130 | 0,161 | 0,067 | 38,048 | -1,000 | 2,668 | 9,636 |

**R neto con los costes dobles**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| tramo 1 | 234 | 78 | -0,465 | -0,477 | -108,697 | -2,914 | 2,417 | 110,428 |
| tramo 2 | 177 | 59 | -0,533 | -0,679 | -94,323 | -2,972 | 1,884 | 101,453 |
| tramo 3 | 185 | 45 | -0,757 | -0,841 | -140,007 | -3,290 | 1,356 | 141,029 |
| tramo 4 | 236 | 80 | -0,442 | -0,411 | -104,305 | -8,707 | 2,173 | 104,463 |

**Salidas, por motivo**

|  | stop | objetivo | invalidacion | tiempo | liquidacion_fin_de_ventana |
|---|---|---|---|---|---|
| tramo 1 | 65 | 61 | 55 | 52 | 1 |
| tramo 2 | 48 | 59 | 40 | 30 | 0 |
| tramo 3 | 66 | 48 | 49 | 22 | 0 |
| tramo 4 | 55 | 82 | 59 | 40 | 0 |

### Por instrumento

**Recuentos**

|  | Señales largas | Señales cortas | Decisiones | Ejecutadas | Operaciones | Caducadas | No ejecutadas |
|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 8 | 11 | 9 | 9 | 9 | 10 | 0 |
| FUTURES AVAXUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES BCHUSDTM | 0 | 4 | 1 | 1 | 1 | 3 | 0 |
| FUTURES ENAUSDTM | 2 | 1 | 0 | 0 | 0 | 3 | 0 |
| FUTURES ETHUSDTM | 61 | 43 | 28 | 27 | 27 | 76 | 1 |
| FUTURES EVAAUSDTM | 23 | 21 | 14 | 14 | 14 | 30 | 0 |
| FUTURES FHEUSDTM | 0 | 5 | 1 | 1 | 1 | 4 | 0 |
| FUTURES HYPEUSDTM | 14 | 18 | 13 | 12 | 12 | 19 | 1 |
| FUTURES ICPUSDTM | 11 | 6 | 11 | 9 | 9 | 6 | 2 |
| FUTURES INJUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES LINKUSDTM | 13 | 6 | 6 | 6 | 6 | 13 | 0 |
| FUTURES NIGHTUSDTM | 1 | 3 | 1 | 1 | 1 | 3 | 0 |
| FUTURES PEPEUSDTM | 30 | 27 | 18 | 18 | 18 | 39 | 0 |
| FUTURES PIEVERSEUSDTM | 0 | 10 | 3 | 3 | 3 | 7 | 0 |
| FUTURES SOLUSDTM | 85 | 76 | 40 | 39 | 39 | 121 | 1 |
| FUTURES SOXLUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES UNIUSDTM | 2 | 0 | 1 | 1 | 1 | 1 | 0 |
| FUTURES XBTUSDTM | 38 | 21 | 21 | 17 | 17 | 38 | 4 |
| FUTURES XRPUSDTM | 62 | 35 | 33 | 31 | 31 | 64 | 2 |
| FUTURES ZECUSDTM | 18 | 22 | 16 | 14 | 14 | 24 | 2 |
| SPOT ASTER-USDT | 49 | 0 | 11 | 11 | 11 | 38 | 0 |
| SPOT AVAX-USDT | 160 | 0 | 45 | 43 | 43 | 115 | 2 |
| SPOT BNB-USDT | 152 | 0 | 48 | 44 | 44 | 104 | 4 |
| SPOT BTC-USDT | 130 | 0 | 38 | 37 | 37 | 92 | 1 |
| SPOT DOGE-USDT | 131 | 0 | 33 | 32 | 32 | 98 | 1 |
| SPOT DOT-USDT | 149 | 0 | 42 | 39 | 39 | 107 | 3 |
| SPOT ETH-USDT | 123 | 0 | 35 | 33 | 33 | 88 | 2 |
| SPOT EVAA-USDT | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT LTC-USDT | 139 | 0 | 39 | 38 | 38 | 100 | 1 |
| SPOT NEAR-USDT | 164 | 0 | 49 | 49 | 49 | 115 | 0 |
| SPOT PEPE-USDT | 121 | 0 | 35 | 35 | 35 | 86 | 0 |
| SPOT PIEVERSE-USDT | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT SOL-USDT | 135 | 0 | 33 | 30 | 30 | 102 | 3 |
| SPOT SUI-USDT | 98 | 0 | 24 | 23 | 23 | 74 | 1 |
| SPOT TAO-USDT | 140 | 0 | 43 | 41 | 41 | 97 | 2 |
| SPOT UNI-USDT | 120 | 0 | 31 | 30 | 30 | 89 | 1 |
| SPOT XLM-USDT | 143 | 0 | 54 | 53 | 53 | 89 | 1 |
| SPOT XMR-USDT | 105 | 0 | 36 | 34 | 34 | 69 | 2 |
| SPOT XRP-USDT | 135 | 0 | 36 | 33 | 33 | 99 | 3 |
| SPOT ZEC-USDT | 95 | 0 | 26 | 24 | 24 | 69 | 2 |

**Caducadas y no ejecutadas, por motivo**

|  | caduca_vela_no_utilizable | caduca_contexto | caduca_sin_disparo | ocupado | opuestas | fin_de_ventana | posicion_tomada | caduca_sin_minuto | no_entra |
|---|---|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 0 | 1 | 9 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES AVAXUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES BCHUSDTM | 0 | 1 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ENAUSDTM | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ETHUSDTM | 0 | 31 | 45 | 1 | 0 | 0 | 0 | 0 | 0 |
| FUTURES EVAAUSDTM | 0 | 12 | 18 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES FHEUSDTM | 0 | 1 | 3 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES HYPEUSDTM | 0 | 9 | 10 | 1 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ICPUSDTM | 0 | 0 | 6 | 2 | 0 | 0 | 0 | 0 | 0 |
| FUTURES INJUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES LINKUSDTM | 0 | 5 | 8 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES NIGHTUSDTM | 0 | 1 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES PEPEUSDTM | 0 | 11 | 28 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES PIEVERSEUSDTM | 0 | 2 | 5 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES SOLUSDTM | 0 | 43 | 78 | 1 | 0 | 0 | 0 | 0 | 0 |
| FUTURES SOXLUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES UNIUSDTM | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES XBTUSDTM | 0 | 10 | 28 | 4 | 0 | 0 | 0 | 0 | 0 |
| FUTURES XRPUSDTM | 0 | 19 | 45 | 2 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ZECUSDTM | 0 | 8 | 16 | 2 | 0 | 0 | 0 | 0 | 0 |
| SPOT ASTER-USDT | 0 | 16 | 22 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT AVAX-USDT | 0 | 35 | 80 | 2 | 0 | 0 | 0 | 0 | 0 |
| SPOT BNB-USDT | 0 | 37 | 67 | 4 | 0 | 0 | 0 | 0 | 0 |
| SPOT BTC-USDT | 0 | 29 | 63 | 1 | 0 | 0 | 0 | 0 | 0 |
| SPOT DOGE-USDT | 0 | 33 | 65 | 1 | 0 | 0 | 0 | 0 | 0 |
| SPOT DOT-USDT | 0 | 39 | 68 | 3 | 0 | 0 | 0 | 0 | 0 |
| SPOT ETH-USDT | 0 | 29 | 59 | 2 | 0 | 0 | 0 | 0 | 0 |
| SPOT EVAA-USDT | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT LTC-USDT | 0 | 37 | 63 | 1 | 0 | 0 | 0 | 0 | 0 |
| SPOT NEAR-USDT | 0 | 36 | 79 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT PEPE-USDT | 0 | 26 | 60 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT PIEVERSE-USDT | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT SOL-USDT | 0 | 35 | 67 | 3 | 0 | 0 | 0 | 0 | 0 |
| SPOT SUI-USDT | 0 | 23 | 51 | 1 | 0 | 0 | 0 | 0 | 0 |
| SPOT TAO-USDT | 0 | 28 | 69 | 2 | 0 | 0 | 0 | 0 | 0 |
| SPOT UNI-USDT | 0 | 24 | 65 | 1 | 0 | 0 | 0 | 0 | 0 |
| SPOT XLM-USDT | 0 | 29 | 60 | 1 | 0 | 0 | 0 | 0 | 0 |
| SPOT XMR-USDT | 0 | 27 | 42 | 2 | 0 | 0 | 0 | 0 | 0 |
| SPOT XRP-USDT | 0 | 32 | 67 | 3 | 0 | 0 | 0 | 0 | 0 |
| SPOT ZEC-USDT | 0 | 23 | 46 | 1 | 0 | 0 | 0 | 0 | 1 |

**R neto**

|  | Operaciones | Aciertos | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 9 | 2 | -0,292 | -0,137 | -2,625 | -1,091 | 1,014 | 2,625 |
| FUTURES AVAXUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES BCHUSDTM | 1 | 1 | 0,131 | 0,131 | 0,131 | 0,131 | 0,131 | 0,000 |
| FUTURES ENAUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ETHUSDTM | 27 | 9 | -0,323 | -0,557 | -8,714 | -1,292 | 1,988 | 10,139 |
| FUTURES EVAAUSDTM | 14 | 7 | 0,048 | 0,189 | 0,668 | -1,081 | 1,599 | 2,158 |
| FUTURES FHEUSDTM | 1 | 0 | -0,021 | -0,021 | -0,021 | -0,021 | -0,021 | 0,021 |
| FUTURES HYPEUSDTM | 12 | 6 | 0,053 | 0,236 | 0,636 | -1,195 | 1,248 | 3,467 |
| FUTURES ICPUSDTM | 9 | 5 | -0,214 | 0,173 | -1,924 | -1,125 | 0,621 | 3,143 |
| FUTURES INJUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES LINKUSDTM | 6 | 3 | 0,351 | 0,336 | 2,105 | -0,830 | 1,400 | 1,168 |
| FUTURES NIGHTUSDTM | 1 | 1 | 1,002 | 1,002 | 1,002 | 1,002 | 1,002 | 0,000 |
| FUTURES PEPEUSDTM | 18 | 9 | 0,045 | 0,002 | 0,814 | -1,269 | 1,466 | 2,466 |
| FUTURES PIEVERSEUSDTM | 3 | 0 | -1,036 | -1,034 | -3,108 | -1,043 | -1,030 | 3,108 |
| FUTURES SOLUSDTM | 39 | 20 | -0,084 | 0,037 | -3,270 | -1,330 | 1,586 | 7,324 |
| FUTURES SOXLUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES UNIUSDTM | 1 | 1 | 1,243 | 1,243 | 1,243 | 1,243 | 1,243 | 0,000 |
| FUTURES XBTUSDTM | 17 | 5 | -0,345 | -0,777 | -5,863 | -1,526 | 1,933 | 7,239 |
| FUTURES XRPUSDTM | 31 | 15 | 0,043 | -0,077 | 1,322 | -1,232 | 1,979 | 3,833 |
| FUTURES ZECUSDTM | 14 | 5 | -0,029 | -0,045 | -0,409 | -1,063 | 1,642 | 3,842 |
| SPOT ASTER-USDT | 11 | 4 | -0,008 | -0,191 | -0,084 | -1,131 | 1,119 | 2,058 |
| SPOT AVAX-USDT | 43 | 11 | -0,596 | -1,096 | -25,622 | -1,772 | 1,476 | 26,860 |
| SPOT BNB-USDT | 44 | 11 | -0,595 | -0,804 | -26,181 | -1,935 | 2,482 | 26,181 |
| SPOT BTC-USDT | 37 | 15 | -0,408 | -0,336 | -15,106 | -4,853 | 4,327 | 17,366 |
| SPOT DOGE-USDT | 32 | 16 | -0,164 | -0,035 | -5,261 | -1,387 | 1,307 | 6,456 |
| SPOT DOT-USDT | 39 | 16 | -0,206 | -0,553 | -8,015 | -1,507 | 1,298 | 13,645 |
| SPOT ETH-USDT | 33 | 8 | -0,442 | -0,577 | -14,579 | -1,457 | 1,908 | 16,121 |
| SPOT EVAA-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT LTC-USDT | 38 | 13 | -0,316 | -0,370 | -12,017 | -1,649 | 1,928 | 13,003 |
| SPOT NEAR-USDT | 49 | 19 | -0,206 | -0,234 | -10,078 | -1,518 | 1,921 | 13,963 |
| SPOT PEPE-USDT | 35 | 17 | -0,135 | -0,032 | -4,732 | -1,531 | 1,480 | 9,142 |
| SPOT PIEVERSE-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT SOL-USDT | 30 | 11 | -0,261 | -0,181 | -7,816 | -1,724 | 1,536 | 9,307 |
| SPOT SUI-USDT | 23 | 8 | -0,200 | -0,226 | -4,596 | -1,646 | 1,817 | 5,923 |
| SPOT TAO-USDT | 41 | 9 | -0,348 | -0,436 | -14,258 | -1,497 | 2,420 | 14,458 |
| SPOT UNI-USDT | 30 | 10 | -0,185 | -0,304 | -5,539 | -1,358 | 1,908 | 9,109 |
| SPOT XLM-USDT | 53 | 23 | -0,234 | -0,313 | -12,420 | -1,863 | 1,880 | 15,385 |
| SPOT XMR-USDT | 34 | 12 | -0,162 | -0,263 | -5,518 | -1,438 | 1,139 | 6,038 |
| SPOT XRP-USDT | 33 | 13 | -0,194 | -0,252 | -6,394 | -1,622 | 1,934 | 6,427 |
| SPOT ZEC-USDT | 24 | 11 | -0,152 | -0,246 | -3,660 | -1,353 | 1,501 | 6,914 |

**R bruto**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 9 | 2 | -0,225 | -0,091 | -2,023 | -1,000 | 1,105 | 2,128 |
| FUTURES AVAXUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES BCHUSDTM | 1 | 1 | 0,278 | 0,278 | 0,278 | 0,278 | 0,278 | 0,000 |
| FUTURES ENAUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ETHUSDTM | 27 | 10 | -0,116 | -0,350 | -3,134 | -1,000 | 2,211 | 5,935 |
| FUTURES EVAAUSDTM | 14 | 7 | 0,113 | 0,272 | 1,583 | -1,000 | 1,631 | 2,000 |
| FUTURES FHEUSDTM | 1 | 1 | 0,012 | 0,012 | 0,012 | 0,012 | 0,012 | 0,000 |
| FUTURES HYPEUSDTM | 12 | 6 | 0,195 | 0,344 | 2,340 | -1,000 | 1,437 | 3,000 |
| FUTURES ICPUSDTM | 9 | 5 | -0,128 | 0,244 | -1,153 | -1,000 | 0,662 | 2,569 |
| FUTURES INJUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES LINKUSDTM | 6 | 3 | 0,476 | 0,448 | 2,854 | -0,753 | 1,557 | 1,016 |
| FUTURES NIGHTUSDTM | 1 | 1 | 1,052 | 1,052 | 1,052 | 1,052 | 1,052 | 0,000 |
| FUTURES PEPEUSDTM | 18 | 11 | 0,197 | 0,117 | 3,552 | -1,000 | 1,664 | 2,000 |
| FUTURES PIEVERSEUSDTM | 3 | 0 | -1,000 | -1,000 | -3,000 | -1,000 | -1,000 | 3,000 |
| FUTURES SOLUSDTM | 39 | 23 | 0,144 | 0,215 | 5,632 | -1,000 | 1,816 | 3,017 |
| FUTURES SOXLUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES UNIUSDTM | 1 | 1 | 1,369 | 1,369 | 1,369 | 1,369 | 1,369 | 0,000 |
| FUTURES XBTUSDTM | 17 | 6 | 0,013 | -0,386 | 0,229 | -1,000 | 2,195 | 3,736 |
| FUTURES XRPUSDTM | 31 | 18 | 0,220 | 0,138 | 6,831 | -1,000 | 2,203 | 1,869 |
| FUTURES ZECUSDTM | 14 | 8 | 0,033 | 0,023 | 0,458 | -1,000 | 1,720 | 3,506 |
| SPOT ASTER-USDT | 11 | 5 | 0,207 | -0,026 | 2,274 | -1,000 | 1,396 | 1,290 |
| SPOT AVAX-USDT | 43 | 12 | -0,283 | -0,757 | -12,179 | -1,000 | 1,997 | 14,521 |
| SPOT BNB-USDT | 44 | 17 | -0,060 | -0,242 | -2,638 | -1,000 | 3,081 | 6,992 |
| SPOT BTC-USDT | 37 | 21 | 0,424 | 0,227 | 15,699 | -1,000 | 6,238 | 3,567 |
| SPOT DOGE-USDT | 32 | 18 | 0,115 | 0,183 | 3,694 | -1,000 | 1,520 | 3,563 |
| SPOT DOT-USDT | 39 | 18 | 0,078 | -0,081 | 3,032 | -1,012 | 1,509 | 7,375 |
| SPOT ETH-USDT | 33 | 14 | -0,044 | -0,141 | -1,468 | -1,000 | 2,216 | 7,652 |
| SPOT EVAA-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT LTC-USDT | 38 | 17 | -0,008 | -0,059 | -0,312 | -1,000 | 2,200 | 6,137 |
| SPOT NEAR-USDT | 49 | 25 | 0,052 | 0,032 | 2,533 | -1,000 | 2,085 | 6,962 |
| SPOT PEPE-USDT | 35 | 19 | 0,120 | 0,140 | 4,197 | -1,000 | 1,762 | 3,721 |
| SPOT PIEVERSE-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT SOL-USDT | 30 | 18 | 0,069 | 0,094 | 2,077 | -1,000 | 1,860 | 2,243 |
| SPOT SUI-USDT | 23 | 12 | 0,031 | 0,024 | 0,708 | -1,000 | 1,969 | 2,636 |
| SPOT TAO-USDT | 41 | 10 | -0,128 | -0,251 | -5,263 | -1,000 | 2,668 | 7,743 |
| SPOT UNI-USDT | 30 | 15 | 0,061 | 0,015 | 1,843 | -1,000 | 2,101 | 4,080 |
| SPOT XLM-USDT | 53 | 26 | 0,116 | 0,000 | 6,161 | -1,051 | 2,141 | 5,798 |
| SPOT XMR-USDT | 34 | 17 | 0,103 | -0,026 | 3,496 | -1,000 | 1,589 | 2,265 |
| SPOT XRP-USDT | 33 | 17 | 0,158 | 0,019 | 5,201 | -1,000 | 2,244 | 2,624 |
| SPOT ZEC-USDT | 24 | 11 | 0,068 | -0,106 | 1,622 | -1,044 | 1,979 | 4,623 |

**R neto con los costes dobles**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 9 | 2 | -0,359 | -0,183 | -3,227 | -1,183 | 0,923 | 3,227 |
| FUTURES AVAXUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES BCHUSDTM | 1 | 0 | -0,016 | -0,016 | -0,016 | -0,016 | -0,016 | 0,016 |
| FUTURES ENAUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ETHUSDTM | 27 | 7 | -0,529 | -0,743 | -14,294 | -1,585 | 1,766 | 15,242 |
| FUTURES EVAAUSDTM | 14 | 7 | -0,018 | 0,105 | -0,247 | -1,162 | 1,567 | 2,539 |
| FUTURES FHEUSDTM | 1 | 0 | -0,055 | -0,055 | -0,055 | -0,055 | -0,055 | 0,055 |
| FUTURES HYPEUSDTM | 12 | 6 | -0,089 | 0,128 | -1,067 | -1,391 | 1,074 | 3,935 |
| FUTURES ICPUSDTM | 9 | 5 | -0,300 | 0,082 | -2,696 | -1,250 | 0,580 | 3,716 |
| FUTURES INJUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES LINKUSDTM | 6 | 3 | 0,226 | 0,223 | 1,356 | -0,908 | 1,243 | 1,320 |
| FUTURES NIGHTUSDTM | 1 | 1 | 0,952 | 0,952 | 0,952 | 0,952 | 0,952 | 0,000 |
| FUTURES PEPEUSDTM | 18 | 8 | -0,107 | -0,113 | -1,925 | -1,538 | 1,269 | 3,690 |
| FUTURES PIEVERSEUSDTM | 3 | 0 | -1,072 | -1,069 | -3,216 | -1,086 | -1,061 | 3,216 |
| FUTURES SOLUSDTM | 39 | 18 | -0,312 | -0,146 | -12,173 | -1,661 | 1,356 | 13,836 |
| FUTURES SOXLUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES UNIUSDTM | 1 | 1 | 1,117 | 1,117 | 1,117 | 1,117 | 1,117 | 0,000 |
| FUTURES XBTUSDTM | 17 | 5 | -0,703 | -1,180 | -11,955 | -2,052 | 1,671 | 12,948 |
| FUTURES XRPUSDTM | 31 | 14 | -0,135 | -0,291 | -4,186 | -1,463 | 1,755 | 5,860 |
| FUTURES ZECUSDTM | 14 | 5 | -0,091 | -0,112 | -1,276 | -1,126 | 1,565 | 4,263 |
| SPOT ASTER-USDT | 11 | 3 | -0,222 | -0,381 | -2,443 | -1,261 | 1,013 | 3,768 |
| SPOT AVAX-USDT | 43 | 10 | -0,909 | -1,297 | -39,066 | -2,544 | 1,270 | 40,139 |
| SPOT BNB-USDT | 44 | 8 | -1,130 | -1,350 | -49,725 | -2,871 | 1,884 | 49,725 |
| SPOT BTC-USDT | 37 | 8 | -1,241 | -1,000 | -45,911 | -8,707 | 2,417 | 45,911 |
| SPOT DOGE-USDT | 32 | 12 | -0,444 | -0,268 | -14,216 | -1,775 | 1,094 | 14,216 |
| SPOT DOT-USDT | 39 | 16 | -0,489 | -0,739 | -19,062 | -2,010 | 1,087 | 21,623 |
| SPOT ETH-USDT | 33 | 5 | -0,839 | -1,017 | -27,689 | -2,239 | 1,601 | 28,037 |
| SPOT EVAA-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT LTC-USDT | 38 | 12 | -0,624 | -0,727 | -23,721 | -2,299 | 1,656 | 23,721 |
| SPOT NEAR-USDT | 49 | 13 | -0,463 | -0,490 | -22,688 | -2,036 | 1,758 | 24,563 |
| SPOT PEPE-USDT | 35 | 10 | -0,390 | -0,269 | -13,661 | -2,061 | 1,199 | 15,751 |
| SPOT PIEVERSE-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT SOL-USDT | 30 | 10 | -0,590 | -0,443 | -17,708 | -2,448 | 1,213 | 17,708 |
| SPOT SUI-USDT | 23 | 5 | -0,430 | -0,428 | -9,900 | -2,292 | 1,666 | 10,495 |
| SPOT TAO-USDT | 41 | 8 | -0,567 | -0,737 | -23,252 | -1,993 | 2,173 | 23,252 |
| SPOT UNI-USDT | 30 | 9 | -0,431 | -0,514 | -12,920 | -1,717 | 1,714 | 15,580 |
| SPOT XLM-USDT | 53 | 20 | -0,585 | -0,569 | -31,001 | -2,726 | 1,619 | 32,735 |
| SPOT XMR-USDT | 34 | 12 | -0,427 | -0,494 | -14,531 | -1,876 | 0,761 | 14,531 |
| SPOT XRP-USDT | 33 | 10 | -0,545 | -0,487 | -17,988 | -2,434 | 1,625 | 17,988 |
| SPOT ZEC-USDT | 24 | 9 | -0,373 | -0,387 | -8,941 | -1,662 | 1,123 | 10,575 |

**Salidas, por motivo**

|  | stop | objetivo | invalidacion | tiempo | liquidacion_fin_de_ventana |
|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 4 | 2 | 3 | 0 | 0 |
| FUTURES AVAXUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES BCHUSDTM | 0 | 0 | 1 | 0 | 0 |
| FUTURES ENAUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES ETHUSDTM | 10 | 6 | 7 | 4 | 0 |
| FUTURES EVAAUSDTM | 5 | 7 | 2 | 0 | 0 |
| FUTURES FHEUSDTM | 0 | 0 | 1 | 0 | 0 |
| FUTURES HYPEUSDTM | 4 | 6 | 2 | 0 | 0 |
| FUTURES ICPUSDTM | 3 | 2 | 2 | 2 | 0 |
| FUTURES INJUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES LINKUSDTM | 0 | 3 | 2 | 1 | 0 |
| FUTURES NIGHTUSDTM | 0 | 1 | 0 | 0 | 0 |
| FUTURES PEPEUSDTM | 5 | 6 | 4 | 3 | 0 |
| FUTURES PIEVERSEUSDTM | 3 | 0 | 0 | 0 | 0 |
| FUTURES SOLUSDTM | 12 | 14 | 6 | 7 | 0 |
| FUTURES SOXLUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES UNIUSDTM | 0 | 1 | 0 | 0 | 0 |
| FUTURES XBTUSDTM | 5 | 6 | 6 | 0 | 0 |
| FUTURES XRPUSDTM | 4 | 12 | 7 | 8 | 0 |
| FUTURES ZECUSDTM | 4 | 5 | 3 | 2 | 0 |
| SPOT ASTER-USDT | 1 | 3 | 5 | 2 | 0 |
| SPOT AVAX-USDT | 21 | 10 | 6 | 6 | 0 |
| SPOT BNB-USDT | 16 | 11 | 12 | 5 | 0 |
| SPOT BTC-USDT | 7 | 16 | 7 | 7 | 0 |
| SPOT DOGE-USDT | 9 | 10 | 5 | 8 | 0 |
| SPOT DOT-USDT | 13 | 14 | 9 | 3 | 0 |
| SPOT ETH-USDT | 12 | 8 | 7 | 6 | 0 |
| SPOT EVAA-USDT | 0 | 0 | 0 | 0 | 0 |
| SPOT LTC-USDT | 13 | 7 | 8 | 9 | 1 |
| SPOT NEAR-USDT | 10 | 14 | 16 | 9 | 0 |
| SPOT PEPE-USDT | 9 | 13 | 8 | 5 | 0 |
| SPOT PIEVERSE-USDT | 0 | 0 | 0 | 0 | 0 |
| SPOT SOL-USDT | 10 | 8 | 5 | 7 | 0 |
| SPOT SUI-USDT | 3 | 4 | 8 | 8 | 0 |
| SPOT TAO-USDT | 11 | 8 | 19 | 3 | 0 |
| SPOT UNI-USDT | 7 | 8 | 7 | 8 | 0 |
| SPOT XLM-USDT | 15 | 20 | 11 | 7 | 0 |
| SPOT XMR-USDT | 5 | 9 | 10 | 10 | 0 |
| SPOT XRP-USDT | 5 | 9 | 8 | 11 | 0 |
| SPOT ZEC-USDT | 8 | 7 | 6 | 3 | 0 |

El régimen por ventana y por instrumento está en el JSON.

## Datos para D15

Los resultados por volatilidad servirán, si procede, para proponer el filtro de volatilidad extrema (D15), que decide el propietario.

## Registro completo

Las señales, las decisiones y las operaciones de cada ejecución están en el PC, fuera de Git: `C:/kbot_historico/informes/reversion_media_desarrollo_3490a1c_adee5c8a2a90`.
