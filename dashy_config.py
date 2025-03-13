import json
import os

class Config:
    def __init__(self, path):
        if not path:
            raise KeyError("You did not provide a path for the config when initalizing the class.")
        
        if os.path.exists(path):
            self.path = path
            if not self.read_config_file():
                raise Exception("Unable to read config")
        else:
            raise FileNotFoundError("Configuration file not found")
    def read_config_file(self):
        file_path = self.path
        try:
            with open(file_path, 'r') as file:
                config_data = json.load(file)
            if not config_data:
                raise Exception("Your configuration is empty!")
            self.config_data = config_data
            return True
        except FileNotFoundError:
            print(f"Error: File '{file_path}' not found.")
            raise FileNotFoundError(f"Error: File '{file_path}' not found.")
        except json.JSONDecodeError:
            print(f"Error: Unable to parse config JSON in '{file_path}'.")
            raise json.JSONDecodeError(f"Error: Unable to parse config JSON in '{file_path}'.")
        except Exception as e:
            print(f"An error occurred: {str(e)}")
            raise e
