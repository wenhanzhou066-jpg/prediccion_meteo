# 🌦️ Sistema de Predicción Meteorológica para España

> Dashboard interactivo de pronóstico a 10 días con ML, datos geoespaciales y visualización en tiempo real.

![Stack](https://img.shields.io/badge/React-19-61DAFB?logo=react)
![Stack](https://img.shields.io/badge/XGBoost-GPU-orange?logo=python)
![Stack](https://img.shields.io/badge/Supabase-Postgres%20+%20PostGIS-3ECF8E?logo=supabase)
![Stack](https://img.shields.io/badge/Leaflet-1.9-199900?logo=leaflet)
![Stack](https://img.shields.io/badge/Vercel-deployed-000000?logo=vercel)



## Resumen ejecutivo

Aplicación end-to-end de predicción meteorológica para ~200 estaciones repartidas por España. Un pipeline de Python descarga años de datos históricos de Open-Meteo, entrena un modelo XGBoost multi-output en GPU que predice 7 variables meteorológicas a 240 horas vista (10 días), e inserta los resultados en Supabase Postgres con PostGIS. El frontend en React 19 + Leaflet consume una **API REST autogenerada por Supabase (PostgREST)** envuelta en funciones RPC de Postgres, y renderiza mapas interactivos, overlays de radar en tiempo real, imágenes de satélite, predicciones Pysteps y pronósticos por estación. Desplegado en Vercel.

---

## Arquitectura

```
┌─────────────────────────────────────────────────────────────────────┐
│                         PIPELINE DE DATOS Y ML                      │
│                                                                     │
│  Open-Meteo API          Parquet local          XGBoost (GPU)       │
│  (histórico 2015-2024) → parts_parquet/*.parquet → modelo_completo  │
│  (actuals semanales)                             .pkl  (~17 MB)     │
│         │                                            │              │
│         └── validación MAE/RMSE semanal ─────────────┘              │
│                             ↓                                       │
│                    prediccion_ml.py                                 │
│               (sliding window, ~200 estaciones)                     │
│                             │                                       │
│              ┌──────────────┼──────────────────┐                    │
│              ↓              ↓                  ↓                    │
│       ciclo_prediccion  paso_horario   prediccion_punto             │
│              └──────────────┴──────────────────┘                    │
│                       SUPABASE POSTGRES + PostGIS                   │
│              ┌──────────────────────────────────┐                   │
│              │  estacion_punto  │  radar (raster)│                  │
│              │  pysteps frames  │  satélite frames│                 │
│              └──────────────────────────────────┘                   │
│                             │                                       │
│              API REST (PostgREST) + 13 funciones RPC                │
│                             │                                       │
└─────────────────────────────┼───────────────────────────────────────┘
                              ↓
                  HTTPS / JSON (REST endpoints)
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                       FRONTEND (Vercel)                             │
│                                                                     │
│  React 19 + Vite 8 + Leaflet + @supabase/supabase-js               │
│                                                                     │
│  Mapa de estaciones  │  Radar 10 min  │  Pysteps horario/10 min    │
│  Panel pronóstico 24h│  Satélite COM2602│  Acumulación 24h         │
│  Modal de aviso (memoria Supabase)                                  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Stack tecnológico

| Capa | Tecnología | Versión |
|---|---|---|
| **Frontend** | React | 19.2 |
| | Vite | 8.0 (beta) |
| | Leaflet + react-leaflet | 1.9 / 5.0 |
| | @supabase/supabase-js | 2.96 |
| | react-icons | 5.5 |
| | lunarphase-js | 2.0 |
| **API** | PostgREST (Supabase) | autogenerada |
| | Funciones RPC de Postgres | 13 |
| **Base de datos** | Supabase Postgres | 15 |
| | PostGIS (raster) | — |
| **ML / Python** | XGBoost (GPU / CUDA) | 3.2 |
| | scikit-learn MultiOutputRegressor | 1.8 |
| | pandas + pyarrow (Parquet) | 3.0 / 23 |
| | openmeteo-requests | 1.7 |
| | joblib (serialización) | 1.5 |
| **Deploy** | Vercel | — |

---

## API REST

El backend del frontend es una **API REST estándar generada automáticamente por PostgREST**, el servicio que Supabase expone sobre cada base de datos Postgres. No hay servidor intermedio (Express, FastAPI, etc.) — cualquier función Postgres es accesible vía HTTP/JSON.

### Cómo funciona la capa REST

Cada función RPC del esquema `public` se publica automáticamente como un endpoint REST:

```
POST https://<proyecto>.supabase.co/rest/v1/rpc/obtener_predicciones_geojson
Headers:
  apikey: <SUPABASE_ANON_KEY>
  Content-Type: application/json
Body:
  { "p_offset": 24 }
```

El cliente JavaScript `@supabase/supabase-js` es un **wrapper fino sobre esta API REST**: una llamada como `supabase.rpc('obtener_radar_frame', { p_id_raster: 42 })` se traduce a la petición HTTP anterior. Esto significa que el proyecto utiliza una arquitectura REST real (auditable desde DevTools → Network), pero con la ergonomía de un SDK.

### Por qué RPC envuelve cada endpoint

Las RPC encapsulan en el servidor:

- **Unpacking de raster** (PostGIS `ST_DumpValues` → bytes plano de píxeles)
- **Filtrado temporal** (último ciclo de predicción, frames más recientes)
- **Cálculos geoespaciales** (bounds, GeoJSON)

Esto reduce drásticamente el payload enviado al navegador y oculta la estructura interna de las tablas. El frontend solo conoce los 13 endpoints públicos, no el esquema de la base de datos.

---

## Frontend

### Visualizaciones

El dashboard es una **SPA monolítica** (`src/App.jsx`) que expone las siguientes capas y vistas, todas consultando la API REST de Supabase vía RPC:

| Vista | Descripción |
|---|---|
| **Modal de aviso** | Aviso informativo en la primera visita sobre las limitaciones del plan gratuito de Supabase. Se descarta con un clic y se recuerda con `localStorage` (`disclaimer_dismissed_v1`) |
| **Mapa de estaciones** | Markers circulares coloreados por temperatura actual para ~200 estaciones de España |
| **Búsqueda de estación** | Búsqueda en tiempo real con navegación por teclado (flechas ↑↓) |
| **Panel de estación** | Pronóstico 24h por hora: temperatura, humedad, viento, presión |
| **Radar (10 min)** | Overlay raster con 30 frames, control de reproducción y selector de fecha |
| **Pysteps horario** | Predicción de precipitación (extrapolación nowcasting) con 30 frames |
| **Pysteps 10 min** | Predicción de precipitación a 10 minutos (36 frames, mayor resolución temporal) |
| **Acumulación 24h** | Lluvia acumulada en 24 horas, 24 frames |
| **Satélite COM2602** | Imagen de satélite EUMETSAT con 30 frames |
| **Fase lunar** | Fase de la luna por hora del pronóstico (lunarphase-js) |

### Renderizado de raster

Los frames de radar, Pysteps y satélite se almacenan en Supabase como datos de píxeles. El frontend los convierte en tiempo real a imágenes PNG para renderizarlas como overlays de Leaflet:

```
pixel data (Uint8Array desde la API REST de Supabase)
    ↓
colormap aplicado (10 paradas navy→magenta, 0–300 mm/h)
    ↓
canvas API: putImageData()
    ↓
canvas.toDataURL("image/png")
    ↓
L.imageOverlay(dataUrl, bounds)  →  Leaflet raster overlay
```

La carga de frames se realiza en **batches paralelos de 4** para equilibrar velocidad y presión sobre el pool de conexiones de Supabase.

### Layout: bottom-stack unificado

Todos los paneles inferiores (slider temporal + 5 reproductores de capas) viven dentro de un único contenedor `.bottom-stack` (`position: fixed`, `display: flex; flex-direction: column`). Cuando se abre el panel lateral de una estación, una sola regla CSS desplaza todo el conjunto hacia la izquierda con una transición suave de 300 ms, garantizando que el ancho de los paneles permanezca uniforme. Esta refactorización eliminó ~15 clases modificadoras (`.with-radar`, `.with-pysteps`, etc.) y ~60 líneas de reglas `.panel-abierto` específicas por panel.

### Conexión a Supabase (REST + RPC wrapper)

```js
// src/App.jsx
const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY
);

// Bajo el capó, esta llamada lanza POST /rest/v1/rpc/obtener_predicciones_geojson
const { data } = await supabase.rpc('obtener_predicciones_geojson', {
  p_offset: hora,
});
```

Clave anónima únicamente — solo lectura pública. No hay autenticación ni cuentas de usuario.

### Decisión de diseño: SPA monolítica sin router

El dashboard es una vista única donde todas las capas coexisten simultáneamente. Se descartó React Router para no añadir complejidad sin beneficio. Sin Redux ni Zustand porque el estado es local a la vista y la comunicación entre componentes es directa. Tres hooks (`useState`, `useEffect`, `useRef`) cubren todos los casos de uso.

### Despliegue (Vercel)

El frontend se despliega en Vercel con configuración en el panel de control:

| Setting | Valor |
|---|---|
| Root Directory | `frontend` |
| Framework | Vite |
| Install Command | `npm install` |
| Build Command | `npm run build` |
| Output Directory | `dist` |
| Variables de entorno | `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` |

No se utiliza `vercel.json` — toda la configuración vive en el dashboard.

---

## Base de datos: Supabase Postgres + PostGIS

### Tablas principales

| Tabla | Contenido |
|---|---|
| `estacion_punto` | ~200 estaciones activas: nombre, coordenadas, altitud |
| `ciclo_prediccion` | Metadatos de cada ciclo de predicción (timestamp, estado) |
| `paso_horario` | 240 pasos horarios por ciclo (0–239 h) |
| `prediccion_punto` | 7 variables × 240 horas × ~200 estaciones = **~336.000 filas por ciclo** |
| `radar` | Frames raster de radar (PostGIS raster WKB) |
| `pysteps_*` | Frames de predicción Pysteps (horario y 10 min) |
| `acumulacion_*` | Frames de acumulación de lluvia |
| `com2602_*` | Frames de imagen satelital COM2602 |

### Endpoints REST (RPC)

El frontend consume 13 endpoints REST, cada uno respaldado por una función RPC de Postgres:

| Endpoint REST (`POST /rest/v1/rpc/...`) | Propósito |
|---|---|
| `obtener_limites_geojson` | Polígonos GeoJSON de regiones |
| `obtener_predicciones_geojson` | Predicciones de todas las estaciones para un offset horario |
| `obtener_prediccion_estacion` | Pronóstico 24h de una estación concreta |
| `obtener_radar_reciente` | Lista de frames de radar disponibles |
| `obtener_radar_frame` | Datos de píxeles de un frame de radar |
| `obtener_pysteps_reciente` | Lista de frames Pysteps horarios |
| `obtener_pysteps_frame` | Datos de píxeles Pysteps horario |
| `obtener_pysteps_10min_reciente` | Lista de frames Pysteps 10 min |
| `obtener_pysteps_10min_frame` | Datos de píxeles Pysteps 10 min |
| `obtener_acumulacion_reciente` | Lista de frames de acumulación |
| `obtener_acumulacion_frame` | Datos de píxeles de acumulación |
| `obtener_com2602_reciente` | Lista de frames de satélite |
| `obtener_com2602_frame` | Datos de píxeles de satélite |

### Carga de datos raster

Los rasters (radar, satélite) se cargan mediante un pipeline geoespacial:


GeoTIFF (GDAL)  →  raster2pgsql  →  WKB  →  INSERT en Supabase


Implementado en `entrenamiento/test_subir_radar.py` y `test_subir_gdal.py`.

---

## Limitaciones del plan gratuito de Supabase

El proyecto utiliza el **plan gratuito de Supabase**, que tiene memoria y ancho de banda limitados para datos ráster pesados. Cuando hay muchas peticiones simultáneas o el ráster es muy grande, Supabase puede no entregar los datos. Por este motivo se muestra el modal de aviso en la primera visita: las capas de radar, Pysteps, predicción 10-minutal, acumulación horaria y COM2602 pueden no cargarse o tardar más de lo esperado. La predicción puntual por estación, el slider temporal y el buscador funcionan siempre con normalidad porque consumen pocos bytes por petición.

---

## Pipeline de ML — Recopilación de datos

### Fuente de datos

**Open-Meteo Archive API** (gratuita, sin API key). Cubre datos históricos horarios con un delay de ~5 días.

Variables descargadas por estación:
- `temperature_2m` (°C)
- `precipitation` (mm)
- `windspeed_10m` (m/s)
- `winddirection_10m` (°)
- `windgusts_10m` (m/s)
- `relativehumidity_2m` (%)
- `surface_pressure` (hPa)

### Histórico inicial

`entrenamiento/descarga_test.py` descarga **9+ años de histórico (2015–2024)** para las ~240 ciudades del mapa. Los datos se almacenan como **un archivo Parquet por estación** en `parts_parquet/`, lo que permite:
- Lectura columnar eficiente (solo las columnas necesarias)
- Compresión automática (Snappy/Zstandard via pyarrow)
- Actualización incremental sin reescribir todo el histórico

### Loop semanal de actualización

`entrenamiento/recopilacion_semanal.py` se ejecuta cada semana y:
1. Descarga los últimos 7 días de datos reales de Open-Meteo
2. Los compara contra las predicciones almacenadas en Supabase → calcula MAE/RMSE de validación
3. Añade los nuevos datos a los Parquets existentes
4. Lanza el reentrenamiento (`ML_entrenamiento.py`) con el histórico ampliado

---

## Pipeline de ML — Entrenamiento

### Modelo

**XGBoost `MultiOutputRegressor`**: 7 regresores XGBoost independientes entrenados en paralelo, uno por variable objetivo.

### Estrategia: pares T → T+1

Para evitar data leakage, el modelo se entrena con **pares consecutivos**: dado el estado meteorológico en el instante T, predice el estado en T+1. Esto permite construir series de predicción mediante ventana deslizante (ver inferencia).

### Features de entrada (12)

| Feature | Descripción |
|---|---|
| `ciudad_id` | ID numérico de la estación (1–240) |
| `latitud`, `longitud` | Coordenadas geográficas |
| `altitud_m` | Altitud en metros |
| `hora_target` | Hora del día del instante T+1 (0–23) |
| `dia_anio_target` | Día del año del instante T+1 (1–365) |
| `mes_target` | Mes del instante T+1 (1–12) |
| `temp_actual` | Temperatura en T (°C) |
| `humedad_actual` | Humedad relativa en T (%) |
| `presion_actual` | Presión en T (hPa) |
| `viento_vel_actual` | Velocidad del viento en T (m/s) |
| `viento_dir_actual` | Dirección del viento en T (°) |

### Variables objetivo (7)

| Target | Descripción |
|---|---|
| `temperatura_2m` | Temperatura a 2 m (°C) |
| `precipitacion_mm` | Precipitación (mm) |
| `viento_vel_ms` | Velocidad media del viento (m/s) |
| `viento_racha_ms` | Racha máxima (m/s) |
| `humedad_rel_pct` | Humedad relativa (%) |
| `presion_hpa` | Presión superficial (hPa) |
| `reflectividad_dbz` | Reflectividad de radar (dBZ), derivada de la precipitación mediante la ecuación de Marshall-Palmer: `Z = 200 · R^1.6` |

### Hiperparámetros

```python
XGB_PARAMS = {
    "n_estimators":     500,
    "max_depth":        6,
    "learning_rate":    0.05,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 3,
    "device":           "cuda",   # entrenamiento en GPU
    "tree_method":      "hist",
    "max_bin":          128,
    "sampling_method":  "gradient_based",
}
```

### Métricas (conjunto de test, 20% temporal)

| Variable | MAE | RMSE |
|---|---|---|
| `temperatura_2m` | ~0.49 °C | — |
| `precipitacion_mm` | ~0.12 mm | — |
| `viento_vel_ms` | — | — |
| `humedad_rel_pct` | — | — |
| `presion_hpa` | — | — |

> Las métricas completas se generan en `entrenamiento/models/metricas.json` al entrenar.

### Serialización

El modelo se guarda con Joblib como `entrenamiento/models/modelo_completo.pkl` (~17 MB), incluyendo el objeto `MultiOutputRegressor`, la lista de features y la lista de targets.

---

## Pipeline de ML — Inferencia y ventana deslizante

`entrenamiento/prediccion_ml.py` genera el ciclo de predicción completo:

### Ventana deslizante (sliding window)

```
Hora 0:  estado real de Open-Meteo  →  modelo  →  pred[1]
Hora 1:  pred[1]                    →  modelo  →  pred[2]
Hora 2:  pred[2]                    →  modelo  →  pred[3]
  ...
Hora 239: pred[238]                 →  modelo  →  pred[239]
```

Esto permite extender el pronóstico a **240 horas (10 días)** con un único modelo T→T+1, sin necesidad de entrenar modelos separados por horizonte.

### Escala de la predicción

```
7 variables × 240 horas × ~200 estaciones ≈ 336.000 filas por ciclo
```

Insertadas en tres tablas de Supabase:
- `ciclo_prediccion` — metadatos del ciclo (timestamp de ejecución)
- `paso_horario` — los 240 pasos horarios
- `prediccion_punto` — el producto cartesiano variables × horas × estaciones

El **versionado por ciclo** permite al frontend mostrar siempre la predicción más reciente y comparar pronósticos de distintos momentos en el tiempo.

---

## Cómo ejecutar el proyecto

### Requisitos

- Python ≥ 3.11, CUDA ≥ 12 (opcional, para entrenamiento en GPU)
- Node.js ≥ 20
- Cuenta de Supabase con las tablas y RPCs creadas

### 1. Instalar dependencias Python

```bash
cd entrenamiento
pip install -r requirements.txt
```

### 2. Descargar histórico

```bash
python descarga_test.py
```

### 3. Entrenar el modelo

```bash
python ML_entrenamiento.py
# Salida: models/modelo_completo.pkl, models/metricas.json
```

### 4. Generar predicciones e insertar en Supabase

```bash
# Crear .env con SUPABASE_URL y SUPABASE_KEY
python prediccion_ml.py
```

### 5. Levantar el frontend

```bash
cd ../frontend
cp .env.example .env   # Añadir VITE_SUPABASE_URL y VITE_SUPABASE_ANON_KEY
npm install
npm run dev
```

### 6. Loop semanal (opcional)

```bash
cd entrenamiento
python recopilacion_semanal.py   # Valida, actualiza datos y reentrena
```

---

## Decisiones técnicas

### Por qué API REST autogenerada (PostgREST) en lugar de un backend propio

Sin servidor adicional que mantener: PostgREST traduce el esquema Postgres a endpoints REST estándar automáticamente. Cada RPC es un endpoint HTTP auditable desde DevTools. Se evita el coste (tiempo + dinero) de hostear un Express/FastAPI intermedio que solo reexpondría las mismas operaciones. Si en el futuro se necesita lógica fuera de Postgres (rate limiting, auth compleja, integraciones con terceros), se puede añadir como Supabase Edge Function sin romper la arquitectura actual.

### Por qué RPC en lugar de queries PostgREST directas a tablas

Las RPC permiten encapsular en el servidor el unpacking del raster, el filtrado por ciclo más reciente y los cálculos geoespaciales — reduciendo el payload por petición de megabytes a kilobytes. Además ocultan el esquema interno: el frontend conoce 13 endpoints estables, no la estructura de las 8+ tablas subyacentes.

### Por qué XGBoost y no una red neuronal (LSTM/Transformer)

Los datos son **tabulares estructurados** (temperatura, presión, humedad…), dominio donde XGBoost supera sistemáticamente a las redes en problemas de horizonte corto. Además: latencia de inferencia baja (sin GPU en producción), modelo interpretable (feature importances), menor riesgo de sobreajuste con pocos datos nuevos, y serialización simple a `.pkl`.

### Por qué T→T+1 con sliding window en lugar de modelo multi-horizonte

Un modelo que predice directamente H=240 necesita H veces más datos etiquetados y tiende a sobreajustar horizontes lejanos. El enfoque T→T+1 reutiliza todos los pares del histórico, produce un modelo más pequeño y los errores de propagación son aceptables para meteorología a 10 días.

### Por qué Parquet local + Supabase (no solo Supabase)

El entrenamiento requiere millones de filas en lectura aleatoria — Parquet columnar es 10–100× más rápido que una query a Postgres para este patrón. Supabase sirve para el servicio online (lectura por estación, por hora, por ciclo) donde Postgres brilla. Separar estos accesos por capa evita sobrecargar la base de datos durante el entrenamiento.

### Por qué Leaflet y no Mapbox o Google Maps

Leaflet es open source, sin API key, y soporta overlays raster arbitrarios (necesarios para radar y satélite). Mapbox ofrece mejor calidad visual pero requiere clave de pago y no aporta capacidades adicionales para este caso de uso.

---

## Skills que demuestra este proyecto

| Área | Evidencia |
|---|---|
| **Ingeniería de datos** | ETL con Open-Meteo API, almacenamiento Parquet columnar, pipeline de validación continua MAE/RMSE, carga de rasters GeoTIFF con GDAL/raster2pgsql |
| **ML aplicado** | Regresión multi-output, entrenamiento en GPU (CUDA), sliding window para pronóstico extendido, split temporal (sin data leakage), métricas por variable |
| **Geoespacial** | PostGIS + raster WKB, GeoJSON para límites administrativos, overlays raster en Leaflet con colormap personalizado, integración GDAL |
| **API REST** | Diseño de endpoints REST con PostgREST + RPC, payload optimization (unpacking server-side), versionado por ciclo |
| **Full-stack** | React 19, Supabase Postgres, canvas API para renderizado de píxeles, visualización de series temporales, despliegue continuo en Vercel |
| **UX / CSS** | Layout responsive con un único contenedor `.bottom-stack`, modal de onboarding con persistencia en `localStorage`, transiciones coordinadas al abrir el panel lateral |

---

## Estructura del repositorio

```
prediccion_meteo/
├── entrenamiento/
│   ├── ML_entrenamiento.py          # Entrenamiento XGBoost GPU
│   ├── ml_entrenamiento_individual.py # Variante por estación
│   ├── prediccion_ml.py             # Inferencia con sliding window
│   ├── recopilacion_semanal.py      # Loop semanal de actualización
│   ├── descarga_test.py             # Descarga histórico Open-Meteo
│   ├── test_subir_radar.py          # Test de carga de radar PostGIS
│   ├── test_subir_gdal.py           # Test de carga GeoTIFF
│   ├── test_subir_supabase.py       # Test de carga Supabase
│   ├── requirements.txt
│   ├── models/
│   │   ├── modelo_completo.pkl      # Modelo serializado (~17 MB)
│   │   ├── ciudad_id_map.json       # Mapa nombre → ID
│   │   └── metricas.json            # MAE/RMSE por variable
│   └── parts_parquet/               # Datos históricos por estación
│       └── part_<ciudad>.parquet
└── frontend/
    ├── src/
    │   ├── App.jsx                  # SPA monolítica (~1300 líneas)
    │   ├── App.css                  # Estilos (~38 KB)
    │   └── main.jsx
    ├── package.json
    └── vite.config.js
```
