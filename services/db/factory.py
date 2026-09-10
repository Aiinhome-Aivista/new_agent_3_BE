from config import Config
from .default_provider import DefaultDBProvider
from .aws_provider import AWSDBProvider
from .azure_provider import AzureDBProvider

class DBFactory:
    @staticmethod
    def get_provider():
        provider = Config.DB_PROVIDER.upper()
        if provider == "AWS":
            return AWSDBProvider()
        elif provider == "AZURE":
            return AzureDBProvider()
        else:
            return DefaultDBProvider()
