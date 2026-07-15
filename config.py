import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    FLASK_ENV = os.getenv("FLASK_ENV", "development")
    FLASK_DEBUG = os.getenv("FLASK_DEBUG", "True").lower() == "true"
    SECRET_KEY = os.getenv("SECRET_KEY", "change_this_secret_key")
    
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "3306")
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "your_mysql_password")
    DB_NAME = os.getenv("DB_NAME", "kt_manager_db")
    
    LLM_API_URL = os.getenv("LLM_API_URL", "http://122.163.121.176:3041/api/generate")
    LLM_MODEL = os.getenv("LLM_MODEL", "mistral-small:24b")
