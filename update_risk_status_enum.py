from db import execute_write

def update_schema():
    try:
        # Alter the ENUM in the risks table to support the new states
        query = "ALTER TABLE risks MODIFY COLUMN status ENUM('open','in_progress','escalated','waiting_for_approval','solved','resolved') DEFAULT 'open';"
        execute_write(query)
        print("Schema updated successfully: risks status enum expanded.")
    except Exception as e:
        print(f"Error updating schema: {e}")

if __name__ == "__main__":
    update_schema()
