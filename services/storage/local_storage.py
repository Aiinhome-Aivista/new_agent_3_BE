import os
from config import Config
from .base import BaseStorageProvider

class LocalStorageProvider(BaseStorageProvider):
    def save_file(self, file_name, file_stream, project_id=None, project_name=None):
        base_path = Config.UPLOAD_PATH
        
        # Build path based on optional parameters
        if project_name:
            path = os.path.join(base_path, str(project_name))
        elif project_id:
            path = os.path.join(base_path, str(project_id))
        else:
            path = base_path
            
        os.makedirs(path, exist_ok=True)
        file_path = os.path.join(path, file_name)
        
        # Determine if file_stream is bytes or a file-like object
        if isinstance(file_stream, bytes):
            with open(file_path, 'wb') as f:
                f.write(file_stream)
        else:
            with open(file_path, 'wb') as f:
                file_content = file_stream.read()
                f.write(file_content)
                file_stream.seek(0)
                
        return file_path
