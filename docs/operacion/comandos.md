# Diccionario de comandos del servidor (etapa E4)

Cada comando que Claude ejecuta en el servidor por SSH (respuesta P-E4-1) aparece aquí con lo que hace, en orden de uso. El propietario puede ejecutar cualquiera de ellos por su cuenta; los que empiezan por `sudo` necesitan al administrador (`ubuntu`). **Ninguno pide ni guarda claves ni contraseñas.**

## Conexión desde el PC del propietario

| Comando (PowerShell) | Qué hace |
|---|---|
| `ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\kbot_admin" -C "kbot admin"` | Crea el par de claves del administrador. La frase de paso solo la conoce el propietario |
| `ssh-add "$env:USERPROFILE\.ssh\kbot_admin"` | Carga la clave en el agente de Windows hasta el siguiente reinicio: Claude puede conectarse sin ver la frase |
| `ssh ubuntu@<IP> 'comando'` | Ejecuta un comando en el servidor y vuelve. Es la forma en que Claude trabaja: un comando visible cada vez |
| `ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\kbot_sync" -C "kbot sync"` | La clave del usuario de sincronización del disco externo (E4c), distinta de la del administrador |

## E4a. Sistema

| Comando (en el servidor) | Qué hace |
|---|---|
| `sudo git clone --branch <etiqueta> https://github.com/Adrigr90/kucoin-trading-bot /opt/kbot` | Primera copia del repositorio en el servidor, ya en la etiqueta aprobada: `main` no tiene `deploy/` hasta que se integre E4 |
| `sudo bash /opt/kbot/deploy/install/01_sistema.sh /dev/sdb` | Endurece el sistema: comprueba y monta el volumen de datos de 110 GB (en esta instancia, `/dev/sdb`) en `/srv/kbot` (si no está conectado, se niega a seguir), paquetes de seguridad, usuarios de servicio sin shell, SSH solo con clave, `rpcbind` parado y enmascarado (la imagen lo trae escuchando en el puerto 111 y nada usa NFS), el cortafuegos de la imagen de Oracle (`iptables`) **sin cambiar sus reglas IPv4** (comprueba que siguen siendo las de la imagen y se detiene si no) e IPv6 cerrado, la guarda del servicio de metadatos como servicio propio (`kbot-metadata-guard`, cargado después del cortafuegos), `fail2ban`, actualizaciones de seguridad sin reinicio, bloqueo REST compartido y unidades de systemd. Se puede repetir |
| `sudo bash /opt/kbot/deploy/install/02_codigo.sh <etiqueta>` | Pone el código en la etiqueta aprobada, instala exactamente `uv.lock` con un `uv` 0.13.7 cuya descarga se comprueba contra `deploy/install/uv.sha256` (la huella que Claude verificó en el PC contra el archivo y el `.sha256` que publica la versión; una huella distinta aborta), sin verificar todavía las unidades (lo hace `04_verificar.sh`) |
| `sudo /opt/kbot/.venv/bin/python -m kbot.ops --report /opt/kbot/docs/operacion/comprobacion_<fecha>.md` | La comprobación automática de F2-A10: puertos, `sshd`, el cortafuegos (`iptables` e `ip6tables`), `fail2ban`, guarda de metadatos, actualizaciones, usuarios y PostgreSQL. Escribe el informe que se guarda en el repositorio |
| `sudo systemctl start kbot-netcheck@kbot_ingest` y `sudo journalctl -u kbot-netcheck@kbot_ingest` | B19 desde el contexto real de un servicio, con `IPAddressDeny=any`: el socket de PostgreSQL funciona para un usuario con rol (y se rechaza para `kbot_sync` o `kbot_restore`) y KuCoin es inalcanzable |
| `sudo systemctl start kbot-guardcheck@kbot_ingest` (y `@kbot_archive`, `@kbot_sync`, `@kbot_backup`, `@snap_daemon`) y `sudo journalctl -u kbot-guardcheck@kbot_ingest` | La guarda del servicio de metadatos (E4-D04) desde el contexto real de cada usuario y **sin ningún otro bloqueo**: solo la regla del núcleo puede rechazar la conexión. Se rechaza para todos salvo `root`, `snap_daemon`, el usuario con el que corre el agente de Oracle, y, desde E4c-2, `kbot_upload`, el de las subidas, que no tiene acceso a la base (`kbot_backup` se rechaza siempre) |
| `sudo ss -Htnp state all dst 169.254.169.254` | Quién está conectado al servicio de metadatos. Con la guarda activa, el agente de Oracle (`agent`, `updater` o `gomon`, que corren como `snap_daemon`) debe seguir apareciendo, y ningún proceso de `kbot` |
| `sudo systemctl status kbot-metadata-guard` y `sudo nft -j list table inet kbot_guard` | Estado de la guarda y la regla cargada |
| `sudo iptables -S` y `sudo ip6tables -S` | Muestran el cortafuegos: en IPv4, las reglas de la imagen (entrada nueva solo para SSH y las reglas propias de Oracle; la IP del propietario se limita en la lista de seguridad de Oracle, no aquí); en IPv6, todo cerrado. Si `fail2ban` ha bloqueado alguna dirección, aparece además su cadena `f2b-sshd` |
| `sudo iptables-restore < /etc/iptables/rules.v4`, `sudo ip6tables-restore < /etc/iptables/rules.v6` y `sudo systemctl restart fail2ban` | Recargan el cortafuegos desde sus archivos, **reemplazando** lo cargado. **Nunca `netfilter-persistent reload`**: la imagen lo configura para añadir en vez de reemplazar, y duplica las reglas (17 nov 2026) |
| *(en la consola web de Oracle, no en el servidor)* | **Cuando cambia la IP doméstica:** *Networking → Virtual cloud networks → `kbot-vcn` → Security Lists → la lista por defecto → Ingress Rules*, editar la regla de SSH y poner como origen `<IP nueva>/32` (`oracle_instancia.md` §4). Se puede hacer también desde el móvil. El servidor no guarda la IP, así que no hace falta entrar en él (decisión del propietario del 17 nov 2026) |
| `sudo systemctl status svc-ingest` | Estado del grabador |
| `sudo journalctl -u svc-ingest --since "1 hour ago"` | Registro del grabador de la última hora |
| `sudo journalctl -u svc-ingest -o cat --since today \| grep -E 'ws_disconnected\|task_ended\|stop_'` | Desconexiones y paradas del grabador de hoy, con su causa y la espera hasta el siguiente intento (desde la corrección del 18 nov 2026) |
| `sudo journalctl -u svc-ingest -o cat --since today \| grep ws_slow_pong` | Los pongs que tardaron más de 1 s: cuánto tardó KuCoin en enviarlos, cuánto desde entonces y cuántos mensajes llegaron antes |
| `systemctl show svc-ingest -p NRestarts` | Cuántas veces ha vuelto a arrancar systemd el grabador tras un fallo |
| `sudo -u postgres psql -d kbot -c "SELECT market, cause, count(DISTINCT start_at) FROM market.data_gaps WHERE start_at > now() - interval '1 day' GROUP BY 1, 2"` | Cortes de las últimas 24 h por mercado y causa (solo lectura) |
| `sudo systemctl enable --now kbot-archive-daily.timer svc-ingest` | Arranca el grabador y el archivo diario (al final de E4b, con PostgreSQL ya instalado) |
| `sudo systemctl stop svc-ingest` | Para el grabador |
| `sudo reboot` | Reinicia el servidor. Solo lo decide el propietario (las actualizaciones nunca reinician solas) |

## E4b. PostgreSQL de producción

| Comando (en el servidor) | Qué hace |
|---|---|
| `sudo bash /opt/kbot/deploy/install/03_postgresql.sh` | Instala PostgreSQL 18 (PGDG, arm64), crea el clúster `18/main` con los datos en `/srv/kbot/postgresql`, aplica la configuración de producción (sin TCP, solo `peer`, sin mapa, `max_locks_per_transaction = 256`), los roles y la base de `sql/roles.sql` sentencia a sentencia (`kbot.ops.bootstrap`: lo que ya existe se salta, nada se borra) y las migraciones `0001 → 0003` como `kbot_owner`. Se puede repetir tras una interrupción en cualquier punto |
| `sudo bash /opt/kbot/deploy/install/04_verificar.sh` | **La verificación completa**, al final: unidades de systemd, endurecimiento (F2-A10), PostgreSQL contra 02 §9 y F1-A6 con los usuarios reales. Ejecuta todas aunque alguna falle y no cambia nada en el servidor |
| `sudo -u postgres /opt/kbot/.venv/bin/python -m kbot.ops.pgcheck --repo /opt/kbot` | Compara el clúster con 02 §9: huella de las funciones `SECURITY DEFINER`, atributos de los roles, ningún ajuste ligado a una base de datos, **privilegios efectivos, pertenencias y propietarios exactamente como la matriz**, `pg_hba` solo `local peer` sin mapa, `listen_addresses` vacío, bloqueos y la migración del código desplegado |
| `sudo bash /opt/kbot/deploy/install/f1a6_servidor.sh` | F1-A6 con los usuarios reales (punto 31): cada usuario de servicio conecta solo con su rol y ve su `statement_timeout`, los usuarios sin rol son rechazados, los propietarios no pueden entrar y nadie puede crear nada ni hacerse `kbot_owner`; un rechazo solo cuenta si PostgreSQL responde con el código `42501` |
| `sudo -u postgres psql -d kbot -c '\dt market.*'` | Mira las tablas del esquema de mercado |
| `sudo pg_lsclusters` | Estado del clúster |

## E4c. Copias

| Comando (en el servidor) | Qué hace |
|---|---|
| `sudo bash /opt/kbot/deploy/install/05_copias.sh` | Instala las claves públicas de las copias (nunca una privada), los directorios y las unidades; hace una primera copia de cada clase como comprobación y activa los temporizadores (cada hora en el minuto 40 y los domingos a las 03:40 UTC). Desde E4c-2, además: crea el usuario `kbot_upload`, pasa las copias al grupo que las sube, prepara `/srv/kbot/subidas` con su contador de peticiones, carga el guardián de metadatos que deja pasar a `kbot_upload`, lo comprueba desde cada usuario, y hace una primera subida al cubo como comprobación (necesita el cubo, el grupo dinámico y la política de `copias_object_storage.md`). No se lanza entre los minutos 40 y 50. Se puede repetir |
| `sudo systemctl start kbot-backup.service` | Hace ahora una copia (la de los días sellados que falten y la caliente) y aplica la retención |
| `sudo systemctl list-timers 'kbot-backup*' kbot-upload.timer` | Cuándo tocan las próximas copias y la próxima subida |
| `sudo journalctl -u kbot-backup -n 20` | Registro de las últimas copias |
| `sudo ls -l /srv/kbot/backups` | Las copias que hay en el servidor; cada una es un directorio con `volcado.age` (cifrado) y `manifiesto.json` |
| `sudo systemctl start kbot-upload.service` | Hace ahora un ciclo de subida al cubo (E4c-2): lista el cubo, sube lo que falta, lo relee y aplica la retención |
| `sudo journalctl -u kbot-upload -n 30` | Registro de las últimas subidas, con sus errores |
| `sudo cat /srv/kbot/subidas/estado.json` | El estado de las subidas: el último listado, las copias verificadas fuera, lo que ocupa el cubo, las peticiones de los últimos 32 días y la hora de la última subida completa (el RPO frente a la pérdida del servidor) |
| `sudo systemctl start kbot-guardcheck@kbot_upload` | Comprueba que `kbot_upload` llega a los metadatos de la instancia (y con `@kbot_backup`, que `kbot_backup` no) |
| `sudo systemctl start kbot-backup-previa.service` | La copia autocontenida previa a una actualización; `02_codigo.sh` la hace sola desde E4c-2 |
| `python -m tools.simular_copias --subida-caliente-cada 6` | En el PC, no en el servidor: la simulación del presupuesto del cubo (120 días por defecto) |

Las copias solo se descifran con las claves privadas del propietario, que nunca están en el servidor.

## Lo que nunca se ejecuta

- Nada que cree claves de API de Oracle, de KuCoin o de otro servicio.
- Nada que pase la cuenta de Oracle a pago ni cree recursos fuera de Always Free (D01).
- Nada que borre particiones de producción antes de la prueba integral de restauración (02 §3.9).
