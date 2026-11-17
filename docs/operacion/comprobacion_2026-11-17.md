# Comprobación del servidor (F2-A10), 2026-11-17 10:52 UTC

- OK   listening ports: only SSH on the network
- OK   sshd: keys only, no root, no forwarding
- OK   firewall: IPv4 exactly as the Oracle image ships it (SSH limited to the owner in the security list of Oracle), IPv6 closed; addresses banned by fail2ban: 0
- OK   netfilter-persistent: active
- OK   fail2ban: active
- OK   kbot-metadata-guard: active
- OK   metadata guard: output hook, reject by user, exempt ['root', 'snap_daemon']
- OK   unattended-upgrades: daily, no automatic reboot
- OK   service users: all present, none with a shell
- OK   postgresql: no TCP, peer only

Resultado: todo en verde
