import os
import boto3
from botocore.exceptions import ClientError
import logging

logger = logging.getLogger(__name__)

def get_s3_client():
    return boto3.client(
        's3',
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        region_name=os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
    )

def upload_to_s3(file_stream, bucket_name, object_name):
    """
    Upload a file-like object to an S3 bucket
    """
    s3_client = get_s3_client()
    try:
        import io
        # Read the file content into memory to prevent boto3 from closing the original stream
        file_content = file_stream.read()
        # Reset the original stream's pointer so it can be read again by the caller
        file_stream.seek(0)
        
        # Upload a copy to S3
        s3_client.upload_fileobj(io.BytesIO(file_content), bucket_name, object_name)
        return True, "Success"
    except ClientError as e:
        logger.error(f"Failed to upload to S3: {e}")
        return False, str(e)
    except Exception as e:
        logger.error(f"Unexpected error uploading to S3: {e}")
        return False, str(e)

def log_s3_upload(file_name, original_format, s3_key, uploaded_by, module_type):
    from db import execute_write
    query = """
        INSERT INTO s3_file_uploads 
        (file_name, original_format, s3_key, uploaded_by, module_type)
        VALUES (%s, %s, %s, %s, %s)
    """
    try:
        insert_id = execute_write(query, (file_name, original_format, s3_key, uploaded_by, module_type))
        return insert_id
    except Exception as e:
        logger.error(f"Failed to log S3 upload to DB: {e}")
        return None

def generate_presigned_url(bucket_name, object_name, expiration=3600):
    """Generate a presigned URL to share an S3 object"""
    s3_client = get_s3_client()
    try:
        response = s3_client.generate_presigned_url('get_object',
                                                    Params={'Bucket': bucket_name,
                                                            'Key': object_name},
                                                    ExpiresIn=expiration)
    except ClientError as e:
        logger.error(f"Failed to generate presigned URL: {e}")
        return None
    return response
