# Guía: el disco externo de las copias (E4c-3)

Para el propietario. Estos pasos se hacen en el PC, en una ventana de PowerShell. Llevan a un pen drive, o a un disco externo, una copia de lo que guarda el servidor: el archivo de mercado (los Parquet) y las copias cifradas de la base. Así, si se pierden a la vez el servidor y el cubo de Oracle, los datos siguen en casa (02 §13.1, punto 4, E4c-3).

**Nada de esto da acceso a Oracle ni a la base.** El PC entra en el servidor como `kbot_sync`, un usuario que solo puede leer las copias y el archivo y dejar sus recibos en una carpeta propia de 256 MiB. Las copias siguen cifradas con tus claves `age`, que nunca van al disco ni al servidor.

## Lo que necesitas

- **Un pen drive de 128 GB**, o un disco externo. Con los tamaños de hoy da para algo más de un año: se llenan de 69 a 87 GB al año.
- **Formateado en NTFS.**
- **Conectarlo al menos una vez por semana** y ejecutar el guion.

## 1. Preparar el pen drive (una sola vez)

**Formatear borra todo lo que tenga el pen drive.** Si tiene algo tuyo, cópialo antes a otro sitio.

1. Conecta el pen drive y abre el **Explorador de archivos** → **Este equipo**. Mira qué letra le da Windows, por ejemplo `E:`.
2. Clic derecho sobre la unidad → **Formatear…**
3. En **Sistema de archivos**, elige **NTFS**. En **Etiqueta del volumen**, escribe `KBOT`. Deja marcado **Formato rápido**.
4. Pulsa **Iniciar** y confirma.

## 2. La clave de `kbot_sync` (una sola vez)

Es una clave distinta de la del administrador (`kbot_admin`): solo sirve para leer las copias y dejar recibos.

1. En una ventana de PowerShell:

   ```
   ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\kbot_sync" -C "kbot sync"
   ```

2. Te pide una **frase de paso** dos veces. Elige una que no uses en otro sitio y guárdala como la del administrador. **No se la digas a nadie, tampoco a Claude.**
3. Cárgala en el agente de Windows, para que el guion no tenga que pedírtela:

   ```
   ssh-add "$env:USERPROFILE\.ssh\kbot_sync"
   ```

4. Muestra la **parte pública**:

   ```
   Get-Content "$env:USERPROFILE\.ssh\kbot_sync.pub"
   ```

   Es una sola línea que empieza por `ssh-ed25519` y acaba en `kbot sync`.
5. **Pasa a Claude solo esa línea.** El archivo `kbot_sync`, el que no acaba en `.pub`, es la parte privada: no sale nunca de tu PC.

Claude guarda la línea pública en el repositorio (`deploy/etc/kbot/kbot_sync.pub`) y la instala en el servidor con `06_externo.sh`, con tu permiso, como siempre. Esa instalación se niega a seguir si el archivo tiene algo que no sea exactamente una clave pública.

## 3. La primera sincronización (una sola vez, después de la instalación)

1. Conecta el pen drive. Si su letra no es `E:`, cámbiala en las órdenes siguientes.
2. En PowerShell, ve a la carpeta del repositorio:

   ```
   cd "$env:USERPROFILE\Desktop\KuCoin_Trading_Bot\kucoin-trading-bot"
   ```

3. Ejecuta el guion con `-Inicializar`:

   ```
   powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\pc\sincronizar.ps1 -Destino E:\kbot -Inicializar
   ```

   `-ExecutionPolicy Bypass` deja ejecutar este guion sin cambiar la configuración de Windows.

   `-Inicializar` crea la carpeta `E:\kbot` y dentro un archivo `.kbot-disco`, con un identificador al azar. **No lo borres:** es lo que dice al guion y al servidor que ese es tu disco.
4. La primera vez se descarga todo lo que hay en el servidor, así que tarda más que las siguientes. El tiempo depende de tu conexión.

**Solo la primera vez lleva `-Inicializar`.** Sin él, si el pen drive no está conectado o no es el tuyo, el guion se detiene sin escribir nada. Así nunca llena por error otra carpeta del PC.

## 4. Cada semana

1. Si has reiniciado el PC desde la última vez, carga la clave en el agente:

   ```
   ssh-add "$env:USERPROFILE\.ssh\kbot_sync"
   ```

2. Conecta el pen drive y ejecuta, desde la carpeta del repositorio:

   ```
   powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\pc\sincronizar.ps1 -Destino E:\kbot
   ```

3. Lee el resumen del final. Cada visita lleva de 1,3 a 1,7 GB.
4. Expulsa el pen drive desde Windows (icono de la bandeja → **Expulsar**) antes de quitarlo.

**Una generación del archivo necesita dos visitas** para quedar verificada fuera. En la primera, el guion deja su recibo y el servidor le pone una pregunta: unos bytes al azar de cada archivo. En la siguiente, el guion la responde leyendo del pen drive. Con una visita por semana, una generación queda verificada en una o dos semanas.

**Una vez al mes** el guion comprueba también, byte a byte, todo lo que ya estaba verificado. Esa visita tarda más. Puedes forzarla cuando quieras con `-Completa`.

## 5. Qué dice el resumen

| Línea | Qué significa |
|---|---|
| `Descargado y comprobado: N archivos` | Lo nuevo que ha llegado al pen drive. Cada archivo se comprueba con su huella SHA-256 antes de dejarlo en su sitio |
| `Recibos enviados: N de generación` | Las generaciones que el guion ha comprobado sobre el pen drive y ha comunicado al servidor |
| `Desafíos respondidos: N` | Las preguntas del servidor que ha respondido con los bytes del pen drive |
| `Punto recuperable del disco: caliente-… (hace X h Y min)` | La copia de la base más reciente que el pen drive tiene completa, con todo lo que necesita para restaurarse. **Su antigüedad es lo que perderías** si se perdieran a la vez el servidor y el cubo |
| `Espacio libre en el disco` | Lo que queda en el pen drive |
| `AVISO: …` | Algo que conviene saber pero no es un fallo. Por ejemplo, una copia que aún no se puede usar porque le falta una pieza, o un punto recuperable de más de 7 días |
| `ERROR: …` | Algo ha fallado. El guion termina con código 1 |

**Si sale un `ERROR`,** vuelve a ejecutar el guion: lo que no coincidió no se guarda y se descarga de nuevo. Si se repite, pásame el resumen.

**Si pone `Permission denied (publickey)`,** falta cargar la clave: haz el `ssh-add` del paso 4.1.

## 6. Si cambias de pen drive

- El servidor admite como mucho **2 discos** a la vez.
- Para usar uno nuevo, haz la primera sincronización con él (apartado 3) y dime que quieres retirar el anterior. Claude lo retira en el servidor con tu permiso:

  ```
  sudo -u kbot_archive /opt/kbot/.venv/bin/python -m kbot.archive.externo retirar-disco <identificador>
  ```

  El identificador es el contenido de `E:\kbot\.kbot-disco` del pen drive antiguo.
- **No borres el pen drive antiguo** hasta que el nuevo tenga su punto recuperable.

## Lo que no se hace nunca

- Pasar a Claude, o subir a cualquier sitio, el archivo `kbot_sync` sin `.pub`, su frase de paso o tus claves `age` privadas.
- Guardar en el pen drive las claves `age` privadas: siguen en la tarjeta SD y en papel.
- Borrar o editar a mano lo que hay en `E:\kbot`. Si necesitas espacio, dímelo y lo vemos.
