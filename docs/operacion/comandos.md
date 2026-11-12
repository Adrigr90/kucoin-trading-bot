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
| `sudo git clone https://github.com/Adrigr90/kucoin-trading-bot /opt/kbot` | Primera copia del repositorio en el servidor |
| `sudo bash /opt/kbot/deploy/install/01_sistema.sh <IP del propietario>/32` | Endurece el sistema: comprueba y monta el volumen de datos de 110 GB en `/srv/kbot` (si no está conectado, se niega a seguir), paquetes de seguridad, usuarios de servicio sin shell, SSH solo con clave, `ufw` solo con SSH desde esa IP, la guarda del servicio de metadatos como servicio propio (`kbot-metadata-guard`, cargado después de `ufw`), `fail2ban`, actualizaciones de seguridad sin reinicio, bloqueo REST compartido y unidades de systemd. Se puede repetir |
| `sudo bash /opt/kbot/deploy/install/03_codigo.sh <etiqueta>` | Pone el código en la etiqueta aprobada, instala exactamente `uv.lock` con un `uv` 0.13.7 cuya descarga se comprueba contra `deploy/install/uv.sha256` (la huella que Claude verificó en el PC contra el archivo y el `.sha256` que publica la versión; una huella distinta aborta), y verifica las unidades de systemd ya con su intérprete |
| `sudo /opt/kbot/.venv/bin/python -m kbot.ops --report /opt/kbot/docs/operacion/comprobacion_<fecha>.md` | La comprobación automática de F2-A10: puertos, `sshd`, `ufw`, `fail2ban`, guarda de metadatos, actualizaciones, usuarios y PostgreSQL. Escribe el informe que se guarda en el repositorio |
| `sudo systemctl start kbot-netcheck@kbot_ingest` y `sudo journalctl -u kbot-netcheck@kbot_ingest` | B19 desde el contexto real de un servicio, con `IPAddressDeny=any`: el socket de PostgreSQL funciona para un usuario con rol (y se rechaza para `kbot_sync` o `kbot_restore`) y KuCoin es inalcanzable |
| `sudo systemctl start kbot-guardcheck@kbot_ingest` (y `@kbot_archive`, `@kbot_sync`, `@kbot_backup`) y `sudo journalctl -u kbot-guardcheck@kbot_ingest` | La guarda del servicio de metadatos (E4-D04) desde el contexto real de cada usuario y **sin ningún otro bloqueo**: solo la regla del núcleo puede rechazar la conexión. Se rechaza para todos salvo `root` (y `kbot_backup` a partir de E4c, cuando la guarda, `netcheck` y `kbot.ops` se actualicen a la vez) |
| `sudo systemctl status kbot-metadata-guard` y `sudo nft -j list table inet kbot_guard` | Estado de la guarda y la regla cargada |
| `sudo ufw status verbose` | Muestra el cortafuegos |
| `sudo ufw allow from <IP nueva>/32 to any port 22 proto tcp` y `sudo ufw delete allow from <IP vieja>/32 to any port 22 proto tcp` | Cuando cambia la IP doméstica. Si ya no se puede entrar por SSH, se hace desde la consola serie de Oracle |
| `sudo systemctl status svc-ingest` | Estado del grabador |
| `sudo journalctl -u svc-ingest --since "1 hour ago"` | Registro del grabador de la última hora |
| `sudo systemctl enable --now kbot-archive-daily.timer svc-ingest` | Arranca el grabador y el archivo diario (al final de E4b, con PostgreSQL ya instalado) |
| `sudo systemctl stop svc-ingest` | Para el grabador |
| `sudo reboot` | Reinicia el servidor. Solo lo decide el propietario (las actualizaciones nunca reinician solas) |

## Lo que nunca se ejecuta

- Nada que cree claves de API de Oracle, de KuCoin o de otro servicio.
- Nada que pase la cuenta de Oracle a pago ni cree recursos fuera de Always Free (D01).
- Nada que borre particiones de producción antes de la prueba integral de restauración (02 §3.9).
