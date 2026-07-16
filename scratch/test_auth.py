import requests
import json
import jwt

BASE_URL = "http://127.0.0.1:5000/api/auth"

def run_tests():
    print("=== STARTING AUTHENTICATION MODULE TESTS ===")
    
    # 1. Missing fields registration
    print("\nTest 1: Register with missing fields")
    payload = {
        "full_name": "Test User",
        "email": "test@example.com"
    }
    r = requests.post(f"{BASE_URL}/register", json=payload)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
    assert r.status_code == 400
    assert not r.json()["success"]
    assert "Missing" in r.json()["message"]
    print("Test 1 Passed!")

    # 2. Invalid email format
    print("\nTest 2: Register with invalid email format")
    payload = {
        "full_name": "Test User",
        "email": "invalid-email",
        "password": "Password123!",
        "confirm_password": "Password123!",
        "role": "PwC Leadership"
    }
    r = requests.post(f"{BASE_URL}/register", json=payload)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
    assert r.status_code == 400
    assert not r.json()["success"]
    assert "Invalid email" in r.json()["message"]
    print("Test 2 Passed!")

    # 3. Weak password
    print("\nTest 3: Register with weak password")
    payload = {
        "full_name": "Test User",
        "email": "test@example.com",
        "password": "weak",
        "confirm_password": "weak",
        "role": "PwC Leadership"
    }
    r = requests.post(f"{BASE_URL}/register", json=payload)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
    assert r.status_code == 400
    assert not r.json()["success"]
    assert "Password must be" in r.json()["message"]
    print("Test 3 Passed!")

    # 4. Password mismatch
    print("\nTest 4: Register with mismatched password and confirm password")
    payload = {
        "full_name": "Test User",
        "email": "test@example.com",
        "password": "Password123!",
        "confirm_password": "Password1234!",
        "role": "PwC Leadership"
    }
    r = requests.post(f"{BASE_URL}/register", json=payload)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
    assert r.status_code == 400
    assert not r.json()["success"]
    assert "Passwords do not match" in r.json()["message"]
    print("Test 4 Passed!")

    # 5. Unsupported role
    print("\nTest 5: Register with unsupported role")
    payload = {
        "full_name": "Test User",
        "email": "test@example.com",
        "password": "Password123!",
        "confirm_password": "Password123!",
        "role": "Unknown Role"
    }
    r = requests.post(f"{BASE_URL}/register", json=payload)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
    assert r.status_code == 400
    assert not r.json()["success"]
    assert "Invalid role" in r.json()["message"]
    print("Test 5 Passed!")

    # 6. Successful registration
    print("\nTest 6: Register successfully with unique email and valid data")
    email = f"user_{int(datetime.datetime.utcnow().timestamp())}@example.com"
    payload = {
        "full_name": "Delivery Manager Test",
        "email": email,
        "password": "Password123!",
        "confirm_password": "Password123!",
        "role": "Delivery / Engagement Manager"
    }
    r = requests.post(f"{BASE_URL}/register", json=payload)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
    assert r.status_code == 201
    assert r.json()["success"]
    assert "id" in r.json()["data"]
    print("Test 6 Passed!")

    # 7. Duplicate email registration
    print("\nTest 7: Register again with the duplicate email")
    r = requests.post(f"{BASE_URL}/register", json=payload)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
    assert r.status_code == 400
    assert not r.json()["success"]
    assert "Email is already registered" in r.json()["message"]
    print("Test 7 Passed!")

    # 8. Incorrect password login
    print("\nTest 8: Login with incorrect password")
    login_payload = {
        "email": email,
        "password": "WrongPassword!"
    }
    r = requests.post(f"{BASE_URL}/login", json=login_payload)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
    assert r.status_code == 401
    assert not r.json()["success"]
    assert "Invalid email or password" in r.json()["message"]
    print("Test 8 Passed!")

    # 9. Successful login
    print("\nTest 9: Login with correct password")
    login_payload = {
        "email": email,
        "password": "Password123!"
    }
    r = requests.post(f"{BASE_URL}/login", json=login_payload)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
    assert r.status_code == 200
    res_data = r.json()
    assert res_data["success"]
    assert "access_token" in res_data["data"]
    assert "token_expiry" in res_data["data"]
    assert res_data["data"]["email"] == email
    assert res_data["data"]["role"] == "Delivery / Engagement Manager"
    
    # 10. Decode/Verify JWT token structure
    print("\nTest 10: Decode/Verify the generated JWT Token")
    token = res_data["data"]["access_token"]
    decoded = jwt.decode(token, options={"verify_signature": False})
    print(f"Decoded token payload: {decoded}")
    assert decoded["email"] == email
    assert decoded["role"] == "Delivery / Engagement Manager"
    assert "sub" in decoded
    print("Test 10 Passed!")
    
    print("\n=== ALL TESTS PASSED SUCCESSFULLY ===")

import datetime
if __name__ == "__main__":
    run_tests()
