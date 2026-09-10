from config import Config
from .base import BaseStorageProvider
import logging

logger = logging.getLogger(__name__)

class AzureStorageProvider(BaseStorageProvider):
    def __init__(self):
        self.connection_string = Config.AZURE_STORAGE_CONNECTION_STRING
        self.container_name = Config.AZURE_CONTAINER_NAME

    def save_file(self, file_name, file_stream, project_id=None, project_name=None):
        if not self.connection_string or not self.container_name:
            logger.error("Azure Storage credentials missing: set AZURE_STORAGE_CONNECTION_STRING and AZURE_CONTAINER_NAME.")
            return None

        try:
            from azure.storage.blob import BlobServiceClient

            # Build Blob path
            folder_parts = []
            if project_name:
                folder_parts.append(str(project_name))
            elif project_id:
                folder_parts.append(str(project_id))
                
            folder_path = "/".join(filter(None, folder_parts))
            blob_name = f"{folder_path}/{file_name}" if folder_path else file_name

            blob_service_client = BlobServiceClient.from_connection_string(self.connection_string)
            container_client = blob_service_client.get_container_client(self.container_name)

            # Create container if it does not exist
            try:
                if not container_client.exists():
                    container_client.create_container()
            except Exception as ce:
                logger.warning(f"Could not verify or auto-create container {self.container_name}: {ce}")

            blob_client = container_client.get_blob_client(blob=blob_name)

            if isinstance(file_stream, bytes):
                blob_client.upload_blob(file_stream, overwrite=True)
            else:
                file_content = file_stream.read()
                file_stream.seek(0)
                blob_client.upload_blob(file_content, overwrite=True)

            logger.info(f"Successfully uploaded {file_name} to Azure Blob Storage as {blob_name}")
            return blob_name
        except Exception as e:
            logger.error(f"Failed to upload to Azure Blob Storage: {e}")
            return None
