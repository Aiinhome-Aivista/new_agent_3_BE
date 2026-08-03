import db

def add_columns():
    queries = [
        "ALTER TABLE users ADD COLUMN google_token TEXT NULL;",
        "ALTER TABLE users ADD COLUMN ms_token TEXT NULL;"
    ]
    for q in queries:
        try:
            db.execute_write(q)
            print(f"Executed: {q}")
        except Exception as e:
            print(f"Failed or already exists: {e}")

if __name__ == '__main__':
    add_columns()
