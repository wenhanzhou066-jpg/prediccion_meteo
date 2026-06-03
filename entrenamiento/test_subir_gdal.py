"""
Sube TIFFs de radar a local pgAdmin usando psycopg2 + ST_FromGDALRaster.
Lee el TIFF como bytes y deja que PostGIS local lo convierta (GDAL local tiene GTiff).

Uso:
    python test_subir_gdal.py <ruta_al_tiff>
    python test_subir_gdal.py          (usa el primer TIFF en ejercicios/radares_tiff)

Requiere: psycopg2
"""

import sys
from pathlib import Path
from datetime import datetime

import psycopg2

# ─── Conexión local (pgAdmin) ─────────────────────────────────────────────────
DB_HOST     = "localhost"
DB_PORT     = 5432
DB_NAME     = "postgres"
DB_USER     = "postgres"
DB_PASSWORD = "postgres"   # tu contraseña local


def fecha_desde_nombre(nombre: str) -> str:
    """down_radw202602200730_4326.tif → '2026-02-20T07:30:00+00'"""
    ts = nombre.replace("down_radw", "").replace("_4326.tif", "")
    return datetime.strptime(ts, "%Y%m%d%H%M").strftime("%Y-%m-%dT%H:%M:%S+00")


def subir_tiff(tif_path: Path, fecha: str) -> None:
    data = tif_path.read_bytes()

    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )
    try:
        with conn:
            with conn.cursor() as cur:
                sql = """
                    INSERT INTO radar (fecha, archivo)
                    VALUES (%s, ST_FromGDALRaster(%s))
                    RETURNING id_raster, fecha
                """
                cur.execute(sql, (fecha, psycopg2.Binary(data)))
                row = cur.fetchone()
                print(f"  Insertado: id_raster={row[0]}, fecha={row[1]}")
    finally:
        conn.close()


def main():
    carpeta = Path(__file__).parent.parent / "GUK" / "SMH" / "radares_tiff"
    tiffs   = sorted(carpeta.glob("down_radw*_4326.tif"))

    if not tiffs:
        print("No se encontraron TIFFs en ejercicios/radares_tiff/")
        sys.exit(1)

    # Ordenar por fecha en el nombre y tomar los 4 más recientes
    def clave_fecha(p: Path):
        try:
            return fecha_desde_nombre(p.name)
        except Exception:
            return ""

    recientes = sorted(tiffs, key=clave_fecha)[-4:]
    print(f"Subiendo los {len(recientes)} TIFFs más recientes → {DB_HOST}\n")

    for tif_path in recientes:
        try:
            fecha = fecha_desde_nombre(tif_path.name)
        except Exception:
            from datetime import timezone
            fecha = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00")

        print(f"Archivo: {tif_path.name}  ({tif_path.stat().st_size / 1024:.1f} KB)  fecha={fecha}")
        subir_tiff(tif_path, fecha)

    print("\nOK — todos insertados")


if __name__ == "__main__":
    main()
