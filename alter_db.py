from db import get_connection

def add_project_config_column():
    conn = get_connection()
    if conn:
        cursor = conn.cursor()
        try:
            # Check if column exists first
            cursor.execute("SHOW COLUMNS FROM kt_plans LIKE 'project_config'")
            result = cursor.fetchone()
            if not result:
                cursor.execute("ALTER TABLE kt_plans ADD COLUMN project_config JSON NULL")
                conn.commit()
                print("Successfully added project_config column to kt_plans table.")
            else:
                print("Column project_config already exists.")
        except Exception as e:
            print(f"Error modifying database: {e}")
            conn.rollback()
        finally:
            cursor.close()
            conn.close()

if __name__ == '__main__':
    add_project_config_column()
