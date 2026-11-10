import os
import shutil
import uuid
import sys
import webview
import threading

from pathlib import Path


import cv2
import numpy as np
from PIL import Image
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

import uvicorn

import bg_removal

app = FastAPI()

PORT = int(os.environ.get("PORT", "8000"))
URL = f"http://127.0.0.1:{PORT}"

TMP_DIR = Path("tmp")
TMP_DIR.mkdir(exist_ok=True)


def _session_dir(session_id: str) -> Path:
    d = TMP_DIR / session_id
    d.mkdir(exist_ok=True)
    return d


def get_static_path():
    # PyInstaller creates a temp folder and stores path in _MEIPASS
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "static"
    return Path(".") / "static"


def run_server():
    # We run uvicorn in a function so we can put it in a background thread
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")


# --- Image Processing Functions ---


def apply_clahe(img: np.ndarray, clip_limit: float = 2.0) -> np.ndarray:
    """Apply CLAHE to the L channel in LAB color space for even lighting."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
    l = clahe.apply(l)
    lab = cv2.merge([l, a, b])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def detect_face(img: np.ndarray) -> tuple[int, int, int, int] | None:
    """Detect the largest face using Haar cascades. Returns (x, y, w, h) or None."""
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    face_cascade = cv2.CascadeClassifier(cascade_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
    )
    if len(faces) == 0:
        return None
    faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
    x, y, w, h = faces[0]
    return (int(x), int(y), int(w), int(h))


def calculate_crop_region(
    face_box: tuple[int, int, int, int],
    img_w: int,
    img_h: int,
    target_face_pct: float = 0.825,
) -> tuple[int, int, int, int]:
    """Calculate a 7:9 crop region where the head is ~80-85% of the crop height."""
    fx, fy, fw, fh = face_box
    ASPECT = 630 / 810

    head_height = fh * 1.3
    crop_h = head_height / target_face_pct
    crop_w = crop_h * ASPECT

    face_cx = fx + fw / 2
    crop_x = face_cx - crop_w / 2

    face_cy = fy + fh / 2
    crop_y = face_cy - crop_h * 0.42

    crop_x = max(0, min(crop_x, img_w - crop_w))
    crop_y = max(0, min(crop_y, img_h - crop_h))
    crop_w = min(crop_w, img_w - crop_x)
    crop_h = min(crop_h, img_h - crop_y)

    return (int(crop_x), int(crop_y), int(crop_w), int(crop_h))


def resize_to_passport(img: Image.Image) -> Image.Image:
    """Resize image to exactly 630x810 pixels."""
    return img.convert("RGB").resize((630, 810), Image.LANCZOS)


# --- API Endpoints ---

app.mount("/static", StaticFiles(directory=get_static_path()), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = get_static_path() / "index.html"
    return FileResponse(index_path)


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    session_id = str(uuid.uuid4())
    session = _session_dir(session_id)
    ext = Path(file.filename).suffix or ".jpg"
    original_path = session / f"original{ext}"
    with open(original_path, "wb") as f:
        content = await file.read()
        f.write(content)
    return {"session_id": session_id}


@app.get("/image/{session_id}/{step}")
async def get_image(session_id: str, step: str):
    session = TMP_DIR / session_id
    if not session.exists():
        raise HTTPException(404, "Session not found")
    matches = list(session.glob(f"{step}.*"))
    if not matches:
        raise HTTPException(404, f"Image '{step}' not found")
    return FileResponse(matches[0])


@app.post("/remove-background")
async def remove_background(body: dict):
    session_id = body["session_id"]
    mode = body.get("mode", "remote")  # "remote" | "local" | "skip"
    session = _session_dir(session_id)
    original = list(session.glob("original.*"))
    if not original:
        raise HTTPException(404, "Original image not found")

    out_path = session / "no_bg.png"

    try:
        if mode == "skip":
            bg_removal.passthrough(original[0], out_path)
        elif mode == "local":
            bg_removal.remove_background_local(original[0], out_path)
        elif mode == "remote":
            api_key = os.environ.get("REMOVE_BG_API_KEY")
            if not api_key:
                raise HTTPException(
                    400, "REMOVE_BG_API_KEY not set; use mode 'local' or 'skip'"
                )
            bg_removal.remove_background_remote(original[0], out_path, api_key)
        else:
            raise HTTPException(400, f"Unknown mode: {mode}")
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    return {"status": "ok"}


@app.get("/bg-removal/status")
async def bg_removal_status():
    return {
        "has_api_key": bool(os.environ.get("REMOVE_BG_API_KEY")),
        "model_cached": bg_removal.is_model_cached(),
    }


@app.post("/bg-removal/download-model")
async def bg_removal_download_model():
    started = bg_removal.start_download()
    if not started:
        raise HTTPException(409, "Download already in progress")
    return {"status": "started"}


@app.get("/bg-removal/download-progress")
async def bg_removal_download_progress():
    return bg_removal.get_download_state()


@app.post("/fix-lighting")
async def fix_lighting(body: dict):
    session_id = body["session_id"]
    session = _session_dir(session_id)
    no_bg = list(session.glob("no_bg.*"))
    if not no_bg:
        raise HTTPException(404, "Background-removed image not found")

    src = cv2.imread(str(no_bg[0]))

    if body.get("skip", False):
        out_path = session / "lighting.png"
        cv2.imwrite(str(out_path), src)
        return {"status": "ok"}

    clip_limit = body.get("clip_limit", 2.0)
    result = apply_clahe(src, clip_limit=clip_limit)
    out_path = session / "lighting.png"
    cv2.imwrite(str(out_path), result)
    return {"status": "ok"}


@app.post("/detect-face")
async def detect_face_endpoint(body: dict):
    session_id = body["session_id"]
    session = _session_dir(session_id)
    lighting = list(session.glob("lighting.*"))
    if not lighting:
        raise HTTPException(404, "Lighting-corrected image not found")

    img = cv2.imread(str(lighting[0]))
    face_box = detect_face(img)
    h, w = img.shape[:2]

    suggested_crop = None
    if face_box is not None:
        suggested_crop = list(calculate_crop_region(face_box, w, h))

    return {
        "face_box": list(face_box) if face_box is not None else None,
        "suggested_crop": suggested_crop,
        "image_size": [w, h],
    }


@app.post("/crop")
async def crop(body: dict):
    session_id = body["session_id"]
    session = _session_dir(session_id)
    lighting = list(session.glob("lighting.*"))
    if not lighting:
        raise HTTPException(404, "Lighting-corrected image not found")

    img = Image.open(lighting[0])
    x, y, w, h = body["x"], body["y"], body["w"], body["h"]
    cropped = img.crop((x, y, x + w, y + h))
    out_path = session / "cropped.png"
    cropped.save(out_path, "PNG")
    return {"status": "ok"}


@app.post("/export")
async def export(body: dict):
    session_id = body["session_id"]
    session = _session_dir(session_id)
    cropped = list(session.glob("cropped.*"))
    if not cropped:
        raise HTTPException(404, "Cropped image not found")

    img = Image.open(cropped[0])
    result = resize_to_passport(img)
    out_path = session / "final.jpg"
    result.save(out_path, "JPEG", quality=95)
    return {"status": "ok"}


class Api:
    """Methods exposed to the page as window.pywebview.api.* in the desktop window.

    The native webview ignores <a download>, so saving goes through a Save dialog.
    """

    def save_final(self, session_id: str):
        src = TMP_DIR / session_id / "final.jpg"
        if not src.exists():
            return {"ok": False, "error": "Final image not found"}
        result = webview.windows[0].create_file_dialog(
            webview.FileDialog.SAVE, save_filename="passport-photo.jpg"
        )
        if not result:
            return {"ok": False, "cancelled": True}
        dest = result if isinstance(result, str) else result[0]
        try:
            shutil.copyfile(src, dest)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "path": dest}


if __name__ == "__main__":
    if "--browser" in sys.argv:
        # Run without the desktop window and open the UI in the default browser
        import webbrowser

        threading.Timer(1.0, webbrowser.open, args=[URL]).start()
        run_server()
        sys.exit(0)

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    window = webview.create_window(
        "Passport Photo Tool", URL, width=800, height=600, js_api=Api()
    )

    webview.start()
