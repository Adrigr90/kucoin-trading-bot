Este informe no es la validación de la estrategia (D16, F8): sirve para ver si la familia merece seguir.

# Informe exploratorio de «tendencia» en la partición «desarrollo»

## Familia, versión y ficha

- **Familia:** tendencia, con la ficha de `02` §12.3.4, aprobada.
- **Versión:** `6f08bb8669babfdcf629a6e1f0d2e0ba62a64a72+8866b51449f119568d5012acb47487d37d411b66fda8d615661564b144fa36d3`.
  - commit `6f08bb8669babfdcf629a6e1f0d2e0ba62a64a72`;
  - hash de los parámetros `8866b51449f119568d5012acb47487d37d411b66fda8d615661564b144fa36d3`.
- **Parámetros** (§12.3.3):

```json
{
 "familia": "tendencia",
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
  "ema_rapida": 20,
  "ema_lenta": 50
 },
 "senal": {
  "ema_rapida": 20,
  "ema_lenta": 50,
  "retroceso": 5,
  "macd": [
   12,
   26,
   9
  ],
  "atr": 14
 },
 "ejecucion": {
  "velas": 4
 },
 "stop": {
  "multiplo_atr": "2"
 },
 "arrastre": {
  "multiplo_atr": "3"
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

Minutos presentes en las ventanas, sin el calentamiento. En Futures no hay velas de un minuto antes del 1 ene 2025.

| Instrumento | Presentes | Minutos | Cobertura | Días no válidos |
|---|---|---|---|---|
| FUTURES ASTERUSDTM | 182680 | 632160 | 28,90 % | — |
| FUTURES AVAXUSDTM | 489033 | 632160 | 77,36 % | — |
| FUTURES BCHUSDTM | 465180 | 632160 | 73,59 % | — |
| FUTURES ENAUSDTM | 531239 | 632160 | 84,04 % | — |
| FUTURES ETHUSDTM | 569038 | 632160 | 90,01 % | — |
| FUTURES EVAAUSDTM | 173150 | 632160 | 27,39 % | — |
| FUTURES FHEUSDTM | 75477 | 632160 | 11,94 % | — |
| FUTURES HYPEUSDTM | 539115 | 632160 | 85,28 % | — |
| FUTURES ICPUSDTM | 453547 | 632160 | 71,75 % | — |
| FUTURES INJUSDTM | 509944 | 632160 | 80,67 % | — |
| FUTURES LINKUSDTM | 559145 | 632160 | 88,45 % | — |
| FUTURES NIGHTUSDTM | 73280 | 632160 | 11,59 % | — |
| FUTURES PEPEUSDTM | 568306 | 632160 | 89,90 % | — |
| FUTURES PIEVERSEUSDTM | 74445 | 632160 | 11,78 % | — |
| FUTURES SOLUSDTM | 569832 | 632160 | 90,14 % | — |
| FUTURES SOXLUSDTM | 0 | 632160 | 0,00 % | — |
| FUTURES UNIUSDTM | 462679 | 632160 | 73,19 % | — |
| FUTURES XBTUSDTM | 566630 | 632160 | 89,63 % | — |
| FUTURES XRPUSDTM | 568307 | 632160 | 89,90 % | — |
| FUTURES ZECUSDTM | 408316 | 632160 | 64,59 % | — |
| SPOT ASTER-USDT | 186660 | 632160 | 29,53 % | — |
| SPOT AVAX-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT BNB-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT BTC-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT DOGE-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT DOT-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT ETH-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT EVAA-USDT | 0 | 632160 | 0,00 % | — |
| SPOT LTC-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT NEAR-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT PEPE-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT PIEVERSE-USDT | 0 | 632160 | 0,00 % | — |
| SPOT SOL-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT SUI-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT TAO-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT UNI-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT XLM-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT XMR-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT XRP-USDT | 632160 | 632160 | 100,00 % | — |
| SPOT ZEC-USDT | 632160 | 632160 | 100,00 % | — |

## Porcentaje solo REST

Minutos originales de las operaciones, cada uno una vez por operación. En el histórico todos son solo REST.

| Procedencia | Minutos | Solo REST | Porcentaje |
|---|---|---|---|
| decisiones | 30856005 | 30856005 | 100,00 % |
| resultados | 31240893 | 31240893 | 100,00 % |

## Modelo de ejecución y costes

- **Ejecución** (§12.4.4): entrada a mercado en la apertura del primer minuto que empieza después de la decisión; stop a su nivel, o en la apertura si el minuto ya abre más allá; objetivo a su nivel, nunca mejor; salidas por cierre, por tiempo y por el final de la ventana, a mercado. Sin precio observado no hay ejecución.
- **Comisión** por lado, de tomador, la de cada instrumento leída de KuCoin (§12.5.3).
- **Deslizamiento:** 0,05 % por ejecución.
- **Prueba de esfuerzo:** comisión y deslizamiento por 2.
- **Sin funding** (§12.4.4).

| Instrumento | Comisión de tomador | Origen | Leída |
|---|---|---|---|
| FUTURES ASTERUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:49.993144+00:00 |
| FUTURES AVAXUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:52.390817+00:00 |
| FUTURES BCHUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:50.538092+00:00 |
| FUTURES ENAUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:52.918736+00:00 |
| FUTURES ETHUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:47.650812+00:00 |
| FUTURES EVAAUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:48.244242+00:00 |
| FUTURES FHEUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:52.656275+00:00 |
| FUTURES HYPEUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:49.406709+00:00 |
| FUTURES ICPUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:50.799260+00:00 |
| FUTURES INJUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:51.606367+00:00 |
| FUTURES LINKUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:52.127280+00:00 |
| FUTURES NIGHTUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:51.339370+00:00 |
| FUTURES PEPEUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:50.255279+00:00 |
| FUTURES PIEVERSEUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:49.715416+00:00 |
| FUTURES SOLUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:48.542950+00:00 |
| FUTURES SOXLUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:51.069556+00:00 |
| FUTURES UNIUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:51.866588+00:00 |
| FUTURES XBTUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:47.915220+00:00 |
| FUTURES XRPUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:49.140016+00:00 |
| FUTURES ZECUSDTM | 0,06 % | takerFeeRate 0.00060 | 2026-11-23T10:47:48.842746+00:00 |
| SPOT ASTER-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:43.790609+00:00 |
| SPOT AVAX-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:47.112595+00:00 |
| SPOT BNB-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:45.748811+00:00 |
| SPOT BTC-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:41.457480+00:00 |
| SPOT DOGE-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:46.022685+00:00 |
| SPOT DOT-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:46.565484+00:00 |
| SPOT ETH-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:41.729277+00:00 |
| SPOT EVAA-USDT | 0,3 % | classC 0.003 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:44.478227+00:00 |
| SPOT LTC-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:45.480417+00:00 |
| SPOT NEAR-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:44.787115+00:00 |
| SPOT PEPE-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:44.081741+00:00 |
| SPOT PIEVERSE-USDT | 0,2 % | classB 0.002 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:46.288262+00:00 |
| SPOT SOL-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:43.248381+00:00 |
| SPOT SUI-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:46.835457+00:00 |
| SPOT TAO-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:45.190432+00:00 |
| SPOT UNI-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:47.377325+00:00 |
| SPOT XLM-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:42.500483+00:00 |
| SPOT XMR-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:42.922244+00:00 |
| SPOT XRP-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:42.023267+00:00 |
| SPOT ZEC-USDT | 0,1 % | classA 0.001 x takerFeeCoefficient 1.00 | 2026-11-23T10:47:43.519823+00:00 |

## Resultados

En R, por operación. **Aciertos:** operaciones con R neto > 0. **Caída máxima:** sobre la curva de R acumulado por minuto de salida, desde 0, con las salidas de un mismo minuto sumadas en un paso.

### En total

**Recuentos**

|  | Señales largas | Señales cortas | Decisiones | Ejecutadas | Operaciones | Caducadas | No ejecutadas |
|---|---|---|---|---|---|---|---|
| Total | 796 | 50 | 376 | 318 | 318 | 470 | 58 |

**Caducadas y no ejecutadas, por motivo**

|  | caduca_vela_no_utilizable | caduca_contexto | caduca_sin_disparo | ocupado | opuestas | fin_de_ventana | posicion_tomada | caduca_sin_minuto | no_entra |
|---|---|---|---|---|---|---|---|---|---|
| Total | 0 | 2 | 468 | 58 | 0 | 0 | 0 | 0 | 0 |

**R neto**

|  | Operaciones | Aciertos | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| Total | 318 | 85 | -0,215 | -0,930 | -68,411 | -1,662 | 16,995 | 71,811 |

**R bruto**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| Total | 318 | 96 | -0,084 | -0,872 | -26,750 | -1,014 | 17,270 | 39,881 |

**R neto con los costes dobles**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| Total | 318 | 84 | -0,346 | -1,074 | -110,073 | -2,324 | 16,719 | 112,633 |

**Salidas, por motivo**

|  | stop | objetivo | invalidacion | tiempo | liquidacion_fin_de_ventana |
|---|---|---|---|---|---|
| Total | 317 | 0 | 1 | 0 | 0 |

**Por régimen de la vela de contexto** (dirección/volatilidad, §12.3.2)

| Régimen | Operaciones | R bruto medio | R neto medio |
|---|---|---|---|
| tendencia/expansion | 82 | -0,032 | -0,136 |
| tendencia/compresion | 38 | 0,078 | -0,097 |
| tendencia/normal | 148 | -0,111 | -0,264 |
| tendencia/sin_etiqueta | 50 | -0,215 | -0,291 |

**Por régimen de la vela de señal** (dirección/volatilidad, §12.3.2)

| Régimen | Operaciones | R bruto medio | R neto medio |
|---|---|---|---|
| tendencia/expansion | 30 | -0,404 | -0,522 |
| tendencia/compresion | 25 | 0,197 | 0,088 |
| tendencia/normal | 37 | 0,086 | -0,052 |
| rango/expansion | 23 | -0,274 | -0,390 |
| rango/compresion | 68 | -0,071 | -0,219 |
| rango/normal | 66 | 0,025 | -0,107 |
| transicion/expansion | 15 | -0,545 | -0,650 |
| transicion/compresion | 20 | -0,556 | -0,678 |
| transicion/normal | 34 | 0,176 | 0,035 |

### Por ventana

**La familia no gana en ningún tramo** (R neto sumado ≤ 0 en los cuatro).

**Recuentos**

|  | Señales largas | Señales cortas | Decisiones | Ejecutadas | Operaciones | Caducadas | No ejecutadas |
|---|---|---|---|---|---|---|---|
| tramo 1 | 137 | 5 | 59 | 51 | 51 | 83 | 8 |
| tramo 2 | 228 | 12 | 107 | 89 | 89 | 133 | 18 |
| tramo 3 | 286 | 8 | 142 | 120 | 120 | 152 | 22 |
| tramo 4 | 145 | 25 | 68 | 58 | 58 | 102 | 10 |

**Caducadas y no ejecutadas, por motivo**

|  | caduca_vela_no_utilizable | caduca_contexto | caduca_sin_disparo | ocupado | opuestas | fin_de_ventana | posicion_tomada | caduca_sin_minuto | no_entra |
|---|---|---|---|---|---|---|---|---|---|
| tramo 1 | 0 | 0 | 83 | 8 | 0 | 0 | 0 | 0 | 0 |
| tramo 2 | 0 | 1 | 132 | 18 | 0 | 0 | 0 | 0 | 0 |
| tramo 3 | 0 | 1 | 151 | 22 | 0 | 0 | 0 | 0 | 0 |
| tramo 4 | 0 | 0 | 102 | 10 | 0 | 0 | 0 | 0 | 0 |

**R neto**

|  | Operaciones | Aciertos | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| tramo 1 | 51 | 12 | -0,398 | -1,042 | -20,286 | -1,288 | 3,573 | 23,686 |
| tramo 2 | 89 | 19 | -0,288 | -1,057 | -25,593 | -1,420 | 16,995 | 25,593 |
| tramo 3 | 120 | 38 | -0,113 | -0,764 | -13,530 | -1,662 | 6,084 | 28,215 |
| tramo 4 | 58 | 16 | -0,155 | -1,048 | -9,003 | -1,430 | 10,270 | 20,309 |

**R bruto**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| tramo 1 | 51 | 14 | -0,293 | -1,000 | -14,927 | -1,000 | 3,721 | 19,167 |
| tramo 2 | 89 | 22 | -0,166 | -0,904 | -14,785 | -1,000 | 17,270 | 16,945 |
| tramo 3 | 120 | 42 | 0,038 | -0,644 | 4,523 | -1,014 | 6,206 | 20,077 |
| tramo 4 | 58 | 18 | -0,027 | -0,991 | -1,561 | -1,000 | 10,380 | 14,385 |

**R neto con los costes dobles**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| tramo 1 | 51 | 12 | -0,503 | -1,098 | -25,645 | -1,577 | 3,425 | 28,205 |
| tramo 2 | 89 | 19 | -0,409 | -1,123 | -36,401 | -1,840 | 16,719 | 36,401 |
| tramo 3 | 120 | 37 | -0,263 | -0,877 | -31,583 | -2,324 | 5,963 | 38,780 |
| tramo 4 | 58 | 16 | -0,284 | -1,108 | -16,445 | -1,860 | 10,159 | 26,233 |

**Salidas, por motivo**

|  | stop | objetivo | invalidacion | tiempo | liquidacion_fin_de_ventana |
|---|---|---|---|---|---|
| tramo 1 | 51 | 0 | 0 | 0 | 0 |
| tramo 2 | 88 | 0 | 1 | 0 | 0 |
| tramo 3 | 120 | 0 | 0 | 0 | 0 |
| tramo 4 | 58 | 0 | 0 | 0 | 0 |

### Por instrumento

**Recuentos**

|  | Señales largas | Señales cortas | Decisiones | Ejecutadas | Operaciones | Caducadas | No ejecutadas |
|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES AVAXUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES BCHUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ENAUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ETHUSDTM | 3 | 16 | 14 | 12 | 12 | 5 | 2 |
| FUTURES EVAAUSDTM | 8 | 2 | 1 | 1 | 1 | 9 | 0 |
| FUTURES FHEUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES HYPEUSDTM | 0 | 1 | 0 | 0 | 0 | 1 | 0 |
| FUTURES ICPUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES INJUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES LINKUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES NIGHTUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES PEPEUSDTM | 1 | 2 | 1 | 1 | 1 | 2 | 0 |
| FUTURES PIEVERSEUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES SOLUSDTM | 7 | 21 | 14 | 12 | 12 | 14 | 2 |
| FUTURES SOXLUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES UNIUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES XBTUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES XRPUSDTM | 6 | 6 | 7 | 7 | 7 | 5 | 0 |
| FUTURES ZECUSDTM | 11 | 2 | 4 | 3 | 3 | 9 | 1 |
| SPOT ASTER-USDT | 11 | 0 | 5 | 4 | 4 | 6 | 1 |
| SPOT AVAX-USDT | 37 | 0 | 17 | 14 | 14 | 20 | 3 |
| SPOT BNB-USDT | 40 | 0 | 16 | 15 | 15 | 24 | 1 |
| SPOT BTC-USDT | 46 | 0 | 20 | 17 | 17 | 26 | 3 |
| SPOT DOGE-USDT | 35 | 0 | 15 | 13 | 13 | 20 | 2 |
| SPOT DOT-USDT | 42 | 0 | 19 | 14 | 14 | 23 | 5 |
| SPOT ETH-USDT | 47 | 0 | 23 | 20 | 20 | 24 | 3 |
| SPOT EVAA-USDT | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT LTC-USDT | 44 | 0 | 18 | 15 | 15 | 26 | 3 |
| SPOT NEAR-USDT | 34 | 0 | 22 | 21 | 21 | 12 | 1 |
| SPOT PEPE-USDT | 34 | 0 | 23 | 17 | 17 | 11 | 6 |
| SPOT PIEVERSE-USDT | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT SOL-USDT | 47 | 0 | 19 | 19 | 19 | 28 | 0 |
| SPOT SUI-USDT | 30 | 0 | 13 | 9 | 9 | 17 | 4 |
| SPOT TAO-USDT | 39 | 0 | 16 | 14 | 14 | 23 | 2 |
| SPOT UNI-USDT | 40 | 0 | 14 | 14 | 14 | 26 | 0 |
| SPOT XLM-USDT | 38 | 0 | 12 | 9 | 9 | 26 | 3 |
| SPOT XMR-USDT | 77 | 0 | 35 | 26 | 26 | 42 | 9 |
| SPOT XRP-USDT | 43 | 0 | 20 | 17 | 17 | 23 | 3 |
| SPOT ZEC-USDT | 76 | 0 | 28 | 24 | 24 | 48 | 4 |

**Caducadas y no ejecutadas, por motivo**

|  | caduca_vela_no_utilizable | caduca_contexto | caduca_sin_disparo | ocupado | opuestas | fin_de_ventana | posicion_tomada | caduca_sin_minuto | no_entra |
|---|---|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES AVAXUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES BCHUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ENAUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ETHUSDTM | 0 | 0 | 5 | 2 | 0 | 0 | 0 | 0 | 0 |
| FUTURES EVAAUSDTM | 0 | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES FHEUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES HYPEUSDTM | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ICPUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES INJUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES LINKUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES NIGHTUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES PEPEUSDTM | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES PIEVERSEUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES SOLUSDTM | 0 | 0 | 14 | 2 | 0 | 0 | 0 | 0 | 0 |
| FUTURES SOXLUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES UNIUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES XBTUSDTM | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES XRPUSDTM | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 |
| FUTURES ZECUSDTM | 0 | 0 | 9 | 1 | 0 | 0 | 0 | 0 | 0 |
| SPOT ASTER-USDT | 0 | 0 | 6 | 1 | 0 | 0 | 0 | 0 | 0 |
| SPOT AVAX-USDT | 0 | 0 | 20 | 3 | 0 | 0 | 0 | 0 | 0 |
| SPOT BNB-USDT | 0 | 0 | 24 | 1 | 0 | 0 | 0 | 0 | 0 |
| SPOT BTC-USDT | 0 | 0 | 26 | 3 | 0 | 0 | 0 | 0 | 0 |
| SPOT DOGE-USDT | 0 | 0 | 20 | 2 | 0 | 0 | 0 | 0 | 0 |
| SPOT DOT-USDT | 0 | 0 | 23 | 5 | 0 | 0 | 0 | 0 | 0 |
| SPOT ETH-USDT | 0 | 0 | 24 | 3 | 0 | 0 | 0 | 0 | 0 |
| SPOT EVAA-USDT | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT LTC-USDT | 0 | 0 | 26 | 3 | 0 | 0 | 0 | 0 | 0 |
| SPOT NEAR-USDT | 0 | 0 | 12 | 1 | 0 | 0 | 0 | 0 | 0 |
| SPOT PEPE-USDT | 0 | 0 | 11 | 6 | 0 | 0 | 0 | 0 | 0 |
| SPOT PIEVERSE-USDT | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT SOL-USDT | 0 | 0 | 28 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT SUI-USDT | 0 | 0 | 17 | 4 | 0 | 0 | 0 | 0 | 0 |
| SPOT TAO-USDT | 0 | 1 | 22 | 2 | 0 | 0 | 0 | 0 | 0 |
| SPOT UNI-USDT | 0 | 0 | 26 | 0 | 0 | 0 | 0 | 0 | 0 |
| SPOT XLM-USDT | 0 | 0 | 26 | 3 | 0 | 0 | 0 | 0 | 0 |
| SPOT XMR-USDT | 0 | 1 | 41 | 9 | 0 | 0 | 0 | 0 | 0 |
| SPOT XRP-USDT | 0 | 0 | 23 | 3 | 0 | 0 | 0 | 0 | 0 |
| SPOT ZEC-USDT | 0 | 0 | 48 | 4 | 0 | 0 | 0 | 0 | 0 |

**R neto**

|  | Operaciones | Aciertos | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES AVAXUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES BCHUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ENAUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ETHUSDTM | 12 | 2 | -0,740 | -1,066 | -8,884 | -1,107 | 0,828 | 9,148 |
| FUTURES EVAAUSDTM | 1 | 1 | 2,140 | 2,140 | 2,140 | 2,140 | 2,140 | 0,000 |
| FUTURES FHEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES HYPEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ICPUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES INJUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES LINKUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES NIGHTUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES PEPEUSDTM | 1 | 0 | -1,049 | -1,049 | -1,049 | -1,049 | -1,049 | 1,049 |
| FUTURES PIEVERSEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES SOLUSDTM | 12 | 0 | -0,909 | -1,068 | -10,904 | -1,130 | -0,008 | 10,904 |
| FUTURES SOXLUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES UNIUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES XBTUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES XRPUSDTM | 7 | 2 | -0,374 | -0,789 | -2,619 | -1,139 | 0,821 | 2,619 |
| FUTURES ZECUSDTM | 3 | 2 | 0,096 | 0,113 | 0,288 | -0,909 | 1,084 | 0,909 |
| SPOT ASTER-USDT | 4 | 0 | -1,104 | -1,091 | -4,417 | -1,144 | -1,091 | 4,417 |
| SPOT AVAX-USDT | 14 | 4 | -0,410 | -0,881 | -5,746 | -1,137 | 2,325 | 6,934 |
| SPOT BNB-USDT | 15 | 7 | 0,543 | -0,004 | 8,147 | -1,275 | 5,095 | 1,788 |
| SPOT BTC-USDT | 17 | 2 | -0,765 | -0,886 | -13,004 | -1,662 | 1,178 | 13,004 |
| SPOT DOGE-USDT | 13 | 7 | 0,548 | 0,162 | 7,127 | -1,134 | 4,948 | 3,585 |
| SPOT DOT-USDT | 14 | 4 | -0,412 | -0,939 | -5,763 | -1,185 | 2,603 | 7,167 |
| SPOT ETH-USDT | 20 | 5 | -0,440 | -1,154 | -8,799 | -1,319 | 3,320 | 8,799 |
| SPOT EVAA-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT LTC-USDT | 15 | 1 | -0,772 | -1,041 | -11,576 | -1,181 | 0,658 | 11,576 |
| SPOT NEAR-USDT | 21 | 4 | -0,494 | -1,081 | -10,369 | -1,180 | 2,632 | 10,756 |
| SPOT PEPE-USDT | 17 | 5 | -0,327 | -1,042 | -5,566 | -1,146 | 3,117 | 6,478 |
| SPOT PIEVERSE-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT SOL-USDT | 19 | 2 | -0,604 | -1,077 | -11,476 | -1,144 | 3,287 | 11,476 |
| SPOT SUI-USDT | 9 | 4 | 0,618 | -0,744 | 5,562 | -1,102 | 6,084 | 4,803 |
| SPOT TAO-USDT | 14 | 3 | -0,389 | -0,614 | -5,450 | -1,132 | 1,611 | 5,992 |
| SPOT UNI-USDT | 14 | 5 | 0,405 | -0,956 | 5,670 | -1,121 | 10,270 | 4,868 |
| SPOT XLM-USDT | 9 | 2 | -0,487 | -0,783 | -4,382 | -1,142 | 0,517 | 5,049 |
| SPOT XMR-USDT | 26 | 11 | 0,617 | -0,662 | 16,045 | -1,182 | 16,995 | 6,914 |
| SPOT XRP-USDT | 17 | 4 | -0,162 | -0,952 | -2,761 | -1,210 | 4,931 | 7,448 |
| SPOT ZEC-USDT | 24 | 8 | -0,026 | -0,433 | -0,625 | -1,189 | 4,204 | 9,552 |

**R bruto**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES AVAXUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES BCHUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ENAUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ETHUSDTM | 12 | 2 | -0,659 | -1,000 | -7,911 | -1,000 | 0,911 | 8,450 |
| FUTURES EVAAUSDTM | 1 | 1 | 2,157 | 2,157 | 2,157 | 2,157 | 2,157 | 0,000 |
| FUTURES FHEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES HYPEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ICPUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES INJUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES LINKUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES NIGHTUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES PEPEUSDTM | 1 | 0 | -1,000 | -1,000 | -1,000 | -1,000 | -1,000 | 1,000 |
| FUTURES PIEVERSEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES SOLUSDTM | 12 | 1 | -0,827 | -1,000 | -9,921 | -1,000 | 0,073 | 9,921 |
| FUTURES SOXLUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES UNIUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES XBTUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES XRPUSDTM | 7 | 2 | -0,284 | -0,690 | -1,988 | -1,000 | 0,910 | 2,000 |
| FUTURES ZECUSDTM | 3 | 2 | 0,118 | 0,133 | 0,353 | -0,895 | 1,115 | 0,895 |
| SPOT ASTER-USDT | 4 | 0 | -1,000 | -1,000 | -4,000 | -1,000 | -1,000 | 4,000 |
| SPOT AVAX-USDT | 14 | 4 | -0,298 | -0,786 | -4,176 | -1,000 | 2,506 | 5,682 |
| SPOT BNB-USDT | 15 | 9 | 0,775 | 0,169 | 11,621 | -1,000 | 5,392 | 1,110 |
| SPOT BTC-USDT | 17 | 3 | -0,419 | -0,691 | -7,123 | -1,000 | 1,586 | 7,123 |
| SPOT DOGE-USDT | 13 | 7 | 0,658 | 0,257 | 8,553 | -1,000 | 5,112 | 2,916 |
| SPOT DOT-USDT | 14 | 4 | -0,296 | -0,845 | -4,145 | -1,000 | 2,793 | 5,737 |
| SPOT ETH-USDT | 20 | 5 | -0,260 | -1,000 | -5,207 | -1,000 | 3,468 | 5,632 |
| SPOT EVAA-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT LTC-USDT | 15 | 2 | -0,651 | -1,000 | -9,767 | -1,000 | 0,882 | 9,767 |
| SPOT NEAR-USDT | 21 | 6 | -0,380 | -0,993 | -7,978 | -1,000 | 2,754 | 8,731 |
| SPOT PEPE-USDT | 17 | 5 | -0,236 | -1,000 | -4,014 | -1,000 | 3,254 | 5,268 |
| SPOT PIEVERSE-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT SOL-USDT | 19 | 4 | -0,469 | -0,939 | -8,911 | -1,000 | 3,479 | 9,096 |
| SPOT SUI-USDT | 9 | 4 | 0,721 | -0,663 | 6,490 | -1,000 | 6,206 | 4,314 |
| SPOT TAO-USDT | 14 | 3 | -0,294 | -0,529 | -4,120 | -1,000 | 1,723 | 5,084 |
| SPOT UNI-USDT | 14 | 5 | 0,501 | -0,843 | 7,020 | -1,000 | 10,380 | 3,942 |
| SPOT XLM-USDT | 9 | 3 | -0,356 | -0,687 | -3,208 | -1,000 | 0,577 | 4,329 |
| SPOT XMR-USDT | 26 | 11 | 0,750 | -0,581 | 19,491 | -1,000 | 17,270 | 5,355 |
| SPOT XRP-USDT | 17 | 4 | -0,022 | -0,918 | -0,369 | -1,000 | 5,029 | 5,656 |
| SPOT ZEC-USDT | 24 | 9 | 0,058 | -0,356 | 1,402 | -1,014 | 4,364 | 8,288 |

**R neto con los costes dobles**

|  | Operaciones | R > 0 | R medio | Mediana | Suma | Peor | Mejor | Caída máxima |
|---|---|---|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES AVAXUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES BCHUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ENAUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ETHUSDTM | 12 | 2 | -0,821 | -1,131 | -9,856 | -1,214 | 0,744 | 9,856 |
| FUTURES EVAAUSDTM | 1 | 1 | 2,122 | 2,122 | 2,122 | 2,122 | 2,122 | 0,000 |
| FUTURES FHEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES HYPEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES ICPUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES INJUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES LINKUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES NIGHTUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES PEPEUSDTM | 1 | 0 | -1,097 | -1,097 | -1,097 | -1,097 | -1,097 | 1,097 |
| FUTURES PIEVERSEUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES SOLUSDTM | 12 | 0 | -0,991 | -1,136 | -11,888 | -1,260 | -0,088 | 11,888 |
| FUTURES SOXLUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES UNIUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES XBTUSDTM | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| FUTURES XRPUSDTM | 7 | 2 | -0,464 | -0,881 | -3,250 | -1,279 | 0,731 | 3,250 |
| FUTURES ZECUSDTM | 3 | 2 | 0,075 | 0,094 | 0,224 | -0,922 | 1,052 | 0,922 |
| SPOT ASTER-USDT | 4 | 0 | -1,208 | -1,182 | -4,833 | -1,288 | -1,181 | 4,833 |
| SPOT AVAX-USDT | 14 | 3 | -0,523 | -0,977 | -7,316 | -1,274 | 2,145 | 8,187 |
| SPOT BNB-USDT | 15 | 7 | 0,311 | -0,176 | 4,672 | -1,549 | 4,797 | 2,646 |
| SPOT BTC-USDT | 17 | 2 | -1,111 | -1,145 | -18,885 | -2,324 | 0,802 | 18,885 |
| SPOT DOGE-USDT | 13 | 7 | 0,438 | 0,066 | 5,700 | -1,268 | 4,785 | 4,254 |
| SPOT DOT-USDT | 14 | 4 | -0,527 | -1,023 | -7,380 | -1,371 | 2,412 | 8,699 |
| SPOT ETH-USDT | 20 | 5 | -0,620 | -1,308 | -12,391 | -1,638 | 3,171 | 12,391 |
| SPOT EVAA-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT LTC-USDT | 15 | 1 | -0,892 | -1,082 | -13,385 | -1,362 | 0,435 | 13,385 |
| SPOT NEAR-USDT | 21 | 4 | -0,608 | -1,162 | -12,761 | -1,367 | 2,511 | 12,780 |
| SPOT PEPE-USDT | 17 | 5 | -0,419 | -1,085 | -7,118 | -1,292 | 2,979 | 7,687 |
| SPOT PIEVERSE-USDT | 0 | 0 | — | — | 0,000 | — | — | 0,000 |
| SPOT SOL-USDT | 19 | 2 | -0,739 | -1,179 | -14,042 | -1,305 | 3,095 | 14,042 |
| SPOT SUI-USDT | 9 | 4 | 0,515 | -0,825 | 4,634 | -1,203 | 5,963 | 5,292 |
| SPOT TAO-USDT | 14 | 3 | -0,484 | -0,698 | -6,781 | -1,263 | 1,499 | 6,900 |
| SPOT UNI-USDT | 14 | 5 | 0,309 | -1,068 | 4,321 | -1,243 | 10,159 | 5,839 |
| SPOT XLM-USDT | 9 | 2 | -0,617 | -0,880 | -5,556 | -1,283 | 0,458 | 5,769 |
| SPOT XMR-USDT | 26 | 11 | 0,485 | -0,742 | 12,599 | -1,376 | 16,719 | 8,473 |
| SPOT XRP-USDT | 17 | 4 | -0,303 | -0,985 | -5,152 | -1,420 | 4,833 | 9,595 |
| SPOT ZEC-USDT | 24 | 8 | -0,111 | -0,511 | -2,652 | -1,363 | 4,045 | 10,817 |

**Salidas, por motivo**

|  | stop | objetivo | invalidacion | tiempo | liquidacion_fin_de_ventana |
|---|---|---|---|---|---|
| FUTURES ASTERUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES AVAXUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES BCHUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES ENAUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES ETHUSDTM | 12 | 0 | 0 | 0 | 0 |
| FUTURES EVAAUSDTM | 1 | 0 | 0 | 0 | 0 |
| FUTURES FHEUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES HYPEUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES ICPUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES INJUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES LINKUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES NIGHTUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES PEPEUSDTM | 1 | 0 | 0 | 0 | 0 |
| FUTURES PIEVERSEUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES SOLUSDTM | 12 | 0 | 0 | 0 | 0 |
| FUTURES SOXLUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES UNIUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES XBTUSDTM | 0 | 0 | 0 | 0 | 0 |
| FUTURES XRPUSDTM | 7 | 0 | 0 | 0 | 0 |
| FUTURES ZECUSDTM | 3 | 0 | 0 | 0 | 0 |
| SPOT ASTER-USDT | 4 | 0 | 0 | 0 | 0 |
| SPOT AVAX-USDT | 14 | 0 | 0 | 0 | 0 |
| SPOT BNB-USDT | 15 | 0 | 0 | 0 | 0 |
| SPOT BTC-USDT | 17 | 0 | 0 | 0 | 0 |
| SPOT DOGE-USDT | 13 | 0 | 0 | 0 | 0 |
| SPOT DOT-USDT | 14 | 0 | 0 | 0 | 0 |
| SPOT ETH-USDT | 20 | 0 | 0 | 0 | 0 |
| SPOT EVAA-USDT | 0 | 0 | 0 | 0 | 0 |
| SPOT LTC-USDT | 15 | 0 | 0 | 0 | 0 |
| SPOT NEAR-USDT | 21 | 0 | 0 | 0 | 0 |
| SPOT PEPE-USDT | 17 | 0 | 0 | 0 | 0 |
| SPOT PIEVERSE-USDT | 0 | 0 | 0 | 0 | 0 |
| SPOT SOL-USDT | 19 | 0 | 0 | 0 | 0 |
| SPOT SUI-USDT | 9 | 0 | 0 | 0 | 0 |
| SPOT TAO-USDT | 14 | 0 | 0 | 0 | 0 |
| SPOT UNI-USDT | 14 | 0 | 0 | 0 | 0 |
| SPOT XLM-USDT | 9 | 0 | 0 | 0 | 0 |
| SPOT XMR-USDT | 25 | 0 | 1 | 0 | 0 |
| SPOT XRP-USDT | 17 | 0 | 0 | 0 | 0 |
| SPOT ZEC-USDT | 24 | 0 | 0 | 0 | 0 |

El régimen por ventana y por instrumento está en el JSON.

## Datos para D15

Los resultados por volatilidad servirán, si procede, para proponer el filtro de volatilidad extrema (D15), que decide el propietario.

## Registro completo

Las señales, las decisiones y las operaciones de cada ejecución están en el PC, fuera de Git: `C:/kbot_historico/informes/tendencia_desarrollo_6f08bb8_8866b51449f1`.
