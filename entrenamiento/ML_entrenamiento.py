"""
ML_entrenamiento.py
===================
Entrena un modelo XGBoost multi-output con GPU (CUDA).
Usa el ciudad_id_map.json existente (IDs 6-245 que coinciden con estacion_punto).
Lee los parquets individuales de parts_parquet/ directamente.

CORRECCIÓN: Entrena con pares (T → T+1) para evitar data leakage.
  - Features: condiciones en hora T + temporales de hora T+1
  - Targets: condiciones en hora T+1
  - Incluye temperatura como feature de entrada

Requisitos:
    pip install xgboost scikit-learn pandas numpy pyarrow joblib

Uso:
    python ML_entrenamiento.py

Salida:
    - models/modelo_completo.pkl
    - models/metricas.json
    - log_entrenamiento.txt
"""

import json
import logging
import time
import unicodedata
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.multioutput import MultiOutputRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import train_test_split

# ─── Helper: normalizar nombre (quitar acentos) ─────────────────────────────
def normalizar_nombre(s):
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("log_entrenamiento.txt", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─── Configuración ────────────────────────────────────────────────────────────
PARTS_DIR    = Path("parts_parquet")
MODELS_DIR   = Path("models")
CIUDAD_MAP   = MODELS_DIR / "ciudad_id_map.json"
MODELS_DIR.mkdir(exist_ok=True)

# Features: condiciones ACTUALES (hora T) + temporales del OBJETIVO (hora T+1)
FEATURES = [
    "ciudad_id",
    "latitud",
    "longitud",
    "altitud_m",
    # Temporales de la hora OBJETIVO (T+1)
    "hora_target",
    "dia_anio_target",
    "mes_target",
    # Condiciones ACTUALES (hora T) — lo que sabemos ahora
    "temp_actual",
    "humedad_actual",
    "presion_actual",
    "viento_vel_actual",
    "viento_dir_actual",
]

TARGETS = [
    "temperatura_2m",
    "precipitacion_mm",
    "viento_vel_ms",
    "viento_racha_ms",
    "humedad_rel_pct",
    "presion_hpa",
    "reflectividad_dbz",
]

XGB_PARAMS = {
    "n_estimators":      500,
    "max_depth":         6,
    "learning_rate":     0.05,
    "subsample":         0.8,
    "colsample_bytree":  0.8,
    "min_child_weight":  3,
    "device":            "cuda",
    "tree_method":       "hist",
    "max_bin":           128,
    "sampling_method":   "gradient_based",
    "random_state":      42,
    "n_jobs":            1,
}

# ─── Reflectividad ────────────────────────────────────────────────────────────
def aniadir_reflectividad(df):
    if "reflectividad_dbz" not in df.columns:
        log.info("Calculando reflectividad_dbz (Marshall-Palmer)...")
        R = df["precipitacion_mm"].values
        df["reflectividad_dbz"] = np.where(
            R > 0,
            10 * np.log10(200 * R ** 1.6),
            -32.0
        )
    return df

# ─── Cargar datos y crear pares (T → T+1) ───────────────────────────────────
def cargar_datos():
    if not CIUDAD_MAP.exists():
        raise FileNotFoundError(f"No se encontró {CIUDAD_MAP}. Créalo antes de entrenar.")

    with open(CIUDAD_MAP, encoding="utf-8") as f:
        ciudad_a_id = json.load(f)
    log.info(f"ciudad_id_map cargado: {len(ciudad_a_id)} ciudades (IDs {min(ciudad_a_id.values())}-{max(ciudad_a_id.values())})")

    norm_a_id = {normalizar_nombre(k): v for k, v in ciudad_a_id.items()}
    norm_a_original = {normalizar_nombre(k): k for k in ciudad_a_id}

    parts = sorted(PARTS_DIR.glob("part_*.parquet"))
    if not parts:
        raise FileNotFoundError(f"No hay parquets en {PARTS_DIR}/")

    log.info(f"Cargando {len(parts)} parquets y creando pares (T → T+1)...")
    t0 = time.time()

    pares_list = []
    ciudades_cargadas = set()

    for p in parts:
        df_part = pd.read_parquet(p)
        nombre = df_part["nombre"].iloc[0] if "nombre" in df_part.columns else None
        if nombre is None:
            continue

        nombre_norm = normalizar_nombre(nombre)
        if nombre in ciudad_a_id:
            cid = ciudad_a_id[nombre]
            ciudades_cargadas.add(nombre)
        elif nombre_norm in norm_a_id:
            nombre_original = norm_a_original[nombre_norm]
            df_part["nombre"] = nombre_original
            cid = norm_a_id[nombre_norm]
            ciudades_cargadas.add(nombre_original)
        else:
            continue

        # Ordenar por tiempo
        df_part = df_part.sort_values("hora_validez").reset_index(drop=True)

        # Añadir reflectividad
        df_part = aniadir_reflectividad(df_part)

        # Eliminar filas con nulos en columnas críticas
        cols_check = [
            "temperatura_2m", "precipitacion_mm", "viento_vel_ms",
            "viento_dir_deg", "viento_racha_ms", "humedad_rel_pct",
            "presion_hpa", "reflectividad_dbz",
        ]
        df_part = df_part.dropna(subset=cols_check)

        if len(df_part) < 2:
            continue

        # Crear pares: fila T (features) → fila T+1 (targets)
        lat = df_part["latitud"].iloc[0]
        lon = df_part["longitud"].iloc[0]
        alt = df_part["altitud_m"].iloc[0]

        vals = df_part[cols_check + ["hora_validez"]].values

        # Índices de columnas en vals
        # 0:temp, 1:precip, 2:viento_vel, 3:viento_dir, 4:viento_racha,
        # 5:humedad, 6:presion, 7:reflectividad, 8:hora_validez
        for i in range(len(vals) - 1):
            hora_validez_target = pd.Timestamp(vals[i + 1, 8])

            pares_list.append([
                cid, lat, lon, alt,
                # Temporales del OBJETIVO (T+1)
                hora_validez_target.hour,
                hora_validez_target.day_of_year,
                hora_validez_target.month,
                # Condiciones ACTUALES (T)
                vals[i, 0],  # temp_actual
                vals[i, 5],  # humedad_actual
                vals[i, 6],  # presion_actual
                vals[i, 2],  # viento_vel_actual
                vals[i, 3],  # viento_dir_actual
                # TARGETS (T+1)
                vals[i + 1, 0],  # temperatura_2m
                vals[i + 1, 1],  # precipitacion_mm
                vals[i + 1, 2],  # viento_vel_ms
                vals[i + 1, 4],  # viento_racha_ms
                vals[i + 1, 5],  # humedad_rel_pct
                vals[i + 1, 6],  # presion_hpa
                vals[i + 1, 7],  # reflectividad_dbz
            ])

    if not pares_list:
        raise RuntimeError("No se generaron pares de entrenamiento")

    all_cols = FEATURES + TARGETS
    df_pares = pd.DataFrame(pares_list, columns=all_cols)

    log.info(f"  {len(df_pares):,} pares (T→T+1) creados en {time.time()-t0:.1f}s")
    log.info(f"  {len(ciudades_cargadas)} ciudades cargadas")

    sin_datos = set(ciudad_a_id.keys()) - ciudades_cargadas
    if sin_datos:
        log.warning(f"  {len(sin_datos)} ciudades sin parquet: {sorted(sin_datos)[:10]}...")

    return df_pares

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 55)
    log.info("ENTRENAMIENTO XGBOOST MULTI-OUTPUT (T → T+1)")
    log.info(f"Features: {FEATURES}")
    log.info(f"Targets:  {TARGETS}")
    log.info("=" * 55)

    # Verificar GPU
    try:
        test = xgb.XGBRegressor(device="cuda", tree_method="hist", n_estimators=1)
        test.fit([[1, 2]], [1])
        log.info("GPU CUDA: disponible")
    except Exception as e:
        log.warning(f"GPU no disponible, usando CPU: {e}")
        XGB_PARAMS["device"] = "cpu"

    df = cargar_datos()

    X = df[FEATURES].values.astype(np.float32)
    Y = df[TARGETS].values.astype(np.float32)

    # Split temporal 80/20 sin mezclar
    X_train, X_test, Y_train, Y_test = train_test_split(
        X, Y, test_size=0.2, shuffle=False
    )
    log.info(f"Train: {len(X_train):,} | Test: {len(X_test):,}")

    # Entrenar
    log.info("Entrenando MultiOutputRegressor...")
    modelo = MultiOutputRegressor(
        xgb.XGBRegressor(**XGB_PARAMS),
        n_jobs=1
    )

    t0 = time.time()
    modelo.fit(X_train, Y_train)
    t_train = time.time() - t0
    log.info(f"Entrenamiento completado en {t_train:.1f}s")

    # Métricas por variable
    Y_pred = modelo.predict(X_test)
    metricas = {}
    log.info(f"{'Variable':<22} {'MAE':>8} {'RMSE':>8} {'BIAS':>8}")
    log.info("-" * 50)
    for i, target in enumerate(TARGETS):
        mae  = mean_absolute_error(Y_test[:, i], Y_pred[:, i])
        rmse = mean_squared_error(Y_test[:, i], Y_pred[:, i]) ** 0.5
        bias = float(np.mean(Y_pred[:, i] - Y_test[:, i]))
        metricas[target] = {
            "mae":  round(mae, 4),
            "rmse": round(rmse, 4),
            "bias": round(bias, 4),
        }
        log.info(f"{target:<22} {mae:>8.3f} {rmse:>8.3f} {bias:>+8.3f}")

    # Guardar modelo
    modelo_path = MODELS_DIR / "modelo_completo.pkl"
    joblib.dump({
        "modelo":   modelo,
        "features": FEATURES,
        "targets":  TARGETS,
    }, modelo_path)
    size_mb = modelo_path.stat().st_size / (1024**2)
    log.info(f"Modelo guardado: {modelo_path} ({size_mb:.1f} MB)")

    # Guardar métricas
    metricas_path = MODELS_DIR / "metricas.json"
    with open(metricas_path, "w") as f:
        json.dump(metricas, f, indent=2)

    log.info("=" * 55)
    log.info(f"COMPLETADO en {t_train/60:.1f} minutos")
    log.info(f"Modelo:   {modelo_path}")
    log.info(f"Metricas: {metricas_path}")
    log.info("=" * 55)


if __name__ == "__main__":
    main()
