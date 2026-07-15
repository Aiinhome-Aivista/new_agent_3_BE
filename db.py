import mysql.connector
from mysql.connector import pooling
from config import Config

# Create a connection pool
try:
    db_pool = pooling.MySQLConnectionPool(
        pool_name="kt_manager_pool",
        pool_size=5,
        pool_reset_session=True,
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=Config.DB_NAME
    )
except mysql.connector.Error as err:
    print(f"Error creating connection pool: {err}")
    db_pool = None

def get_connection():
    if not db_pool:
        raise Exception("Database connection pool not initialized")
    return db_pool.get_connection()

def execute_query(query, params=None):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(query, params or ())
        result = cursor.fetchall()
        return result
    finally:
        cursor.close()
        conn.close()

def execute_write(query, params=None):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(query, params or ())
        conn.commit()
        return cursor.lastrowid
    finally:
        cursor.close()
        conn.close()
