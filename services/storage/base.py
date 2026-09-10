from abc import ABC, abstractmethod

class BaseStorageProvider(ABC):
    @abstractmethod
    def save_file(self, file_name, file_stream, project_id=None, project_name=None):
        """
        Save a file and return its URI or reference path.
        """
        pass
