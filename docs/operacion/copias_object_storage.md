# Guía: el cubo de Object Storage para las copias (E4c-2)

Para el propietario. Son pasos en la consola web de Oracle (https://cloud.oracle.com), en castellano, con el nombre en inglés de cada botón. Lo que crea es gratuito, dentro de los límites de *Always Free*: 20 GB y 50.000 peticiones al mes.

**El revisor aprobó el diseño de E4c-2** el 26 nov 2026 (`ae0b217`, `02` §13.1, punto 4), así que ya puedes hacerlo. Hasta que el código esté instalado nada lo usa, y no pasa nada porque ya exista.

Haz los cuatro apartados en este orden: la regla de ciclo de vida necesita antes la política.

## 1. El cubo

1. Menú ☰ (arriba a la izquierda) → **Storage** (almacenamiento) → **Buckets** (cubos), en el apartado *Object Storage & Archive Storage*.
2. En **Compartment** (compartimento), elige el raíz: el que tiene el nombre de tu cuenta.
3. Pulsa **Create Bucket** (crear cubo) y rellena:
   - **Bucket Name** (nombre): `kbot-backups`;
   - **Default Storage Tier** (nivel): **Standard**;
   - **Enable Auto-Tiering** (escalonado automático): **sin marcar**;
   - **Enable Object Versioning** (versionado de objetos): **marcado**. Es lo que permite recuperar una copia borrada durante un día;
   - **Emit Object Events** (eventos): sin marcar;
   - **Encryption** (cifrado): deja *Encrypt using Oracle managed keys* (claves de Oracle). Las copias ya van cifradas por nosotros.
4. Pulsa **Create** (crear).

## 2. El grupo dinámico: solo este servidor

1. Menú ☰ → **Identity & Security** (identidad y seguridad) → **Domains** (dominios) → **Default** (el dominio por defecto).
2. En el menú del dominio, **Dynamic groups** (grupos dinámicos) → **Create dynamic group** (crear grupo dinámico).
3. Rellena:
   - **Name** (nombre): `kbot-servidor`;
   - **Description** (descripción): `El servidor del bot, para subir las copias`;
   - **Matching rules** (reglas): *Match any rules defined below* (cualquiera de las reglas);
   - **Rule 1** (regla 1), copiada tal cual:

     ```
     instance.id = 'ocid1.instance.oc1.eu-frankfurt-1.antheljtqfngndqcrywoc64v4oyhx2ylypf2xn3qwx3f4tag7jf2glask6ha'
     ```
4. Pulsa **Create** (crear).

## 3. La política: lo que puede hacer el servidor, y nada más

1. Menú ☰ → **Identity & Security** (identidad y seguridad) → **Policies** (políticas).
2. En **Compartment** (compartimento), el raíz.
3. Pulsa **Create Policy** (crear política) y rellena:
   - **Name** (nombre): `kbot-copias`;
   - **Description** (descripción): `Copias del bot en kbot-backups`;
   - **Compartment**: el raíz.
4. Activa **Show manual editor** (editor manual) y pega estas tres líneas tal cual:

   ```
   Allow dynamic-group 'Default'/'kbot-servidor' to manage objects in tenancy where all {target.bucket.name = 'kbot-backups', any {request.permission = 'OBJECT_CREATE', request.permission = 'OBJECT_INSPECT', request.permission = 'OBJECT_READ', request.permission = 'OBJECT_DELETE'}}
   Allow dynamic-group 'Default'/'kbot-servidor' to read buckets in tenancy where target.bucket.name = 'kbot-backups'
   Allow service objectstorage-eu-frankfurt-1 to manage object-family in tenancy where target.bucket.name = 'kbot-backups'
   ```

   **Qué significan:**
   - **La primera:** el servidor puede subir, listar, leer y borrar copias en ese cubo. No puede sobrescribir una copia ni destruir sus versiones anteriores, así que una copia borrada por error se recupera durante un día.
   - **La segunda:** el servidor puede comprobar que el versionado está activo.
   - **La tercera:** Oracle puede aplicar la regla del apartado 4, que borra las versiones anteriores pasado un día.
5. Pulsa **Create** (crear). Si la consola da un error de sintaxis, no cambies nada por tu cuenta: hazme una captura y lo revisamos.

## 4. La regla de ciclo de vida: las versiones anteriores, un día

1. Vuelve al cubo `kbot-backups`: menú ☰ → **Storage** → **Buckets** → `kbot-backups`.
2. En el menú del cubo, **Lifecycle Policy Rules** (reglas de ciclo de vida) → **Create Rule** (crear regla). Rellena:
   - **Name** (nombre): `borrar-versiones-anteriores`;
   - **Target** (objetivo): **Previous object versions** (versiones anteriores de los objetos);
   - **Lifecycle Action** (acción): **Delete** (borrar);
   - **Number of days** (días): `1`;
   - **State** (estado): **Enabled** (activada).
3. Pulsa **Create** (crear).

## Cuando termines

Escríbeme «hecho». Desde el servidor compruebo que todo responde: que subir funciona, que sobrescribir falla, que se puede leer y borrar, y que el versionado está activo. Lo hago sin ninguna contraseña ni clave en el servidor, porque la identidad la da el propio grupo dinámico.
