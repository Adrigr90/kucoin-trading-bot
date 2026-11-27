# Crear la instancia en Oracle Cloud (etapa E4a, guía para el propietario)

Todo lo de esta guía lo hace el propietario en la consola de Oracle (https://cloud.oracle.com/), con Claude guiándole. **Oracle nunca cobra mientras la cuenta no se pase a pago, y no se pasa nunca** (D01). Comprobado el 11 nov 2026: la cuota de la cuenta en Frankfurt es de 16 núcleos y 96 GB de A1; lo gratuito para siempre es menos, y la instancia de abajo cabe en ello.

## 1. Clave SSH (en el PC, antes de nada)

En PowerShell:

```
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\kbot_admin" -C "kbot admin"
```

Pide una frase de paso: elegirla y **no decírsela a nadie**, tampoco a Claude. Deja dos archivos: `kbot_admin` (privada, nunca sale del PC) y `kbot_admin.pub` (pública, se pega en Oracle).

## 2. Red (antes de la instancia)

Menú ☰ → **Networking** → **Virtual cloud networks** → **Start VCN Wizard** → **Create VCN with Internet Connectivity**: nombre `kbot-vcn`, compartimento raíz, el resto por defecto → **Create**. Crea una subred pública y otra privada; se usa la pública. **Se hace antes** porque, si el formulario de la instancia crea la red, la casilla de la IP pública puede quedar desactivada (16 nov 2026).

## 2b. Instancia

Menú ☰ → **Compute** → **Instances** → **Create instance**.

| Campo | Valor |
|---|---|
| Name | `kbot` |
| Compartment | el raíz (`adritrb (root)`) |
| Placement | región **Germany Central (Frankfurt)**; dominio de disponibilidad: cualquiera (si uno no tiene capacidad, probar otro) |
| Image | **Ubuntu** → **Canonical Ubuntu 24.04** (la de **aarch64**, para Ampere) |
| Shape | **Ampere** → **VM.Standard.A1.Flex**, **2 OCPU** y **12 GB** de memoria. Debe aparecer la etiqueta *Always Free-eligible* |
| Networking | **Select existing virtual cloud network** → `kbot-vcn`; **Select existing subnet** → `public subnet-kbot-vcn`; **asignar IPv4 pública: sí** |
| Add SSH keys | **Paste public keys** → pegar el contenido de `kbot_admin.pub` |
| Boot volume | **50 GB** (el mínimo que la consola permita a partir de 47) |
| Oracle Cloud Agent (en *Show advanced options*) | **Compute Instance Monitoring: activado** (imprescindible para B30) |

Elegir primero la forma (*Shape*) y después la imagen: así la lista solo enseña imágenes compatibles. La imagen es **Canonical Ubuntu 24.04**, no la *Minimal*. En *Advanced options → Oracle Cloud Agent*, dejar **solo** Compute Instance Monitoring.

Pulsar **Create**. Si sale *Out of capacity*, cambiar solo el dominio de disponibilidad (con *Previous*) y repetir; si fallan los tres, volver a intentarlo más tarde: **nunca cambiar a una forma de pago**. El 15 nov 2026 fallaron los tres; el 16 nov funcionó AD-3.

Apuntar la **IP pública** y el **OCID** de la instancia (en su página, *Instance information*).

## 3. Volumen de datos (110 GB)

Menú ☰ → **Storage** → **Block Volumes** → **Create block volume**: nombre `kbot-datos`, mismo compartimento y dominio de disponibilidad que la instancia, **110 GB**, rendimiento *Balanced*. Después, desde el volumen → **Attached instances** → **Attach to instance**: **Read/write**, tipo **Paravirtualized** (por defecto sale *iSCSI*), y la instancia eligiendo primero el compartimento y después `kbot` (el botón de la propia instancia puede salir desactivado y su buscador no encontrarla). La consola puede no ofrecer *Device path*: el 16 nov 2026 el volumen apareció en el servidor como **`/dev/sdb`**, y ese es el dispositivo que se pasa a `01_sistema.sh`, que lo monta por su etiqueta `kbot-data`.

## 4. Lista de seguridad: SSH solo desde la IP del propietario

En la VCN → **Security Lists** → la lista por defecto → **Ingress Rules**: la regla de entrada de SSH (origen `0.0.0.0/0`, TCP, puerto 22) se edita para que su origen sea `<IP pública del propietario>/32`. Las reglas ICMP se dejan: son avisos de la red y no abren ningún servicio. (La IP del propietario se ve en https://api.ipify.org desde el PC.) **Esta regla es el único filtro por IP** (decisión del propietario del 17 nov 2026): cuando la IP doméstica cambie, basta con editar aquí el origen de la regla de SSH, desde el PC o desde el móvil, sin entrar en el servidor.

**Desde el 26 nov 2026** (decisión del propietario, provisional mientras se desarrolla), el origen es el bloque del proveedor, **`79.112.0.0/12`**: la IP de casa cambió tres veces en nueve días (79.112…, 79.117… y 79.116…), siempre dentro de él. El acceso sigue siendo solo con clave, y `fail2ban` sigue activo.

## 5. Primera conexión

En PowerShell:

```
ssh -i "$env:USERPROFILE\.ssh\kbot_admin" ubuntu@<IP pública>
```

La primera vez pregunta si se confía en la huella del servidor: responder `yes`. Si entra, la instancia está lista para `01_sistema.sh` (diccionario de comandos). Después, `ssh-add` para que Claude pueda conectarse sin la frase.

## 6. Lo que viene después (E4c)

Grupo dinámico, política, cubo con versionado y alarma de correo: se crean cuando llegue la subetapa de copias, con su propia guía.
