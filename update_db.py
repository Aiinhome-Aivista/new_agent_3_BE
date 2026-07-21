from db import execute_write

def update_schema():
    try:
        query = "ALTER TABLE knowledge_documents ADD COLUMN kt_day VARCHAR(50);"
        execute_write(query)
        print("Schema updated successfully: kt_day column added.")
    except Exception as e:
        print(f"Error updating schema: {e}")

if __name__ == "__main__":
    update_schema()
