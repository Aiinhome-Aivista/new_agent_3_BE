import os
import json
import logging
from flask import Blueprint, request, jsonify, redirect
from config import Config
from db import execute_query, execute_write
from google_auth_oauthlib.flow import Flow
import msal

logger = logging.getLogger(__name__)
integrations_bp = Blueprint('integrations_bp', __name__)

GOOGLE_SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
MS_SCOPES = ['Files.Read']

def get_google_flow():
    client_config = {
        "web": {
            "client_id": Config.GOOGLE_CLIENT_ID,
            "client_project_id": "kt-manager",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": Config.GOOGLE_CLIENT_SECRET,
            "redirect_uris": ["http://localhost:5000/api/integrations/google/callback"]
        }
    }
    return Flow.from_client_config(client_config, scopes=GOOGLE_SCOPES, redirect_uri="http://localhost:5000/api/integrations/google/callback")

def get_ms_app():
    return msal.ConfidentialClientApplication(
        Config.MS_CLIENT_ID,
        authority="https://login.microsoftonline.com/common",
        client_credential=Config.MS_CLIENT_SECRET
    )

def _get_user_id(request):
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return None
    token = auth_header.split(' ')[1]
    try:
        import jwt
        payload = jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
        return payload.get('sub')
    except Exception:
        return None

# ------------- GOOGLE DRIVE -------------

@integrations_bp.route('/google/login', methods=['GET'])
def google_login():
    user_id = _get_user_id(request)
    if not user_id:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
        
    if not Config.GOOGLE_CLIENT_ID or not Config.GOOGLE_CLIENT_SECRET:
        return jsonify({"success": False, "message": "Google OAuth is not configured on the server."}), 500

    try:
        import base64
        import urllib.parse
        state_payload = base64.b64encode(str(user_id).encode()).decode()
        
        params = {
            "client_id": Config.GOOGLE_CLIENT_ID,
            "redirect_uri": "http://localhost:5000/api/integrations/google/callback",
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state_payload,
            "include_granted_scopes": "true"
        }
        auth_url = "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode(params)
        
        return jsonify({"success": True, "url": auth_url}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@integrations_bp.route('/google/callback', methods=['GET'])
def google_callback():
    state = request.args.get('state')
    code = request.args.get('code')
    
    if not code or not state:
        return jsonify({"success": False, "message": "Invalid callback"}), 400
        
    import base64
    try:
        user_id = base64.b64decode(state.encode()).decode()
    except Exception:
        return jsonify({"success": False, "message": "Invalid state"}), 400

    import requests
    import json
    try:
        token_url = "https://oauth2.googleapis.com/token"
        data = {
            "code": code,
            "client_id": Config.GOOGLE_CLIENT_ID,
            "client_secret": Config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": "http://localhost:5000/api/integrations/google/callback",
            "grant_type": "authorization_code"
        }
        r = requests.post(token_url, data=data)
        token_json = r.json()
        
        if "error" in token_json:
            raise Exception(token_json.get("error_description", token_json["error"]))
            
        creds_dict = {
            "token": token_json.get("access_token"),
            "refresh_token": token_json.get("refresh_token"),
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": Config.GOOGLE_CLIENT_ID,
            "client_secret": Config.GOOGLE_CLIENT_SECRET,
            "scopes": GOOGLE_SCOPES
        }
        token_data = json.dumps(creds_dict)
        
        # Save to DB
        execute_write("UPDATE users SET google_token = %s WHERE id = %s", (token_data, user_id))
        
        # Redirect back to frontend
        return redirect("http://localhost:5173/knowledge-base?integration=google_success")
    except Exception as e:
        logger.error(f"Google Callback Error: {e}")
        return redirect("http://localhost:5173/knowledge-base?integration=google_error")

# ------------- ONEDRIVE -------------

@integrations_bp.route('/microsoft/login', methods=['GET'])
def ms_login():
    user_id = _get_user_id(request)
    if not user_id:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
        
    if not Config.MS_CLIENT_ID or not Config.MS_CLIENT_SECRET:
        return jsonify({"success": False, "message": "Microsoft OAuth is not configured on the server."}), 500

    try:
        app = get_ms_app()
        import base64
        state_payload = base64.b64encode(str(user_id).encode()).decode()
        auth_url = app.get_authorization_request_url(MS_SCOPES, state=state_payload, redirect_uri="http://localhost:5000/api/integrations/microsoft/callback")
        return jsonify({"success": True, "url": auth_url}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@integrations_bp.route('/microsoft/callback', methods=['GET'])
def ms_callback():
    state = request.args.get('state')
    code = request.args.get('code')
    
    if not code or not state:
        return jsonify({"success": False, "message": "Invalid callback"}), 400
        
    import base64
    try:
        user_id = base64.b64decode(state.encode()).decode()
    except Exception:
        return jsonify({"success": False, "message": "Invalid state"}), 400

    try:
        app = get_ms_app()
        result = app.acquire_token_by_authorization_code(code, scopes=MS_SCOPES, redirect_uri="http://localhost:5000/api/integrations/microsoft/callback")
        
        if "access_token" in result:
            # Save token to DB
            token_data = json.dumps(result)
            execute_write("UPDATE users SET ms_token = %s WHERE id = %s", (token_data, user_id))
            return redirect("http://localhost:5173/knowledge-base?integration=ms_success")
        else:
            logger.error(f"MS Callback Error: {result.get('error_description')}")
            return redirect("http://localhost:5173/knowledge-base?integration=ms_error")
    except Exception as e:
        logger.error(f"MS Callback Error: {e}")
        return redirect("http://localhost:5173/knowledge-base?integration=ms_error")
