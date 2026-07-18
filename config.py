import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'), override=True)

class Config:
    FLASK_ENV = os.getenv("FLASK_ENV", "development")
    FLASK_DEBUG = os.getenv("FLASK_DEBUG", "True").lower() == "true"
    SECRET_KEY = os.getenv("SECRET_KEY", "change_this_secret_key_32_bytes_long")
    
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "3306")
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "your_mysql_password")
    DB_NAME = os.getenv("DB_NAME", "kt_manager_db")
    
    LLM_API_URL = os.getenv("LLM_API_URL", "http://122.163.121.176:3041/api/generate")
    LLM_MODEL = os.getenv("LLM_MODEL", "mistral-small:24b")
    
    # SMTP Email Configuration
    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    SENDER_EMAIL = os.getenv("SENDER_EMAIL")
    
    # Organizer Email Notification Setting
    ALWAYS_NOTIFY_ORGANIZER = os.getenv("ALWAYS_NOTIFY_ORGANIZER", "True").lower() == "true"
    
    # Google OAuth Configuration
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
    
    # AI Assessment Configuration
    ASSESSMENT_QUESTION_COUNT = int(os.getenv("ASSESSMENT_QUESTION_COUNT", "5"))
