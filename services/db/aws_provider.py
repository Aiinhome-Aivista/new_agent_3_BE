from mysql.connector import pooling
import mysql.connector
from config import Config
from .base import BaseDBProvider

class AWSDBProvider(BaseDBProvider):
    def init_pool(self):
        try:
            pool = pooling.MySQLConnectionPool(
                pool_name="aws_kt_manager_pool",
                pool_size=5,
                pool_reset_session=True,
                host=Config.AWS_RDS_HOST,
                port=Config.AWS_RDS_PORT,
                user=Config.AWS_RDS_USER,
                password=Config.AWS_RDS_PASSWORD,
                database=Config.AWS_RDS_DATABASE
            )
            return pool
        except mysql.connector.Error as err:
            print(f"Error creating AWS connection pool: {err}")
            return None

    def get_direct_connection(self):
        return mysql.connector.connect(
            host=Config.AWS_RDS_HOST,
            port=Config.AWS_RDS_PORT,
            user=Config.AWS_RDS_USER,
            password=Config.AWS_RDS_PASSWORD,
            database=Config.AWS_RDS_DATABASE
        )
