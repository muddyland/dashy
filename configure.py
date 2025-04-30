import jinja2
import os 
import json 

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

# Check if the environment variables are set correctly
if not all([cam_model, data_dir, locked_dir, thumbnails_dir]):
    raise ValueError("Missing required environment variables.")

if __name__ == "__main__":
    # Load the template file
    template_loader = jinja2.FileSystemLoader(searchpath='templates')
    template_env = jinja2.Environment(loader=template_loader)
    template = template_env.get_template('nginx-template.jinja2')
    # Render the template with data
    rendered_content = template.render(cam_ip=cam_ip, cam_port=cam_port, cam_proxy_port=cam_proxy_port)
    with open("/etc/nginx/sites-available/default", "w") as f:
        f.write(rendered_content)
    print("Nginx configuration file generated successfully.")
    
    with open("/dashy/config.json", "w") as f:
        json.dump({
            "cam_ip": cam_ip,
            "cam_wifi_ip": cam_wifi_ip,
            "cam_model": cam_model,
            "video_path": data_dir,
            "download_parking": download_parking,
            "download_locked": download_locked
        }, f, indent=4)
    print("Configuration file generated successfully.")