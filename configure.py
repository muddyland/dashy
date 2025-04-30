import jinja2
import os 
import json 
# Dashy Port
dashy_proxy_port = os.environ.get('DASHY_PROXY_PORT', '80')
# Dashy Port
dashy_port = os.environ.get('DASHY_PORT', '5000')
# Only works on machines that can connect or route to the default IP 
cam_ip = os.environ.get('CAM_IP', '192.168.1.254')
# Cam WiFi IP
cam_wifi_ip = os.environ.get('CAM_WIFI_IP')
# Cam model
cam_model = os.environ.get('CAM_MODEL', 'A229-Plus')
# Default camera port is 80, odds are that you are not going to change this
cam_port = os.environ.get('CAM_PORT', '80')
# Cam proxy port is 8080 by default 
cam_proxy_port = os.environ.get('CAM_PROXY_PORT', '8080')
# Data Dir 
data_dir = os.environ.get('DATA_DIR', '/dashy/videos')
# Directory for the videos 
locked_dir = os.environ.get('VIDEOS_DIR', f'{data_dir}/locked')
# Directory for the thumbnails
thumbnails_dir = os.environ.get('THUMBNAILS_DIR', f'{data_dir}/thumbnails')
# Download Parking mode clips 
download_parking = bool(os.environ.get('DOWNLOAD_PARKING', True))
# Download Locked mode clips 
download_locked = bool(os.environ.get('DOWNLOAD_LOCKED', True))
# SSL Enabled
ssl_enabled = bool(os.environ.get('SSL_ENABLED', False))
# SSL Cert Path
ssl_cert_path = os.environ.get('SSL_CERT_PATH')
# SSL Key Path
ssl_key_path = os.environ.get('SSL_KEY_PATH')
if ssl_enabled:
    if not ssl_cert_path or not ssl_key_path:
        raise ValueError("SSL is enabled but SSL certificate or key path is missing.")
    # Check cert and key paths to make sure they are valid
    if not os.path.isfile(ssl_cert_path) or not os.path.isfile(ssl_key_path):
        raise ValueError("SSL certificate or key path is not a valid file.")

# Check if the environment variables are set correctly
if not all([cam_model, data_dir, locked_dir, thumbnails_dir]):
    raise ValueError("Missing required environment variables.")

if __name__ == "__main__":
    # Load the template file
    template_loader = jinja2.FileSystemLoader(searchpath='templates')
    template_env = jinja2.Environment(loader=template_loader)
    template = template_env.get_template('nginx-template.jinja2')
    # Render the template with data
    rendered_content = template.render(dashy_port=dashy_port, dashy_proxy_port=dashy_proxy_port, cam_ip=cam_ip, cam_port=cam_port, cam_proxy_port=cam_proxy_port, ssl_enabled=ssl_enabled, ssl_cert_path=ssl_cert_path, ssl_key_path=ssl_key_path)
    with open("/etc/nginx/sites-available/default", "w") as f:
        f.write(rendered_content)
    print("Nginx configuration file generated successfully.")
    
    with open("/dashy/config.json", "w") as f:
        json.dump({
            "proxy_port" : dashy_proxy_port,
            "cam_ip": cam_ip,
            "cam_wifi_ip": cam_wifi_ip,
            "cam_model": cam_model,
            "cam_proxy_port" : cam_proxy_port,
            "video_path": data_dir,
            "download_parking": download_parking,
            "download_locked": download_locked
        }, f, indent=4)
    print("Configuration file generated successfully.")
    
    # Set owner recursivly as we are root\
    os.chown("/dashy", 1000, 1000)
    os.chmod("/dashy", 0o755)
    print("Set dashy dir permissions successfully.")
    
    # Check to make sure the data_dir exists and is writable
    if not os.path.exists(data_dir):
        print("Data directory does not exist. Creating it...")
        os.makedirs(data_dir)
    if not os.access(data_dir, os.W_OK):
        # Set permissions for the data_dir
        os.chmod(data_dir, 0o755)
        os.chown(data_dir, 1000, 1000)
        print("Set data dir permissions successfully.")
        
    # Check to make sure thumbnail and locked video directories exist and are writable
    if not os.path.exists(thumbnails_dir):
        print("Thumbnails directory does not exist. Creating it...")
        os.makedirs(thumbnails_dir)
    if not os.access(thumbnails_dir, os.W_OK):
        # Set permissions for the thumbnails_dir
        os.chmod(thumbnails_dir, 0o755)
        os.chown(thumbnails_dir, 1000, 1000)
        print("Permissions set for thumbnails directory.")
        
    if not os.path.exists(locked_dir):
        print("Locked videos directory does not exist. Creating it...")
        os.makedirs(locked_dir)
    if not os.access(locked_dir, os.W_OK):
        # Set permissions for the locked_videos_dir
        os.chmod(locked_dir, 0o755)
        os.chown(locked_dir, 1000, 1000)
        print("Permissions set for locked videos directory.")
