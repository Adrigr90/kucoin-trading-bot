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
4. **Al pararse, el grabador escribe lo que deja abierto** (`RecorderCore.stop`):
   - cada hueco abierto, cerrado en ese instante y con su causa;
   - el tiempo que un instrumento que salió del universo ya no podrá cerrar.

   Al arrancar, el hueco de la parada empieza donde acaba el último hueco escrito, si es posterior al último minuto o intervalo escrito, así que nunca se solapan.
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

## Lo que queda

- **Una parada que no llega a escribir lo abierto sigue perdiendo el hueco abierto.** Pasa con un proceso matado (`SIGKILL`) o un corte de luz. Los minutos escritos con la conexión caída conservan sus filas S3, y los que no tienen vela quedan `sin_explicar`.
- **No se sabe por qué KuCoin respondió HTTP 400.** No está documentado, y el código anterior no lo registraba.
- **La espera del pong (10 s) es provisional** hasta medir los pongs lentos con el grabador corregido.
- **Pendiente de revisión.** El despliegue (`02_codigo.sh` con la etiqueta nueva y el reinicio del grabador) lo decide el propietario.
