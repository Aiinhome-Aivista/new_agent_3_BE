import mysql.connector
from config import Config
import os

def setup_database():
    print(f"Connecting to MySQL server at {Config.DB_HOST}...")
    try:
        # Connect to MySQL server without specifying database to create it
        conn = mysql.connector.connect(
            host=Config.DB_HOST,
            port=Config.DB_PORT,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD
        )
        cursor = conn.cursor()
        
        print(f"Creating database {Config.DB_NAME} if it does not exist...")
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {Config.DB_NAME}")
        cursor.execute(f"USE {Config.DB_NAME}")
        
        # Run schema.sql
        schema_path = os.path.join(os.path.dirname(__file__), 'models', 'schema.sql')
        print(f"Executing schema script from {schema_path}...")
        
        with open(schema_path, 'r') as file:
            sql_script = file.read()
            
        # Execute each statement
        for statement in sql_script.split(';'):
            if statement.strip():
                cursor.execute(statement)
                
        conn.commit()
        print("Database setup complete!")
        
    except mysql.connector.Error as err:
        print(f"Error: {err}")
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

if __name__ == "__main__":
    setup_database()
