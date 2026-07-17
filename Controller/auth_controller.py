import datetime
import bcrypt
import jwt
from flask import Blueprint, request, jsonify
from config import Config
from db import execute_query

auth_bp = Blueprint('auth_bp', __name__)

@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.json or {}
    
    # 1. Validate fields presence
    email = data.get('email')
    password = data.get('password')
    
    if not email or not password:
        return jsonify({
            "success": False,
            "message": "Email and password are required"
        }), 400
        
    email = str(email).strip()
    password = str(password)
    
    try:
        # 2. Retrieve user
        query = "SELECT id, full_name, email, password_hash, role, is_active FROM users WHERE email = %s"
        users = execute_query(query, (email,))
        
        if not users:
            return jsonify({
                "success": False,
                "message": "Invalid email or password"
            }), 401
            
        user = users[0]
        
        # 3. Verify hashed password
        if not bcrypt.checkpw(password.encode('utf-8'), user['password_hash'].encode('utf-8')):
            return jsonify({
                "success": False,
                "message": "Invalid email or password"
            }), 401
            
        # 4. Check if account is active
        is_active = bool(user['is_active'])
        if not is_active:
            return jsonify({
                "success": False,
                "message": "Account is inactive. Please contact your administrator."
            }), 403
            
        # 5. Generate JWT Access Token (expires in 1 hour / 3600 seconds)
        expires_in_seconds = 3600
        expiry_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=expires_in_seconds)
        payload = {
            "sub": str(user['id']),
            "email": user['email'],
            "role": user['role'],
            "exp": expiry_time,
            "iat": datetime.datetime.utcnow()
        }
        
        token = jwt.encode(payload, Config.SECRET_KEY, algorithm="HS256")
        if isinstance(token, bytes):
            token = token.decode('utf-8')
            
        return jsonify({
            "success": True,
            "message": "Login successful",
            "data": {
                "id": user['id'],
                "full_name": user['full_name'],
                "email": user['email'],
                "role": user['role'],
                "access_token": token,
                "expires_in": expires_in_seconds
            }
        }), 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": f"Internal database error: {str(e)}"
        }), 500
