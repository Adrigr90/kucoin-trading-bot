# INFORME DE LA FASE 1: ARQUITECTURA Y ESTRUCTURA INICIAL

**Proyecto:** `kucoin-trading-bot` · **Fecha:** 6 de noviembre de 2026 · **Rama:** `fase-1` · **Alcance:** `05_Fase1_Alcance.md`, aprobado por el propietario el 6 de noviembre de 2026 · **Especificación:** versión 1.1

**Etiquetas:** [H] hecho verificado · [R] recomendación · [P] pendiente. **Estados:** especificado · verificado documentalmente · validado mediante pruebas · implementado.

---

## 1. Resumen

- **Las 10 pruebas de aceptación (A1-A10) pasan.**
- **Integración continua en verde:** ejecución [42250012939](https://github.com/Adrigr90/kucoin-trading-bot/actions/runs/42250012939) sobre el commit `b1e0dab`, con **235 pruebas superadas, 0 omitidas y 0 fallidas**. Los commits posteriores solo añaden documentación.
- **Nada está integrado en `main`.** La integración espera la aprobación del propietario (DM §11).
- La Fase 1 **no se conecta a KuCoin, no usa Oracle y no maneja ninguna clave**.

## 2. Qué se ha construido

| Bloque | Contenido | Dónde |
|---|---|---|
| E1. Repositorio | Estructura, README y copia del Documento Maestro y de la especificación (P1) | `README.md`, `docs/especificacion/` |
| E2. Proyecto Python | Python 3.12 y `uv`, con `uv.lock` y las huellas de cada paquete. Solo versiones publicadas al menos una semana antes del bloqueo | `pyproject.toml`, `uv.lock` |
| E3. Núcleo común | `decimal` (precisión 80 y trampa `Inexact`), `jcs` (RFC 8785), `merkle` (RFC 6962) y `clock` (reloj inyectable) | `src/kbot/core/` |
| E4. Base de datos | Roles sin contraseña con los atributos de `02` §9; base `kbot` sin derechos para PUBLIC; siete esquemas vacíos; `config.risk_limits` con los valores del DM y su huella | `sql/roles.sql`, `migrations/` |
| E5. Integración continua | `uv sync --frozen`, `ruff`, gitleaks sobre todo el historial, lista blanca de SQL, límites frente al DM, PostgreSQL 18 en el runner y todas las pruebas | `.github/workflows/ci.yml`, `ci/`, `tools/` |

## 3. Resultado de las pruebas de aceptación

Fuentes de evidencia: **CI** es la ejecución citada en la sección 1. Cada paso publica sus últimas líneas como anotación, que se puede consultar sin iniciar sesión. **PC** es el PC del propietario (Windows), donde pasan 174 pruebas y las de PostgreSQL y gitleaks se omiten.

| # | Prueba | Resultado | Evidencia |
|---|---|---|---|
| A1 | `uv sync --frozen` instala desde `uv.lock` en un entorno limpio | **Pasa** | Paso «A1» de la CI en una máquina nueva; también en el PC |
| A2 | Toda la CI pasa en la rama de la F1 | **Pasa** | Ejecución 42250012939: todos los pasos en verde |
| A3 | Ningún cálculo fuera de las cuantizaciones lanza `Inexact`, y un cálculo que perdería precisión falla en lugar de redondear | **Pasa** | `tests/unit/test_decimal.py`: propiedades con 400 casos cada una (sumas, restas, productos, divisiones, cuantizaciones y cocientes frente a un oráculo con fracciones exactas); 1/3 y un producto de más de 80 cifras lanzan `Inexact`; los floats lanzan `FloatOperation`; RC15 |
| A4 | `jcs` reproduce los ejemplos de la RFC 8785 y serializa los decimales como texto | **Pasa** | `tests/unit/test_jcs.py`: ejemplo de §3.2.2-3.2.3, bytes UTF-8 de §3.2.4, orden de §3.2.3 y los 26 valores del apéndice B |
| A5 | Dos implementaciones independientes (`hashlib` y OpenSSL) dan los valores de RC14 | **Pasa** | `tests/unit/test_merkle.py`. Raíz vacía `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`; hoja vacía `6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d`. Además, las 9 raíces de referencia de transparency-dev coinciden con las dos implementaciones |
| A6 | Roles sin contraseña, solo `peer`, sin TCP, identidad no autorizada rechazada, sin `CREATE`, PUBLIC sin derechos | **Pasa** | `tests/db/test_roles.py` en PostgreSQL 18.6. Detalle en la sección 4 |
| A7 | `config.risk_limits` coincide con el DM; un servicio que intenta modificarla recibe «permiso denegado»; la huella coincide | **Pasa** | `tests/db/test_risk_limits.py`: los 8 roles de servicio, conectados de verdad, reciben `42501` en `INSERT`, `UPDATE`, `DELETE` y `TRUNCATE`; `kbot_backup` puede leer pero no escribir |
| A8 | La CI rechaza una sentencia fuera de la lista blanca, un secreto de prueba y un límite distinto del DM | **Pasa** | `tests/ci/test_sql_guard.py` (17 casos en `roles.sql`, 22 en migraciones y 4 en módulos Python); `tests/ci/test_gitleaks.py` (un token generado al vuelo se detecta); `tests/ci/test_limits_check.py` (4 límites cambiados, una huella alterada y un DM cambiado) |
| A9 | Prueba de dos sesiones: las claves foráneas toman `FOR KEY SHARE` | **Pasa** | `tests/db/test_fk_locks.py`: con una fila hija sin confirmar, `FOR NO KEY UPDATE NOWAIT` sobre el padre funciona y `FOR UPDATE NOWAIT` falla con `55P03`; al revertir, funciona |
| A10 | Coste de GitHub Actions dentro de lo gratuito | **Pasa** | Repositorio público (P2). Según la documentación de GitHub, los runners estándar son gratuitos en repositorios públicos [H, `02` §16]. `tests/ci/test_workflow.py` exige runners estándar, acciones fijadas por commit y permisos de solo lectura. Cada ejecución dura unos 40 segundos |

## 4. Detalle de A6 (seguridad de PostgreSQL)

PostgreSQL 18.6 del repositorio oficial PGDG, **instalado en el propio runner de Ubuntu**, sin contenedor (corrección 1 de la revisión de la sección 9).

| Comprobación | Resultado |
|---|---|
| (a) Atributos de cada rol frente a la tabla de `02` §9 | Coinciden exactamente: `kbot_owner` y `kbot_fn_owner` sin login; 8 servicios con login, 5 conexiones y 30 s; `kbot_backup` con login, 2 conexiones y sin límite de tiempo. Ningún rol tiene contraseña ni atributos de superusuario. Una sesión real de cada rol ve su `statement_timeout`, y la sexta conexión de `kbot_web` se rechaza |
| (b) `pg_hba_file_rules` | Solo reglas `local` con `peer`, sin errores |
| (c) Escucha TCP | `listen_addresses` vacío y ningún socket TCP de PostgreSQL (`ss`) |
| (d) Identidades no autorizadas | El usuario del sistema `nobody` recibe «Peer authentication failed»; `kbot_owner` y `kbot_fn_owner` reciben «not permitted to log in» |
| (e) Privilegios | Ningún servicio, ni `kbot_backup` ni `kbot_fn_owner`, tiene `CREATE` en ningún esquema, ni `CREATE` o `TEMPORARY` en la base. PUBLIC no tiene derechos en ningún esquema ni en la base. Una sesión real de `kbot_ingest` recibe `42501` al crear una tabla, una tabla temporal o un esquema |

**Que la contraseña no sirve se deduce** de (a) y (b), como pidió el revisor, y no de un intento por TCP.

**Huella del catálogo de funciones `SECURITY DEFINER`:** vacía en la F1 y comparada con `sql/security_definer_catalog.json`. El control queda activo para las fases siguientes.

## 5. Verificaciones de `04` que se cierran

| Verificación | Estado nuevo | Evidencia |
|---|---|---|
| H07 (decimales sin redondeo silencioso) | **Validado mediante pruebas** | A3 |
| B18 (`FOR KEY SHARE` en claves foráneas) | **Validado mediante pruebas**: la afirmación de C69 pasa de [I] a [H] en PostgreSQL 18.6 | A9 |
| B20 (`pglast` con la gramática de PostgreSQL 18) | **Validado**: `pglast` 8.7 con la gramática 18.6; analiza todas las migraciones y reconoce sintaxis exclusiva de la 18 (columnas `VIRTUAL`) | A8 y CI |
| B25 (coste de GitHub Actions) | **Verificado documentalmente y validado** | A10 |
| B26 (RC14 con dos implementaciones) | **Validado mediante pruebas** | A5 |

## 6. Decisiones de implementación (dentro del alcance aprobado)

1. **PUBLIC pierde `CONNECT` y `TEMPORARY` en la base `kbot`**, y solo los roles con login reciben `CONNECT`. Así se cumple del todo «los servicios sin `CREATE` en ningún esquema», incluido el esquema temporal.
2. **Solo `kbot_risk` y `kbot_watchdog` pueden leer `config.risk_limits`**, porque el riesgo los aplica y el watchdog los vigila. `kbot_backup` los lee a través de `pg_read_all_data`.
3. **La huella de los límites** es SHA-256 del JSON canónico (RFC 8785) de sus valores. Para los límites del DM es `a9c038cb25b193017be417fa0915ae6076f3d94c26b2fac7c0a8d3697ff57ec1`.
4. **La comparación con el DM lee su texto literal** (`docs/especificacion/01_Documento_Maestro.md`, §6). Por eso se copió también el Documento Maestro (P1). Si cambia cualquier frase de los límites, la CI falla hasta que alguien lo revise.
5. **Las migraciones guardan todo su SQL en archivos `.sql`**, y la CI rechaza cualquier módulo de migración que ejecute SQL por otra vía.
6. **Cadena de suministro:** solo paquetes publicados al menos una semana antes del bloqueo; acciones de GitHub fijadas por commit; gitleaks fijado por su SHA-256, comprobado contra la lista oficial de su versión.
7. **Los commits usan la dirección privada de GitHub del propietario**, no su correo personal.

## 7. Observaciones y límites conocidos

- **La configuración `peer` de la CI no es la de producción.** En la CI, un mapa de `pg_ident.conf` deja que el usuario del runner se conecte como cada rol. En el servidor, cada servicio tendrá su propio usuario del sistema (`02` §1.1). Esa configuración se escribe al desplegar (F2 si D03; si no, F9), junto con la comprobación B19 (`IPAddressDeny`).
- **Los límites de conexiones y de tiempo son provisionales** y se ajustan con las mediciones de la F2 (punto 83 de `04`).
- **La comprobación diaria de deriva** contra la base de datos real queda para la F9, como estaba previsto.
- **Los registros completos de la CI** exigen iniciar sesión en GitHub. Las anotaciones, en cambio, son públicas, y bastan para comprobar los resultados de este informe.
- **D18 aprobada:** los backtests se harán en el PC del propietario con WSL2, que se instalará al llegar a esa fase.

## 8. Estado de la especificación tras la Fase 1

| Estado | Antes | Después |
|---|---|---|
| Validado mediante pruebas | 0 | H07, B18, B20, B25, B26, RC14, RC15 y la parte de PostgreSQL y roles de `02` §9 |
| Implementado | 0 | Lo anterior, en la rama `fase-1`, **pendiente de integrar en `main`** |

## 9. Siguiente paso

1. **El propietario revisa este informe** (y, si quiere, el revisor) y **aprueba la integración de `fase-1` en `main`** mediante la pull request.
2. Después, el alcance de la Fase 2 (datos de KuCoin). Según D35, antes de implementarla toca la **ronda 4 de revisión** (secciones 3 y 7 de `02`) y decidir D03, D04, D26 y D29.
