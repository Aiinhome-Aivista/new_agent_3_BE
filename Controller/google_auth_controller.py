from google_auth_oauthlib.flow import Flow
from flask import Blueprint, request, redirect, jsonify, session
import logging
import os
from config import Config

logger = logging.getLogger(__name__)

google_auth_bp = Blueprint('google_auth_bp', __name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_flow():
    client_config = {
        "web": {
            "client_id": Config.GOOGLE_CLIENT_ID,
            "client_secret": Config.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [Config.GOOGLE_REDIRECT_URI]
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=Config.GOOGLE_REDIRECT_URI
    )
    return flow

@google_auth_bp.route('/login', methods=['GET'])
def login():
    logger.info("Google Login Started")
    try:
        if not Config.GOOGLE_CLIENT_ID or not Config.GOOGLE_CLIENT_SECRET or not Config.GOOGLE_REDIRECT_URI:
            logger.error("OAuth Failed: Client configurations are missing in .env")
            return jsonify({"success": False, "message": "Google OAuth configuration is missing on server."}), 500
            
        flow = get_flow()
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            prompt='consent',
            include_granted_scopes='true'
        )
        
        session['oauth_state'] = state
        session['code_verifier'] = flow.code_verifier
        
        return redirect(authorization_url)
    except Exception as e:
        logger.error(f"OAuth Failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@google_auth_bp.route('/callback', methods=['GET'])
def callback():
    logger.info("OAuth Callback Received")
    try:
        state = request.args.get('state')
        code = request.args.get('code')
        
        if not code:
            logger.error("OAuth Failed: Authorization code missing in callback.")
            return jsonify({"success": False, "message": "Missing authorization code."}), 400
            
        saved_state = session.get('oauth_state')
        if not state or state != saved_state:
            logger.error("OAuth Failed: State parameter mismatch or missing.")
            return jsonify({"success": False, "message": "State mismatch. Possible CSRF attack."}), 400
            
        code_verifier = session.get('code_verifier')
        if not code_verifier:
            logger.error("OAuth Failed: Code verifier missing in session.")
            return jsonify({"success": False, "message": "Missing code verifier. Session might have expired."}), 400

        # Allow HTTP redirect URI locally
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
        
        flow = get_flow()
        flow.code_verifier = code_verifier
        
        try:
            flow.fetch_token(authorization_response=request.url)
        except Exception as exchange_err:
            error_str = str(exchange_err)
            logger.error(f"OAuth Failed: Token exchange failed: {error_str}")
            if "invalid_grant" in error_str:
                return jsonify({"success": False, "message": "Authorization code is invalid or has expired (invalid_grant)."}), 400
            return jsonify({"success": False, "message": f"Token exchange failed: {error_str}"}), 400
            
        creds = flow.credentials
        
        # Save the credentials to token.json
        token_path = r'd:\pwc\agent3\new_agent_3_BE\token.json'
        with open(token_path, 'w') as token_file:
            token_file.write(creds.to_json())
            
        logger.info("token.json Created")
        logger.info("OAuth Success")
        
        # Clean up session keys
        session.pop('oauth_state', None)
        session.pop('code_verifier', None)
        
        return jsonify({
            "success": True,
            "message": "Google Calendar connected successfully."
        }), 200
        
    except Exception as e:
        logger.error(f"OAuth Failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
