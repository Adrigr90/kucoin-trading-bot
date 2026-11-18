# Claves de cifrado de las copias (E4c): lo que hace el propietario

Las copias se cifran con `age` para dos destinatarios: la clave **principal** y la de **recuperación** (`02` §13.1, punto 4). En el servidor solo van las claves **públicas** (empiezan por `age1`). Las privadas (empiezan por `AGE-SECRET-KEY-`) se guardan en una **tarjeta SD** y en **papel**: **nunca** van al servidor, a Git ni a Claude. Por eso estos pasos los hace el propietario en su propia ventana de PowerShell, no Claude.

**Decisión del propietario del 17 nov 2026:**
- **Las claves privadas van en una tarjeta SD de 4 GB.** Es un soporte extraíble que solo se conecta cuando hace falta (al crear las claves y en la restauración integral) y queda guardado el resto del tiempo.
- **No van en el disco externo** que preveía el diseño, porque el propietario no lo tiene a mano.
- **La copia en papel es obligatoria**, porque una tarjeta SD puede fallar.
- **El histórico de la Fase 3** (unos 2 GB) va en el disco del PC.
- **La copia en casa de las copias del servidor** (E4c-3, unos 32 GB al año) no cabe ni en la SD ni en el PC; se decide al llegar a esa parte.

## Pasos, en el PC

1. **Instalar `age`**, en una ventana de PowerShell:

   ```
   winget install --id FiloSottile.age -e --source winget
   ```

   Si pregunta por los términos, responder `Y`.
2. **Cerrar esa ventana y abrir otra**, para que Windows encuentre el programa. Comprobarlo con:

   ```
   age-keygen --version
   ```

3. **Meter la tarjeta SD** y mirar qué letra le da Windows (Explorador de archivos → «Este equipo»), por ejemplo `E:`.
4. **Crear las dos claves en la SD**, cambiando `E:` por su letra:

   ```
   New-Item -ItemType Directory -Force E:\kbot_claves
   age-keygen -o E:\kbot_claves\age_principal.txt
   age-keygen -o E:\kbot_claves\age_recuperacion.txt
   ```

   Cada orden muestra una línea `Public key: age1…`: esa es la clave pública.
5. **Guardar en papel la parte secreta** de cada clave: abrir el archivo con `notepad E:\kbot_claves\age_principal.txt` (y lo mismo con el de recuperación), sin cambiar nada, y copiar o imprimir la línea `AGE-SECRET-KEY-1…`.
6. **Sacar la SD** y guardarla en un sitio distinto del papel.
7. **Pasar a Claude solo las dos líneas `age1…`**, diciendo cuál es la principal y cuál la de recuperación.

Si se pierden las dos claves privadas, las copias cifradas no se pueden abrir. Por eso hay dos, y cada una en dos sitios: la SD y el papel.
