from pathlib import Path
import pandas as pd


def Process_read_folder():
    # Leer CSV
    carpeta = Path(".//data/")
    archivos = list(carpeta.glob("*.csv"))
    if not archivos:
        print(f"No se encontraron archivos CSV en {carpeta.absolute}")
        return
    else:
        print(f"Procesando archivos CSV: {len(archivos)} ARCHIVOS...\n")
        df = []
        for archivo in archivos:
            print(f"Procesando archivo: {archivo.name}")
            df.append(
                pd.read_csv(archivo, skiprows=9, dtype=str, keep_default_na=False)
            )
            # csvs = pd.read_csv(archivo)
            # df.extend(csvs.values())
        return pd.concat(df)
