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
| `sudo bash /opt/kbot/deploy/install/01_sistema.sh <IP del propietario>/32` | Endurece el sistema: paquetes de seguridad, usuarios de servicio sin shell, volumen de datos en `/srv/kbot`, SSH solo con clave, `ufw` solo con SSH desde esa IP, guarda del servicio de metadatos, `fail2ban`, actualizaciones de seguridad sin reinicio, bloqueo REST compartido y unidades de systemd. Se puede repetir |
| `sudo bash /opt/kbot/deploy/install/03_codigo.sh <etiqueta>` | Pone el código en la etiqueta aprobada e instala exactamente `uv.lock` con un `uv` verificado por SHA-256 (`KBOT_UV_SHA256`) |
| `sudo /opt/kbot/.venv/bin/python -m kbot.ops --report /opt/kbot/docs/operacion/comprobacion_<fecha>.md` | La comprobación automática de F2-A10: puertos, `sshd`, `ufw`, `fail2ban`, guarda de metadatos, actualizaciones, usuarios y PostgreSQL. Escribe el informe que se guarda en el repositorio |
| `sudo systemctl start kbot-netcheck@kbot_ingest` y `sudo journalctl -u kbot-netcheck@kbot_ingest` | La prueba del aislamiento de red desde el contexto real de un servicio (B19 y E4-D04): el socket de PostgreSQL funciona, KuCoin no, el servicio de metadatos se rechaza |
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
