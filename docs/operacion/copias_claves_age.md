# Claves de cifrado de las copias (E4c): lo que hace el propietario

Las copias se cifran con `age` para dos destinatarios: la clave **principal** y la de **recuperación** (`02` §13.1, punto 4). En el servidor solo van las claves **públicas** (empiezan por `age1`). Las privadas (empiezan por `AGE-SECRET-KEY-`) se guardan en el disco externo y en papel: **nunca** van al servidor, a Git ni a Claude. Por eso estos pasos los hace el propietario en su propia ventana de PowerShell, no Claude.

## Pasos, en el PC

1. **Conectar el disco externo** y mirar qué letra le da Windows (Explorador de archivos → «Este equipo»), por ejemplo `D:`.
2. **Instalar `age`**, en una ventana de PowerShell:

   ```
   winget install --id FiloSottile.age -e --source winget
   ```

   Si pregunta por los términos, responder `Y`.
3. **Cerrar esa ventana y abrir otra**, para que Windows encuentre el programa. Comprobarlo con:

   ```
   age-keygen --version
   ```

4. **Crear las dos claves en el disco externo**, cambiando `D:` por la letra del disco:

   ```
   New-Item -ItemType Directory -Force D:\kbot_claves
   age-keygen -o D:\kbot_claves\age_principal.txt
   age-keygen -o D:\kbot_claves\age_recuperacion.txt
   ```

   Cada orden muestra una línea `Public key: age1…`: esa es la clave pública.
5. **Guardar en papel la parte secreta** de cada clave: abrir el archivo con `notepad D:\kbot_claves\age_principal.txt` (y lo mismo con el de recuperación), sin cambiar nada, y copiar o imprimir la línea `AGE-SECRET-KEY-1…`. Guardar el papel en un sitio seguro, separado del disco.
6. **Pasar a Claude solo las dos líneas `age1…`**, diciendo cuál es la principal y cuál la de recuperación.

Si se pierden las dos claves privadas, las copias cifradas no se pueden abrir. Por eso hay dos, y cada una en dos sitios: el disco externo y el papel.
