"""
Sube TIFFs de radar a Supabase usando raster2pgsql + psql.
raster2pgsql convierte el TIFF a SQL localmente (sin GDAL en Supabase).

Uso:
    python test_subir_radar.py <ruta_al_tiff>
    python test_subir_radar.py          (usa el primer TIFF en GUK/SMH/radares_tiff)

Tabla en la base de datos:
    CREATE TABLE IF NOT EXISTS radar (
        id_raster  SERIAL PRIMARY KEY,
        fecha      TIMESTAMPTZ,
        archivo    RASTER
    );
"""

import sys
import os
import subprocess
from pathlib import Path
from datetime import datetime
import glob

# ─── Conexión Supabase ───────────────────────────────────────────────────────
DB_HOST     = "localhost"
DB_PORT     = "5432"
DB_NAME     = "postgres"
DB_USER     = "postgres"
DB_PASSWORD = "postgres"   # la que pusiste al instalar PostgreSQL

# ─── Rutas a ejecutables PostGIS (ajusta la versión si es distinta) ──────────
PG_BIN = next(
    (Path(p) for p in glob.glob("C:/Program Files/PostgreSQL/*/bin") if Path(p).exists()),
    Path("C:/Program Files/PostgreSQL/17/bin")
)
RASTER2PGSQL = PG_BIN / "raster2pgsql.exe"
PSQL         = PG_BIN / "psql.exe"


def fecha_desde_nombre(nombre: str) -> str:
    """down_radw202602200730_4326.tif → '2026-02-20T07:30:00+00'"""
    ts = nombre.replace("down_radw", "").replace("_4326.tif", "")
    return datetime.strptime(ts, "%Y%m%d%H%M").strftime("%Y-%m-%dT%H:%M:%S+00")


def subir_tiff(tif_path: Path, fecha: str) -> None:
    # 1. raster2pgsql genera el SQL con el raster en binario
    #    -f archivo → nombre de columna "archivo" (coincide con la tabla)
    r2p = subprocess.run(
        [str(RASTER2PGSQL), "-s", "4326", "-f", "archivo", "-a", str(tif_path), "public.radar"],
        capture_output=True, text=True, check=True
    )

    sql = r2p.stdout

    # 2. Añadimos la columna fecha al INSERT generado
    #    raster2pgsql genera: INSERT INTO "public"."radar" ("archivo") VALUES ('\x...'::raster);
    sql = sql.replace(
        'INSERT INTO "public"."radar" ("archivo") VALUES (',
        f'INSERT INTO "public"."radar" ("archivo", "fecha") VALUES ('
    )
    sql = sql.replace(
        "::raster);",
        f"::raster, '{fecha}');"
    )

    # 3. Enviamos el SQL a Supabase via psql (password via env var para evitar problemas con @)
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD
    env["PGUSER"]     = DB_USER
    env["PGPASSFILE"] = "nul"   # evita leer pgpass con caracteres no-UTF8
    result = subprocess.run(
        [str(PSQL), "-h", DB_HOST, "-p", DB_PORT, "-d", DB_NAME,
         "-v", "ON_ERROR_STOP=1"],
        input=sql, text=True, capture_output=True, env=env
    )

    if result.returncode != 0:
        raise RuntimeError(f"psql error:\n{result.stderr}")

    print(result.stdout.strip() or "(sin salida — INSERT puede haber fallado silenciosamente)")
    if result.stderr.strip():
        print("STDERR:", result.stderr.strip())


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args:
        tif_path = Path(args[0])
    else:
        carpeta = Path(__file__).parent.parent / "GUK" / "SMH" / "radares_tiff"
        tiffs   = sorted(carpeta.glob("down_radw*_4326.tif"))
        if not tiffs:
            print("No se encontraron TIFFs en GUK/SMH/radares_tiff/")
            sys.exit(1)
        tif_path = tiffs[0]
        print(f"Usando TIFF: {tif_path.name}")

    if not tif_path.exists():
        print(f"Archivo no encontrado: {tif_path}")
        sys.exit(1)

    if not RASTER2PGSQL.exists():
        print(f"raster2pgsql no encontrado en: {RASTER2PGSQL}")
        print("Ajusta la variable PG_BIN con la ruta correcta.")
        sys.exit(1)

    try:
        fecha = fecha_desde_nombre(tif_path.name)
    except Exception:
        from datetime import timezone
        fecha = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00")

    print(f"Archivo: {tif_path.name}")
    print(f"Fecha:   {fecha}")
    print(f"Tamaño:  {tif_path.stat().st_size / 1024:.1f} KB")
    print("Subiendo via raster2pgsql → psql → Supabase...")

    subir_tiff(tif_path, fecha)
    print("OK")


if __name__ == "__main__":
    main()