"""
prediccion_ml.py
================
Predicción meteorológica ML a 10 días con ventanas deslizantes.

Flujo:
    1. Carga modelo y coordenadas (Supabase + ESTACIONES)
    2. Filtra estaciones que tengan parquet descargado
    3. Obtiene condiciones meteorológicas actuales de OpenMeteo
    4. Predice 240 horas (10 días) por ventana deslizante
    5. Inserta en Supabase (ciclo + pasos + prediccion_punto)
    6. Guarda JSON de salida

Requisitos:
    pip install supabase python-dotenv joblib numpy xgboost
    pip install openmeteo-requests requests-cache retry-requests

Uso:
    python prediccion_ml.py
"""

import json
import os
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import openmeteo_requests
import requests_cache
from retry_requests import retry
from dotenv import load_dotenv
from supabase import create_client

from descarga_test import ESTACIONES

# ─── Configuración ────────────────────────────────────────────────────────────
MODELO_PATH    = Path("models/modelo_completo.pkl")
CIUDAD_MAP_PATH = Path("models/ciudad_id_map.json")
COORDS_CACHE   = Path("models/estaciones_coords.json")
OUTPUT_PATH    = Path("prediccion_test.json")
PARTS_DIR      = Path("parts_parquet")

N_HORAS = 240  # 10 días × 24 horas

load_dotenv()
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]


# ─── Helper ──────────────────────────────────────────────────────────────────
def normalizar_nombre(s):
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def nombre_seguro(s):
    s = s.strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace(" ", "_")
    import re
    return re.sub(r"[^0-9A-Za-z_\-]", "", s)


# ─── Cargar modelo ────────────────────────────────────────────────────────────
def cargar_modelo():
    if not MODELO_PATH.exists():
        raise FileNotFoundError(f"Modelo no encontrado: {MODELO_PATH}")
    if not CIUDAD_MAP_PATH.exists():
        raise FileNotFoundError(f"Mapeo de ciudades no encontrado: {CIUDAD_MAP_PATH}")

    modelo_data = joblib.load(MODELO_PATH)

    with open(CIUDAD_MAP_PATH, encoding="utf-8") as f:
        ciudad_a_id = json.load(f)

    return modelo_data, ciudad_a_id


# ─── Obtener/cachear coordenadas de estaciones ──────────────────────────────
def obtener_estaciones_coords(sb, ciudad_a_id):
    """Obtiene estaciones de Supabase + coordenadas de ESTACIONES. Cachea en JSON."""

    if COORDS_CACHE.exists():
        with open(COORDS_CACHE, encoding="utf-8") as f:
            cached = json.load(f)
        print(f"  Coordenadas cargadas desde caché ({len(cached)} estaciones)")
        return cached

    print("  Consultando estaciones en Supabase...")
    resultado = (
        sb.table("estacion_punto")
        .select("id, nombre, altitud_m")
        .eq("activa", True)
        .execute()
    )

    # Mapa nombre → coordenadas desde ESTACIONES (descarga_test)
    coords_map = {}
    norm_coords = {}
    for nombre, lat, lon, alt in ESTACIONES:
        coords_map[nombre] = (lat, lon, alt)
        norm_coords[normalizar_nombre(nombre)] = (lat, lon, alt, nombre)

    estaciones = {}
    for est in resultado.data:
        nombre_db = est["nombre"]
        nombre_norm = normalizar_nombre(nombre_db)

        # Match directo o normalizado
        if nombre_db in coords_map:
            lat, lon, alt = coords_map[nombre_db]
        elif nombre_norm in norm_coords:
            lat, lon, alt, _ = norm_coords[nombre_norm]
        elif nombre_db in ciudad_a_id:
            # Sin coordenadas, saltar
            print(f"    WARN: {nombre_db} sin coordenadas, saltando")
            continue
        else:
            continue

        if nombre_db not in ciudad_a_id:
            # Intentar match normalizado con ciudad_a_id
            matched = False
            for k in ciudad_a_id:
                if normalizar_nombre(k) == nombre_norm:
                    nombre_db = k
                    matched = True
                    break
            if not matched:
                continue

        estaciones[nombre_db] = {
            "id": est["id"],
            "latitud": lat,
            "longitud": lon,
            "altitud_m": float(est["altitud_m"]) if est.get("altitud_m") else alt,
        }

    # Cachear
    with open(COORDS_CACHE, "w", encoding="utf-8") as f:
        json.dump(estaciones, f, indent=2, ensure_ascii=False)

    print(f"  {len(estaciones)} estaciones cacheadas en {COORDS_CACHE}")
    return estaciones


# ─── Filtrar por parquets existentes ─────────────────────────────────────────
def filtrar_por_parquets(estaciones):
    """Solo mantiene estaciones que tengan parquet descargado."""
    parquets_existentes = {p.stem.replace("part_", "") for p in PARTS_DIR.glob("part_*.parquet")}

    filtradas = {}
    for nombre, datos in estaciones.items():
        safe = nombre_seguro(nombre)
        if safe in parquets_existentes:
            filtradas[nombre] = datos

    descartadas = set(estaciones.keys()) - set(filtradas.keys())
    if descartadas:
        print(f"  {len(descartadas)} estaciones sin parquet descartadas")

    return filtradas


# ─── Obtener condiciones actuales de OpenMeteo ───────────────────────────────
def obtener_condiciones_actuales(estaciones):
    """Obtiene condiciones meteorológicas actuales desde OpenMeteo forecast API."""
    cache = requests_cache.CachedSession(".openmeteo_cache_pred", expire_after=3600)
    session = retry(cache, retries=3, backoff_factor=2.0)
    cliente = openmeteo_requests.Client(session=session)

    variables = [
        "temperature_2m",
        "relativehumidity_2m",
        "surface_pressure",
        "windspeed_10m",
        "winddirection_10m",
    ]

    ahora_hora = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    condiciones = {}
    total = len(estaciones)

    for idx, (nombre, datos) in enumerate(estaciones.items()):
        try:
            resp = cliente.weather_api(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": datos["latitud"],
                    "longitude": datos["longitud"],
                    "hourly": variables,
                    "timezone": "UTC",
                    "wind_speed_unit": "ms",
                    "forecast_days": 1,
                }
            )[0]

            h = resp.Hourly()
            fechas = [
                ahora_hora.replace(tzinfo=None) + timedelta(hours=i)
                for i in range(h.VariablesLength())
            ]

            # Encontrar la hora más cercana a ahora
            hora_actual = ahora_hora.replace(tzinfo=None)
            idx_hora = 0
            for i, f in enumerate(fechas):
                if f >= hora_actual:
                    idx_hora = i
                    break

            condiciones[nombre] = {
                "temperatura_2m":   float(h.Variables(0).ValuesAsNumpy()[idx_hora]),
                "humedad_rel_pct":  float(h.Variables(1).ValuesAsNumpy()[idx_hora]),
                "presion_hpa":      float(h.Variables(2).ValuesAsNumpy()[idx_hora]),
                "viento_vel_ms":    float(h.Variables(3).ValuesAsNumpy()[idx_hora]),
                "viento_dir_deg":   float(h.Variables(4).ValuesAsNumpy()[idx_hora]),
            }

        except Exception as e:
            print(f"    WARN: No se pudo obtener datos actuales de {nombre}: {e}")
            condiciones[nombre] = {
                "temperatura_2m": 15.0,
                "humedad_rel_pct": 65.0,
                "presion_hpa": 1013.0,
                "viento_vel_ms": 3.5,
                "viento_dir_deg": 220.0,
            }

        if idx < total - 1:
            time.sleep(0.5)

        if (idx + 1) % 50 == 0:
            print(f"    [{idx+1}/{total}] condiciones obtenidas...")

    print(f"  Condiciones actuales obtenidas para {len(condiciones)} estaciones")
    return condiciones


# ─── Predicción por ventana deslizante ───────────────────────────────────────
def predecir_ventana_deslizante(modelo_data, ciudad_a_id, estaciones, condiciones, n_horas):
    """Predice n_horas para todas las estaciones con ventana deslizante.

    Hora 0: usa condiciones reales de OpenMeteo como input.
    Hora 1+: usa las predicciones anteriores (humedad, presión, viento)
    como input para la siguiente hora.
    """
    modelo = modelo_data["modelo"]
    targets = modelo_data["targets"]

    ahora = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    # Preparar lista ordenada de estaciones
    lista_est = [(nombre, datos) for nombre, datos in estaciones.items()]
    n_est = len(lista_est)

    # Índices de targets que se retroalimentan
    idx_temp    = targets.index("temperatura_2m")
    idx_humedad = targets.index("humedad_rel_pct")
    idx_presion = targets.index("presion_hpa")
    idx_viento  = targets.index("viento_vel_ms")

    # Estado meteorológico actual por estación (se actualiza cada hora)
    estado = np.zeros((n_est, 5), dtype=np.float32)  # temp, humedad, presion, viento_vel, viento_dir
    for i, (nombre, _) in enumerate(lista_est):
        c = condiciones.get(nombre, {})
        estado[i, 0] = c.get("temperatura_2m", 15.0)
        estado[i, 1] = c.get("humedad_rel_pct", 65.0)
        estado[i, 2] = c.get("presion_hpa", 1013.0)
        estado[i, 3] = c.get("viento_vel_ms", 3.5)
        estado[i, 4] = c.get("viento_dir_deg", 220.0)

    # Features estáticas por estación
    estaticas = np.zeros((n_est, 4), dtype=np.float32)  # ciudad_id, lat, lon, alt
    for i, (nombre, datos) in enumerate(lista_est):
        estaticas[i, 0] = ciudad_a_id[nombre]
        estaticas[i, 1] = datos["latitud"]
        estaticas[i, 2] = datos["longitud"]
        estaticas[i, 3] = datos["altitud_m"]

    # Almacenar todas las predicciones: [hora][estación] = {...}
    todas = []

    print(f"  Prediciendo {n_horas} horas para {n_est} estaciones...")
    t0 = time.time()

    for h in range(n_horas):
        hora_pred = ahora + timedelta(hours=h)
        hora_val = hora_pred.hour
        dia_anio = hora_pred.timetuple().tm_yday
        mes = hora_pred.month

        # Construir input batch: (n_est, 12)
        # Orden: ciudad_id, lat, lon, alt, hora_target, dia_anio_target, mes_target,
        #        temp_actual, humedad_actual, presion_actual, viento_vel_actual, viento_dir_actual
        X = np.zeros((n_est, 12), dtype=np.float32)
        X[:, 0:4] = estaticas                      # ciudad_id, lat, lon, alt
        X[:, 4] = hora_val                          # hora_target
        X[:, 5] = dia_anio                          # dia_anio_target
        X[:, 6] = mes                               # mes_target
        X[:, 7] = estado[:, 0]                      # temp_actual
        X[:, 8] = estado[:, 1]                      # humedad_actual
        X[:, 9] = estado[:, 2]                      # presion_actual
        X[:, 10] = estado[:, 3]                     # viento_vel_actual
        X[:, 11] = estado[:, 4]                     # viento_dir_actual

        # Predicción batch
        Y = modelo.predict(X)  # (n_est, n_targets)

        # Guardar resultados de esta hora
        hora_resultados = {
            "hora_validez": hora_pred.isoformat(),
            "offset_horas": h,
            "predicciones": {},
        }

        for i, (nombre, _) in enumerate(lista_est):
            hora_resultados["predicciones"][nombre] = {
                t: round(float(Y[i, j]), 4) for j, t in enumerate(targets)
            }

        todas.append(hora_resultados)

        # Ventana deslizante: actualizar estado meteorológico con predicciones
        estado[:, 0] = Y[:, idx_temp]       # temp → próxima hora
        estado[:, 1] = Y[:, idx_humedad]    # humedad → próxima hora
        estado[:, 2] = Y[:, idx_presion]    # presión → próxima hora
        estado[:, 3] = Y[:, idx_viento]     # viento_vel → próxima hora
        # viento_dir (estado[:, 4]) se mantiene (no es target del modelo)

        if (h + 1) % 24 == 0:
            print(f"    Día {(h+1)//24}/10 completado")

    elapsed = time.time() - t0
    print(f"  {n_horas * n_est:,} predicciones en {elapsed:.1f}s")

    return todas


# ─── Insertar en Supabase ────────────────────────────────────────────────────
def insertar_en_supabase(sb, estaciones, todas_predicciones):
    """Crea ciclo + pasos + predicciones en Supabase."""
    ahora = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    fin = ahora + timedelta(hours=N_HORAS)

    # 0. Desactivar ciclos anteriores
    sb.table("ciclo_prediccion").update({"activo": False}).eq("activo", True).execute()
    print("  Ciclos anteriores desactivados")

    # 1. Crear ciclo
    ciclo = (
        sb.table("ciclo_prediccion")
        .insert({
            "inicio_validez": ahora.isoformat(),
            "fin_validez": fin.isoformat(),
            "modelo": "xgboost_v1",
            "version": "1.0",
            "activo": True,
            "metadatos": {
                "n_estaciones": len(estaciones),
                "n_horas": N_HORAS,
                "tipo": "ventana_deslizante_10d",
            },
        })
        .execute()
    )
    ciclo_id = ciclo.data[0]["id"]
    print(f"  Ciclo creado: id={ciclo_id}")

    # 2. Obtener pasos creados por trigger y crear los que falten
    pasos_existentes = (
        sb.table("paso_horario")
        .select("id, offset_horas")
        .eq("ciclo_id", ciclo_id)
        .execute()
    )
    offset_a_paso = {p["offset_horas"]: p["id"] for p in pasos_existentes.data}

    # Crear pasos faltantes en lotes
    pasos_faltantes = []
    for h in range(N_HORAS):
        if h not in offset_a_paso:
            pasos_faltantes.append({
                "ciclo_id": ciclo_id,
                "hora_validez": (ahora + timedelta(hours=h)).isoformat(),
                "offset_horas": h,
            })

    if pasos_faltantes:
        print(f"  Creando {len(pasos_faltantes)} pasos horarios...")
        lote = 100
        for i in range(0, len(pasos_faltantes), lote):
            batch = pasos_faltantes[i:i+lote]
            res = sb.table("paso_horario").insert(batch).execute()
            for p in res.data:
                offset_a_paso[p["offset_horas"]] = p["id"]

    print(f"  {len(offset_a_paso)} pasos horarios listos")

    # 3. Insertar predicciones en lotes
    print(f"  Insertando predicciones...")
    registros = []

    for hora_data in todas_predicciones:
        h = hora_data["offset_horas"]
        paso_id = offset_a_paso[h]

        for nombre, pred in hora_data["predicciones"].items():
            est_id = estaciones[nombre]["id"]
            registros.append({
                "paso_id": paso_id,
                "estacion_id": est_id,
                "ciclo_id": ciclo_id,
                "temperatura_2m": pred.get("temperatura_2m"),
                "precipitacion_mm": pred.get("precipitacion_mm"),
                "precip_max_5km": pred.get("precipitacion_mm"),
                "precip_media_5km": pred.get("precipitacion_mm"),
                "viento_vel_ms": pred.get("viento_vel_ms"),
                "viento_dir_deg": None,
                "viento_racha_ms": pred.get("viento_racha_ms"),
                "humedad_rel_pct": pred.get("humedad_rel_pct"),
                "presion_hpa": pred.get("presion_hpa"),
                "reflectividad_dbz": pred.get("reflectividad_dbz"),
                "reflex_max_5km": pred.get("reflectividad_dbz"),
            })

    # Insertar en lotes de 500
    lote = 500
    total = len(registros)
    for i in range(0, total, lote):
        batch = registros[i:i+lote]
        sb.table("prediccion_punto").insert(batch).execute()
        if (i + lote) % 5000 < lote:
            print(f"    [{min(i+lote, total):,}/{total:,}] insertados")

    print(f"  {total:,} predicciones insertadas")
    return ciclo_id


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 65)
    print("PREDICCIÓN ML 10 DÍAS — Ventana deslizante")
    print("=" * 65)

    # 1. Cargar modelo
    print("\n[1/6] Cargando modelo...")
    modelo_data, ciudad_a_id = cargar_modelo()
    print(f"  Targets: {modelo_data['targets']}")

    # 2. Obtener coordenadas
    print("\n[2/6] Obteniendo coordenadas de estaciones...")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    estaciones = obtener_estaciones_coords(sb, ciudad_a_id)

    # 3. Filtrar por parquets existentes
    print("\n[3/6] Filtrando estaciones con parquet...")
    estaciones = filtrar_por_parquets(estaciones)
    print(f"  {len(estaciones)} estaciones con parquet disponible")

    if not estaciones:
        raise RuntimeError("No hay estaciones con parquet descargado")

    # 4. Obtener condiciones actuales
    print(f"\n[4/6] Obteniendo condiciones actuales de OpenMeteo...")
    condiciones = obtener_condiciones_actuales(estaciones)

    # 5. Predecir 10 días
    print(f"\n[5/6] Predicción ventana deslizante ({N_HORAS} horas)...")
    todas_predicciones = predecir_ventana_deslizante(
        modelo_data, ciudad_a_id, estaciones, condiciones, N_HORAS
    )

    # 6. Insertar en Supabase
    print("\n[6/6] Insertando en Supabase...")
    ciclo_id = insertar_en_supabase(sb, estaciones, todas_predicciones)

    # Guardar JSON
    print(f"\nGuardando JSON en {OUTPUT_PATH}...")
    ahora = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    json_salida = {
        "ciclo_id": ciclo_id,
        "inicio": ahora.isoformat(),
        "fin": (ahora + timedelta(hours=N_HORAS)).isoformat(),
        "n_horas": N_HORAS,
        "n_estaciones": len(estaciones),
        "estaciones": {
            nombre: {
                "id": datos["id"],
                "latitud": datos["latitud"],
                "longitud": datos["longitud"],
                "altitud_m": datos["altitud_m"],
            }
            for nombre, datos in estaciones.items()
        },
        "predicciones": todas_predicciones,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(json_salida, f, indent=2, ensure_ascii=False)

    # Resumen
    total_pred = len(estaciones) * N_HORAS
    print("\n" + "=" * 65)
    print(f"COMPLETADO")
    print(f"  Ciclo:        {ciclo_id}")
    print(f"  Estaciones:   {len(estaciones)}")
    print(f"  Horas:        {N_HORAS} (10 días)")
    print(f"  Predicciones: {total_pred:,}")
    print(f"  JSON:         {OUTPUT_PATH}")
    print("=" * 65)


if __name__ == "__main__":
    main()
