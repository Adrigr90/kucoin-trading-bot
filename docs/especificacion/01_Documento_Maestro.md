# PROYECTO: BOT DE TRADING INTELIGENTE PARA KUCOIN

**Versión:** 1.0 — Especificaciones iniciales
**Fecha:** 3 de noviembre de 2026
**Entorno de desarrollo:** Windows + Claude Code
**Entorno previsto de ejecución:** servidor remoto gratuito, inicialmente Oracle Cloud Always Free
**Lenguaje principal:** Python

> Texto literal del Documento Maestro v1.0 facilitado por el propietario. Es la **fuente de verdad** del proyecto. No se modifica.

---

## 1. Objetivo general

Desarrollar desde cero un bot de análisis y trading para KuCoin, especializado exclusivamente en criptomonedas.

El sistema deberá analizar continuamente los mercados Spot y Futures, detectar oportunidades, estudiar noticias y contexto macroeconómico, evaluar riesgos y generar señales de trading.

El bot tendrá una arquitectura modular, adaptativa y ampliable, con inteligencia artificial utilizada de forma selectiva.

El desarrollo comenzará exclusivamente con análisis y simulación de operaciones mediante dinero ficticio.

No podrá operar con dinero real hasta recibir autorización expresa del propietario.

## 2. Funcionamiento general

- Mercado: criptomonedas.
- Exchange: KuCoin.
- Modalidades: Spot y Futures.
- Universo: selección dinámica de criptomonedas según liquidez y oportunidades.
- Horizonte: adaptativo.
- Autonomía inicial: análisis y simulación, sin órdenes reales.
- Capital ficticio inicial: 10.000 USDT.

La selección de activos deberá considerar liquidez, volumen, spread, volatilidad y disponibilidad de datos.

## 3. Noticias y fuentes

El sistema vigilará:

- Noticias sobre criptomonedas y proyectos.
- Listados y retiradas de exchanges.
- Regulación, sanciones y decisiones gubernamentales.
- Hackeos y vulnerabilidades.
- Inflación, tipos de interés y decisiones de bancos centrales.
- Movimientos institucionales y adopción.
- Liquidez, volumen y acontecimientos de volatilidad.

### Fuentes

- Fuentes oficiales y primarias como autoridad para confirmar hechos.
- X y Reddit exclusivamente como fuentes complementarias de sentimiento, con cautela.
- Las redes sociales nunca constituirán confirmación factual.
- Los rumores no podrán activar operaciones.

Cada noticia deberá conservar fuente, URL, fecha, hora y estado de verificación.

Estados posibles: confirmada, pendiente de verificación y descartada.

El sistema deberá investigar automáticamente las noticias pendientes, detectar duplicados y evitar contar varias publicaciones de un mismo acontecimiento como noticias independientes.

Si no existe confirmación oficial, la noticia no podrá justificar una operación.

## 4. Inteligencia artificial

La IA tendrá cinco funciones:

1. Análisis de noticias.
2. Análisis del mercado.
3. Análisis de sentimiento.
4. Evaluación de oportunidades.
5. Evaluación de riesgos.

### Arquitectura de IA por niveles

- Nivel 1: monitorización continua mediante cálculos y reglas, sin IA.
- Nivel 2: análisis moderado mediante IA cuando aparezcan señales relevantes.
- Nivel 3: análisis profundo ante oportunidades o acontecimientos importantes.

Los indicadores matemáticos tendrán prioridad inicialmente. La IA complementará los resultados y podrá señalar contradicciones.

El equilibrio podrá revisarse en el futuro.

### Presupuesto

- Máximo inicial: 25 €/mes.
- Control del consumo.
- Aviso al alcanzar el 80 %.
- Al alcanzar el 100 %, suspender llamadas de IA de pago.
- La monitorización y las funciones no dependientes de IA deberán continuar.

Claude Pro y los costes de programación son independientes de este presupuesto.

## 5. Estrategias de trading

El sistema incorporará:

- Seguimiento de tendencias.
- Rupturas (breakout).
- Reversión a la media.
- Scalping.
- Análisis de volumen y flujo de mercado.

La selección será adaptativa, pudiendo combinar estrategias cuando las señales sean compatibles.

### Indicadores

- RSI.
- MACD.
- EMA y SMA.
- Bandas de Bollinger.
- ATR.
- VWAP.
- ADX.
- Volumen relativo.
- Soportes y resistencias.
- Profundidad del libro de órdenes.

### Temporalidades

1 minuto, 5 minutos, 15 minutos, 1 hora, 4 horas y 1 día.

Se combinarán diferentes temporalidades para contrastar señales y contexto.

### Aprendizaje

El sistema analizará resultados y propondrá mejoras. No podrá aplicar modificaciones de estrategias o parámetros importantes sin autorización.

## 6. Gestión del riesgo

### Parámetros iniciales

- Capital ficticio: 10.000 USDT.
- Riesgo máximo planificado por operación: 0,5 %.
- Pérdida diaria máxima: 2 %.
- Apalancamiento máximo Futures: 2x.
- Drawdown máximo: 10 %.
- Stop-loss obligatorio en todas las operaciones.

Los límites serán controles obligatorios, no recomendaciones que las estrategias puedan ignorar.

El tamaño de posición deberá calcularse según la distancia al stop, el capital disponible y los costes.

Se contabilizarán comisiones, financiación de Futures, deslizamiento y resultados realizados y no realizados.

Al alcanzar límites de riesgo, el sistema deberá impedir nuevas operaciones y generar una alerta.

El apalancamiento inicial deberá utilizar margen aislado cuando corresponda.

Queda prohibido aumentar automáticamente posiciones perdedoras para recuperar pérdidas.

## 7. Infraestructura

### Alojamiento

Oracle Cloud Always Free como primera opción.

No se autorizarán gastos de infraestructura: límite inicial de 0 €/mes.

La disponibilidad de recursos gratuitos no está garantizada. Si Oracle no proporciona capacidad suficiente, el proyecto deberá detenerse y presentar alternativas para aprobación, sin contratar servicios automáticamente.

### Decisiones técnicas pendientes

Claude Code deberá evaluar y justificar:

- Sistema operativo del servidor.
- Arquitectura de base de datos.
- Recursos mínimos del servidor.
- Organización de servicios y procesos.
- Estrategia de despliegue.

### Control del bot

- Telegram para notificaciones y alertas.
- Panel web para estadísticas y administración.
- Registro persistente de señales, decisiones y operaciones.

### Base de datos

Debe conservar precios necesarios para el análisis, señales, noticias, operaciones simuladas, resultados, errores y eventos del sistema.

Claude Code propondrá la solución adecuada, considerando SQLite y PostgreSQL, entre otras alternativas justificadas.

## 8. Seguridad y conexión con KuCoin

En la primera fase:

- Utilizar datos reales de mercado mediante conexiones de solo lectura.
- No enviar órdenes al exchange.
- No habilitar permisos de trading.
- No habilitar permisos de retirada.
- No incluir claves API en el código.
- Utilizar variables de entorno o almacenamiento seguro de secretos.
- Restringir IP cuando sea posible.

El paper trading será un simulador propio, no una operativa real.

## 9. Presupuesto y mantenimiento

- Infraestructura: 0 €/mes.
- IA: máximo 25 €/mes.
- Datos: gratuitos inicialmente.
- Servicios de pago: únicamente con autorización expresa.

### Mantenimiento

El bot podrá solucionar automáticamente incidencias técnicas habituales dentro de límites seguros.

Deberá reconectar, reiniciarse si procede, detectar datos obsoletos, registrar errores y avisar por Telegram.

Los cambios importantes requerirán autorización.

### Copias de seguridad

- Diarias.
- Antes de actualizaciones.
- Conservación de histórico.
- Verificación periódica de restauración.

### Actualizaciones

Las actualizaciones técnicas menores podrán automatizarse después de superar las pruebas.

Los cambios importantes en estrategias, riesgo y operativa necesitarán autorización.

### Informes

Telegram deberá comunicar:

- Oportunidades detectadas.
- Noticias importantes.
- Resumen diario de operaciones.
- Estadísticas diarias y semanales.
- Errores y desconexiones inmediatamente.
- Consumo de IA al 80 % y 100 %.
- Resumen semanal.

## 10. Desarrollo y validación

El desarrollo será modular.

Cada módulo deberá contar con documentación, pruebas y revisión antes de pasar al siguiente.

Se utilizará Git y GitHub para control de versiones.

La documentación se redactará en español.

### Pruebas obligatorias

1. Backtesting con datos históricos.
2. Paper trading con datos reales.
3. Pruebas de estabilidad y funcionamiento continuo.

Se deberá evitar el uso accidental de información futura en backtesting y tener en cuenta comisiones, deslizamiento y financiación.

### Fases

1. Arquitectura y estructura inicial.
2. Recopilación y almacenamiento de datos.
3. Indicadores y estrategias.
4. Gestión del riesgo.
5. Simulador Spot y Futures.
6. Noticias e inteligencia artificial.
7. Telegram y panel web.
8. Integración y pruebas.
9. Despliegue y monitorización.

## 11. Autorizaciones y dinero real

Claude Code deberá trabajar de forma supervisada.

El propietario aprobará cada módulo antes de continuar.

No se permitirá activar trading real automáticamente.

Para cualquier futura transición a dinero real serán obligatorios:

- Revisión de resultados de backtesting.
- Paper trading documentado.
- Pruebas de estabilidad.
- Validación de controles de riesgo.
- Auditoría de seguridad.
- Autorización manual expresa.

El proyecto permanecerá en modo análisis y simulación hasta recibir dicha autorización.

---

**FIN DEL DOCUMENTO MAESTRO**
