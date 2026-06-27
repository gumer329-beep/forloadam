from datetime import datetime
from scripts.config import OUTPUT_PATH


def export_csv(df, df_name="resultado"):
    """Repository for exporting data to CSV files"""
    now = datetime.now()
    ts_file = now.strftime("%Y%m%d_%H%M%S")
    output_file = f"{OUTPUT_PATH}/{df_name}_{ts_file}.csv"
    df.to_csv(output_file, index=False, encoding="utf-8-sig")
    print(f"\n📄 Archivo: {output_file}")
