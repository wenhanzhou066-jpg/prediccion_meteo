"""
train_xgboost_individual.py
============================
Entrena un modelo XGBoost multi-output con GPU (CUDA) usando UN SOLO parquet.
Útil para pruebas rápidas con datos parciales.

Requisitos:
    pip install xgboost scikit-learn pandas numpy pyarrow joblib

Uso:
    python ml_entrenamiento_individual.py
    python ml_entrenamiento_individual.py parts_parquet/part_Grid_CN6.parquet

Salida:
    - models/modelo_completo.pkl
    - models/ciudad_id_map.json
    - models/metricas.json
    - log_entrenamiento.txt
"""

import json
import logging
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.multioutput import MultiOutputRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import train_test_split

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
# Si se pasa un archivo como argumento, usarlo; si no, buscar el primero disponible
if len(sys.argv) > 1:
    PARQUET_PATH = Path(sys.argv[1])
else:
    # Buscar primer parquet en parts_parquet/
    parts = list(Path("parts_parquet").glob("part_*.parquet"))
    if not parts:
        raise FileNotFoundError("No se encontraron archivos part_*.parquet en parts_parquet/")
    PARQUET_PATH = parts[0]

MODELS_DIR = Path("models")
MODELS_DIR.mkdir(exist_ok=True)

FEATURES = [
    "ciudad_id",
    "latitud",
    "longitud",
    "altitud_m",
    "hora",
    "dia_anio",
    "mes",
    "humedad_rel_pct",
    "presion_hpa",
    "viento_vel_ms",
    "viento_dir_deg",
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
    "n_estimators":     500,
    "max_depth":        6,
    "learning_rate":    0.05,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 3,
    "device":           "cuda",
    "tree_method":      "hist",
    "random_state":     42,
    "n_jobs":           -1,
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

# ─── Cargar datos ─────────────────────────────────────────────────────────────
def cargar_datos():
    log.info(f"Cargando {PARQUET_PATH}...")
    t0 = time.time()

    df = pd.read_parquet(PARQUET_PATH)
    log.info(f"  {len(df):,} filas cargadas en {time.time()-t0:.1f}s")

    df = aniadir_reflectividad(df)

    # Ciudad ID numérico
    log.info("Generando ciudad_id...")
    ciudades = sorted(df["nombre"].unique())
    ciudad_a_id = {c: i for i, c in enumerate(ciudades)}
    df["ciudad_id"] = df["nombre"].map(ciudad_a_id).astype(np.int16)

    # Guardar mapeo para inferencia en producción
    map_path = MODELS_DIR / "ciudad_id_map.json"
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(ciudad_a_id, f, indent=2, ensure_ascii=False)
    log.info(f"  Mapeo guardado: {map_path} ({len(ciudad_a_id)} ciudades)")

    # Limpiar nulos
    df = df.dropna(subset=FEATURES + TARGETS)
    log.info(f"  {len(df):,} filas tras limpiar nulos")

    return df

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 55)
    log.info("ENTRENAMIENTO XGBOOST MULTI-OUTPUT (INDIVIDUAL)")
    log.info(f"Archivo: {PARQUET_PATH}")
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
        n_jobs=1  # XGBoost ya paraleliza internamente con GPU
    )

    t0 = time.time()
    modelo.fit(X_train, Y_train)
    t_train = time.time() - t0
    log.info(f"Entrenamiento completado en {t_train:.1f}s")

    # Métricas por variable
    Y_pred = modelo.predict(X_test)
    metricas = {}
    log.info(f"{'Variable':<22} {'MAE':>8} {'RMSE':>8}")
    log.info("-" * 42)
    for i, target in enumerate(TARGETS):
        mae  = mean_absolute_error(Y_test[:, i], Y_pred[:, i])
        rmse = mean_squared_error(Y_test[:, i], Y_pred[:, i]) ** 0.5
        bias = float(np.mean(Y_pred[:, i] - Y_test[:, i]))
        metricas[target] = {
            "mae":  round(mae, 4),
            "rmse": round(rmse, 4),
            "bias": round(bias, 4),
        }
        log.info(f"{target:<22} {mae:>8.3f} {rmse:>8.3f}")

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
