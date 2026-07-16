import requests
import json
import jwt

BASE_URL = "http://127.0.0.1:5000/api/auth"

def run_tests():
    print("=== STARTING AUTHENTICATION LOGIN TESTS ===")
    
    # 1. Missing fields
    print("\nTest 1: Login with missing fields")
    payload = {
        "email": "sanjib@gmail.com"
    }
    r = requests.post(f"{BASE_URL}/login", json=payload)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
    assert r.status_code == 400
    assert not r.json()["success"]
    assert "required" in r.json()["message"]
    print("Test 1 Passed!")

    # 2. Invalid email format / non-existent user
    print("\nTest 2: Login with non-existent user")
    payload = {
        "email": "nonexistent@gmail.com",
        "password": "123456"
    }
    r = requests.post(f"{BASE_URL}/login", json=payload)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
    assert r.status_code == 401
    assert not r.json()["success"]
    assert r.json()["message"] == "Invalid email or password"
    print("Test 2 Passed!")

    # 3. Incorrect password
    print("\nTest 3: Login with incorrect password")
    payload = {
        "email": "sanjib@gmail.com",
        "password": "wrongpassword"
    }
    r = requests.post(f"{BASE_URL}/login", json=payload)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
    assert r.status_code == 401
    assert not r.json()["success"]
    assert r.json()["message"] == "Invalid email or password"
    print("Test 3 Passed!")

    # 4. Successful login
    print("\nTest 4: Successful login with seeded user (Sanjib Sau)")
    payload = {
        "email": "sanjib@gmail.com",
        "password": "123456"
    }
    r = requests.post(f"{BASE_URL}/login", json=payload)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
    assert r.status_code == 200
    
    res = r.json()
    assert res["success"] is True
    assert res["message"] == "Login successful"
    assert "data" in res
    
    data = res["data"]
    assert isinstance(data["id"], int)
    assert data["full_name"] == "Sanjib Sau"
    assert data["email"] == "sanjib@gmail.com"
    assert data["role"] == "PwC Leadership"
    assert "access_token" in data
    assert data["expires_in"] == 3600
    print("Test 4 Passed!")

    # 5. Successful login of other seeded users
    users_to_test = [
        {"email": "pabitra@gmail.com", "name": "Pabitra Sarkar", "role": "Delivery / Engagement Manager"},
        {"email": "dipak@gmail.com", "name": "Dipak Saha", "role": "Outgoing SME (Knowledge Giver)"},
        {"email": "ayan@gmail.com", "name": "Ayan Manna", "role": "Incoming Team Member (Knowledge Receiver)"}
    ]
    
    for u in users_to_test:
        print(f"\nTest: Successful login with seeded user ({u['name']})")
        payload = {
            "email": u["email"],
            "password": "123456"
        }
        r = requests.post(f"{BASE_URL}/login", json=payload)
        print(f"Status: {r.status_code}")
        assert r.status_code == 200
        res = r.json()
        assert res["success"] is True
        data = res["data"]
        assert data["full_name"] == u["name"]
        assert data["email"] == u["email"]
        assert data["role"] == u["role"]
        assert "access_token" in data
        assert data["expires_in"] == 3600
        print(f"Passed login for {u['name']}!")
        
    print("\n=== ALL TESTS PASSED SUCCESSFULLY ===")

if __name__ == "__main__":
    run_tests()
