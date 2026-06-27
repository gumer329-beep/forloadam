#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Script completo para:
- Leer CSV de layout
- Normalizar encabezados, fechas y decimales
- Mapear columnas (p. ej. Sucursal -> IdSucursal) contra catálogos en MariaDB
- Aplicar políticas de limpieza de decimales (interactivo o desde archivo)
- Validar columnas contra DDL
- Insertar en MariaDB con RunId y exportar snapshot pre/post-insert para auditoría

Ajusta rutas, credenciales y nombres de tablas/columnas según tu entorno.
"""

# ========================= BLOQUE 0: IMPORTS Y CONFIGURACIÓN GLOBAL =========================
from scripts.services.cleaning_services import cleaning_service
import os
import re
import sys
import json
import unicodedata
import chardet
import pandas as pd
import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus
import warnings
import logging

# python LayoutNewTPVAMEX.py

# ------------------ RUTAS Y ARCHIVOS ------------------
base_dir = os.path.dirname(os.path.abspath(__file__))  # carpeta del script
data_dir = os.path.join(base_dir, "Data")  # carpeta donde se colocan los CSV
# csv_path = "20260412TPVAMEX.csv"  # nombre del archivo CSV (solo nombre)
# configured_path = os.path.join(data_dir, csv_path)  # ruta completa al CSV


# ------------------ CONEXIÓN A MARIA DB ------------------
# Ajusta estas credenciales según tu entorno
user = "usrAnalistaCred03"  # usuario DB
password = quote_plus(
    "9TVOL0vVsP79QEg9"
)  # contraseña (quote_plus para caracteres especiales)
host = "192.168.1.21"  # host o IP del servidor
db = "InsumosCombustible"  # base de datos destino
target_table = "Tesoreria_TpvAmex"  # tabla destino para insertar

# Engine SQLAlchemy (requiere pymysql instalado)
# maria_engine = create_engine(
#     f"mysql+pymysql://{user}:{password}@{host}:3306/{db}?local_infile=1"
# )
maria_engine = create_engine(
    f"mysql+pymysql://{user}:{password}@{host}:3306/{db}?charset=utf8mb4&local_infile=1"
)


# ------------------ REGLAS DEL LAYOUT / VALIDACIONES ------------------
# Columnas que deben existir en el CSV y no pueden quedar vacías (filas sin estos campos se marcan inválidas)
not_null_cols = [
    "FechaEnvio",
    "NumeroFacturaDeCargos",
    "IdAfiliacion",
    "NumeroDePago",
    "NumeroEstablecimiento",
    "SucursalQueEnvia",
    "ConteoDeTransacciones",
    "FechaPago",
    "NombreSucursal",
    "Descripcion",
    "FechaTransaccion",
    "NumeroDeMensualidades",
]

# Columnas de fecha opcionales (pueden faltar o ser NaT sin invalidar la fila)
nullable_date_cols = [""]

# Columnas de fecha que deben parsearse correctamente; si fallan, el script aborta
date_strict_cols = ["FechaEnvio", "FechaPago", "FechaTransaccion"]

# Columnas de fecha permisivas (si fallan, se pueden aplicar fallbacks configurables)
date_permissive_cols = []  # ej: ["OtraFechaPermisiva"]

# Columnas decimales a normalizar (asegúrate de que los nombres coincidan exactamente con el CSV)
decimal_cols = [
    "CargosTotales",
    "Creditos",
    "MontoDeEnvio",
    "TotalDelEnvio",
    "MontoDelDescuento",
    "CuotasEIncentivos",
    "MontoDePago",
    "IVA",
]

# Columnas a excluir al comparar con DDL (metadatos, audit columns, etc.)
exclude_cols = {"Id", "CrtdDateTime"}

# Validaciones post-insert: {columna: "sum" | "count" | ...}
validations = {"TotalDelEnvio": "sum"}

# ------------------ MAPEOS GENÉRICOS ------------------
mapping_configs = [
    {
        "columna_csv": "Sucursal",
        "tabla_catalogo": "Tesoreria_Sucursal",
        "columna_relacion_catalogo": "SucursalNetsuite",
        "columna_id_catalogo": "Id",
        "alias_destino": "IdSucursal",
        "manual_map": {},  # ejemplo: {"BK ADO Coatzacoalcos": "BK ADO Coatzacoalcos"}
        "enabled": False,  # True o False para activar/desactivar este mapeo
    },
    # Puedes añadir más mapeos aquí
]

# ------------------ CONTROL GLOBAL DE MAPEOS ------------------
mapping_enabled = False  # True = ejecutar BLOQUE 2; False = saltar todos los mapeos

# ------------------ POLÍTICAS, ARCHIVOS Y FLAGS ------------------
policy_file = os.path.join(
    base_dir, f"decimal_{target_table}_policies.json"
)  # archivo para persistir políticas
verbose = True  # True = prints detallados; False = silencioso
batch_mode = False  # True = aplicar políticas guardadas sin preguntar
default_decimal_policy = (
    "null"  # política por defecto en batch si no hay entrada en decimal_policies.json
)

# Columnas decimales críticas: si quedan NULLs en estas columnas, abortar
critical_decimals = []  # ej: ["VentaTotal"]

# ------------------ VALIDACIONES DEFENSIVAS (asegurar variables definidas) ------------------
if "date_strict_cols" not in globals():
    date_strict_cols = []
if "date_permissive_cols" not in globals():
    date_permissive_cols = []
if "nullable_date_cols" not in globals():
    nullable_date_cols = []
if "decimal_cols" not in globals() or not isinstance(decimal_cols, list):
    decimal_cols = []

# ------------------ ADICIÓN: CONFIGURACIÓN DE LOGGING EN CARPETA Logs ------------------
log_dir = os.path.join(base_dir, "Logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(
    log_dir, f"import_{target_table}_{datetime.now().strftime('%Y%m%d')}.log"
)

# Configuración mínima de logging; si ya tienes logging configurado en otro sitio, esto no lo sobrescribe intencionalmente
logging.basicConfig(
    level=logging.INFO,
    filename=log_file,
    filemode="a",
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)
# ------------------ FIN ADICIÓN ------------------

# ------------------ RESUMEN INICIAL ------------------
if verbose:
    print("\n======================== BLOQUE 0: VARIABLES ========================")
    print(f"📌 Tabla destino: {target_table}")
    # print(f"📌 Archivo CSV: {csv_path}")
    print(f"📌 Columnas NOT NULL: {not_null_cols}")
    print(f"📌 Columnas decimales: {decimal_cols}")
    print(f"📌 Columnas fecha estrictas: {date_strict_cols}")
    print(f"📌 Columnas fecha permisivas: {date_permissive_cols}")
    print(f"📌 Columnas fecha opcionales: {nullable_date_cols}")
    print(f"📌 Mapeos definidos: {[m['columna_csv'] for m in mapping_configs]}")
    print(f"📌 Mapeos activados globalmente: {mapping_enabled}")
    print(f"📌 Verbose: {verbose}  Batch mode: {batch_mode}")
    logger.info("Bloque 0 inicializado. Logging configurado en: %s", log_file)
# ========================== BLOQUE 1: LECTURA CSV Y FORMATO DE FECHAS =========================
if verbose:
    print(
        "\n========================= BLOQUE 1: LECTURA CSV Y FORMATO DE FECHAS ========================="
    )

# if not os.path.exists(configured_path):
#     print(f"❌ No se encontró el archivo configurado: {configured_path}")
#     sys.exit(1)

# Detectar encoding y leer todo como str
# #with open(configured_path, "rb") as f:
# raw = f.read(8192)
# enc = chardet.detect(raw)["encoding"] or "utf-8"
# df = pd.read_csv(configured_path, encoding="utf8", dtype=str, keep_default_na=False)
df = cleaning_service()


# ----------------- Utilitarios globales -----------------
def normalize_colname(c):
    if isinstance(c, str):
        c = c.strip().lstrip("\ufeff")
        return re.sub(r"\s+", " ", c)
    return c


def normalize_text_for_map(s):
    if pd.isna(s):
        return ""
    s = str(s).strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = s.replace("’", "'").replace("‘", "'").replace("´", "'")
    s = re.sub(r"[^a-z0-9\s\-\']", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_and_format_date_col(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip().replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
    parsed = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if parsed.isna().any():
        raw_digits = s.str.replace(r"[^0-9]", "", regex=True)
        try_ymd = pd.to_datetime(raw_digits, format="%Y%m%d", errors="coerce")
        try_dmy = pd.to_datetime(raw_digits, format="%d%m%Y", errors="coerce")
        mask_na = parsed.isna()
        parsed.loc[mask_na] = try_ymd.loc[mask_na].fillna(try_dmy.loc[mask_na])
    if parsed.isna().any():
        parsed2 = pd.to_datetime(s, errors="coerce")
        mask_na = parsed.isna()
        parsed.loc[mask_na] = parsed2.loc[mask_na]
    return parsed.dt.strftime("%Y-%m-%d %H:%M:%S").where(parsed.notna(), pd.NA)


# Normalizar encabezados y trim de valores
df.columns = [normalize_colname(c) for c in df.columns]
for col in df.columns:
    df[col] = df[col].astype(str).str.strip()

if verbose:
    print("Columnas detectadas en CSV:", list(df.columns))
    print("Filas totales:", len(df))

# Limpiar listas de configuración
not_null_cols = [c for c in not_null_cols if c and c.strip()]
nullable_date_cols = [c for c in nullable_date_cols if c and c.strip()]
decimal_cols = [c for c in decimal_cols if c and c.strip()]

# Detectar candidato a Fecha y renombrar si aplica
date_candidates = [
    c
    for c in df.columns
    if any(k in c.lower() for k in ("fecha", "date", "txn", "batch"))
]
if "Fecha" in not_null_cols and "Fecha" not in df.columns and date_candidates:
    guessed = date_candidates[0]
    if verbose:
        print(f"ℹ️ Renombrando '{guessed}' -> 'Fecha'")
    df.rename(columns={guessed: "Fecha"}, inplace=True)

# Aplicar parseo de fechas
date_cols_to_process = list(
    set(
        [c for c in not_null_cols if "fecha" in c.lower() or "date" in c.lower()]
        + nullable_date_cols
    )
)
date_cols_to_process += date_strict_cols + date_permissive_cols
date_cols_to_process = list(
    dict.fromkeys(date_cols_to_process)
)  # dedupe preservando orden

for col in date_cols_to_process:
    if col in df.columns:
        formatted = parse_and_format_date_col(df[col])
        if col in date_strict_cols:
            n_bad = formatted.isna().sum()
            if n_bad > 0:
                sample_bad = (
                    df.loc[formatted.isna(), col].astype(str).unique()[:20].tolist()
                )
                print(
                    f"\n❌ ERROR: columna de fecha estricta '{col}' contiene {n_bad} valores no parseables."
                )
                for v in sample_bad:
                    print(f"   - {v}")
                sys.exit(1)
            df[col] = formatted
            if verbose:
                print(f"✅ Columna estricta '{col}' formateada correctamente.")
        else:
            n_bad = formatted.isna().sum()
            if n_bad > 0 and verbose:
                print(
                    f"⚠️ Columna permisiva '{col}': {n_bad} valores no parseables (muestra): {df.loc[formatted.isna(), col].astype(str).unique()[:10].tolist()}"
                )
            df[col] = formatted

# Preparar decimales (limpieza ligera; conversión final en bloque específico)
for col in decimal_cols:
    if col in df.columns:
        df[col] = (
            df[col]
            .astype(str)
            .str.strip()
            .str.replace(r"[^0-9\.\-\,\(\)]", "", regex=True)
        )

# Máscara de válidos
present_not_null = [c for c in not_null_cols if c in df.columns]
if present_not_null:
    mask_valid = df[present_not_null].notna().all(axis=1)
else:
    mask_valid = pd.Series([True] * len(df), index=df.index)

df_validos = df[mask_valid].copy()
df_invalidos = df[~mask_valid].copy()

if verbose:
    print(f"✅ Filas válidas (con {present_not_null}): {len(df_validos)}")
    print(f"⚠️ Filas inválidas (sin {present_not_null}): {len(df_invalidos)}")

# ========================== BLOQUE 2: MAPEOS GENÉRICOS =========================
if verbose:
    print(
        "\n========================= BLOQUE 2: MAPEOS GENÉRICOS ========================="
    )


def clean_invisible(s: str) -> str:
    if pd.isna(s):
        return ""
    s = str(s)
    s = (
        s.replace("\ufeff", "")
        .replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\u200e", "")
        .replace("\u200f", "")
    )
    s = re.sub(r"[\x00-\x1F\x7F]", "", s)
    s = s.strip()
    return re.sub(r"\s+", " ", s)


def normalize_text_for_map_local(s):
    if pd.isna(s):
        return ""
    s = str(s).strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = s.replace("’", "'").replace("‘", "'").replace("´", "'")
    s = re.sub(r"[^a-z0-9\s\-\']", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Si el mapeo global está deshabilitado, saltar todo el bloque
if not mapping_enabled:
    if verbose:
        print(
            "⚠️ Mapeos deshabilitados por configuración (mapping_enabled=False). Se omite BLOQUE 2."
        )
else:
    for cfg in mapping_configs:
        if not cfg.get("enabled", True):
            if verbose:
                print(
                    f"ℹ️ Mapeo para '{cfg.get('columna_csv')}' deshabilitado (enabled=False). Se salta."
                )
            continue

        columna_csv = cfg["columna_csv"]
        tabla_catalogo = cfg["tabla_catalogo"]
        columna_rel_catalogo = cfg["columna_relacion_catalogo"]
        columna_id_catalogo = cfg["columna_id_catalogo"]
        alias_destino = cfg["alias_destino"]
        manual_map_local = cfg.get("manual_map", {})

        if verbose:
            print(
                f"\n--- Mapeo: CSV '{columna_csv}' -> {tabla_catalogo}.{columna_rel_catalogo} (traer {columna_id_catalogo} as {alias_destino})"
            )

        # Leer catálogo (traer id y columna de relación)
        try:
            with maria_engine.connect() as conn:
                catalog_df = pd.read_sql(
                    f"SELECT {columna_id_catalogo} AS {alias_destino}, {columna_rel_catalogo} FROM {tabla_catalogo}",
                    conn,
                )
        except Exception as e:
            print(f"❌ Error leyendo catálogo {tabla_catalogo}: {e}")
            sys.exit(1)

        if verbose:
            print("📋 Columnas catálogo:", list(catalog_df.columns))

        # Normalizar catálogo
        catalog_df[columna_rel_catalogo] = (
            catalog_df[columna_rel_catalogo].astype(str).apply(clean_invisible)
        )
        catalog_df["__norm_cat"] = catalog_df[columna_rel_catalogo].apply(
            normalize_text_for_map_local
        )

        # Preparar columna origen en df_validos
        raw_col = f"__{columna_csv}_raw"
        norm_col = f"__{columna_csv}_norm"
        if columna_csv in df_validos.columns:
            df_validos[raw_col] = (
                df_validos[columna_csv].astype(str).apply(clean_invisible)
            )
        else:
            df_validos[raw_col] = ""
            if verbose:
                print(
                    f"⚠️ Advertencia: columna '{columna_csv}' no encontrada en CSV; se crea vacía para el mapeo."
                )

        df_validos[norm_col] = df_validos[raw_col].apply(normalize_text_for_map_local)

        # Aplicar mapeo manual local (si existe)
        if manual_map_local:
            manual_map_norm = {
                normalize_text_for_map_local(k): normalize_text_for_map_local(v)
                for k, v in manual_map_local.items()
            }
            df_validos[norm_col] = df_validos[norm_col].replace(manual_map_norm)

        # Evitar crear alias_destino antes del merge para no generar sufijos
        if alias_destino in df_validos.columns:
            df_validos.drop(columns=[alias_destino], inplace=True)

        # Merge por columna normalizada (traer id con nombre temporal)
        temp_id_col = f"{alias_destino}_cat"
        df_validos = df_validos.merge(
            catalog_df[[alias_destino, "__norm_cat"]].rename(
                columns={alias_destino: temp_id_col}
            ),
            how="left",
            left_on=norm_col,
            right_on="__norm_cat",
        )

        # Consolidar columna destino: preferir valores traídos
        df_validos[alias_destino] = df_validos[temp_id_col]
        # Eliminar auxiliares creados por este mapeo
        for aux in [raw_col, norm_col, "__norm_cat", temp_id_col]:
            if aux in df_validos.columns:
                df_validos.drop(columns=[aux], inplace=True)

        # Reporte de mapeo
        total = len(df_validos)
        mapped = df_validos[alias_destino].notna().sum()
        unmapped = total - mapped
        print(
            f"📌 Total filas: {total}; ✅ Mapeadas: {mapped}; ❌ No mapeadas: {unmapped}"
        )

        if unmapped > 0:
            unmapped_vals = sorted(
                df_validos.loc[df_validos[alias_destino].isna(), raw_col]
                .astype(str)
                .str.strip()
                .replace("", "<VACIO>")
                .unique()
                .tolist()
            )
            print(
                f"Valores únicos no mapeados (muestra hasta 50) para '{columna_csv}':"
            )
            for v in unmapped_vals[:50]:
                print(f"   - {v}")
            print(
                "Detén y añade equivalencias en 'mapping_configs' -> manual_map, o corrige el catálogo."
            )
            sys.exit(1)

        if verbose:
            print(
                f"✅ Mapeo '{columna_csv}' completado. Columna destino: '{alias_destino}'"
            )

# ========================= LIMPIEZA Y POLÍTICAS PARA DECIMALES =========================
if verbose:
    print(
        "\n======================== LIMPIEZA Y POLÍTICAS PARA DECIMALES ========================="
    )

# Cargar políticas guardadas si existen
if os.path.exists(policy_file):
    try:
        with open(policy_file, "r", encoding="utf-8") as f:
            col_policy = json.load(f)
    except Exception:
        col_policy = {}
else:
    col_policy = {}


# Normalizador heurístico para números (ya definido en el script principal)
def normalize_number_str(x: str):
    if pd.isna(x):
        return pd.NA
    s = str(x).strip()
    if s == "":
        return pd.NA
    s = re.sub(r"[^\d\-\+\.,\(\)]", "", s)
    if re.match(r"^\(.*\)$", s):
        s = "-" + s.replace("(", "").replace(")", "")
    # Formatos comunes: "1.234,56" -> "1234.56" ; "1,234.56" -> "1234.56"
    if re.match(r"^\d{1,3}(\.\d{3})+,\d+$", s):
        s = s.replace(".", "").replace(",", ".")
    elif re.match(r"^\d{1,3}(,\d{3})+\.\d+$", s):
        s = s.replace(",", "")
    elif "," in s and "." not in s and re.search(r",\d{1,2}$", s):
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except Exception:
        return pd.NA


# Crear columnas temporales cleaned para cada decimal
for col in decimal_cols:
    if col in df_validos.columns and f"__{col}__cleaned" not in df_validos.columns:
        df_validos[f"__{col}__cleaned"] = df_validos[col].apply(normalize_number_str)

# Resumen de problemas detectados
decimal_problems = {}
for col in decimal_cols:
    if col in df_validos.columns:
        n_invalid = int(df_validos[f"__{col}__cleaned"].isna().sum())
        decimal_problems[col] = n_invalid

if verbose:
    print("Resumen problemas decimales (columna: no_convertibles):", decimal_problems)
    for col, n in decimal_problems.items():
        if n:
            sample_vals = (
                df_validos.loc[df_validos[f"__{col}__cleaned"].isna(), col]
                .unique()[:5]
                .tolist()
            )
            print(f"⚠️ Muestra no convertibles en '{col}': {sample_vals}")

# Interfaz compacta: mostrar menú completo solo la primera vez
show_full_decimal_menu = True

for col, n_invalid in decimal_problems.items():
    if col not in df_validos.columns:
        continue

    # Si no hay problemas, aplicar cleaned y continuar
    if n_invalid == 0:
        df_validos[col] = df_validos[f"__{col}__cleaned"]
        df_validos.drop(columns=[f"__{col}__cleaned"], inplace=True)
        continue

    # Política ya guardada
    if col in col_policy:
        action = col_policy[col]
        if verbose:
            print(f"Usando política guardada para '{col}': {action}")
    elif batch_mode:
        action = default_decimal_policy
        if verbose:
            print(f"Batch mode: aplicando acción por defecto '{action}' para '{col}'")
    else:
        # Mostrar menú completo solo la primera vez
        if show_full_decimal_menu:
            print("\nElige acción para esta columna:")
            print("  1) null   -> insertar NULL donde no convierta")
            print("  2) zero   -> rellenar con 0")
            print("  3) custom -> rellenar con valor numérico que indiques")
            print("  4) abort  -> detener y revisar CSV")
            show_full_decimal_menu = False

        # Mensaje compacto con muestra
        sample = (
            df_validos.loc[df_validos[f"__{col}__cleaned"].isna(), col]
            .unique()[:5]
            .tolist()
        )
        print(
            f"\nColumna '{col}' tiene {n_invalid} valores no convertibles. Muestra: {sample}"
        )
        choice = input("Escribe 1/2/3/4 (enter = 1 null): ").strip()

        if choice == "2":
            action = "zero"
        elif choice == "3":
            val = input("Escribe valor numérico para rellenar (ej 0.0): ").strip()
            action = f"custom:{val}"
        elif choice == "4":
            print(
                "Abortando por elección del usuario. Corrige CSV y vuelve a ejecutar."
            )
            sys.exit(1)
        else:
            action = "null"

        save = (
            input("¿Guardar esta política para futuros runs? (Si/No): ").strip().lower()
        )
        if save == "si":
            col_policy[col] = action
            try:
                with open(policy_file, "w", encoding="utf-8") as f:
                    json.dump(col_policy, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print("⚠️ No se pudo guardar la política:", str(e))

    # Aplicar acción con conversión segura a numérico y tipo nullable Float64
    cleaned = df_validos[f"__{col}__cleaned"]
    serie_num = pd.to_numeric(cleaned, errors="coerce")

    if action == "null":
        df_validos[col] = serie_num.astype("Float64")
    elif action == "zero":
        df_validos[col] = serie_num.fillna(0).astype("Float64")
    elif action.startswith("custom:"):
        try:
            v = float(action.split(":", 1)[1])
            df_validos[col] = serie_num.fillna(v).astype("Float64")
        except Exception:
            print(f"Valor custom inválido para {col}. Se usará NULL.")
            df_validos[col] = serie_num.astype("Float64")
    elif action == "abort":
        print(
            f"❌ Política 'abort' para '{col}' detectada. Abortando ejecución para revisar CSV."
        )
        sys.exit(1)
    else:
        # fallback seguro
        df_validos[col] = serie_num.astype("Float64")

    # Eliminar columna temporal
    if f"__{col}__cleaned" in df_validos.columns:
        df_validos.drop(columns=[f"__{col}__cleaned"], inplace=True)

if verbose:
    print("✅ Políticas aplicadas y columnas decimales normalizadas.")

# ========================== BLOQUE 3: VALIDACIÓN DDL =========================
if verbose:
    print(
        "\n========================= BLOQUE 3: VALIDACIÓN DDL ========================="
    )

# Obtener columnas de la tabla destino desde INFORMATION_SCHEMA
try:
    with maria_engine.connect() as conn:
        q = text(
            """
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table
        """
        )
        res = conn.execute(q, {"schema": db, "table": target_table})
        maria_cols = [r[0] for r in res.fetchall()]
except Exception as e:
    print("❌ Error consultando columnas en MariaDB:", str(e))
    sys.exit(1)

# Filtrar columnas excluidas
maria_cols_filtered = [c for c in maria_cols if c not in exclude_cols]

# Columnas detectadas en CSV (df_validos)
csv_cols = [c for c in df_validos.columns if c not in exclude_cols]

# Comparación
common = [c for c in csv_cols if c in maria_cols_filtered]
extras_csv = [c for c in csv_cols if c not in maria_cols_filtered]
missing_in_csv = [c for c in maria_cols_filtered if c not in csv_cols]

print("📋 Validación de columnas:")
print(f"   Columnas MariaDB: {len(maria_cols)}")
print(f"   Columnas excluidas MariaDB: {len(exclude_cols)}")
print(f"   Columnas CSV detectadas: {len(csv_cols)}")
print(f"   Columnas coincidentes: {len(common)}")
if extras_csv:
    print(f"ℹ️ Extras en CSV: {extras_csv}")
if missing_in_csv:
    print(f"⚠️ Columnas en MariaDB no encontradas en CSV: {missing_in_csv}")

# Asegurar que IdSucursal esté presente si se mapeó
if "IdSucursal" in df_validos.columns and "IdSucursal" not in maria_cols_filtered:
    print(
        "⚠️ Atención: 'IdSucursal' existe en CSV pero no en la tabla destino. Revisa DDL o mapeo."
    )

# Filas válidas finales
print(f"✅ Filas válidas finales: {len(df_validos)}")
print(f"⚠️ Filas inválidas finales: {len(df_invalidos)}")

# ========================== BLOQUE 4: PREPARAR E INSERTAR (SIN auditoría) =========================
if verbose:
    print(
        "\n========================= BLOQUE 4: PREPARAR E INSERTAR (SIN auditoría) ========================="
    )

# ADICIÓN: capturar Id máximo previo a la inserción (si la tabla tiene columna Id autoincremental)
prev_max_id = None
try:
    if "maria_engine" in globals() and maria_engine is not None:
        with maria_engine.connect() as conn:
            q = text(f"SELECT MAX(Id) FROM {db}.{target_table}")
            res = conn.execute(q)
            prev_max_id = res.scalar()
            print(f"🔎 MAX(Id) previo a la inserción: {prev_max_id}")
            try:
                logger.info("Previo a inserción, MAX(Id)=%s", prev_max_id)
            except Exception:
                pass
    else:
        print("⚠️ maria_engine no está definido; no se obtuvo MAX(Id) previo.")
        try:
            logger.warning(
                "maria_engine no está definido; no se obtuvo MAX(Id) previo."
            )
        except Exception:
            pass
except Exception as e:
    print("⚠️ Error obteniendo MAX(Id) previo:", str(e))
    try:
        logger.exception("No se pudo obtener MAX(Id) previo a inserción")
    except Exception:
        pass
# FIN ADICIÓN

# Columnas a insertar según DDL vs df_validos
cols_to_insert = [c for c in maria_cols_filtered if c in df_validos.columns]
if "IdSucursal" in df_validos.columns and "IdSucursal" not in cols_to_insert:
    cols_to_insert.append("IdSucursal")

if not cols_to_insert:
    print(
        "❌ No hay columnas coincidentes para insertar. Revisa la configuración o el CSV."
    )
    sys.exit(1)

# DataFrame que vamos a insertar (NO se añaden columnas de auditoría)
df_insert = df_validos[cols_to_insert].copy()

# Guardar snapshot pre-insert (copia local del DataFrame que se va a insertar)
# timestamp simple para nombre de archivo
ts = datetime.now().strftime("%Y%m%dT%H%M%S")
snapshot_pre = os.path.join(data_dir, f"audit_pre_insert_{ts}.csv")
try:
    df_insert.to_csv(snapshot_pre, index=False, encoding="utf-8")
    print(f"📁 Snapshot pre-insert guardado en: {snapshot_pre}")
    try:
        logger.info("Snapshot pre-insert guardado en: %s", snapshot_pre)
    except Exception:
        pass
except Exception as e:
    print("⚠️ No se pudo guardar snapshot pre-insert:", str(e))
    try:
        logger.exception("No se pudo guardar snapshot pre-insert: %s", str(e))
    except Exception:
        pass

# NO intentamos crear ni insertar columnas de auditoría en la tabla destino.
# (Se eliminó cualquier ALTER TABLE o adición de _RunId / _RunTimestampUTC)

# Mostrar resumen compacto de inserción (columnas envueltas para legibilidad)
print(f"📊 Filas a insertar: {len(df_insert)}")
if verbose:
    cols_str = ", ".join(list(df_insert.columns))
    max_width = 80
    wrapped = []
    while cols_str:
        if len(cols_str) <= max_width:
            wrapped.append(cols_str)
            break
        cut = cols_str.rfind(",", 0, max_width)
        if cut == -1:
            cut = max_width
        wrapped.append(cols_str[: cut + 1].strip())
        cols_str = cols_str[cut + 1 :].strip()
    print("📋 Columnas a insertar:")
    for line in wrapped:
        print("   " + line)

# Confirmación clara y por defecto negativa
confirm = input("⚠️ Escribe 'Si' para confirmar la importación a MariaDB (enter = No): ")
if confirm.strip().lower() != "si":
    print("Importación cancelada por el usuario.")
    try:
        logger.info("Importación cancelada por el usuario.")
    except Exception:
        pass
    sys.exit(0)

# ADICIÓN: ejecutar inserción en bloque (to_sql) usando df_insert y marcar éxito con flag insert_success
insert_success = False
try:
    df_insert = df_insert.fillna("").astype(str)
    df_insert.to_sql(
        name=target_table,
        con=maria_engine,
        if_exists="append",
        index=False,
        chunksize=500,
    )
    print("✅ Importación completada correctamente.")
    try:
        logger.info("Importación completada correctamente.")
    except Exception:
        pass
    insert_success = True
except Exception as e:
    print("❌ Error durante la importación de datos a MariaDB")
    print("Detalles:", str(e))
    try:
        logger.exception("Error durante la importación de datos a MariaDB: %s", str(e))
    except Exception:
        pass
    sys.exit(1)
# FIN ADICIÓN

# ========================== BLOQUE 5: POST-INSERT (snapshot local) =========================
if verbose:
    print(
        "\n========================= BLOQUE 5: POST-INSERT (snapshot local) ========================="
    )

# Ejecutar post-insert solo si la inserción fue exitosa
if "insert_success" in globals() and insert_success:
    # No intentamos recuperar por _RunId porque ya no se insertan columnas de auditoría.
    # En su lugar guardamos un snapshot local post-insert (copia del df_insert que se insertó).
    ts_post = datetime.now().strftime("%Y%m%dT%H%M%S")
    snapshot_post = os.path.join(data_dir, f"audit_post_insert_{ts_post}.csv")
    try:
        # df_insert contiene los datos que se enviaron a la BD
        df_insert.to_csv(snapshot_post, index=False, encoding="utf-8-sig")
        print(f"📁 Snapshot post-insert guardado en: {snapshot_post}")
        try:
            logger.info("Snapshot post-insert guardado en: %s", snapshot_post)
        except Exception:
            pass
    except Exception as e:
        print("⚠️ No se pudo guardar snapshot post-insert local:", str(e))
        try:
            logger.exception(
                "No se pudo guardar snapshot post-insert local: %s", str(e)
            )
        except Exception:
            pass

    # ADICIÓN: validación con MAX(Id) post-insert (usar Id autoincremental para medir filas insertadas)
    new_max_id = None
    try:
        if "maria_engine" in globals() and maria_engine is not None:
            with maria_engine.connect() as conn:
                q = text(f"SELECT MAX(Id) FROM {db}.{target_table}")
                res = conn.execute(q)
                new_max_id = res.scalar()
                print(f"🔎 MAX(Id) después de la inserción: {new_max_id}")
                try:
                    logger.info("MAX(Id) después de inserción=%s", new_max_id)
                except Exception:
                    pass

                # Conteo por Id autoincremental
                if prev_max_id is not None and new_max_id is not None:
                    inserted_count = int(new_max_id) - int(prev_max_id)
                    print(
                        f"✅ Filas esperadas: {len(df_insert)}, Filas insertadas (por Id): {inserted_count}"
                    )
                    try:
                        logger.info(
                            "Filas esperadas=%d, Filas insertadas=%d",
                            len(df_insert),
                            inserted_count,
                        )
                    except Exception:
                        pass
                    # Bandera de éxito por conteo
                    if inserted_count == len(df_insert):
                        print(
                            "🏁 Conteo OK: número de filas insertadas coincide con filas a insertar."
                        )
                    else:
                        print(
                            "❗ Conteo DIFERENTE: filas insertadas no coinciden con filas a insertar."
                        )
                else:
                    print("⚠️ No se pudo comparar MAX(Id) previo/post (alguno es NULL).")
                    try:
                        logger.warning(
                            "No se pudo comparar MAX(Id) previo/post. prev=%s new=%s",
                            prev_max_id,
                            new_max_id,
                        )
                    except Exception:
                        pass
        else:
            print(
                "⚠️ maria_engine no está definido; no se pudo validar MAX(Id) post-insert."
            )
            try:
                logger.warning(
                    "maria_engine no está definido; no se obtuvo MAX(Id) post-insert."
                )
            except Exception:
                pass
    except Exception as e:
        print("⚠️ No se pudo obtener MAX(Id) post-insert:", str(e))
        try:
            logger.exception("No se pudo obtener MAX(Id) post-insert: %s", str(e))
        except Exception:
            pass
    # FIN ADICIÓN

    # ADICIÓN: comparativa de sumas CSV vs BD para columnas en validations
    try:
        # Calcular sumas locales (CSV)
        local_sums = {}
        for col in validations.keys():
            if col in df_insert.columns:
                local_sums[col] = pd.to_numeric(df_insert[col], errors="coerce").sum(
                    skipna=True
                )
            else:
                local_sums[col] = None

        # Consultar sumas en BD para el rango de Ids insertados (si prev_max_id/new_max_id disponibles)
        db_sums = {}
        if (
            prev_max_id is not None
            and new_max_id is not None
            and new_max_id > prev_max_id
        ):
            try:
                with maria_engine.connect() as conn:
                    for col, agg in validations.items():
                        if col in df_insert.columns:
                            # Evitar inyección: usar parámetros y nombres de columna seguros (asumimos col proviene de configuración)
                            q_sum = text(
                                f"SELECT SUM(`{col}`) FROM {db}.{target_table} WHERE Id > :prev_id AND Id <= :new_id"
                            )
                            res = conn.execute(
                                q_sum, {"prev_id": prev_max_id, "new_id": new_max_id}
                            )
                            db_val = res.scalar()
                            db_sums[col] = db_val if db_val is not None else 0.0
                        else:
                            db_sums[col] = None
            except Exception as e:
                print("⚠️ Error consultando sumas en BD:", str(e))
                try:
                    logger.exception("Error consultando sumas en BD: %s", str(e))
                except Exception:
                    pass
        else:
            # Si no hay rango de Ids, no podemos calcular sumas por rango; intentar sumar toda la tabla como fallback (opcional)
            try:
                with maria_engine.connect() as conn:
                    for col in validations.keys():
                        if col in df_insert.columns:
                            q_sum_all = text(
                                f"SELECT SUM(`{col}`) FROM {db}.{target_table}"
                            )
                            res = conn.execute(q_sum_all)
                            db_val = res.scalar()
                            db_sums[col] = db_val if db_val is not None else 0.0
                        else:
                            db_sums[col] = None
                print(
                    "ℹ️ Nota: no se pudo usar rango Ids (prev/new), se comparó con suma total de la tabla como fallback."
                )
                try:
                    logger.info(
                        "Se usó suma total de tabla como fallback para comparativa."
                    )
                except Exception:
                    pass
            except Exception as e:
                print("⚠️ Error consultando sumas totales en BD:", str(e))
                try:
                    logger.exception(
                        "Error consultando sumas totales en BD: %s", str(e)
                    )
                except Exception:
                    pass

        # Mostrar comparativa y banderitas por columna
        for col in validations.keys():
            local_val = local_sums.get(col)
            db_val = db_sums.get(col)
            if local_val is None:
                print(
                    f"ℹ️ Columna {col}: no presente en df_insert, se omite comparativa."
                )
                continue
            # Normalizar None a 0.0 para comparación numérica
            local_val_num = float(local_val) if local_val is not None else 0.0
            db_val_num = float(db_val) if db_val is not None else 0.0
            diff = local_val_num - db_val_num
            print(
                f"🔢 Comparativa {col}: CSV_sum={local_val_num} | DB_sum={db_val_num} | Diff={diff}"
            )
            try:
                logger.info(
                    "Comparativa %s: CSV_sum=%s DB_sum=%s Diff=%s",
                    col,
                    local_val_num,
                    db_val_num,
                    diff,
                )
            except Exception:
                pass
            # Banderita de éxito/fallo (tolerancia exacta; si quieres tolerancia relativa, ajustar aquí)
            if abs(diff) < 1e-6:
                print(f"🏁 Validación {col}: OK (suma CSV coincide con suma BD).")
            else:
                print(f"❗ Validación {col}: DIFERENCIA detectada (revisar).")
    except Exception as e:
        print("⚠️ Error durante la comparativa de sumas:", str(e))
        try:
            logger.exception("Error durante la comparativa de sumas: %s", str(e))
        except Exception:
            pass
    # FIN ADICIÓN

    # Comparaciones rápidas de integridad (conteo y sumas para columnas de validación) - ya mostradas arriba
    try:
        local_count = len(df_insert)
        print(f"Conteo local pre-insert: {local_count}")
        # Ya mostramos sumas locales; si quieres, repetir aquí
    except Exception as e:
        print("⚠️ Error calculando conteo local:", str(e))
        try:
            logger.exception("Error calculando conteo local: %s", str(e))
        except Exception:
            pass

    if verbose:
        print("ℹ️ Nota: validación realizada con MAX(Id) y sumas locales/BD.")
else:
    # Si la inserción no fue exitosa o no se ejecutó, informar y no ejecutar post-insert
    try:
        logger.info(
            "Post-insert omitido porque la inserción no fue exitosa o no se ejecutó."
        )
    except Exception:
        pass
    if verbose:
        print(
            "⚠️ Post-insert omitido porque la inserción no fue exitosa o no se ejecutó."
        )
# ========================== FIN DEL SCRIPT =========================
if verbose:
    print("\n========================= FIN DEL PROCESO =========================")
