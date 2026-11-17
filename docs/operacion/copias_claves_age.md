# Claves de cifrado de las copias (E4c): lo que hace el propietario

Las copias se cifran con `age` para dos destinatarios: la clave **principal** y la de **recuperación** (`02` §13.1, punto 4). En el servidor solo van las claves **públicas** (empiezan por `age1`). Las privadas (empiezan por `AGE-SECRET-KEY-`) se guardan en el disco externo y en papel: **nunca** van al servidor, a Git ni a Claude.

## Pasos, en el PC, con el disco externo conectado

1. Descargar `age` para Windows de https://github.com/FiloSottile/age/releases: el archivo `age-v…-windows-amd64.zip` de la última versión. Descomprimirlo en la carpeta de usuario, de modo que quede `C:\Users\Adri\age\age-keygen.exe`.
2. En PowerShell, cambiando `E:` por la letra del disco externo:

   ```
   New-Item -ItemType Directory -Force E:\kbot_claves
   & "$env:USERPROFILE\age\age-keygen.exe" -o E:\kbot_claves\age_principal.txt
   & "$env:USERPROFILE\age\age-keygen.exe" -o E:\kbot_claves\age_recuperacion.txt
   ```

   Cada orden muestra una línea `Public key: age1…`: esa es la clave pública.
3. Copiar en papel la línea `AGE-SECRET-KEY-…` de cada archivo (se ve con `notepad E:\kbot_claves\age_principal.txt`) y guardar el papel.
4. Pasar a Claude **solo las dos líneas `age1…`**, diciendo cuál es la principal y cuál la de recuperación.

Si se pierden las dos claves privadas, las copias cifradas no se pueden abrir. Por eso hay dos, y cada una en dos sitios.
