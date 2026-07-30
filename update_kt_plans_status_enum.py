from db import execute_write

if __name__ == "__main__":
    print("Updating kt_plans status enum...")
    query = "ALTER TABLE kt_plans MODIFY COLUMN status ENUM('draft', 'approved', 'closed') DEFAULT 'draft';"
    try:
        execute_write(query)
        print("Successfully updated kt_plans status enum.")
    except Exception as e:
        print(f"Error: {e}")
