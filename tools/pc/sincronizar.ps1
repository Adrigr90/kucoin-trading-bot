<#
.SYNOPSIS
    Copia al disco externo las copias y el archivo del servidor de kbot, comprueba cada
    archivo y responde a los desafíos del servidor (02 §13.1, punto 4, E4c-3).

.DESCRIPTION
    Lo ejecuta el propietario cuando quiere, con el disco conectado, al menos una vez por
    semana. Entra por SFTP como kbot_sync con su propia clave; el PC no tiene ninguna
    credencial de Oracle y el servidor ninguna del PC.

    Funciona con Windows PowerShell 5.1 y con PowerShell 7, sin módulos externos.

.EXAMPLE
    .\sincronizar.ps1 -Destino E:\kbot -Inicializar     # la primera vez, con un disco vacío
    .\sincronizar.ps1 -Destino E:\kbot                  # las siguientes
    .\sincronizar.ps1 -Destino E:\kbot -Completa        # comprueba también lo ya verificado
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Destino,
    [string]$Clave = (Join-Path (Join-Path $HOME '.ssh') 'kbot_sync'),
    [string]$Servidor = '92.5.8.202',
    [int]$Puerto = 22,
    [string]$Usuario = 'kbot_sync',
    [string]$KnownHosts = '',
    [string]$Sftp = 'sftp',
    [switch]$Inicializar,
    [switch]$Completa
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$Utf8 = New-Object System.Text.UTF8Encoding($false)
$Errores = New-Object System.Collections.Generic.List[string]
$Avisos = New-Object System.Collections.Generic.List[string]
$Descargados = New-Object System.Collections.Generic.List[string]

function Write-Utf8([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, $Utf8)
}

function Read-Json([string]$Path) {
    return ([System.IO.File]::ReadAllText($Path, $Utf8) | ConvertFrom-Json)
}

function Get-Lines([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    return @([System.IO.File]::ReadAllLines($Path, $Utf8) | Where-Object { $_.Trim() -ne '' })
}

function Has-Property($Object, [string]$Name) {
    return ($null -ne $Object) -and ($Object.PSObject.Properties.Name -contains $Name)
}

# --- the right disk: a marker with a random identifier, written once (R-E4-05) ----------------
if (-not (Test-Path -LiteralPath $Destino)) {
    if ($Inicializar) {
        New-Item -ItemType Directory -Path $Destino | Out-Null
    } else {
        throw "No encuentro $Destino. ¿Está conectado el disco externo?"
    }
}
$Marca = Join-Path $Destino '.kbot-disco'
if (-not (Test-Path -LiteralPath $Marca)) {
    if (-not $Inicializar) {
        throw "$Destino no es el disco de kbot (falta .kbot-disco). Si es un disco nuevo, usa -Inicializar."
    }
    $bytes = New-Object byte[] 16
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    Write-Utf8 $Marca ((($bytes | ForEach-Object { $_.ToString('x2') }) -join '') + "`n")
}
$Disco = ([System.IO.File]::ReadAllText($Marca, $Utf8)).Trim()
if ($Disco -notmatch '^[0-9a-f]{32}$') { throw "$Marca no tiene un identificador válido." }

$Temporal = Join-Path $Destino '.kbot-tmp'
if (Test-Path -LiteralPath $Temporal) { Remove-Item -LiteralPath $Temporal -Recurse -Force }
New-Item -ItemType Directory -Path $Temporal | Out-Null
$Salida = Join-Path $Temporal 'salida'
New-Item -ItemType Directory -Path $Salida | Out-Null
$EstadoDisco = Join-Path $Destino '.kbot-estado.json'
$CacheHuellas = Join-Path $Destino '.kbot-huellas.json'

# --- SFTP ---------------------------------------------------------------------------------------
function Invoke-Sftp([string[]]$Commands, [switch]$Listing) {
    $batch = Join-Path $Temporal ('lote-' + [guid]::NewGuid().ToString('N') + '.txt')
    Write-Utf8 $batch (($Commands -join "`n") + "`n")
    $arguments = @('-b', $batch, '-i', $Clave, '-P', "$Puerto",
        '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes')
    if ($KnownHosts) { $arguments += @('-o', "UserKnownHostsFile=$KnownHosts") }
    $arguments += "$Usuario@$Servidor"
    # Windows PowerShell 5.1 turns any line of a native program on stderr into a terminating
    # error under 'Stop': only the exit code decides here.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Sftp @arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    Remove-Item -LiteralPath $batch -Force
    if ($code -ne 0) {
        throw "sftp falló (código $code): $(($output | Out-String).Trim())"
    }
    if ($Listing) {
        return @($output | ForEach-Object { "$_" } | Where-Object { $_ -notmatch '^sftp>' -and $_.Trim() -ne '' } |
            ForEach-Object { ($_.Trim() -split '/')[-1] } | Where-Object { $_ -match '^[A-Za-z0-9._~-]+$' })
    }
}

function Quote([string]$Path) { return '"' + $Path.Replace('\', '/') + '"' }

# --- the fingerprints of the disk, with a cache by size and time (a full check ignores it) ------
$Huellas = @{}
if (Test-Path -LiteralPath $CacheHuellas) {
    $cached = Read-Json $CacheHuellas
    foreach ($p in $cached.PSObject.Properties) { $Huellas[$p.Name] = $p.Value }
}
$Estado = $null
if (Test-Path -LiteralPath $EstadoDisco) { $Estado = Read-Json $EstadoDisco }
$UltimaCompleta = $null
if ((Has-Property $Estado 'ultima_completa') -and $Estado.ultima_completa) {
    $UltimaCompleta = [datetime]::Parse($Estado.ultima_completa, [Globalization.CultureInfo]::InvariantCulture)
}
$Ahora = [datetime]::UtcNow
$TodoEntero = $Completa -or ($null -eq $UltimaCompleta) -or (($Ahora - $UltimaCompleta).TotalDays -ge 30)

$EnEstaVisita = @{}   # what was hashed again in this visit: never twice
function Get-Sha256([string]$Path, [switch]$Fresh) {
    # -Fresh: computed on the disk at least once in this visit; otherwise the cache by size
    # and time may answer, except in a full check.
    $item = Get-Item -LiteralPath $Path
    $key = $item.FullName.Substring((Get-Item -LiteralPath $Destino).FullName.Length).TrimStart('\', '/').Replace('\', '/')
    $stamp = "$($item.Length):$($item.LastWriteTimeUtc.Ticks)"
    $known = $Huellas.ContainsKey($key) -and $Huellas[$key].sello -eq $stamp
    if ($known -and $EnEstaVisita.ContainsKey($key)) { return $Huellas[$key].sha256 }
    if ($known -and -not $Fresh -and -not $TodoEntero) { return $Huellas[$key].sha256 }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    $Huellas[$key] = [ordered]@{ sello = $stamp; sha256 = $hash }
    $EnEstaVisita[$key] = $true
    return $hash
}

# --- 1. what the server publishes -------------------------------------------------------------
$meta = Join-Path $Temporal 'meta'
$desafiosLocal = Join-Path $meta 'desafios'
New-Item -ItemType Directory -Path $desafiosLocal -Force | Out-Null
Invoke-Sftp @(
    "get desafios/generaciones.json $(Quote $meta)/",
    "get desafios/procesados.json $(Quote $meta)/",
    "-get desafios/desafio_*.json $(Quote $desafiosLocal)/",
    "get copias/registro.jsonl $(Quote $meta)/registro.jsonl",
    "-get copias/globales/registro.jsonl $(Quote $meta)/registro_globales.jsonl"
)
$indice = @(Read-Json (Join-Path $meta 'generaciones.json'))
$procesados = Read-Json (Join-Path $meta 'procesados.json')
$listaCopias = @(Invoke-Sftp @('ls -1 copias') -Listing)
$listaGlobales = @(Invoke-Sftp @('-ls -1 copias/globales') -Listing)

function Read-Register([string]$Path) {
    $entries = @{}
    foreach ($line in (Get-Lines $Path)) {
        try { $entry = $line | ConvertFrom-Json } catch { continue }
        if (Has-Property $entry 'copia') { $entries[$entry.copia] = $entry }
    }
    return $entries
}
$registro = Read-Register (Join-Path $meta 'registro.jsonl')
$registroGlobales = Read-Register (Join-Path $meta 'registro_globales.jsonl')

# --- 2. what is wrong or missing on the disk ------------------------------------------------------
$archivoDisco = Join-Path $Destino 'archivo'
$pendientes = @()   # generations this disk must prove in this visit
foreach ($g in $indice) {
    $verificada = @($g.verificada_en_discos) -contains $Disco
    if ($TodoEntero -or -not $verificada) { $pendientes += $g }
}
$aBajar = New-Object System.Collections.Generic.List[object]   # remote, local final, expected SHA
foreach ($g in $indice) {
    foreach ($f in $g.archivos) {
        $local = Join-Path $archivoDisco $f.ruta
        $mal = $true
        if (Test-Path -LiteralPath $local) {
            $comprobar = ($pendientes -contains $g)
            $mal = $comprobar -and ((Get-Sha256 $local -Fresh:$comprobar) -ne $f.sha256)
            if ((Get-Item -LiteralPath $local).Length -ne $f.bytes) { $mal = $true }
        }
        if ($mal) { $aBajar.Add(@("archivo/$($f.ruta)", $local, $f.sha256)) }
    }
}

function Find-Copy([string]$Folder, [string]$Name, [string]$DumpSha, [string]$ManifestSha) {
    # A copy of that name already on the disk with exactly that identity, if any.
    if (-not (Test-Path -LiteralPath $Folder)) { return $null }
    foreach ($dir in @(Get-ChildItem -LiteralPath $Folder -Directory | Where-Object { $_.Name -eq $Name -or $_.Name -like "$Name~*" })) {
        $dump = Join-Path $dir.FullName 'volcado.age'
        $manifest = Join-Path $dir.FullName 'manifiesto.json'
        if ((Test-Path -LiteralPath $dump) -and (Test-Path -LiteralPath $manifest) -and
            (Get-Sha256 $dump) -eq $DumpSha -and (Get-Sha256 $manifest) -eq $ManifestSha) {
            return $dir.FullName
        }
    }
    return $null
}

function Add-Copy([string]$RemoteFolder, [string]$LocalFolder, [string]$Name, $Entry) {
    if (Find-Copy $LocalFolder $Name $Entry.sha256_cifrado $Entry.sha256_manifiesto) { return }
    $target = Join-Path $LocalFolder $Name
    if (Test-Path -LiteralPath $target) {
        # Another copy with the same name and another identity: nothing is overwritten.
        $target = Join-Path $LocalFolder ($Name + '~' + $Entry.sha256_cifrado.Substring(0, 8))
    }
    $aBajar.Add(@("$RemoteFolder/$Name/volcado.age", (Join-Path $target 'volcado.age'), $Entry.sha256_cifrado))
    $aBajar.Add(@("$RemoteFolder/$Name/manifiesto.json", (Join-Path $target 'manifiesto.json'), $Entry.sha256_manifiesto))
}

$copiasDisco = Join-Path $Destino 'copias'
$globalesDisco = Join-Path $copiasDisco 'globales'
foreach ($name in $listaCopias) {
    if ($name -like 'dia-*' -and $registro.ContainsKey($name)) { Add-Copy 'copias' $copiasDisco $name $registro[$name] }
}
foreach ($name in $listaGlobales) {
    if ($name -like 'globales-*' -and $registroGlobales.ContainsKey($name)) { Add-Copy 'copias/globales' $globalesDisco $name $registroGlobales[$name] }
}
# The last daily hot copy: the first of the most recent UTC day the server has.
$calientes = @($listaCopias | Where-Object { $_ -like 'caliente-*' -and $registro.ContainsKey($_) } | Sort-Object)
if ($calientes.Count -gt 0) {
    $ultimoDia = $calientes[-1].Substring(9, 8)
    $diaria = @($calientes | Where-Object { $_.Substring(9, 8) -eq $ultimoDia })[0]
    Add-Copy 'copias' $copiasDisco $diaria $registro[$diaria]
}

# --- 3. download into the disk, check, and only then move into place ----------------------------
if ($aBajar.Count -gt 0) {
    $bajada = Join-Path $Temporal 'bajada'
    New-Item -ItemType Directory -Path $bajada | Out-Null
    $comandos = @()
    for ($i = 0; $i -lt $aBajar.Count; $i++) {
        $comandos += "get -p $($aBajar[$i][0]) $(Quote (Join-Path $bajada "$i"))"
    }
    Invoke-Sftp $comandos
    for ($i = 0; $i -lt $aBajar.Count; $i++) {
        $remote, $final, $expected = $aBajar[$i]
        $file = Join-Path $bajada "$i"
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToLowerInvariant() -ne $expected) {
            Remove-Item -LiteralPath $file -Force
            $Errores.Add("$remote no coincide con su SHA-256: no se guarda")
            continue
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $final) -Force | Out-Null
        Move-Item -LiteralPath $file -Destination $final -Force
        $null = Get-Sha256 $final -Fresh
        $Descargados.Add($remote)
    }
}

# --- 4. the receipts of the generations, computed again on the disk -----------------------------
$enteras = @{}   # generations that pass, entire, the check of this visit
$recibosEscritos = 0
foreach ($g in $pendientes) {
    $archivos = @()
    $entera = $true
    foreach ($f in $g.archivos) {
        $local = Join-Path $archivoDisco $f.ruta
        if (-not (Test-Path -LiteralPath $local)) { $entera = $false; continue }
        $sha = Get-Sha256 $local -Fresh
        $bytes = (Get-Item -LiteralPath $local).Length
        if ($sha -ne $f.sha256 -or $bytes -ne $f.bytes) { $entera = $false }
        $archivos += [ordered]@{ ruta = $f.ruta; bytes = $bytes; filas = $f.filas; sha256 = $sha }
    }
    if ($archivos.Count -eq 0) { continue }
    $recibo = [ordered]@{ tipo = 'generacion'; generacion = $g.generacion; dia = $g.dia; disco = $Disco; archivos = $archivos }
    Write-Utf8 (Join-Path $Salida "recibo_$($g.generacion).json") (ConvertTo-Json $recibo -Depth 6 -Compress)
    $recibosEscritos += 1
    if ($entera) { $enteras[$g.generacion] = $g } else { $Errores.Add("generación $($g.generacion): no está entera en el disco") }
}

# --- 5. the challenges of this disk, answered from the disk only (E4C3-01) -----------------------
$respondidos = 0
foreach ($file in @(Get-ChildItem -LiteralPath $desafiosLocal -Filter 'desafio_*.json')) {
    $d = Read-Json $file.FullName
    if ($d.disco -ne $Disco) { continue }
    if (-not $enteras.ContainsKey($d.generacion)) {
        $Avisos.Add("desafío $($d.id): su generación no pasó entera la comprobación de esta visita; no se responde")
        continue
    }
    $hashes = @()
    foreach ($p in $d.pruebas) {
        $stream = [System.IO.File]::OpenRead((Join-Path $archivoDisco $p.ruta))
        try {
            $buffer = New-Object byte[] $p.bytes
            $null = $stream.Seek([int64]$p.desde, [System.IO.SeekOrigin]::Begin)
            $read = 0
            while ($read -lt $p.bytes) {
                $n = $stream.Read($buffer, $read, $p.bytes - $read)
                if ($n -le 0) { throw "el archivo $($p.ruta) es más corto de lo que pide el desafío" }
                $read += $n
            }
        } finally { $stream.Dispose() }
        $nonce = New-Object byte[] ($p.nonce.Length / 2)
        for ($i = 0; $i -lt $nonce.Length; $i++) { $nonce[$i] = [Convert]::ToByte($p.nonce.Substring(2 * $i, 2), 16) }
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $digest = $sha.ComputeHash([byte[]]($nonce + $buffer))
        $hashes += (($digest | ForEach-Object { $_.ToString('x2') }) -join '')
    }
    $respuesta = [ordered]@{ tipo = 'respuesta'; desafio = $d.id; generacion = $d.generacion; disco = $Disco; sha256 = $hashes }
    Write-Utf8 (Join-Path $Salida "respuesta_$($d.id).json") (ConvertTo-Json $respuesta -Depth 4 -Compress)
    $respondidos += 1
}

# --- 6. the recoverable point: the newest hot copy whose exact dependencies are on the disk ------
$punto = $null
if (Test-Path -LiteralPath $copiasDisco) {
    $candidatas = @(Get-ChildItem -LiteralPath $copiasDisco -Directory | Where-Object { $_.Name -like 'caliente-*' } | Sort-Object Name -Descending)
    foreach ($dir in $candidatas) {
        $name = ($dir.Name -split '~')[0]
        if (-not $registro.ContainsKey($name)) { continue }
        $dump = Join-Path $dir.FullName 'volcado.age'
        $manifest = Join-Path $dir.FullName 'manifiesto.json'
        if (-not ((Test-Path -LiteralPath $dump) -and (Test-Path -LiteralPath $manifest))) { continue }
        if ((Get-Sha256 $dump) -ne $registro[$name].sha256_cifrado -or (Get-Sha256 $manifest) -ne $registro[$name].sha256_manifiesto) { continue }
        $m = Read-Json $manifest
        $dependencias = @()
        $falta = $null
        foreach ($item in @($m.dias_excluidos)) {
            if ($null -eq $item -or $null -eq $item.copia_de_dia) { continue }
            $dep = $item.copia_de_dia
            if (-not (Find-Copy $copiasDisco $dep.copia $dep.sha256 $dep.sha256_manifiesto)) { $falta = $dep.copia; break }
            $dependencias += [ordered]@{ copia = $dep.copia; sha256 = $dep.sha256; sha256_manifiesto = $dep.sha256_manifiesto }
        }
        if ($falta) {
            $Avisos.Add("$name no es recuperable desde el disco: falta $falta con la identidad que fija (E4C3-02)")
            continue
        }
        $punto = [ordered]@{
            tipo = 'punto'; disco = $Disco; copia = $name
            sha256 = $registro[$name].sha256_cifrado; sha256_manifiesto = $registro[$name].sha256_manifiesto
            manifiesto_base64 = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($manifest))
            dependencias = $dependencias; sin_errores = ($Errores.Count -eq 0)
        }
        break
    }
}
if ($punto) { Write-Utf8 (Join-Path $Salida 'recibo_punto.json') (ConvertTo-Json $punto -Depth 6 -Compress) }

# --- 7. delete what the server already processed, then upload ----------------------------------
$comandos = @()
foreach ($p in $procesados.PSObject.Properties) { $comandos += "-rm recibos/$($p.Name)" }
foreach ($file in @(Get-ChildItem -LiteralPath $Salida -File)) { $comandos += "put $(Quote $file.FullName) recibos/$($file.Name)" }
if ($comandos.Count -gt 0) { Invoke-Sftp $comandos }

# --- 8. the state of the disk, and the summary ------------------------------------------------
$nuevoEstado = [ordered]@{
    ultima_visita = $Ahora.ToString('o')
    ultima_completa = $(if ($TodoEntero) { $Ahora.ToString('o') } elseif ($UltimaCompleta) { $UltimaCompleta.ToString('o') } else { $null })
}
Write-Utf8 $EstadoDisco (ConvertTo-Json $nuevoEstado)
Write-Utf8 $CacheHuellas (ConvertTo-Json ([pscustomobject]$Huellas) -Depth 4)
Remove-Item -LiteralPath $Temporal -Recurse -Force

$libre = $null
try { $libre = [System.IO.DriveInfo]::new((Get-Item -LiteralPath $Destino).FullName).AvailableFreeSpace } catch { }
Write-Host "== kbot: sincronización del disco $Disco"
Write-Host "Descargado y comprobado: $($Descargados.Count) archivos."
Write-Host "Recibos enviados: $recibosEscritos de generación$(if ($punto) { ' y el del punto recuperable' })."
Write-Host "Desafíos respondidos: $respondidos."
if ($punto) {
    $hora = [datetime]::ParseExact($punto.copia.Substring(9), 'yyyyMMddTHHmmssZ', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AdjustToUniversal -bor [Globalization.DateTimeStyles]::AssumeUniversal)
    $edad = $Ahora - $hora
    Write-Host ("Punto recuperable del disco: {0} (hace {1:N0} h {2} min)." -f $punto.copia, [math]::Floor($edad.TotalHours), $edad.Minutes)
    if ($edad.TotalDays -gt 7) { $Avisos.Add('el punto recuperable del disco tiene más de 7 días') }
} else {
    $Avisos.Add('el disco aún no tiene ninguna copia caliente completa')
}
if ($null -ne $libre) { Write-Host ("Espacio libre en el disco: {0:N1} GB." -f ($libre / 1GB)) }
foreach ($a in $Avisos) { Write-Host "AVISO: $a" }
foreach ($e in $Errores) { Write-Host "ERROR: $e" }
if ($Errores.Count -gt 0) { exit 1 }
exit 0
