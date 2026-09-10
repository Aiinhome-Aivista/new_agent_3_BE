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
    
    # Provider Flags
    CLOUD_PROVIDER = os.getenv("CLOUD_PROVIDER", "DEFAULT")
    DB_PROVIDER = os.getenv("DB_PROVIDER", "DEFAULT")

    # Database Configuration Keys
    # Local
    MYSQL_HOST = os.getenv("MYSQL_HOST", DB_HOST)
    MYSQL_PORT = os.getenv("MYSQL_PORT", DB_PORT)
    MYSQL_USER = os.getenv("MYSQL_USER", DB_USER)
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", DB_PASSWORD)
    MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", DB_NAME)

    # AWS RDS
    AWS_RDS_HOST = os.getenv("AWS_RDS_HOST")
    AWS_RDS_PORT = os.getenv("AWS_RDS_PORT", "3306")
    AWS_RDS_USER = os.getenv("AWS_RDS_USER")
    AWS_RDS_PASSWORD = os.getenv("AWS_RDS_PASSWORD")
    AWS_RDS_DATABASE = os.getenv("AWS_RDS_DATABASE")

    # Azure DB
    AZURE_DB_HOST = os.getenv("AZURE_DB_HOST")
    AZURE_DB_PORT = os.getenv("AZURE_DB_PORT", "3306")
    AZURE_DB_USER = os.getenv("AZURE_DB_USER")
    AZURE_DB_PASSWORD = os.getenv("AZURE_DB_PASSWORD")
    AZURE_DB_DATABASE = os.getenv("AZURE_DB_DATABASE")

    # Storage Configuration Keys
    # AWS Storage
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
    AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME")
    AWS_S3_BASE_FOLDER = os.getenv("AWS_S3_BASE_FOLDER")
    AWS_S3_AGENT_FOLDER = os.getenv("AWS_S3_AGENT_FOLDER")

    # Azure Storage
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    AZURE_CONTAINER_NAME = os.getenv("AZURE_CONTAINER_NAME")

    # Local Storage
    UPLOAD_PATH = os.getenv("UPLOAD_PATH", "data/uploads")

    LLM_API_URL = os.getenv("LLM_API_URL", "http://122.163.121.176:3041/api/generate")
    LLM_MODEL = os.getenv("LLM_MODEL", "mistral-small:24b")
    
    LLM_CONNECT_TIMEOUT = max(1, int(os.getenv("LLM_CONNECT_TIMEOUT", "10")))
    LLM_READ_TIMEOUT = max(1, int(os.getenv("LLM_READ_TIMEOUT", "300")))
    LLM_MAX_RETRIES = max(0, int(os.getenv("LLM_MAX_RETRIES", "0")))
    LLM_KEEP_ALIVE = os.getenv("LLM_KEEP_ALIVE", "30m").strip()
    CHATBOT3_ANSWER_CHUNK_ROWS = max(
        5, int(os.getenv("CHATBOT3_ANSWER_CHUNK_ROWS", "30"))
    )

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

    # Jira Configuration
    JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "")
