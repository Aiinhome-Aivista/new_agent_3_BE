import os
import logging

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from .base import BaseStorageProvider

logger = logging.getLogger(__name__)


class AzureStorageProvider(BaseStorageProvider):
    """Azure Blob Storage provider using DefaultAzureCredential."""

    def __init__(self):
        self.blob_service_client = None
        self.container_name = os.getenv("AZURE_CONTAINER_NAME")
        self.credential = None

        try:
            account_name = os.getenv("AZURE_STORAGE_ACCOUNT")

            if not account_name:
                raise ValueError(
                    "AZURE_STORAGE_ACCOUNT environment variable is required."
                )

            if not self.container_name:
                raise ValueError(
                    "AZURE_CONTAINER_NAME environment variable is required."
                )

            # Authenticate using Managed Identity or another supported
            # credential configured through DefaultAzureCredential.
            self.credential = DefaultAzureCredential()

            self.blob_service_client = BlobServiceClient(
                account_url=(
                    f"https://{account_name}.blob.core.windows.net"
                ),
                credential=self.credential,
            )

            logger.info(
                "Azure Blob Storage initialized successfully using "
                "DefaultAzureCredential."
            )

        except Exception as e:
            logger.exception(
                "Azure Blob Storage initialization failed: %s", e
            )

    def save_file(
        self,
        file_name,
        file_stream,
        project_id=None,
        project_name=None,
    ):
        """Upload a file to Azure Blob Storage.

        Files are stored under the project name when provided.
        Otherwise, the project ID is used as the folder name.

        Returns:
            str: Uploaded blob name on success.
            None: If the upload fails.
        """

        if not self.blob_service_client:
            logger.error(
                "Azure Blob client is not initialized."
            )
            return None

        if not self.container_name:
            logger.error(
                "Azure container name is not configured."
            )
            return None

        try:
            # Build the blob path.
            folder_name = None

            if project_name:
                folder_name = str(project_name)
            elif project_id:
                folder_name = str(project_id)

            blob_name = (
                f"{folder_name}/{file_name}"
                if folder_name
                else str(file_name)
            )

            # Get the blob client.
            blob_client = self.blob_service_client.get_blob_client(
                container=self.container_name,
                blob=blob_name,
            )

            # Support both bytes and file-like objects.
            if isinstance(file_stream, bytes):
                file_content = file_stream
            else:
                if hasattr(file_stream, "seek"):
                    file_stream.seek(0)

                file_content = file_stream

            # Upload the file, replacing an existing blob if necessary.
            blob_client.upload_blob(
                file_content,
                overwrite=True,
            )

            logger.info(
                "Successfully uploaded %s to Azure Blob Storage as %s/%s",
                file_name,
                self.container_name,
                blob_name,
            )

            return blob_name

        except Exception as e:
            logger.exception(
                "Failed to upload %s to Azure Blob Storage: %s",
                file_name,
                e,
            )
            return None

    def close(self):
        """Close Azure clients and release credential resources."""

        try:
            if self.blob_service_client:
                self.blob_service_client.close()

            if self.credential:
                self.credential.close()

        except Exception as e:
            logger.warning(
                "Error while closing Azure Storage resources: %s", e
            )