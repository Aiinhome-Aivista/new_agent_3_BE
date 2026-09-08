import io
import boto3
from botocore.exceptions import ClientError
import logging
from config import Config
from .base import BaseStorageProvider

logger = logging.getLogger(__name__)

class AWSStorageProvider(BaseStorageProvider):
    def __init__(self):
        self.bucket_name = Config.AWS_S3_BUCKET_NAME
        self.base_folder = Config.AWS_S3_BASE_FOLDER
        self.agent_folder = Config.AWS_S3_AGENT_FOLDER
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=Config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=Config.AWS_SECRET_ACCESS_KEY,
            region_name=Config.AWS_DEFAULT_REGION
        )

    def save_file(self, file_name, file_stream, project_id=None, project_name=None):
        try:
            # Build S3 key
            folder_parts = [self.base_folder, self.agent_folder]
            if project_name:
                folder_parts.append(str(project_name))
            elif project_id:
                folder_parts.append(str(project_id))
                
            folder_path = "/".join(filter(None, folder_parts))
            s3_key = f"{folder_path}/{file_name}"

            if isinstance(file_stream, bytes):
                self.s3_client.upload_fileobj(io.BytesIO(file_stream), self.bucket_name, s3_key)
            else:
                file_content = file_stream.read()
                file_stream.seek(0)
                self.s3_client.upload_fileobj(io.BytesIO(file_content), self.bucket_name, s3_key)
            
            return s3_key
        except ClientError as e:
            logger.error(f"Failed to upload to S3: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error uploading to S3: {e}")
            return None
