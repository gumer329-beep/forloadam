# Main Scrip for limpieza Amex


from scripts.services.cleaning_services import cleaning_service
from scripts.repositories.export_repository import export_csv

if __name__ == "__main__":
    df = cleaning_service()
    export_csv(df, df_name="AMEX")
