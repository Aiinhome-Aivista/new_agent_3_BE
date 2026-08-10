from db import get_connection

def update_stakeholders_table():
    conn = get_connection()
    if conn:
        cursor = conn.cursor()
        try:
            # Add project_id
            cursor.execute("SHOW COLUMNS FROM stakeholders LIKE 'project_id'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE stakeholders ADD COLUMN project_id INT NULL")
                cursor.execute("ALTER TABLE stakeholders ADD FOREIGN KEY (project_id) REFERENCES kt_projects(id) ON DELETE SET NULL")
                print("Added project_id to stakeholders.")
            
            # Add track_name
            cursor.execute("SHOW COLUMNS FROM stakeholders LIKE 'track_name'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE stakeholders ADD COLUMN track_name VARCHAR(255) NULL")
                print("Added track_name to stakeholders.")
                
            conn.commit()
            print("Successfully updated stakeholders table.")
        except Exception as e:
            print(f"Error modifying database: {e}")
            conn.rollback()
        finally:
            cursor.close()
            conn.close()

if __name__ == '__main__':
    update_stakeholders_table()
