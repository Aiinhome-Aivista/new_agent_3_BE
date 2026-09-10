from services.storage.factory import StorageFactory

def save_file(file_name, file_stream, project_id=None, project_name=None):
    """
    Facade to save a file using the dynamically selected storage provider.
    
    :param file_name: The name of the file to save
    :param file_stream: File content as bytes or file-like object
    :param project_id: Optional project identifier for folder grouping
    :param project_name: Optional project name for folder grouping
    :return: File URI or relative path
    """
    provider = StorageFactory.get_provider()
    return provider.save_file(file_name, file_stream, project_id, project_name)
