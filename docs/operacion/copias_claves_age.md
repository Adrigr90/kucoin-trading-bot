# Claves de cifrado de las copias (E4c): lo que hace el propietario

Las copias se cifran con `age` para dos destinatarios: la clave **principal** y la de **recuperación** (`02` §13.1, punto 4). En el servidor solo van las claves **públicas** (empiezan por `age1`). Las privadas (empiezan por `AGE-SECRET-KEY-`) se guardan en el PC, en `C:\kbot_claves`, y en papel: **nunca** van al servidor, a Git ni a Claude. Por eso estos pasos los hace el propietario en su propia ventana de PowerShell, no Claude.

**Decisión del propietario del 17 nov 2026:** las claves privadas, en el disco del PC en lugar del disco externo. Los 2 GB del histórico de la Fase 3 caben también en `C:`. La copia en casa de las copias del servidor (E4c-3) no cabe, porque el archivo crece unos 32 GB al año; se decide al llegar a esa parte. Si el PC se pierde, la copia en papel es la que queda: por eso es obligatoria.

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

3. **Crear las dos claves:**

   ```
   New-Item -ItemType Directory -Force C:\kbot_claves
   age-keygen -o C:\kbot_claves\age_principal.txt
   age-keygen -o C:\kbot_claves\age_recuperacion.txt
   ```

   Cada orden muestra una línea `Public key: age1…`: esa es la clave pública.
4. **Guardar en papel la parte secreta** de cada clave: abrir el archivo con `notepad C:\kbot_claves\age_principal.txt` (y lo mismo con el de recuperación), sin cambiar nada, y copiar o imprimir la línea `AGE-SECRET-KEY-1…`. Guardar el papel en un sitio seguro, lejos del PC.
5. **Pasar a Claude solo las dos líneas `age1…`**, diciendo cuál es la principal y cuál la de recuperación.

Si se pierden las dos claves privadas, las copias cifradas no se pueden abrir. Por eso hay dos, y cada una en dos sitios: el PC y el papel.
