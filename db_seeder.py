import bcrypt
from db import execute_query, execute_write

def seed_users():
    print("Database Seeder: Running seeder...")
    
    # 1. Ensure users table exists
    create_table_query = """
    CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        full_name VARCHAR(255) NOT NULL,
        email VARCHAR(255) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        role VARCHAR(255) NOT NULL,
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    );
    """
    try:
        execute_write(create_table_query)
    except Exception as e:
        print(f"Database Seeder: Error ensuring users table exists: {e}")
        return

    # 2. Define users to seed
    users_to_seed = [
        {
            "full_name": "Pabitra Sarkar",
            "email": "pabitra@gmail.com",
            "role": "Delivery / Engagement Manager",
            "password": "123456"
        },
        {
            "full_name": "Dipak Saha",
            "email": "dipak@gmail.com",
            "role": "Outgoing SME (Knowledge Giver)",
            "password": "123456"
        },
        {
            "full_name": "Ayan Manna",
            "email": "ayan@gmail.com",
            "role": "Incoming Team Member (Knowledge Receiver)",
            "password": "123456"
        },
        {
            "full_name": "Sanjib Sau",
            "email": "sanjib@gmail.com",
            "role": "PwC Leadership",
            "password": "123456"
        }
    ]

    for user in users_to_seed:
        try:
            # Check if user already exists
            check_query = "SELECT id FROM users WHERE email = %s"
            existing = execute_query(check_query, (user["email"],))
            
            if not existing:
                # Hash the password using BCrypt
                salt = bcrypt.gensalt()
                password_hash = bcrypt.hashpw(user["password"].encode('utf-8'), salt).decode('utf-8')
                
                # Insert user into the database
                insert_query = """
                INSERT INTO users (full_name, email, password_hash, role, is_active)
                VALUES (%s, %s, %s, %s, %s)
                """
                params = (user["full_name"], user["email"], password_hash, user["role"], True)
                execute_write(insert_query, params)
                print(f"Database Seeder: Seeded user {user['full_name']} ({user['email']}) successfully.")
            else:
                print(f"Database Seeder: User with email {user['email']} already exists. Skipping duplicate seeding.")
        except Exception as e:
            print(f"Database Seeder: Error seeding user {user['email']}: {e}")

    print("Database Seeder: Finished seeding.")
