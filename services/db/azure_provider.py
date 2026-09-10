from mysql.connector import pooling
import mysql.connector
from config import Config
from .base import BaseDBProvider

class AzureDBProvider(BaseDBProvider):
    def init_pool(self):
        port = int(Config.AZURE_DB_PORT) if Config.AZURE_DB_PORT else 3306
        try:
            pool = pooling.MySQLConnectionPool(
                pool_name="azure_kt_manager_pool",
                pool_size=5,
                pool_reset_session=True,
                host=Config.AZURE_DB_HOST,
                port=port,
                user=Config.AZURE_DB_USER,
                password=Config.AZURE_DB_PASSWORD,
                database=Config.AZURE_DB_DATABASE
            )
            return pool
        except mysql.connector.Error as err:
            print(f"Error creating Azure connection pool: {err}")
            return None

    def get_direct_connection(self):
        port = int(Config.AZURE_DB_PORT) if Config.AZURE_DB_PORT else 3306
        return mysql.connector.connect(
            host=Config.AZURE_DB_HOST,
            port=port,
            user=Config.AZURE_DB_USER,
            password=Config.AZURE_DB_PASSWORD,
            database=Config.AZURE_DB_DATABASE
        )
