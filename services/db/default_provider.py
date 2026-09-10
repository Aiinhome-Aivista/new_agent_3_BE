from mysql.connector import pooling
import mysql.connector
from config import Config
from .base import BaseDBProvider

class DefaultDBProvider(BaseDBProvider):
    def init_pool(self):
        try:
            pool = pooling.MySQLConnectionPool(
                pool_name="local_kt_manager_pool",
                pool_size=5,
                pool_reset_session=True,
                host=Config.MYSQL_HOST,
                port=Config.MYSQL_PORT,
                user=Config.MYSQL_USER,
                password=Config.MYSQL_PASSWORD,
                database=Config.MYSQL_DATABASE
            )
            return pool
        except mysql.connector.Error as err:
            print(f"Error creating local connection pool: {err}")
            return None

    def get_direct_connection(self):
        return mysql.connector.connect(
            host=Config.MYSQL_HOST,
            port=Config.MYSQL_PORT,
            user=Config.MYSQL_USER,
            password=Config.MYSQL_PASSWORD,
            database=Config.MYSQL_DATABASE
        )
