import azure.functions as func
import logging
import pathlib
import os
import time
import requests
from azure.storage.blob import BlobServiceClient

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

def ocr_read(image_url: str) -> str:
    """Equivalent to the Go OcrRead function."""
    endpoint = os.environ.get('AzureOcrEndpoint')
    api_key = os.environ.get('AzureOcrApiKey')
    
    if not endpoint or not api_key:
        logging.error("OCR Endpoint or API Key not configured.")
        return "Configuration Error"

    api_url = f"{endpoint.rstrip('/')}/vision/v3.2/read/analyze"
    headers = {
        'Content-Type': 'application/json',
        'Ocp-Apim-Subscription-Key': api_key
    }
    payload = {'url': image_url}

    response = requests.post(api_url, headers=headers, json=payload)
    response.raise_for_status()

    operation_location = response.headers.get('Operation-Location')
    return analyze_result(operation_location, api_key)

def analyze_result(operation_location: str, api_key: str) -> str:
    """Equivalent to the Go analyzeResult function with polling."""
    headers = {'Ocp-Apim-Subscription-Key': api_key}
    while True:
        response = requests.get(operation_location, headers=headers)
        response.raise_for_status()
        ocr_resp = response.json()
        status = ocr_resp.get('status')

        if status == 'succeeded':
            text_lines = []
            for result in ocr_resp.get('analyzeResult', {}).get('readResults', []):
                for line in result.get('lines', []):
                    text_lines.append(line.get('text'))
            return "\n".join(text_lines)
        elif status in ['notStarted', 'running']:
            logging.info(f"OCR status: {status}, waiting 5 seconds...")
            time.sleep(5)
        else:
            logging.error(f"Unexpected OCR status: {status}")
            return ""

def render_run_ocr_page(blob_url: str) -> str:
    """Returns an HTML page with a 'Run OCR' button by reading from an external template."""
    template_path = pathlib.Path(__file__).parent / "run_ocr.html"
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()
        return template.replace("{blob_url}", blob_url)
    except FileNotFoundError:
        logging.error(f"run_ocr.html not found at {template_path}")
        return "Internal Server Error: OCR template not found."

@app.route(route="img_ocr", methods=["POST"])
def img_ocr(req: func.HttpRequest) -> func.HttpResponse:
    """HTTP endpoint that triggers the OCR process for a given image URL."""
    logging.info('img_ocr endpoint triggered.')
    img_url = req.form.get('img_url')
    
    if not img_url:
        return func.HttpResponse("Error: No image URL provided.", status_code=400)

    extracted_text = ocr_read(img_url)
    return func.HttpResponse(f"Extracted Text:\n\n{extracted_text}", status_code=200)

@app.route(route="img_uploader", methods=["GET", "POST"])
def img_uploader(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed a request.')

    if req.method == "GET":
        index_html_path = pathlib.Path(__file__).parent / "index.html"
        try:
            with open(index_html_path, "r", encoding="utf-8") as f:
                html = f.read()
            return func.HttpResponse(body=html, status_code=200, mimetype="text/html")
        except FileNotFoundError:
            logging.error(f"index.html not found at {index_html_path}")
            return func.HttpResponse("Internal Server Error: HTML template not found.", status_code=500)

    if req.method == "POST":
        try:
            # Get storage connection string from environment variables
            # IMPORTANT: Ensure 'AzureStorageUploadConnection' is set in your local.settings.json
            # or as an application setting in Azure.
            connect_str = os.environ.get('AzureStorageUploadConnection')
            if not connect_str:
                logging.error("AzureStorageUploadConnection environment variable not set.")
                return func.HttpResponse(
                    "Azure Storage connection string is not configured.",
                    status_code=500
                )

            # Initialize BlobServiceClient
            blob_service_client = BlobServiceClient.from_connection_string(connect_str)
            container_name = "uploads" # Define your container name for uploads

            # Get a client for the container
            container_client = blob_service_client.get_container_client(container_name)
            
            # Create the container if it doesn't exist
            try:
                container_client.create_container()
                logging.info(f"Container '{container_name}' created.")
            except Exception as e:
                # Ignore if container already exists, otherwise log error
                if "ContainerAlreadyExists" not in str(e):
                    logging.error(f"Error creating container: {e}")
                    return func.HttpResponse(
                        f"Error preparing storage container: {e}",
                        status_code=500
                    )

            # Check if a file was uploaded
            if req.files:
                uploaded_file = req.files.get('file') # 'file' is the name from the HTML input
                if uploaded_file:
                    filename = uploaded_file.filename
                    file_content = uploaded_file.stream.read() # Read file content

                    # Create a blob client for the new blob and upload
                    blob_client = container_client.get_blob_client(filename)
                    blob_client.upload_blob(file_content, overwrite=True) # Overwrite if blob with same name exists
                    
                    # Return the HTML page with the 'Run OCR' button instead of running OCR immediately
                    logging.info(f"File '{filename}' uploaded. Rendering OCR trigger page.")
                    html_page = render_run_ocr_page(blob_client.url)
                    return func.HttpResponse(html_page, mimetype="text/html", status_code=200)
                else:
                    return func.HttpResponse("No file found in the request.", status_code=400)
            else:
                return func.HttpResponse("No files were uploaded.", status_code=400)

        except Exception as e:
            logging.error(f"An error occurred during file upload: {e}")
            return func.HttpResponse(f"An error occurred: {e}", status_code=500)

    return func.HttpResponse(status_code=405)