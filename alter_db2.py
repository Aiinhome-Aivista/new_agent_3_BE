from db import get_connection

def run_migrations():
    conn = get_connection()
    if conn:
        cursor = conn.cursor()
        try:
            # 1. Create kt_projects table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS kt_projects (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                config JSON NOT NULL,
                created_by INT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (created_by) REFERENCES stakeholders(id) ON DELETE SET NULL
            )
            """)
            print("Successfully created kt_projects table.")

            # 2. Add project_id to kt_plans
            cursor.execute("SHOW COLUMNS FROM kt_plans LIKE 'project_id'")
            result = cursor.fetchone()
            if not result:
                cursor.execute("ALTER TABLE kt_plans ADD COLUMN project_id INT")
                cursor.execute("ALTER TABLE kt_plans ADD FOREIGN KEY (project_id) REFERENCES kt_projects(id) ON DELETE CASCADE")
                print("Successfully added project_id column and FK to kt_plans table.")
            else:
                print("Column project_id already exists in kt_plans.")
                
            conn.commit()
        except Exception as e:
            print(f"Error modifying database: {e}")
            conn.rollback()
        finally:
            cursor.close()
            conn.close()

if __name__ == '__main__':
    run_migrations()
