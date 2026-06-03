"""
Sube TIFFs de radar a Supabase usando raster2pgsql + psql.
raster2pgsql convierte el TIFF a WKB localmente (GDAL corre en tu máquina, no en Supabase).

Uso:
    python test_subir_supabase.py <ruta_al_tiff>
    python test_subir_supabase.py          (usa el primer TIFF en ejercicios/radares_tiff)

Tabla en Supabase:
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

# ─── Conexión Supabase (Session Pooler) ──────────────────────────────────────
PROJECT_REF = "vvfljlnswzppjascsolf"
DB_HOST     = "aws-1-eu-west-1.pooler.supabase.com"
DB_PORT     = "5432"
DB_NAME     = "postgres"
DB_USER     = f"postgres.{PROJECT_REF}"
DB_PASSWORD = "Vives2013@Vives2013"

# ─── Rutas a ejecutables PostGIS ─────────────────────────────────────────────
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
    # 1. raster2pgsql genera WKB localmente — GDAL corre aquí, no en Supabase
    r2p = subprocess.run(
        [str(RASTER2PGSQL), "-s", "4326", "-f", "archivo", "-a", str(tif_path), "public.radar"],
        capture_output=True, text=True, check=True
    )

    sql = r2p.stdout

    # 2. Añadimos la columna fecha al INSERT generado
    sql = sql.replace(
        'INSERT INTO "public"."radar" ("archivo") VALUES (',
        f'INSERT INTO "public"."radar" ("archivo", "fecha") VALUES ('
    )
    sql = sql.replace(
        "::raster);",
        f"::raster, '{fecha}');"
    )

    # 3. Enviamos el SQL a Supabase via psql
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD
    env["PGUSER"]     = DB_USER
    env["PGPASSFILE"] = "nul"
    result = subprocess.run(
        [str(PSQL), "-h", DB_HOST, "-p", DB_PORT, "-d", DB_NAME,
         "-v", "ON_ERROR_STOP=1"],
        input=sql, text=True, capture_output=True, env=env
    )

    if result.returncode != 0:
        raise RuntimeError(f"psql error:\n{result.stderr}")

    print(result.stdout.strip() or "(sin salida)")
    if result.stderr.strip():
        print("STDERR:", result.stderr.strip())


N_RECIENTES = 20   # número de TIFFs a subir


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if not RASTER2PGSQL.exists():
        print(f"raster2pgsql no encontrado en: {RASTER2PGSQL}")
        sys.exit(1)

    if args:
        tiffs = [Path(a) for a in args]
    else:
        carpeta = Path(__file__).parent.parent / "GUK" / "SMH" / "radares_tiff"
        todos   = sorted(carpeta.glob("down_radw*_4326.tif"))
        if not todos:
            print("No se encontraron TIFFs en GUK/SMH/radares_tiff/")
            sys.exit(1)

        def clave_fecha(p: Path):
            try:
                return fecha_desde_nombre(p.name)
            except Exception:
                return ""

        tiffs = sorted(todos, key=clave_fecha)[-N_RECIENTES:]
        print(f"Subiendo los {len(tiffs)} TIFFs más recientes → Supabase\n")

    print(f"Host:    {DB_HOST}")
    print(f"Usuario: {DB_USER}\n")

    ok = 0
    for tif_path in tiffs:
        if not tif_path.exists():
            print(f"No encontrado: {tif_path}")
            continue

        try:
            fecha = fecha_desde_nombre(tif_path.name)
        except Exception:
            from datetime import timezone
            fecha = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00")

        print(f"[{ok+1}/{len(tiffs)}] {tif_path.name}  ({tif_path.stat().st_size/1024:.1f} KB)  fecha={fecha}")
        try:
            subir_tiff(tif_path, fecha)
            ok += 1
        except Exception as e:
            print(f"  ERROR: {e}")

    print(f"\nOK — {ok}/{len(tiffs)} filas insertadas en public.radar")


if __name__ == "__main__":
    main()
