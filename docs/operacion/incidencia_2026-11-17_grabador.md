# Incidencia del grabador en su primera noche en el servidor (17 y 18 de noviembre de 2026)

El grabador funciona en el servidor desde el 17 nov a las 10:56 UTC (`v0.2.3-e4b`; desde las 23:53, `v0.2.4-e4c1`, con el mismo código del grabador). Todo lo que sigue sale de la base de datos y del diario del servidor, leídos sin cambiar nada.

## Qué pasó

| Hora (UTC) | Hecho |
|---|---|
| 17 nov, de 11:00 a 17:45 | La conexión de Spot se corta **30 veces por falta de pong a los 3 s**; la de Futures, 4 veces en todo el día, y se le cierra la conexión otras 2. Cada corte deja un hueco de 11 s a 10 min |
| 17:45:07 | Otro corte de Spot por falta de pong. En uno de los reintentos, **KuCoin rechaza el handshake con HTTP 400**. El cliente de E1 no trataba ese error (`InvalidStatus`) como una desconexión, así que **el stream de Spot terminó sin avisar**: ninguna línea en el diario y nada que lo vigilara |
| De 17:45 a 00:00 | Spot no se graba por WebSocket: ni libros (nivel 5 de 20 pares y nivel 50 de 10), ni operaciones, ni velas del WebSocket. Las velas de last se cierran con REST (`cerrada_rest`), como prevé el diseño cuando el flujo está en S3 |
| 18 nov, 00:00:05 | Cambia el universo y el grabador cierra los streams para renovar las suscripciones. Al esperar el de Spot recibe su excepción (`InvalidStatus: server rejected WebSocket connection: HTTP 400`) y **el proceso cae** |
| 00:00:15 | systemd lo arranca de nuevo (`Restart=on-failure`). Spot vuelve a grabarse a las 00:01:45: **6 h 16 min sin WebSocket de Spot** |

## Lo que se perdió y lo que no

- **Velas:** ninguna. Entre las 18:00 y las 23:00 se cerraron con REST 5.992 de los 6.000 minutos de Spot.
- **Libros, operaciones y velas del WebSocket de Spot:** de 17:45:07 a 00:01:45.
- **La caída perdió el hueco abierto desde las 17:45 en 67 de los 70 canales de Spot.** Los huecos solo se escribían al cerrarse, y al arrancar, el hueco de la parada empezó en el último minuto escrito (23:59). Los otros 3 canales, de ENA-USDT, que salía del universo, se cerraron a medianoche.
- **La caída llegó también antes de que 3 instrumentos de Futures que salían del universo** (APTUSDTM, FFUSDTM y PUMPUSDTM) cerraran su último minuto, y nadie lo cubría después.
- **Efecto en el estado de los minutos:** al cerrar el 17 nov, **13 minutos quedaron `sin_explicar` en lugar de `hueco`**. Son 10 de Spot sin vela REST dentro del hueco perdido y los 3 de las 23:59 de esos instrumentos de Futures.
  - El 17 nov ya era un día incompleto, porque la grabación empezó a las 10:56, así que su completitud no cambia.
  - No se corrige a mano: lo escrito es terminal, y lo que dice («sin explicar») es cierto con lo que quedó en la base.

## Los cortes por falta de pong siguen

Desde el arranque de las 00:00:15 hasta las 10:41 del 18 nov:
- **Spot: 59 cortes por falta de pong.** Suman 2.742 s, el 7 % del tiempo, con una mediana de 32 s y un máximo de 12,7 min. 40 de los 59 empiezan entre los segundos 20 y 40 de un minuto.
- **Futures: 1.**

No parece un problema del proceso:
- el grabador usa el 17 % de un núcleo, y no hay bloqueos del bucle ni libros tardíos;
- las dos conexiones comparten el mismo proceso, y solo se corta la de Spot.

Así que algo propio de Spot retrasa sus pongs. Una explicación posible es que un pong llega detrás de todo lo que KuCoin envió antes por la misma conexión. Con 3 s de espera no se puede saber: la corrección mide cada pong lento.

## La corrección (rama `fase-2-grabador`)

1. **Un rechazo del handshake es una desconexión más** (`kbot.kucoin.ws`): el stream lo registra y vuelve a intentarlo.
   - Tras una sesión conectada o un primer intento fallido, espera 5 s.
   - Cada intento fallido seguido duplica la espera máxima, hasta 60 s, y la espera se elige al azar en su mitad superior. Nunca hay reintentos rápidos.
2. **El grabador vigila sus streams y su vigilante** (`kbot.ingest.service`). Si uno termina por sí solo, ya solo puede ser por un error del propio código: el grabador escribe lo que deja abierto, se detiene con error y systemd lo vuelve a arrancar. **Nunca sigue grabando sin uno de sus mercados.**
3. **Cerrar los streams nunca falla por uno que ya había fallado:** queda en el diario y los demás se cierran.
4. **Al pararse, el grabador escribe los huecos que conoce y no ha escrito** (`RecorderCore.stop`):
   - los cerrados desde el último ciclo;
   - cada hueco abierto, cerrado en ese instante y con su causa;
   - el tiempo que un instrumento que salió del universo ya no podrá cerrar.

   Solo escribe huecos: no cierra intervalos ni minutos, porque tras un ciclo que no se pudo escribir eso dejaría tiempo sin filas y sin hueco.

   Al arrancar, el hueco de la parada empieza en el punto de reanudación, adelantado sobre los huecos ya escritos que lo cubren uno tras otro (`gaps.covered_until`), como el que escribió la parada. **Nunca pasa por un instante que ningún hueco explique.** Si un hueco escrito empieza después del punto de reanudación, el de la parada empieza en el punto y se solapa con él.
5. **Pong: 10 s en lugar de 3.** KuCoin da los 3 s solo como ejemplo («such as 3 second»). Nunca se espera menos que el `pingTimeout` del welcome, si KuCoin lo envía. Para fijar la espera con datos:
   - cada pong de más de 1 s queda en el diario con el tiempo hasta que KuCoin lo envió (su `ts`), el tiempo desde entonces y los mensajes recibidos antes que él;
   - un corte por falta de pong dice cuántos mensajes llegaron desde el ping y hace cuánto llegó el último.
6. **El welcome que KuCoin anunció el 25 ago 2026** (y aplazó el 27 ago, sin fecha nueva): `"data": "welcome"` y `pingTimeout`. Con el código anterior, el día del cambio el grabador habría dejado de conectar.
7. **El diario** recoge:
   - cada desconexión (`ws_disconnected`), con su causa y la espera hasta el siguiente intento;
   - cada reconexión (`ws_connected`);
   - cada pong lento (`ws_slow_pong`);
   - una tarea que termina (`task_ended`);
   - lo escrito al pararse (`stop_recorded`).

## Instalación y primeras medidas (18 nov 2026)

**Instalada antes de su revisión, por decisión del propietario,** porque el código anterior podía volver a dejar Spot horas sin grabar.

| Paso | Resultado |
|---|---|
| Etiqueta `v0.2.5-grabador` (`db5594f`) y `02_codigo.sh` | Correcto a las 11:03 UTC. No reinicia nada |
| `systemctl restart svc-ingest` | Parado a las 11:03:31 y arrancado a las 11:03:53: **unos 27 s sin grabar**. Spot conectó a las 11:03:54 y Futures a las 11:03:59, los dos con la espera del pong de 10 s |
| `04_verificar.sh` | «All checks passed.» |

**En los 21 minutos siguientes, ningún corte**, frente a 2 de Spot en los 20 minutos anteriores.
- **Pongs lentos:** 12 en Spot, de 1,1 a 6,4 s; 4 de ellos habrían sido cortes con la espera de 3 s. Ninguno en Futures.
- **Lo observado:**
  - según el reloj del servidor y el `ts` de KuCoin, el pong se lee siempre unos 126 ms después de su `ts`, y el resto de la espera queda entre el envío del ping y ese `ts`;
  - entre el ping y el pong llegaron de 190 a 1.429 mensajes.
- **Lo que se deduce, sin demostrarlo:**
  - el retraso se produce antes de que KuCoin ponga el `ts` al pong, en el camino de ida o en KuCoin;
  - comparar dos relojes distintos tiene una incertidumbre que estas cifras no permiten separar (RFC 7679, §3.7.1), así que tampoco se descarta del todo una cola al enviar el ping;
  - que la vuelta sea tan estable indica que el pong no espera al leerse en el grabador.

## Revisión (18 nov 2026)

El revisor la dio por **APROBABLE CON CORRECCIONES**. Ve adecuados el pong de 10 s, detener el grabador para que systemd lo vuelva a arrancar y la regla del hueco de la parada. Pidió cinco correcciones, ya hechas:

- **GR-01:** la parada escribe también los huecos de un ciclo cuya escritura falló.
- **GR-02:** la espera creciente nunca desborda.
- **GR-03:** la vigilancia sigue cada segundo mientras un ciclo espera a REST o a la base de datos. El ciclo se cancela sin abandonar una escritura empezada, y la parada la espera.
- **GR-04:** un hueco cuyo inicio ya ocupa otro conserva su cola.
- **GR-05:** una parada pedida no se toma por un fallo.

**Segunda revisión (sobre `f74020e`): APROBABLE CON CORRECCIONES.** GR-02 a GR-05 quedaron resueltos. Pidió tres cosas más, ya hechas:
- **Residual de GR-01:** si falla la escritura del último ciclo de un instrumento que salió del universo, la parada lo cubre desde donde había cerrado en lo escrito.
- **GR-06:** `--sink rows` escribe las mismas colas que PostgreSQL.
- **GR-07:** este informe separa lo observado de lo deducido sobre el retraso del pong.

**Tercera revisión (sobre `8f304f5`): APROBABLE CON CORRECCIONES.** GR-06 y GR-07 quedaron resueltos. Pidió dos cosas más, ya hechas:
- **Último residual de GR-01:** una escritura que termina bien después de cancelar su ciclo lo confirma igualmente, así que la parada no añade huecos sobre lo escrito.
- **GR-08:** una prueba que fallaba si el vigilante detectaba un bloqueo justo antes de la parada.

**Cuarta revisión (sobre `cb4c275`): APROBABLE CON CORRECCIONES.** GR-08 quedó resuelto, sin hallazgos nuevos. Quedaba un último matiz de GR-01, ya corregido: la confirmación de una escritura se hace dentro de la propia escritura, antes de darla por terminada, así que la parada nunca puede llegar antes que ella.

**Quinta revisión (sobre `2ffd88b`): APROBADO.** GR-01 a GR-08 quedan cerrados, sin hallazgos nuevos.

## El primer corte con el código nuevo (18 nov, 12:49 UTC)

Con `v0.2.5-grabador` instalada, KuCoin tuvo unos minutos lentos en Spot, y el diario lo deja ver entero:
- **12:47 a 12:48:** los pongs se alargan a 4,9, 8,4 y 4,1 s.
- **12:49:04:** un pong no llega en 10 s. Mientras tanto seguían entrando datos: 2.104 mensajes desde el ping, el último 0,0 s antes.
- **Cinco reintentos fallidos:**
  - cuatro veces KuCoin no confirma en 10 s las 70 suscripciones (`no acknowledgement`);
  - una vez el handshake expira (`TimeoutError`).
  - Las esperas crecieron: 5,0; 5,0; 5,6; 10,3; 33,6 y 46,6 s.
- **12:53:02:** se reconecta solo, sin reinicios. Queda un hueco `sin_pong` de 4 min en los 70 canales de Spot.

Con el código anterior, un episodio así podía dejar Spot muerto en silencio durante horas, como el 17 nov. Una espera de pong más larga no lo habría evitado, porque las reconexiones también fallaban.

## Lo que queda

- **Una parada que no llega a escribir lo abierto sigue perdiendo el hueco abierto.** Pasa con un proceso matado (`SIGKILL`) o un corte de luz. Los minutos escritos con la conexión caída conservan sus filas S3, y los que no tienen vela quedan `sin_explicar`.
- **No se sabe por qué KuCoin respondió HTTP 400.** No está documentado, y el código anterior no lo registraba; ahora quedaría en el diario.
- **La espera del pong (10 s) se sigue midiendo con más días.** En las primeras 1 h 51 min, el pong más lento que llegó a tiempo tardó 8,4 s, y uno pasó de 10 s (el corte de las 12:49).
- **Instalar la versión aprobada (`2ffd88b`),** con una etiqueta nueva.
