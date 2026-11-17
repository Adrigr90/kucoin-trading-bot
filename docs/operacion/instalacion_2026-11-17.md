# Instalación en la instancia de Oracle (E4a y E4b), 17 de noviembre de 2026

Instancia `kbot` (AD-3, `VM.Standard.A1.Flex`, 2 OCPU, 12 GB, Ubuntu 24.04 aarch64, volumen de datos de 110 GB en `/dev/sdb`). Claude ejecutó cada orden por SSH desde el PC del propietario y con su permiso (P-E4-1). Antes, el revisor había aprobado el cortafuegos y la exención del agente de Oracle (`5d7e344`). El propietario autorizó crear las etiquetas y, durante esta instalación, corregir sin volver a preguntar los fallos pequeños que aparecieran, con la CI en verde antes de cada etiqueta. Los registros completos están en el servidor, en `/var/log/kbot-*.log`.

## Orden y resultado

| Paso | Etiqueta | Resultado |
|---|---|---|
| `git clone --branch` en `/opt/kbot` | `v0.2.0-e4b` (`5d7e344`) | El clon quedó sin archivos de trabajo: el filtro de la salida (un `grep` mal citado por PowerShell) cerró la tubería mientras `git` los escribía. Restaurado con `git reset --hard`; `git fsck --connectivity-only` correcto y ningún cambio local |
| `01_sistema.sh /dev/sdb` | `v0.2.0-e4b` | Hizo todos sus pasos hasta `fail2ban` incluido: volumen `kbot-data` formateado y montado en `/srv/kbot`, paquetes, usuarios, `sshd`, `rules.v6`, guarda y `fail2ban`. **Se detuvo en la comprobación final del cortafuegos** (hallazgos 1 y 2) |
| `01_sistema.sh /dev/sdb` | `v0.2.1-e4b` (`f9d900c`) | Correcto (`exit=0`) |
| `02_codigo.sh` | `v0.2.1-e4b` | Correcto: `uv` 0.13.7 comprobado contra `uv.sha256`; 21 paquetes exactamente de `uv.lock` con CPython 3.12.3; `kbot_ingest` importa el código |
| `03_postgresql.sh` | `v0.2.1-e4b` | PostgreSQL 18.7 (PGDG, aarch64) con el clúster `18/main` en `/srv/kbot/postgresql/18/main`, la configuración de producción, los roles y la base. **Se detuvo en las migraciones** (hallazgo 3) |
| `02_codigo.sh` y `03_postgresql.sh` | `v0.2.2-e4b` (`6532ff7`) | Correctos. `03` se reanudó: lo existente se saltó, y las migraciones llegaron a `0003 (head)` |
| `04_verificar.sh` | `v0.2.2-e4b` | Unidades de systemd, PostgreSQL contra `02` §9 y F1-A6 con los usuarios reales, **correctos**. F2-A10: nueve de diez comprobaciones en verde; **fallaron los puertos a la escucha** (hallazgo 4) |

## Comprobaciones en el servidor, desde el contexto real de cada usuario

| Prueba | Resultado |
|---|---|
| Guarda de metadatos (`kbot-guardcheck@`) | Rechazada para `kbot_ingest`, `kbot_archive`, `kbot_backup`, `kbot_restore` y `kbot_sync`; alcanzable para `snap_daemon` y `root`. Todo como se espera |
| B19 (`kbot-netcheck@`, con `IPAddressDeny=any`) | El socket de PostgreSQL acepta a `kbot_ingest`, `kbot_archive` y `kbot_backup` y rechaza a `kbot_restore` y `kbot_sync` (sin rol); KuCoin, inalcanzable para los cinco |
| Bloqueo REST entre dos usuarios (E4-D06) | Con `kbot_ingest` reteniendo el bloqueo, `kbot_archive` recibe `RestBudgetInUse`; cuando `kbot_ingest` lo suelta, `kbot_archive` lo adquiere. Los dos con `SupplementaryGroups=kbot`, como los servicios |
| Agente de Oracle con la guarda activa | El `updater` mantiene tres conexiones establecidas con `169.254.169.254:80`; el agente y `gomon` siguen en marcha como `snap_daemon`; sin errores en sus últimas 200 líneas de registro |
| F1-A6 con los usuarios reales (`f1a6_servidor.sh`) | Correcto: cada usuario conecta solo con su rol y ve su `statement_timeout`, los usuarios sin rol son rechazados, nadie puede crear nada ni hacerse `kbot_owner` |
| PostgreSQL contra `02` §9 (`pgcheck`) | Correcto: catálogo, roles, ajustes, privilegios, `pg_hba`, sin TCP, bloqueos y migraciones |

## Hallazgos y correcciones

1. **Comprobación final de `01` (de Claude).** `iptables -S | grep -q` falla con `pipefail` cuando `grep` deja de leer pronto e `iptables` recibe una tubería rota, aunque la regla esté cargada. Ahora las reglas se leen enteras antes de buscar en ellas (`f9d900c`).
2. **Recarga del cortafuegos (de la imagen).** La imagen configura `IPTABLES_RESTORE_NOFLUSH=yes` en `/etc/default/netfilter-persistent`, así que `netfilter-persistent reload` añadió las reglas de los archivos a las ya cargadas: todas las de `INPUT`, `FORWARD` y `OUTPUT` quedaron dos veces. No tuvo efecto práctico, porque la segunda copia nunca se alcanza. `01` carga ahora los archivos con `iptables-restore` sin `--noflush`, reemplazando la tabla, y la CI reproduce las dos formas de carga (`f9d900c`).
3. **Migraciones (del entorno).** Alembic 1.20 busca `pyproject.toml` en el directorio actual, que era el del administrador, y `postgres` no puede leerlo (`EACCES`). En la CI el directorio actual es el repositorio. `03` y `04` entran ahora en `/opt/kbot` (`6532ff7`).
4. **`rpcbind` en el puerto 111 (de la imagen).** La imagen trae `nfs-common`, que instala `rpcbind`, activado por su socket en el puerto 111 de todas las direcciones, TCP y UDP. Nada usa NFS, y el cortafuegos y la lista de seguridad ya bloqueaban ese puerto, pero solo SSH debe escuchar (`02` §9). `01` lo para y lo enmascara (`132b91b`).

## Final: todo en verde (`v0.2.3-e4b`, `132b91b`)

| Paso | Resultado |
|---|---|
| `02_codigo.sh v0.2.3-e4b` y `01_sistema.sh /dev/sdb` | Correctos (`exit=0`). Ningún socket en el puerto 111; `rpcbind.socket` y `rpcbind.service`, enmascarados |
| `04_verificar.sh` | **«All checks passed.»** Unidades de systemd; F2-A10 con **diez de diez** comprobaciones en verde ([`comprobacion_2026-11-17.md`](comprobacion_2026-11-17.md)); PostgreSQL contra `02` §9; F1-A6 con los usuarios reales (60 líneas, todas en verde) |

Al terminar la instalación no había nada del bot arrancado.

## Arranque del grabador (decisión del propietario)

El propietario decidió arrancarlo sin esperar a la revisión de la instalación, sabiendo que las copias llegan en E4c. A las 10:56 UTC se ejecutó `sudo systemctl enable --now kbot-archive-daily.timer svc-ingest`:

- `kbot-archive-prepare` creó 540 particiones diarias (de hoy − 30 a hoy + 14) y terminó correctamente.
- `svc-ingest` arrancó con **40 instrumentos** (20 de Spot y 20 de Futures; 10 + 10 con profundidad de nivel 50, como en P3).
- El temporizador del archivo diario corre por primera vez el 18 nov a las 00:20 UTC (más su retraso aleatorio).
- A los dos minutos había 40 velas REST y 40 del WebSocket (el primer minuto cerrado de cada instrumento), 100 filas de calidad por minuto, 60 instantáneas y 450 métricas de profundidad, sin reinicios y sin errores ni avisos en su registro; 117 MiB de memoria de los 1 GiB de su límite.

## Lo que queda

- **Métricas del agente en la consola de Oracle** (*Compute → Instances → kbot → Metrics*): las mira el propietario dentro de un rato, para confirmar que la monitorización sigue llegando con la guarda activa.
- **E4c** (copias y restauración) y **E4d** (operación y medición).
