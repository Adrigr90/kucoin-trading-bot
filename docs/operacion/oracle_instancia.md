# Crear la instancia en Oracle Cloud (etapa E4a, guía para el propietario)

Todo lo de esta guía lo hace el propietario en la consola de Oracle (https://cloud.oracle.com/), con Claude guiándole. **Oracle nunca cobra mientras la cuenta no se pase a pago, y no se pasa nunca** (D01). Comprobado el 11 nov 2026: la cuota de la cuenta en Frankfurt es de 16 núcleos y 96 GB de A1; lo gratuito para siempre es menos, y la instancia de abajo cabe en ello.

## 1. Clave SSH (en el PC, antes de nada)

En PowerShell:

```
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\kbot_admin" -C "kbot admin"
```

Pide una frase de paso: elegirla y **no decírsela a nadie**, tampoco a Claude. Deja dos archivos: `kbot_admin` (privada, nunca sale del PC) y `kbot_admin.pub` (pública, se pega en Oracle).

## 2. Instancia

Menú ☰ → **Compute** → **Instances** → **Create instance**.

| Campo | Valor |
|---|---|
| Name | `kbot` |
| Compartment | el raíz (`adritrb (root)`) |
| Placement | región **Germany Central (Frankfurt)**; dominio de disponibilidad: cualquiera (si uno no tiene capacidad, probar otro) |
| Image | **Ubuntu** → **Canonical Ubuntu 24.04** (la de **aarch64**, para Ampere) |
| Shape | **Ampere** → **VM.Standard.A1.Flex**, **2 OCPU** y **12 GB** de memoria. Debe aparecer la etiqueta *Always Free-eligible* |
| Networking | crear una VCN nueva (`kbot-vcn`) y una subred pública nuevas; **asignar IPv4 pública: sí** |
| Add SSH keys | **Paste public keys** → pegar el contenido de `kbot_admin.pub` |
| Boot volume | **50 GB** (el mínimo que la consola permita a partir de 47) |
| Oracle Cloud Agent (en *Show advanced options*) | **Compute Instance Monitoring: activado** (imprescindible para B30) |

Pulsar **Create**. Si sale *Out of host capacity*, volver a intentarlo más tarde o en otro dominio de disponibilidad: **nunca cambiar a una forma de pago**.

Apuntar la **IP pública** y el **OCID** de la instancia (en su página, *Instance information*).

## 3. Volumen de datos (110 GB)

Menú ☰ → **Storage** → **Block Volumes** → **Create block volume**: nombre `kbot-datos`, mismo compartimento y dominio de disponibilidad que la instancia, **110 GB**, rendimiento *Balanced*. Después, en la instancia → **Attached block volumes** → **Attach block volume** → el volumen, tipo **Paravirtualized**, lectura y escritura. En el servidor aparecerá como `/dev/oracleoci/oraclevdb`.

## 4. Lista de seguridad: SSH solo desde la IP del propietario

En la VCN → **Security Lists** → la lista por defecto → **Ingress Rules**: dejar **una sola regla** de entrada: origen `<IP pública del propietario>/32`, protocolo TCP, puerto de destino 22. Borrar las demás reglas de entrada. (La IP del propietario se ve en https://api.ipify.org desde el PC.) Cuando la IP doméstica cambie, se actualiza aquí y en `ufw` (diccionario de comandos).

## 5. Primera conexión

En PowerShell:

```
ssh -i "$env:USERPROFILE\.ssh\kbot_admin" ubuntu@<IP pública>
```

La primera vez pregunta si se confía en la huella del servidor: responder `yes`. Si entra, la instancia está lista para `01_sistema.sh` (diccionario de comandos). Después, `ssh-add` para que Claude pueda conectarse sin la frase.

## 6. Lo que viene después (E4c)

Grupo dinámico, política, cubo con versionado y alarma de correo: se crean cuando llegue la subetapa de copias, con su propia guía.
