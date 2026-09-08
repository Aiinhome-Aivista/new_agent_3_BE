import mysql.connector
from services.db.factory import DBFactory
from config import Config

# Initialize connection pool using the dynamic DB Factory
db_provider = DBFactory.get_provider()
db_pool = db_provider.init_pool()

def get_connection():
    if db_pool:
        try:
            return db_pool.get_connection()
        except mysql.connector.Error as err:
            print(f"Warning: Failed to get connection from pool ({err}). Falling back to direct connection.")
            
    # Direct connection fallback (Using the selected provider's config)
    return db_provider.get_direct_connection()

def execute_query(sql_query, params=None):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql_query, params or ())
        result = cursor.fetchall()
        return result
    finally:
        cursor.close()
        conn.close()

def execute_write(sql_query, params=None):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(sql_query, params or ())
        conn.commit()
        return cursor.lastrowid
    finally:
        cursor.close()
        conn.close()

# Aliases for architecture document compatibility
get_db_connection = get_connection
query = execute_query
execute = execute_write
