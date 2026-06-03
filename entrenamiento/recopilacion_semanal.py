"""
recopilacion_semanal.py
=======================
Recopilación semanal de datos reales vs predicciones ML.

Flujo:
    1. Descarga últimos 7 días de datos reales (OpenMeteo) para 240 ciudades
    2. Obtiene predicciones propias de Supabase para el mismo periodo
    3. Compara predicciones vs realidad (MAE, RMSE, bias)
    4. Sube resultados a Supabase Storage (bucket datos-semanales)
    5. Agrega datos reales a parquets locales para reentrenamiento

Requisitos:
    pip install openmeteo-requests requests-cache retry-requests
    pip install supabase python-dotenv pandas numpy pyarrow

Uso:
    python recopilacion_semanal.py
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import openmeteo_requests
import requests_cache
from retry_requests import retry
from dotenv import load_dotenv
from supabase import create_client

from descarga_test import ESTACIONES, nombre_seguro

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("log_recopilacion.txt", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─── Configuración ────────────────────────────────────────────────────────────
VARIABLES = [
    "temperature_2m",
    "precipitation",
    "windspeed_10m",
    "winddirection_10m",
    "windgusts_10m",
    "relativehumidity_2m",
    "surface_pressure",
    "rain",
]

RENOMBRAR = {
    "temperature_2m":      "temperatura_2m",
    "precipitation":       "precipitacion_mm",
    "windspeed_10m":       "viento_vel_ms",
    "winddirection_10m":   "viento_dir_deg",
    "windgusts_10m":       "viento_racha_ms",
    "relativehumidity_2m": "humedad_rel_pct",
    "surface_pressure":    "presion_hpa",
    "rain":                "lluvia_mm",
}

VARS_COMPARAR = [
    "temperatura_2m",
    "precipitacion_mm",
    "viento_vel_ms",
    "viento_racha_ms",
    "humedad_rel_pct",
    "presion_hpa",
]

PARTS_DIR    = Path("parts_parquet")
BUCKET_NAME  = "datos-semanales"
PAUSA_ENTRE  = 2        # segundos entre peticiones (7 días es ligero)
PAUSA_RATE   = 65       # segundos si hay rate limit

load_dotenv()
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]


# ─── Ventana temporal ─────────────────────────────────────────────────────────
def calcular_ventana_semanal(offset_dias=2):
    """Devuelve (fecha_inicio, fecha_fin) como strings YYYY-MM-DD.
    offset_dias compensa el retraso del archivo OpenMeteo (1-2 días)."""
    ahora = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    fecha_fin = ahora - timedelta(days=offset_dias)
    fecha_inicio = fecha_fin - timedelta(days=6)  # 7 días en total
    return fecha_inicio.strftime("%Y-%m-%d"), fecha_fin.strftime("%Y-%m-%d")


# ─── Cliente OpenMeteo ────────────────────────────────────────────────────────
def crear_cliente():
    cache = requests_cache.CachedSession(".openmeteo_cache_semanal", expire_after=86400)
    session = retry(cache, retries=3, backoff_factor=2.0)
    return openmeteo_requests.Client(session=session)


# ─── Descarga de una estación (7 días) ───────────────────────────────────────
def descargar_semana(cliente, nombre, lat, lon, altitud, fecha_inicio, fecha_fin, intento=1):
    try:
        resp = cliente.weather_api(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude":        lat,
                "longitude":       lon,
                "start_date":      fecha_inicio,
                "end_date":        fecha_fin,
                "hourly":          VARIABLES,
                "timezone":        "Europe/Madrid",
                "wind_speed_unit": "ms",
            }
        )[0]

        h = resp.Hourly()
        fechas = pd.date_range(
            start=pd.Timestamp(h.Time(),    unit="s", tz="Europe/Madrid"),
            end  =pd.Timestamp(h.TimeEnd(), unit="s", tz="Europe/Madrid"),
            freq =pd.Timedelta(seconds=h.Interval()),
            inclusive="left"
        )

        df = pd.DataFrame({"hora_validez": fechas})
        for i, var in enumerate(VARIABLES):
            df[var] = h.Variables(i).ValuesAsNumpy()

        df = df.rename(columns=RENOMBRAR)
        df["nombre"]    = nombre
        df["latitud"]   = lat
        df["longitud"]  = lon
        df["altitud_m"] = altitud
        df["hora"]      = df["hora_validez"].dt.hour
        df["dia_anio"]  = df["hora_validez"].dt.dayofyear
        df["mes"]       = df["hora_validez"].dt.month
        df["anio"]      = df["hora_validez"].dt.year

        return df

    except Exception as e:
        if "Minutely API request limit exceeded" in str(e):
            if intento <= 2:
                log.warning(f"  Rate limit en {nombre}. Esperando {PAUSA_RATE}s...")
                time.sleep(PAUSA_RATE)
                return descargar_semana(cliente, nombre, lat, lon, altitud,
                                       fecha_inicio, fecha_fin, intento + 1)
            else:
                log.error(f"  Rate limit persistente en {nombre}. Saltando.")
                return None
        log.error(f"  Error en {nombre}: {e}")
        return None


# ─── Descargar todas las estaciones ──────────────────────────────────────────
def descargar_todas(fecha_inicio, fecha_fin):
    cliente = crear_cliente()
    dfs = []
    fallos = []

    for idx, (nombre, lat, lon, altitud) in enumerate(ESTACIONES):
        log.info(f"  [{idx+1}/{len(ESTACIONES)}] {nombre}")
        df = descargar_semana(cliente, nombre, lat, lon, altitud, fecha_inicio, fecha_fin)

        if df is not None and len(df) > 0:
            dfs.append(df)
        else:
            fallos.append(nombre)

        if idx < len(ESTACIONES) - 1:
            time.sleep(PAUSA_ENTRE)

    if fallos:
        log.warning(f"  {len(fallos)} estaciones fallaron: {fallos[:10]}...")

    if not dfs:
        raise RuntimeError("No se pudo descargar ninguna estación")

    return pd.concat(dfs, ignore_index=True)


# ─── Obtener predicciones de Supabase ────────────────────────────────────────
def obtener_predicciones_supabase(sb, fecha_inicio, fecha_fin):
    """Obtiene predicciones del periodo desde Supabase."""
    fecha_inicio_iso = f"{fecha_inicio}T00:00:00+00:00"
    fecha_fin_iso    = f"{fecha_fin}T23:59:59+00:00"

    # Obtener mapa estacion_id → nombre
    est_res = sb.table("estacion_punto").select("id, nombre").execute()
    id_a_nombre = {e["id"]: e["nombre"] for e in est_res.data}

    # Obtener pasos horarios en el rango
    pasos_res = (
        sb.table("paso_horario")
        .select("id, hora_validez, ciclo_id")
        .gte("hora_validez", fecha_inicio_iso)
        .lte("hora_validez", fecha_fin_iso)
        .execute()
    )

    if not pasos_res.data:
        log.warning("No hay pasos horarios en el rango de fechas")
        return pd.DataFrame()

    paso_ids = [p["id"] for p in pasos_res.data]
    paso_a_hora = {p["id"]: p["hora_validez"] for p in pasos_res.data}

    # Obtener predicciones por lotes de paso_ids
    todas_pred = []
    lote = 50
    for i in range(0, len(paso_ids), lote):
        batch = paso_ids[i:i+lote]
        res = (
            sb.table("prediccion_punto")
            .select(
                "estacion_id, paso_id, "
                "temperatura_2m, precipitacion_mm, viento_vel_ms, "
                "viento_dir_deg, viento_racha_ms, humedad_rel_pct, "
                "presion_hpa, reflectividad_dbz"
            )
            .in_("paso_id", batch)
            .execute()
        )
        todas_pred.extend(res.data)

    if not todas_pred:
        log.warning("No hay predicciones en el rango de fechas")
        return pd.DataFrame()

    df = pd.DataFrame(todas_pred)
    df["nombre"] = df["estacion_id"].map(id_a_nombre)
    df["hora_validez"] = df["paso_id"].map(paso_a_hora)
    df["hora_validez"] = pd.to_datetime(df["hora_validez"])

    log.info(f"  {len(df)} predicciones obtenidas de Supabase")
    return df


# ─── Calcular métricas ──────────────────────────────────────────────────────
def calcular_metricas(df_real, df_pred, fecha_inicio, fecha_fin):
    """Compara predicciones vs realidad. Devuelve dict con métricas."""
    # Normalizar timestamps a hora UTC sin minutos
    df_real = df_real.copy()
    df_pred = df_pred.copy()

    df_real["hora_utc"] = pd.to_datetime(df_real["hora_validez"], utc=True).dt.floor("h")
    df_pred["hora_utc"] = pd.to_datetime(df_pred["hora_validez"], utc=True).dt.floor("h")

    # Merge por (nombre, hora)
    merged = pd.merge(
        df_real, df_pred,
        on=["nombre", "hora_utc"],
        suffixes=("_real", "_pred"),
        how="inner"
    )

    log.info(f"  Alineación: {len(df_real)} reales × {len(df_pred)} pred → {len(merged)} coincidencias")

    if merged.empty:
        log.warning("  Sin coincidencias para comparar")
        return {"periodo": {"inicio": fecha_inicio, "fin": fecha_fin},
                "global": {}, "por_estacion": {}, "coincidencias": 0}

    metricas = {
        "fecha_calculo": datetime.now(timezone.utc).isoformat(),
        "periodo": {"inicio": fecha_inicio, "fin": fecha_fin},
        "coincidencias": len(merged),
        "global": {},
        "por_estacion": {},
    }

    # Métricas globales
    for var in VARS_COMPARAR:
        col_r = f"{var}_real" if f"{var}_real" in merged.columns else var
        col_p = f"{var}_pred" if f"{var}_pred" in merged.columns else None

        if col_p is None or col_r not in merged.columns:
            continue

        real = merged[col_r].dropna()
        pred = merged[col_p].reindex(real.index).dropna()
        idx = real.index.intersection(pred.index)

        if len(idx) == 0:
            continue

        r, p = real.loc[idx], pred.loc[idx]
        metricas["global"][var] = {
            "mae":  round(float(np.mean(np.abs(p - r))), 4),
            "rmse": round(float(np.sqrt(np.mean((p - r)**2))), 4),
            "bias": round(float(np.mean(p - r)), 4),
            "n":    len(idx),
        }

    # Métricas por estación
    for nombre in merged["nombre"].unique():
        df_est = merged[merged["nombre"] == nombre]
        metricas["por_estacion"][nombre] = {}

        for var in VARS_COMPARAR:
            col_r = f"{var}_real" if f"{var}_real" in df_est.columns else var
            col_p = f"{var}_pred" if f"{var}_pred" in df_est.columns else None

            if col_p is None or col_r not in df_est.columns:
                continue

            real = df_est[col_r].dropna()
            pred = df_est[col_p].reindex(real.index).dropna()
            idx = real.index.intersection(pred.index)

            if len(idx) == 0:
                continue

            r, p = real.loc[idx], pred.loc[idx]
            metricas["por_estacion"][nombre][var] = {
                "mae":  round(float(np.mean(np.abs(p - r))), 4),
                "rmse": round(float(np.sqrt(np.mean((p - r)**2))), 4),
                "bias": round(float(np.mean(p - r)), 4),
                "n":    len(idx),
            }

    return metricas


# ─── Supabase Storage ────────────────────────────────────────────────────────
def crear_bucket_si_no_existe(sb):
    try:
        sb.storage.get_bucket(BUCKET_NAME)
        log.info(f"  Bucket '{BUCKET_NAME}' ya existe")
    except Exception:
        sb.storage.create_bucket(BUCKET_NAME, options={"public": False})
        log.info(f"  Bucket '{BUCKET_NAME}' creado")


def subir_a_storage(sb, archivo_local, ruta_remota):
    with open(archivo_local, "rb") as f:
        sb.storage.from_(BUCKET_NAME).upload(
            path=ruta_remota,
            file=f,
            file_options={"content-type": "application/octet-stream", "upsert": "true"}
        )
    log.info(f"  Subido: {ruta_remota}")


# ─── Agregar a parquets existentes ───────────────────────────────────────────
def agregar_a_parquets(df_real):
    """Agrega datos reales semanales a los parquets individuales en parts_parquet/."""
    actualizados = 0

    for nombre in df_real["nombre"].unique():
        safe = nombre_seguro(nombre)
        part_path = PARTS_DIR / f"part_{safe}.parquet"
        df_nuevo = df_real[df_real["nombre"] == nombre].copy()

        if part_path.exists():
            df_existente = pd.read_parquet(part_path)
            antes = len(df_existente)
            df_combinado = pd.concat([df_existente, df_nuevo], ignore_index=True)
            df_combinado = df_combinado.drop_duplicates(subset=["hora_validez"], keep="last")
            df_combinado = df_combinado.sort_values("hora_validez").reset_index(drop=True)
            filas_nuevas = len(df_combinado) - antes
        else:
            df_combinado = df_nuevo
            filas_nuevas = len(df_nuevo)
            log.warning(f"  {nombre}: parquet no existía, creando nuevo")

        df_combinado.to_parquet(part_path, index=False, compression="snappy")
        actualizados += 1

        if filas_nuevas > 0:
            log.info(f"  {nombre}: +{filas_nuevas} filas nuevas")

    log.info(f"  {actualizados} parquets actualizados")


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 70)
    log.info("RECOPILACIÓN SEMANAL — Datos reales + Validación ML")
    log.info("=" * 70)

    # 1. Ventana temporal
    log.info("\n[1/6] Calculando ventana temporal...")
    fecha_inicio, fecha_fin = calcular_ventana_semanal(offset_dias=2)
    log.info(f"  Periodo: {fecha_inicio} → {fecha_fin}")

    # 2. Descargar datos reales
    log.info(f"\n[2/6] Descargando datos reales de {len(ESTACIONES)} estaciones...")
    t0 = time.time()
    df_real = descargar_todas(fecha_inicio, fecha_fin)
    log.info(f"  {len(df_real):,} filas descargadas en {(time.time()-t0)/60:.1f} min")

    # Guardar parquet temporal
    parquet_temp = Path(f"semanal_{fecha_inicio}_{fecha_fin}.parquet")
    df_real.to_parquet(parquet_temp, index=False, compression="snappy")

    # 3. Obtener predicciones de Supabase
    log.info("\n[3/6] Obteniendo predicciones de Supabase...")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    df_pred = obtener_predicciones_supabase(sb, fecha_inicio, fecha_fin)

    # 4. Comparar y calcular métricas
    log.info("\n[4/6] Comparando predicciones vs realidad...")
    if not df_pred.empty:
        metricas = calcular_metricas(df_real, df_pred, fecha_inicio, fecha_fin)

        # Mostrar resumen global
        log.info(f"  {'Variable':<22} {'MAE':>8} {'RMSE':>8} {'Bias':>8}")
        log.info("  " + "-" * 50)
        for var, m in metricas["global"].items():
            log.info(f"  {var:<22} {m['mae']:>8.3f} {m['rmse']:>8.3f} {m['bias']:>8.3f}")
    else:
        log.info("  Sin predicciones para comparar (normal si el modelo aún no se ejecutó)")
        metricas = {
            "fecha_calculo": datetime.now(timezone.utc).isoformat(),
            "periodo": {"inicio": fecha_inicio, "fin": fecha_fin},
            "coincidencias": 0,
            "global": {},
            "por_estacion": {},
        }

    # Guardar métricas JSON
    metricas_json = Path(f"metricas_{fecha_inicio}_{fecha_fin}.json")
    with open(metricas_json, "w", encoding="utf-8") as f:
        json.dump(metricas, f, indent=2, ensure_ascii=False)

    # 5. Subir a Supabase Storage
    log.info("\n[5/6] Subiendo a Supabase Storage...")
    crear_bucket_si_no_existe(sb)
    subir_a_storage(sb, parquet_temp, f"parquet/semanal_{fecha_inicio}_{fecha_fin}.parquet")
    subir_a_storage(sb, metricas_json, f"metricas/metricas_{fecha_inicio}_{fecha_fin}.json")

    # 6. Agregar datos reales a parquets locales
    log.info("\n[6/6] Agregando datos a parquets locales...")
    agregar_a_parquets(df_real)

    # Limpieza temporal
    parquet_temp.unlink(missing_ok=True)
    metricas_json.unlink(missing_ok=True)

    # Resumen
    log.info("\n" + "=" * 70)
    log.info("COMPLETADO")
    log.info(f"  Periodo:     {fecha_inicio} → {fecha_fin}")
    log.info(f"  Estaciones:  {df_real['nombre'].nunique()}")
    log.info(f"  Filas reales: {len(df_real):,}")
    log.info(f"  Predicciones: {len(df_pred):,}")
    log.info(f"  Coincidencias: {metricas['coincidencias']}")
    if metricas["global"].get("temperatura_2m"):
        log.info(f"  MAE temp:    {metricas['global']['temperatura_2m']['mae']:.2f}°C")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
