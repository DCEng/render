import os, json, base64, requests, urllib.parse

from flask import Flask, request, redirect, url_for, render_template_string
import time



APS_BASE = "https://developer.api.autodesk.com"

# -------------------------------
# Flask app
# -------------------------------
app = Flask(__name__)

# Replace these with your APS app credentials
CLIENT_ID = "asgCv48a5rhK7Ht1HuQN8RlLIiQ8IHDCBvi6asGJeyfuqSGn"
CLIENT_SECRET = "5aZz7M6YWCuBAWJGXKdZVf0W5YoYPB0O7lE0dsadLqzJzaJ3Xy6G31sudJeft9Mi"

BUCKET_KEY = "bucket62218"
OBJECT_NAME = "test2.f3d"
FILE_PATH = r"test2.f3d"


# -------------------------------
# APS helper functions
# -------------------------------
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

def get_signed_upload(token):
    enc_obj = urllib.parse.quote(OBJECT_NAME, safe="")
    res = requests.get(
        f"{APS_BASE}/oss/v2/buckets/{BUCKET_KEY}/objects/{enc_obj}/signeds3upload",
        params={"parts": 1},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30
    )
    res.raise_for_status()
    data = res.json()
    return data["uploadKey"], data["urls"][0]

def put_to_s3(signed_url):
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

def finalize_upload(token, upload_key, etag):
    enc_obj = urllib.parse.quote(OBJECT_NAME, safe="")
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

@app.route("/", methods=["GET", "POST"])
def entry_point():
    if request.method == "POST":
        # Collect the 6 numeric parameters
        params = [request.form.get(f"param{i}") for i in range(1, 7)]
        # Collect the string parameter (filename)
        filename = request.form.get("filename")

        # # Redirect to your existing viewer route, passing params + filename
        # return redirect(url_for(
            # "viewer",
            # filename=filename,
            # **{f"param{i}": params[i-1] for i in range(1, 7)}
        # ))
        
        viewer()


    # GET: render the form
    return """
    <h2>Enter parameters and file name</h2>
    <form method="post">
        <label>File name (.f3d): <input type="text" name="filename" required></label><br><br>
        <label>Param1: <input type="number" step="any" name="param1" required></label><br>
        <label>Param2: <input type="number" step="any" name="param2" required></label><br>
        <label>Param3: <input type="number" step="any" name="param3" required></label><br>
        <label>Param4: <input type="number" step="any" name="param4" required></label><br>
        <label>Param5: <input type="number" step="any" name="param5" required></label><br>
        <label>Param6: <input type="number" step="any" name="param6" required></label><br><br>
        <button type="submit">Submit</button>
    </form>
    """
    
    

def viewer():
    # Step 1: get token
    token = get_access_token()

    # Step 2: get signed URL
    upload_key, s3_url = get_signed_upload(token)

    # Step 3: upload to S3
    etag = put_to_s3(s3_url)

    # Step 4: finalize and get objectId
    object_id = finalize_upload(token, upload_key, etag)
    
    # Step 5: base64 URN
    urn = get_base64_urn(object_id)
    
    translate_model(token, urn)
    
    wait_for_translation(token, urn, timeout=120)

    return render_template_string(HTML_TEMPLATE, token=token, urn=urn)

# -------------------------------
if __name__ == "__main__":
    app.run(debug=True)

