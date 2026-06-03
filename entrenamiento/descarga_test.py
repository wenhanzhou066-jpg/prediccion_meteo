
import time
import json
import math
import logging
import re
import unicodedata
from pathlib import Path

import pandas as pd
import openmeteo_requests
import requests_cache
from retry_requests import retry

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("log_descarga.txt", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─── Configuración ────────────────────────────────────────────────────────────
FECHA_INICIO = "2015-01-01"
FECHA_FIN    = "2024-12-31"

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

# Cálculo de la pausa correcta
CALLS_POR_PETICION = math.ceil((52 * 10) / 2)          # 260
PAUSA_SEGUNDOS     = int(60 / (600 / CALLS_POR_PETICION)) + 5  # 35s
PAUSA_RATE_LIMIT   = 65

PARTS_DIR       = Path("parts_parquet")
PARTS_DIR.mkdir(exist_ok=True)
SALIDA_PARQUET  = Path("datos_historicos.parquet")
CHECKPOINT      = Path("checkpoint_descarga.json")

# ─── Estaciones: exactamente las 240 ciudades de ciudad_id_map.json ──────────
# Formato: (nombre, latitud, longitud, altitud_m)
ESTACIONES = [
    # ── Álava ──
    ("Vitoria-Gasteiz",           42.846, -2.672,  524),
    ("Llodio",                    43.143, -2.961,  122),
    ("Amurrio",                   43.052, -3.001,  219),
    ("Salvatierra",               42.850, -2.389,  605),
    ("Laguardia",                 42.559, -2.580,  635),
    # ── Albacete ──
    ("Albacete",                  38.994, -1.856,  686),
    ("Hellín",                    38.507, -1.700,  566),
    ("Almansa",                   38.869, -1.097,  712),
    ("Villarrobledo",             39.267, -2.600,  724),
    ("La Roda",                   39.207, -2.156,  716),
    # ── Alicante ──
    ("Alicante",                  38.345, -0.490,   10),
    ("Elche",                     38.267, -0.698,   86),
    ("Benidorm",                  38.534, -0.131,   15),
    ("Torrevieja",                37.979, -0.682,    5),
    ("Alcoy",                     38.698, -0.474,  562),
    # ── Almería ──
    ("Almería",                   36.840, -2.468,   22),
    ("Roquetas de Mar",           36.764, -2.615,   10),
    ("El Ejido",                  36.776, -2.815,  105),
    ("Níjar",                     36.967, -2.206,  356),
    ("Vera",                      37.245, -1.862,   94),
    # ── Asturias ──
    ("Oviedo",                    43.362, -5.849,  336),
    ("Gijón",                     43.535, -5.663,    5),
    ("Avilés",                    43.556, -5.924,   15),
    ("Mieres",                    43.250, -5.775,  209),
    ("Langreo",                   43.300, -5.692,  210),
    # ── Ávila ──
    ("Ávila",                     40.656, -4.700, 1131),
    ("Arenas de San Pedro",       40.211, -5.085,  510),
    ("Piedrahíta",                40.463, -5.328, 1050),
    ("Cebreros",                  40.458, -4.464,  754),
    ("Sotillo de la Adrada",      40.287, -4.600,  670),
    # ── Badajoz ──
    ("Badajoz",                   38.879, -6.970,  185),
    ("Mérida",                    38.917, -6.342,  221),
    ("Almendralejo",              38.683, -6.408,  336),
    ("Don Benito",                38.956, -5.862,  279),
    ("Zafra",                     38.417, -6.417,  508),
    # ── Barcelona ──
    ("Barcelona",                 41.383,  2.183,   12),
    ("LH ospitalet",              41.360,  2.100,   10),
    ("Badalona",                  41.450,  2.247,    3),
    ("Terrassa",                  41.563,  2.008,  277),
    ("Sabadell",                  41.543,  2.109,  190),
    # ── Vizcaya ──
    ("Bilbao",                    43.263, -2.935,   19),
    ("Barakaldo",                 43.296, -2.992,   39),
    ("Getxo",                     43.356, -3.012,   51),
    ("Basauri",                   43.237, -2.885,   71),
    ("Leioa",                     43.328, -2.985,   50),
    # ── Burgos ──
    ("Burgos",                    42.343, -3.697,  861),
    ("Aranda de Duero",           41.668, -3.689,  798),
    ("Miranda de Ebro",           42.687, -2.947,  471),
    ("Medina de Pomar",           42.932, -3.487,  607),
    ("Lerma",                     42.027, -3.753,  849),
    # ── Cáceres ──
    ("Cáceres",                   39.473, -6.372,  459),
    ("Plasencia",                 40.030, -6.088,  352),
    ("Navalmoral de la Mata",     39.893, -5.540,  285),
    ("Coria",                     39.985, -6.533,  263),
    ("Trujillo",                  39.458, -5.881,  564),
    # ── Cádiz ──
    ("Cádiz",                     36.527, -6.292,    7),
    ("Jerez de la Frontera",      36.686, -6.136,   27),
    ("Algeciras",                 36.128, -5.451,   20),
    ("Sanlúcar de Barrameda",     36.778, -6.351,   15),
    ("La Línea de la Concepción", 36.168, -5.349,    2),
    # ── Cantabria ──
    ("Santander",                 43.462, -3.810,   52),
    ("Torrelavega",               43.349, -4.048,   25),
    ("Castro-Urdiales",           43.384, -3.222,   22),
    ("Reinosa",                   42.998, -4.138,  850),
    ("Laredo",                    43.412, -3.413,    5),
    # ── Castellón ──
    ("Castellón",                 39.987, -0.051,   28),
    ("Vila-real",                 39.938, -0.100,   42),
    ("Benicàssim",                40.054,  0.064,   15),
    ("Vinaròs",                   40.470,  0.475,    5),
    ("Onda",                      39.962, -0.261,  194),
    # ── Ciudad Real ──
    ("Ciudad Real",               38.986, -3.919,  635),
    ("Puertollano",               38.687, -4.107,  708),
    ("Tomelloso",                 39.150, -3.025,  662),
    ("Manzanares",                38.997, -3.370,  643),
    ("Valdepeñas",                38.762, -3.384,  720),
    # ── A Coruña ──
    ("A Coruña",                  43.362, -8.411,   21),
    ("Santiago de Compostela",    42.878, -8.544,  264),
    ("Ferrol",                    43.484, -8.236,   13),
    ("Lugo (A Coruña)",           43.100, -8.200,   50),
    ("Carballo",                  43.213, -8.691,  100),
    # ── Córdoba ──
    ("Córdoba",                   37.888, -4.779,  110),
    ("Lucena",                    37.409, -4.485,  487),
    ("Pozoblanco",                38.379, -4.848,  649),
    ("Cabra",                     37.473, -4.442,  452),
    ("Montilla",                  37.586, -4.638,  400),
    # ── Cuenca ──
    ("Cuenca",                    40.070, -2.137, 1000),
    ("Tarancón",                  40.012, -3.007,  806),
    ("Motilla del Palancar",      39.566, -1.892,  896),
    ("San Clemente",              39.403, -2.428,  709),
    ("Minglanilla",               39.537, -1.600,  780),
    # ── Girona ──
    ("Girona",                    41.979,  2.821,   70),
    ("Figueres",                  42.266,  2.965,   39),
    ("Blanes",                    41.674,  2.790,   13),
    ("Olot",                      42.181,  2.490,  443),
    ("Ripoll",                    42.200,  2.190,  682),
    # ── Granada ──
    ("Granada",                   37.177, -3.600,  738),
    ("Motril",                    36.745, -3.518,   47),
    ("Guadix",                    37.300, -3.137,  913),
    ("Loja",                      37.169, -4.151,  486),
    ("Baza",                      37.495, -2.766,  874),
    # ── Guadalajara ──
    ("Guadalajara",               40.632, -3.167,  683),
    ("Azuqueca de Henares",       40.565, -3.264,  600),
    ("Molina de Aragón",          40.843, -1.884, 1063),
    ("Sigüenza",                  41.069, -2.639, 1005),
    ("Brihuega",                  40.763, -2.870,  898),
    # ── Guipúzcoa ──
    ("San Sebastián",             43.318, -1.981,   12),
    ("Irun",                      43.338, -1.789,   10),
    ("Eibar",                     43.184, -2.472,  121),
    ("Errenteria",                43.312, -1.902,   10),
    ("Zarautz",                   43.284, -2.169,    5),
    # ── Huelva ──
    ("Huelva",                    37.261, -6.949,   19),
    ("Moguer",                    37.275, -6.838,   87),
    ("Ayamonte",                  37.213, -7.401,   64),
    ("Lepe",                      37.255, -7.204,   35),
    ("Aracena",                   37.893, -6.563,  732),
    # ── Huesca ──
    ("Huesca",                    42.136, -0.409,  488),
    ("Barbastro",                 42.035,  0.127,  341),
    ("Monzón",                    41.911,  0.186,  279),
    ("Jaca",                      42.567, -0.551,  820),
    ("Sariñena",                  41.793, -0.162,  281),
    # ── Baleares ──
    ("Palma",                     39.569,  2.650,   14),
    ("Calvià",                    39.566,  2.506,  145),
    ("Manacor",                   39.570,  3.209,   80),
    ("Maó",                       39.886,  4.265,   57),
    ("Eivissa",                   38.909,  1.432,   10),
    # ── Jaén ──
    ("Jaén",                      37.779, -3.787,  574),
    ("Linares",                   38.095, -3.636,  419),
    ("Úbeda",                     38.013, -3.370,  757),
    ("Andújar",                   38.039, -4.051,  212),
    ("Baeza",                     37.994, -3.471,  790),
    # ── La Rioja ──
    ("Logroño",                   42.466, -2.445,  384),
    ("Calahorra",                 42.305, -1.965,  358),
    ("Haro",                      42.576, -2.850,  479),
    ("Arnedo",                    42.225, -2.099,  480),
    ("Nájera",                    42.418, -2.733,  485),
    # ── León ──
    ("León",                      42.598, -5.571,  838),
    ("Ponferrada",                42.549, -6.593,  541),
    ("Astorga",                   42.455, -6.056,  869),
    ("Sahagún",                   42.371, -5.033,  816),
    ("La Bañeza",                 42.299, -5.896,  770),
    # ── Lleida ──
    ("Lleida",                    41.618,  0.620,  155),
    ("Balaguer",                  41.791,  0.808,  233),
    ("Cervera",                   41.671,  1.272,  548),
    ("Tremp",                     42.167,  0.895,  468),
    ("Solsona",                   41.993,  1.519,  664),
    # ── Lugo ──
    ("Lugo",                      43.012, -7.556,  465),
    ("Monforte de Lemos",         42.521, -7.512,  298),
    ("Viveiro",                   43.661, -7.593,    6),
    ("Vilalba",                   43.299, -7.681,  490),
    ("Sarria",                    42.779, -7.415,  453),
    # ── Madrid ──
    ("Madrid",                    40.416, -3.703,  667),
    ("Móstoles",                  40.323, -3.865,  661),
    ("Alcorcón",                  40.349, -3.825,  718),
    ("Leganés",                   40.328, -3.764,  665),
    ("Getafe",                    40.305, -3.731,  622),
    # ── Málaga ──
    ("Málaga",                    36.720, -4.420,    7),
    ("Marbella",                  36.510, -4.886,   10),
    ("Vélez-Málaga",              36.782, -4.103,   69),
    ("Estepona",                  36.427, -5.146,   21),
    ("Antequera",                 37.018, -4.559,  512),
    # ── Murcia ──
    ("Murcia",                    37.983, -1.130,   25),
    ("Cartagena",                 37.606, -0.986,   10),
    ("Lorca",                     37.677, -1.697,  353),
    ("Molina de Segura",          38.054, -1.208,  124),
    ("Yecla",                     38.614, -1.115,  606),
    # ── Navarra ──
    ("Pamplona",                  42.812, -1.645,  446),
    ("Tudela",                    42.061, -1.607,  275),
    ("Barañáin",                  42.799, -1.681,  428),
    ("Estella",                   42.671, -2.032,  421),
    ("Tafalla",                   42.527, -1.680,  426),
    # ── Ourense ──
    ("Ourense",                   42.336, -7.864,  125),
    ("Verín",                     41.940, -7.438,  375),
    ("Xinzo de Limia",            42.063, -7.727,  600),
    ("O Carballiño",              42.431, -8.076,  300),
    ("Ribadavia",                 42.289, -8.143,  100),
    # ── Palencia ──
    ("Palencia",                  42.010, -4.532,  736),
    ("Aguilar de Campoo",         42.793, -4.260,  895),
    ("Guardo",                    42.796, -4.841, 1050),
    ("Venta de Baños",            41.920, -4.489,  724),
    ("Herrera de Pisuerga",       42.590, -4.331,  830),
    # ── Pontevedra ──
    ("Vigo",                      42.231, -8.712,   31),
    ("Pontevedra",                42.433, -8.648,   20),
    ("Marín",                     42.392, -8.700,    5),
    ("Vilagarcía de Arousa",      42.596, -8.764,    5),
    ("Cangas",                    42.263, -8.784,   10),
    # ── Salamanca ──
    ("Salamanca",                 40.965, -5.664,  802),
    ("Béjar",                     40.387, -5.763,  953),
    ("Ciudad Rodrigo",            40.596, -6.533,  658),
    ("Santa Marta de Tormes",     40.934, -5.632,  800),
    ("Alba de Tormes",            40.824, -5.515,  828),
    # ── Segovia ──
    ("Segovia",                   40.948, -4.118, 1002),
    ("Cuéllar",                   41.400, -4.320,  857),
    ("Sepúlveda",                 41.297, -3.750, 1015),
    ("Riaza",                     41.279, -3.490, 1190),
    ("Cantalejo",                 41.261, -3.935,  966),
    # ── Sevilla ──
    ("Sevilla",                   37.389, -5.984,    9),
    ("Dos Hermanas",              37.284, -5.920,   42),
    ("Alcalá de Guadaíra",        37.340, -5.838,   44),
    ("Utrera",                    37.186, -5.782,   49),
    ("Écija",                     37.541, -5.082,  110),
    # ── Soria ──
    ("Soria",                     41.764, -2.465, 1063),
    ("Almazán",                   41.486, -2.533,  950),
    ("Ágreda",                    41.862, -1.925,  843),
    ("El Burgo de Osma",          41.586, -3.070,  895),
    ("Medinaceli",                41.171, -2.430, 1224),
    # ── Tarragona ──
    ("Tarragona",                 41.119,  1.244,   56),
    ("Reus",                      41.154,  1.108,  134),
    ("Tortosa",                   40.812,  0.522,   14),
    ("Amposta",                   40.714,  0.582,    8),
    ("Calafell",                  41.200,  1.568,   60),
    # ── Teruel ──
    ("Teruel",                    40.345, -1.106,  900),
    ("Alcañiz",                   41.050, -0.130,  381),
    ("Montalbán",                 40.824, -0.797,  980),
    ("Calanda",                   41.044, -0.227,  466),
    ("Utrillas",                  40.807, -0.878, 1111),
    # ── Toledo ──
    ("Toledo",                    39.857, -4.024,  529),
    ("Talavera de la Reina",      39.960, -4.833,  371),
    ("Illescas",                  40.126, -3.848,  588),
    ("Madridejos",                39.467, -3.532,  687),
    ("Quintanar de la Orden",     39.591, -2.845,  691),
    # ── Valencia ──
    ("Valencia",                  39.469, -0.376,   13),
    ("Gandia",                    38.967, -0.183,   20),
    ("Sagunto",                   39.680, -0.267,   30),
    ("Torrent",                   39.437, -0.465,   62),
    ("Alzira",                    39.151, -0.435,   24),
    # ── Valladolid ──
    ("Valladolid",                41.652, -4.728,  694),
    ("Medina del Campo",          41.310, -4.909,  721),
    ("Laguna de Duero",           41.584, -4.726,  693),
    ("Tordesillas",               41.501, -5.000,  702),
    ("Peñafiel",                  41.598, -4.113,  756),
    # ── Zamora ──
    ("Zamora",                    41.503, -5.744,  649),
    ("Benavente",                 42.002, -5.679,  744),
    ("Toro",                      41.521, -5.395,  745),
    ("Puebla de Sanabria",        42.058, -6.634,  960),
    ("Bermillo de Sayago",        41.334, -6.114,  750),
    # ── Zaragoza ──
    ("Zaragoza",                  41.656, -0.877,  208),
    ("Calatayud",                 41.353, -1.641,  534),
    ("Tarazona",                  41.905, -1.727,  480),
    ("Ejea de los Caballeros",    42.127, -1.138,  320),
    ("Caspe",                     41.234, -0.040,  152),
]

# ─── Cliente OpenMeteo ────────────────────────────────────────────────────────
def crear_cliente():
    cache = requests_cache.CachedSession(".openmeteo_cache", expire_after=86400)
    session = retry(cache, retries=3, backoff_factor=2.0)
    return openmeteo_requests.Client(session=session)


# ─── Helper: nombre seguro (con soporte para acentos) ────────────────────────
def nombre_seguro(s):
    s = s.strip()
    # Transliterar acentos: á→a, ñ→n, ç→c, etc.
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace(" ", "_")
    # mantener solo letras/dígitos/guiones/underscore
    return re.sub(r"[^0-9A-Za-z_\-]", "", s)


def cargar_checkpoint():
    if CHECKPOINT.exists():
        with open(CHECKPOINT) as f:
            data = json.load(f)
        completados = set(data.get("completados", []))
        log.info(f"Checkpoint cargado: {len(completados)} puntos completados")
        return completados
    return set()


def guardar_checkpoint(completados_set):
    with open(CHECKPOINT, "w") as f:
        json.dump({"completados": list(completados_set)}, f)


# ─── Descarga de un punto ─────────────────────────────────────────────────────
def descargar_punto(cliente, nombre, lat, lon, altitud, intento=1):
    try:
        resp = cliente.weather_api(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude":        lat,
                "longitude":       lon,
                "start_date":      FECHA_INICIO,
                "end_date":        FECHA_FIN,
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

        df = df.rename(columns={
            "temperature_2m":      "temperatura_2m",
            "precipitation":       "precipitacion_mm",
            "windspeed_10m":       "viento_vel_ms",
            "winddirection_10m":   "viento_dir_deg",
            "windgusts_10m":       "viento_racha_ms",
            "relativehumidity_2m": "humedad_rel_pct",
            "surface_pressure":    "presion_hpa",
            "rain":                "lluvia_mm",
        })

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
                log.warning(f"  Rate limit. Esperando {PAUSA_RATE_LIMIT}s y reintentando...")
                time.sleep(PAUSA_RATE_LIMIT)
                return descargar_punto(cliente, nombre, lat, lon, altitud, intento + 1)
            else:
                log.error(f"  Rate limit persistente en {nombre}. Saltando.")
                return None
        log.error(f"  Error en {nombre}: {e}")
        return None


# ─── Escritura de parte Parquet ──────────────────────────────────────────────
def guardar_parte(df, nombre):
    safe = nombre_seguro(nombre)
    part_path = PARTS_DIR / f"part_{safe}.parquet"
    df.to_parquet(part_path, index=False, compression="snappy")
    return part_path


# ─── Concatenar partes ──────────────────────────────────────────────────────
def concatenar_partes_a_parquet(final_path=SALIDA_PARQUET):
    # Solo concatenar partes que corresponden a estaciones actuales
    nombres_validos = {nombre_seguro(e[0]) for e in ESTACIONES}

    parts = sorted(PARTS_DIR.glob("part_*.parquet"))
    parts_validas = [p for p in parts if p.stem.replace("part_", "") in nombres_validos]

    if not parts_validas:
        log.warning("No hay partes válidas para concatenar.")
        return

    dfs = []
    for p in parts_validas:
        dfs.append(pd.read_parquet(p))

    df_final = pd.concat(dfs, ignore_index=True)
    df_final["hora_validez"] = pd.to_datetime(df_final["hora_validez"])
    df_final.to_parquet(final_path, index=False, compression="snappy")

    mb_pq = final_path.stat().st_size / (1024**2)
    log.info(f"Parquet final: {final_path} ({mb_pq:.1f} MB)")
    log.info(f"Total filas:   {len(df_final):,}")
    log.info(f"Puntos unicos: {df_final['nombre'].nunique()}")


# ─── Pipeline principal ───────────────────────────────────────────────────────
def main():
    log.info("=" * 65)
    log.info("DESCARGA HISTORICO OPENMETEO - 240 ciudades (ciudad_id_map)")
    log.info(f"Periodo: {FECHA_INICIO} -> {FECHA_FIN}")
    log.info(f"Puntos:  {len(ESTACIONES)}")
    log.info(f"Pausa entre peticiones: {PAUSA_SEGUNDOS}s")
    log.info(f"Tiempo estimado: ~{len(ESTACIONES) * PAUSA_SEGUNDOS / 60:.0f} minutos")
    log.info("=" * 65)

    cliente     = crear_cliente()
    completados = cargar_checkpoint()
    pendientes  = [e for e in ESTACIONES if e[0] not in completados]

    log.info(f"Completados: {len(completados)} | Pendientes: {len(pendientes)}")

    if not pendientes:
        log.info("Todo descargado.")
        if any(PARTS_DIR.glob("part_*.parquet")):
            concatenar_partes_a_parquet()
        return

    inicio = time.time()

    for idx, (nombre, lat, lon, altitud) in enumerate(pendientes):
        n_done = len(completados) + 1
        log.info(f"[{n_done:3d}/{len(ESTACIONES)}] {nombre} ({lat:.3f}, {lon:.3f})")

        df = descargar_punto(cliente, nombre, lat, lon, altitud)

        if df is not None:
            try:
                part_path = guardar_parte(df, nombre)
                completados.add(nombre)
                guardar_checkpoint(completados)

                elapsed = time.time() - inicio
                n_descargados = idx + 1
                eta_min = (elapsed / n_descargados) * (len(pendientes) - n_descargados) / 60
                log.info(f"  OK {len(df):,} filas -> {part_path.name} | ETA restante: {eta_min:.0f} min")
            except Exception as e:
                log.error(f"  Error guardando parte para {nombre}: {e}")
        else:
            log.warning(f"  FALLO en {nombre}")

        if idx < len(pendientes) - 1:
            log.info(f"  Esperando {PAUSA_SEGUNDOS}s...")
            time.sleep(PAUSA_SEGUNDOS)

    # ─── Finalizar ────────────────────────────────────────────────────────────
    elapsed_total = (time.time() - inicio) / 60
    log.info("=" * 65)
    log.info(f"Descarga completada en {elapsed_total:.1f} minutos")

    concatenar_partes_a_parquet()

    if len(completados) == len(ESTACIONES):
        try:
            CHECKPOINT.unlink(missing_ok=True)
            log.info("Checkpoint eliminado. Descarga al 100%.")
        except Exception:
            pass
    else:
        log.warning(f"Descarga parcial: {len(completados)}/{len(ESTACIONES)} puntos.")
        log.warning("Ejecuta el script de nuevo para continuar.")

if __name__ == "__main__":
    main()
