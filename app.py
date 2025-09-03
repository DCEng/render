import os, json, base64, requests, urllib.parse

from flask import Flask, request, redirect, url_for, render_template_string
import time


CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")

APS_BASE = "https://developer.api.autodesk.com"


app = Flask(__name__)



UPLOAD_FOLDER = "uploaded"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

BUCKET_KEY = "bucket62218"



def get_access_token():
    res = requests.post(
        f"{APS_BASE}/authentication/v2/token",
        data={
            "grant_type": "client_credentials",
            "scope": "data:read data:write data:create bucket:read"
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
        timeout=30
    )
    res.raise_for_status()
    return res.json()["access_token"]


def list_objects(token,bucket_key):
    objects = []
    url = f"{APS_BASE}/oss/v2/buckets/{bucket_key}/objects"

    start_at = 0
    limit = 10  # APS allows up to 100 per request

    HEADERS = {
        "Authorization": f"Bearer {token}"
    }

    while True:
        params = {"startAt": start_at, "limit": limit}
        r = requests.get(url, headers=HEADERS, params=params)
        r.raise_for_status()
        data = r.json()

        objects.extend(data.get("items", []))

        # Check if more objects remain
        if start_at + limit >= data.get("count", 0):
            break
        start_at += limit

    return objects
    
    


def upload_file(access_token,OBJECT_NAME,FILE_PATH):
    
    url = f"{APS_BASE}/oss/v2/buckets/{BUCKET_KEY}/objects/{OBJECT_NAME}"
    
    headers = {"Authorization": f"Bearer {access_token}"}
    
    with open(FILE_PATH, "rb") as f:
        res = requests.put(url, headers=headers, data=f)
        
    res.raise_for_status()
    object_id = res.json()["objectId"]
    
    print(f"File uploaded: {OBJECT_NAME}")
    
    return object_id
    
    
def get_signed_upload(token,OBJECT_NAME,FILE_PATH):
    
    enc_obj = urllib.parse.quote(OBJECT_NAME, safe="")
    
    res = requests.get(
        f"{APS_BASE}/oss/v2/buckets/{BUCKET_KEY}/objects/{enc_obj}/signeds3upload",
        params={"parts": 1},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30
    )
    
    res.raise_for_status()
    data = res.json()
    
    return data["uploadKey"], data["urls"]


def delete_object(token, OBJECT_NAME):

    enc_obj = urllib.parse.quote(OBJECT_NAME, safe="")

    res = requests.delete(
        f"{APS_BASE}/oss/v2/buckets/{BUCKET_KEY}/objects/{enc_obj}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30
    )

    try:
        res.raise_for_status()
        return True
    except requests.exceptions.HTTPError as e:
        print(f"Failed to delete {OBJECT_NAME}: {e} - {res.text}")
        return False
        
        
        
def put_to_s3(signed_url,OBJECT_NAME,FILE_PATH):
    
    FILE_PATH = os.path.join(UPLOAD_FOLDER, OBJECT_NAME)
    with open(FILE_PATH, "rb") as f:
        put = requests.put(
            signed_url, data=f,
            headers={"Content-Type": "application/octet-stream"},
            timeout=120
        )
    put.raise_for_status()
    etag = put.headers.get("ETag")
    if not etag:
        raise RuntimeError("Missing ETag from S3 upload")
    return etag.strip('"')  # strip quotes for APS finalize

def finalize_upload(token, upload_key, etag,OBJECT_NAME,FILE_PATH):
    enc_obj = urllib.parse.quote(OBJECT_NAME, safe="")
    FILE_PATH = os.path.join(UPLOAD_FOLDER, OBJECT_NAME)
    payload = {
        "uploadKey": upload_key,
        "eTags": [etag],
        "size": os.path.getsize(FILE_PATH)
    }
    res = requests.post(
        f"{APS_BASE}/oss/v2/buckets/{BUCKET_KEY}/objects/{enc_obj}/signeds3upload",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=30
    )
    if res.status_code != 200:
        print("APS finalize error:", res.text)
    res.raise_for_status()
    return res.json()["objectId"]

def translate_model(token, urn):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = {
        "input": {"urn": urn},
        "output": {"formats": [{"type": "svf", "views": ["3d"]}]}
    }
    res = requests.post(f"{APS_BASE}/modelderivative/v2/designdata/job", headers=headers, json=data)
    res.raise_for_status()
    print("Translation job submitted:", res.json())
    

def wait_for_translation(token, urn, timeout=120):
    headers = {"Authorization": f"Bearer {token}"}
    start = time.time()
    while time.time() - start < timeout:
        res = requests.get(f"{APS_BASE}/modelderivative/v2/designdata/{urn}/manifest", headers=headers)
        if res.status_code == 200:
            status = res.json().get("status")
            if status == "success":
                print("Translation completed!")
                return True
            elif status == "failed":
                print("Translation failed:", res.json())
                return False
        time.sleep(3)
    print("Translation timed out")
    return False


def get_base64_urn(object_id):
    return base64.b64encode(object_id.encode()).decode().rstrip("=")

# -------------------------------
# Flask route
# -------------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>APS Viewer</title>
  <script src="https://developer.api.autodesk.com/modelderivative/v2/viewers/7.*/viewer3D.min.js"></script>
  <link rel="stylesheet" href="https://developer.api.autodesk.com/modelderivative/v2/viewers/7.*/style.min.css">
  <style>html,body,#viewer{margin:0;padding:0;width:100%;height:100%;}</style>
</head>
<body>
  <div id="viewer"></div>
  <script>
    const options = {
      env: 'AutodeskProduction',
      accessToken: '{{token}}'
    };
    const viewerDiv = document.getElementById('viewer');
    const viewer = new Autodesk.Viewing.GuiViewer3D(viewerDiv);    
    console.log("Token:", '{{token}}');
    console.log("URN:", '{{urn}}');    
    Autodesk.Viewing.Initializer(options, () => {
      viewer.start();
      const documentId = 'urn:{{urn}}';
      Autodesk.Viewing.Document.load(documentId, 
        doc => {
          const defaultModel = doc.getRoot().getDefaultGeometry();
          viewer.loadDocumentNode(doc, defaultModel);
        },
        err => console.error(err)
      );
    });
  </script>
</body>
</html>
"""

@app.route("/xx", methods=["GET", "POST"])
def entry_point():
    if request.method == "POST":
        filename = request.form.get("filename")
        params = [request.form.get(f"param{i}") for i in range(1, 7)]

        # store globals (temporary)
        global user_filename, user_params
        user_filename = filename
        user_params = params

        # just call the existing function
        return viewer()

    # HTML form page
    return """
        <form method="post">
            File name: <input type="text" name="filename"><br>
            """ + "".join(
                f'Param {i}: <input type="number" name="param{i}"><br>' for i in range(1, 7)
            ) + """
            <input type="submit" value="Submit">
        </form>
    """
    
    


@app.route("/", methods=["GET", "POST"])
def upload_file():
    
    # OBJECT_NAME = "test2.f3d"
    # FILE_PATH = r"test2.f3d"
    # FILE_PATH = os.path.join(UPLOAD_FOLDER, OBJECT_NAME)


    
    if request.method == "POST":
        if "file" not in request.files:
            return "No file part"
        file = request.files["file"]
        if file.filename == "":
            return "No selected file"

        # --- LOGGING ---
        print(f"Uploaded file object: {file}")              # the FileStorage object
        print(f"Original filename: {file.filename}")       # filename user uploaded
        print(f"Content type: {file.content_type}")        # MIME type
        file.seek(0, os.SEEK_END)
        size = file.tell()
        print(f"File size (bytes): {size}")
        file.seek(0)  # reset pointer before saving
        # --- END LOGGING ---
        
        # Always overwrite with "current.f3d"
        OBJECT_NAME = "current.f3d"
        save_path = os.path.join(app.config["UPLOAD_FOLDER"], OBJECT_NAME)
        file.save(save_path)

        #return f"Upload complete. File is now available at /uploads/{OBJECT_NAME}"
        return viewer(OBJECT_NAME,save_path)

    return """
        <h2>Upload a File (will overwrite current.f3d)</h2>
        <form method="post" enctype="multipart/form-data">
            <input type="file" name="file">
            <input type="submit" value="Upload">
        </form>
    """



    
def viewer(OBJECT_NAME,FILE_PATH):
    # Step 1: get token
    token = get_access_token()

    #delete_object(token, "test2.f3d")
    
    # Step 2: get signed URL
    upload_key, s3_url = get_signed_upload(token,OBJECT_NAME,FILE_PATH)

    print(s3_url[0])
    # Step 3: upload to S3
    etag = put_to_s3(s3_url[0],OBJECT_NAME,FILE_PATH)

    print("XXXXXX",OBJECT_NAME,FILE_PATH)
    # Step 4: finalize and get objectId
    object_id = finalize_upload(token, upload_key, etag,OBJECT_NAME,FILE_PATH)
    
    # Step 5: base64 URN
    urn = get_base64_urn(object_id)
    
    translate_model(token, urn)
    
    wait_for_translation(token, urn, timeout=120)

    return render_template_string(HTML_TEMPLATE, token=token, urn=urn)

# -------------------------------
if __name__ == "__main__":
    app.run(debug=True)

