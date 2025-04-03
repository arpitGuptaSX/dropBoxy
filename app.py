from flask import Flask, request, jsonify, render_template, send_file, after_this_request
import os
import boto3
from botocore.exceptions import ClientError
from werkzeug.utils import secure_filename
import uuid
import datetime
import json
import tempfile
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# AWS S3 Configuration from environment variables
AWS_ACCESS_KEY = os.getenv('AWS_ACCESS_KEY')
AWS_SECRET_KEY = os.getenv('AWS_SECRET_KEY')
AWS_BUCKET_NAME = os.getenv('AWS_BUCKET_NAME')
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')

# Configuration
ALLOWED_EXTENSIONS = {'store', 'jpg', 'jpeg', 'png', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt'}
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100 MB max upload size

app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# Initialize S3 client
try:
    s3_client = boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        region_name=AWS_REGION
    )
    # Test S3 connection
    s3_client.list_buckets()
    logger.info("Successfully connected to AWS S3")
except Exception as e:
    logger.error(f"Failed to initialize S3 client: {str(e)}")
    # We'll continue anyway, but log the error

# Database simulation (in a real app, use a proper database)
DATABASE_FILE = 'file_database.json'

def get_database():
    if not os.path.exists(DATABASE_FILE):
        return []
    
    try:
        with open(DATABASE_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading database file: {str(e)}")
        return []

def save_database(data):
    try:
        with open(DATABASE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving database file: {str(e)}")

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    # Serve the HTML page
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        # Log the request data for debugging
        logger.debug(f"Files in request: {request.files}")
        logger.debug(f"Form data in request: {request.form}")
        
        # Check if the post request has the file part
        if 'file' not in request.files:
            logger.warning("No file part in the request")
            return jsonify({'error': 'No file part'}), 400
        
        file = request.files['file']
        encryption_key = request.form.get('key', '')
        
        logger.debug(f"Received file: {file.filename}")
        logger.debug(f"Encryption key provided: {'Yes' if encryption_key else 'No'}")
        
        # If user does not select file, browser also submits an empty part without filename
        if file.filename == '':
            logger.warning("Empty filename submitted")
            return jsonify({'error': 'No selected file'}), 400
        
        if file:
            try:
                # Secure the filename and generate a unique ID
                original_filename = secure_filename(file.filename)
                file_id = str(uuid.uuid4())
                
                # For security, don't store the full encryption key in the filename
                if encryption_key:
                    s3_filename = f"{file_id}_encrypted_{original_filename}"
                else:
                    s3_filename = f"{file_id}_{original_filename}"
                
                logger.debug(f"S3 filename will be: {s3_filename}")
                
                # Create a temporary file to store the uploaded content
                temp_file = tempfile.NamedTemporaryFile(delete=False)
                file.save(temp_file.name)
                temp_file.close()
                
                # Upload file to S3
                try:
                    with open(temp_file.name, 'rb') as f:
                        logger.debug(f"Uploading to S3 bucket: {AWS_BUCKET_NAME}")
                        s3_client.upload_fileobj(
                            f,
                            AWS_BUCKET_NAME,
                            s3_filename
                        )
                    
                    # Get file size from the temporary file
                    file_size = os.path.getsize(temp_file.name)
                    
                    # Clean up the temporary file
                    os.unlink(temp_file.name)
                    
                    # Record file information
                    database = get_database()
                    database.append({
                        'id': file_id,
                        'original_name': original_filename.replace('.store', ''),
                        'stored_name': s3_filename,
                        'upload_date': datetime.datetime.now().isoformat(),
                        'size': file_size,
                        'has_encryption': bool(encryption_key),
                        'encryption_key': encryption_key if encryption_key else None
                    })
                    save_database(database)
                    
                    logger.info(f"Successfully uploaded file: {original_filename}")
                    return jsonify({
                        'success': True,
                        'file_id': file_id,
                        'message': 'File uploaded successfully to S3'
                    })
                    
                except ClientError as e:
                    error_msg = str(e)
                    logger.error(f"S3 upload error: {error_msg}")
                    # Clean up the temporary file
                    if os.path.exists(temp_file.name):
                        os.unlink(temp_file.name)
                    return jsonify({'error': f"S3 upload error: {error_msg}"}), 500
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Unexpected error during S3 upload: {error_msg}")
                    # Clean up the temporary file
                    if os.path.exists(temp_file.name):
                        os.unlink(temp_file.name)
                    return jsonify({'error': f"Unexpected error: {error_msg}"}), 500
                    
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Error processing file: {error_msg}")
                return jsonify({'error': f"Error processing file: {error_msg}"}), 500
        
        return jsonify({'error': 'File upload failed or file type not allowed'}), 400
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Unhandled exception in upload_file: {error_msg}")
        return jsonify({'error': f"Server error: {error_msg}"}), 500

@app.route('/files', methods=['GET'])
def list_files():
    # List uploaded files (admin functionality)
    try:
        database = get_database()
        return jsonify(database)
    except Exception as e:
        logger.error(f"Error listing files: {str(e)}")
        return jsonify({'error': f"Error listing files: {str(e)}"}), 500

@app.route('/download/<file_id>', methods=['GET'])
def download_file(file_id):
    try:
        # Find the file in the database
        database = get_database()
        file_info = next((item for item in database if item['id'] == file_id), None)
        
        if not file_info:
            logger.warning(f"File not found: {file_id}")
            return jsonify({'error': 'File not found'}), 404
        
        # Download from S3 to a temporary file
        try:
            temp_file = tempfile.NamedTemporaryFile(delete=False)
            temp_file.close()
            
            logger.debug(f"Downloading from S3: {file_info['stored_name']}")
            s3_client.download_file(
                AWS_BUCKET_NAME,
                file_info['stored_name'],
                temp_file.name
            )
            
            # Function to clean up after sending
            @after_this_request
            def cleanup(response):
                try:
                    if os.path.exists(temp_file.name):
                        os.unlink(temp_file.name)
                        logger.debug("Temporary file cleaned up")
                except Exception as e:
                    logger.error(f"Error cleaning up temp file: {str(e)}")
                return response
            
            logger.info(f"Successfully downloaded file: {file_info['original_name']}")
            return send_file(
                temp_file.name,
                as_attachment=True,
                download_name=file_info['original_name']
            )
            
        except ClientError as e:
            error_msg = str(e)
            logger.error(f"S3 download error: {error_msg}")
            if os.path.exists(temp_file.name):
                os.unlink(temp_file.name)
            return jsonify({'error': f"S3 download error: {error_msg}"}), 500
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Unexpected error during download: {error_msg}")
            if os.path.exists(temp_file.name):
                os.unlink(temp_file.name)
            return jsonify({'error': f"Download error: {error_msg}"}), 500
            
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Unhandled exception in download_file: {error_msg}")
        return jsonify({'error': f"Server error: {error_msg}"}), 500

@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({'error': 'File too large'}), 413

@app.errorhandler(500)
def internal_server_error(error):
    logger.error(f"Internal server error: {str(error)}")
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # Make sure database directory exists
    os.makedirs(os.path.dirname(DATABASE_FILE) if os.path.dirname(DATABASE_FILE) else '.', exist_ok=True)
    
    # Initialize empty database if it doesn't exist
    if not os.path.exists(DATABASE_FILE):
        save_database([])
    
    app.run(debug=True, host='0.0.0.0', port=5000)
