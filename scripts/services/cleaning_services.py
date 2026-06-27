# Servicio de limpieza de datos para Amex Power


from scripts.processors.process_input import Process_read_folder, pd


def cleaning_service():
    # LIMPIAR DATAFRAME y renombrar columnas
    df = Process_read_folder()
    # df = df.iloc[8:].reset_index(drop=True)
    df = df.replace(r"MXN\$", "", regex=True)
    # print(df.head(3))

    # Renombrar columnas (ejemplo, ajusta según tus nombres reales)
    df = df.rename(
        columns={
            "Fecha de envío": "FechaEnvio",
            "Número de factura del monto del resumen de los cargos": "NumeroFacturaDeCargos",
            "Número de Pago": "NumeroDePago",
            "Cargos totales": "CargosTotales",
            "Créditos": "Creditos",
            "Monto del envío": "MontoDeEnvio",
            "Total del envío": "TotalDelEnvio",
            "Monto del descuento": "MontoDelDescuento",
            "Cuotas e incentivos": "CuotasEIncentivos",
            "Monto del pago": "MontoDePago",
            "Número de establecimiento receptor del pago": "IdAfiliacion",
            "Número de establecimiento que envía": "NumeroEstablecimiento",
            "Sucursal que envía": "SucursalQueEnvia",
            "Conteo de transacciones": "ConteoDeTransacciones",
            "Fecha de pago": "FechaPago",
            "Nombre de sucursal que envía": "NombreSucursal",
            "IVA": "IVA",
            "Descripción": "Descripcion",
            "Fecha de la transacción": "FechaTransaccion",
            "Número de mensualidades": "NumeroDeMensualidades",
        }
    )
    # Supongamos df ya cargado y columnas renombradas
    date_cols = ["FechaEnvio", "FechaPago", "FechaTransaccion"]

    for c in date_cols:
        if c in df.columns:
            # Verificar tipo
            print(f"{c} dtype antes:", df[c].dtype)
            # Si tiene componente horario o zona, normalizar a fecha
            # Esto deja solo la parte de fecha y la convierte a string YYYY-MM-DD
            df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=True).dt.strftime(
                "%Y-%m-%d"
            )
            df[c] = df[c].astype(str).replace("NaT", "")  # convertir NaT a cadena vacía
            print(f"{c} ejemplo:", df[c].head(1).tolist())
    return df
