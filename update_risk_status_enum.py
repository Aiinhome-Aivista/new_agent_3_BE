from db import execute_write

def update_schema():
    try:
        # First, just add deferred to the end of the ENUM without removing old ones
        # This prevents Data Truncation errors if old rows have waiting_for_approval
        query1 = "ALTER TABLE risks MODIFY COLUMN status ENUM('open','in_progress','escalated','deferred','solved','resolved','waiting_for_approval') DEFAULT 'open';"
        execute_write(query1)
        print("Schema updated successfully: added deferred.")
        
        # Now update existing rows
        query2 = "UPDATE risks SET status = 'deferred' WHERE status = 'waiting_for_approval';"
        execute_write(query2)
        print("Rows migrated to deferred.")
        
        # Finally, restrict the ENUM to only the desired ones
        query3 = "ALTER TABLE risks MODIFY COLUMN status ENUM('open','in_progress','escalated','deferred','solved','resolved') DEFAULT 'open';"
        execute_write(query3)
        print("Schema finalized.")
    except Exception as e:
        print(f"Error updating schema: {e}")

if __name__ == "__main__":
    update_schema()
