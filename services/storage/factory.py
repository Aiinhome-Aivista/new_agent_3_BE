from config import Config
from .local_storage import LocalStorageProvider
from .aws_storage import AWSStorageProvider
from .azure_storage import AzureStorageProvider

class StorageFactory:
    @staticmethod
    def get_provider():
        provider = Config.CLOUD_PROVIDER.upper()
        if provider == "AWS":
            return AWSStorageProvider()
        elif provider == "AZURE":
            return AzureStorageProvider()
        else:
            return LocalStorageProvider()
