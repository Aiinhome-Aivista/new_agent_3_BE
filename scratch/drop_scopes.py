import sys
import os
sys.path.append(os.getcwd())
from db import execute_write

try:
    execute_write("ALTER TABLE kt_projects DROP COLUMN scopes")
    print("Column 'scopes' successfully removed from 'kt_projects' table.")
except Exception as e:
    print(f"Error or column already removed: {e}")
