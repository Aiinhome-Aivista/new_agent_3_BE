from abc import ABC, abstractmethod

class BaseDBProvider(ABC):
    @abstractmethod
    def init_pool(self):
        """
        Initialize and return a MySQL connection pool.
        """
        pass

    @abstractmethod
    def get_direct_connection(self):
        """
        Return a direct MySQL connection (used as fallback).
        """
        pass
