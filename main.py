import os
import uuid
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware

from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from PIL import Image

# Load config from environment / .env
from dotenv import load_dotenv
load_dotenv()

API_KEYS = {k.strip() for k in os.getenv("API_KEYS", "test-key-123").split(",") if k.strip()}
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

BASE_DIR = "data"
IMAGES_DIR = os.path.join(BASE_DIR, "images")
RENDER_DIR = os.path.join(BASE_DIR, "renderings")

os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(RENDER_DIR, exist_ok=True)

# Simple SW color mapping for demo (extend as needed)
# --------- SHERWIN-WILLIAMS COLOR TABLE ----------

SW_COLOR_TABLE = [
    # Whites / Off-Whites
    {"id": "sw-7008", "code": "SW 7008", "name": "Alabaster",        "hex": "#EDE6D9", "family": "warm white"},
    {"id": "sw-7004", "code": "SW 7004", "name": "Snowbound",       "hex": "#ECEBE7", "family": "cool white"},
    {"id": "sw-7005", "code": "SW 7005", "name": "Pure White",      "hex": "#F4F4F0", "family": "neutral white"},
    {"id": "sw-7006", "code": "SW 7006", "name": "Extra White",     "hex": "#F3F5F6", "family": "cool white"},
    {"id": "sw-7042", "code": "SW 7042", "name": "Shoji White",     "hex": "#E2DED2", "family": "warm white"},
    {"id": "sw-7551", "code": "SW 7551", "name": "Greek Villa",     "hex": "#EFE6D8", "family": "warm white"},

    # Beiges / Greiges
    {"id": "sw-7036", "code": "SW 7036", "name": "Accessible Beige","hex": "#C9B8A4", "family": "greige"},
    {"id": "sw-6108", "code": "SW 6108", "name": "Latte",           "hex": "#D0BA9B", "family": "beige"},
    {"id": "sw-7030", "code": "SW 7030", "name": "Anew Gray",       "hex": "#B7ADA1", "family": "greige"},
    {"id": "sw-7029", "code": "SW 7029", "name": "Agreeable Gray",  "hex": "#D1CBC1", "family": "greige"},

    # Grays
    {"id": "sw-7015", "code": "SW 7015", "name": "Repose Gray",     "hex": "#C0B7AE", "family": "warm gray"},
    {"id": "sw-7016", "code": "SW 7016", "name": "Mindful Gray",    "hex": "#B0AAA0", "family": "warm gray"},
    {"id": "sw-7019", "code": "SW 7019", "name": "Gauntlet Gray",   "hex": "#625A54", "family": "dark gray"},
    {"id": "sw-7024", "code": "SW 7024", "name": "Dovetail",        "hex": "#8B7D70", "family": "medium gray"},
    {"id": "sw-7674", "code": "SW 7674", "name": "Peppercorn",      "hex": "#4A4B4D", "family": "charcoal"},

    # Darks / Accents
    {"id": "sw-6258", "code": "SW 6258", "name": "Tricorn Black",   "hex": "#2D2C2F", "family": "black"},
    {"id": "sw-7069", "code": "SW 7069", "name": "Iron Ore",        "hex": "#434447", "family": "charcoal"},
    {"id": "sw-7048", "code": "SW 7048", "name": "Urbane Bronze",   "hex": "#60544D", "family": "brown-gray"},

    # Blues / Greens (nice for doors, accents)
    {"id": "sw-6244", "code": "SW 6244", "name": "Naval",           "hex": "#303B4A", "family": "navy blue"},
    {"id": "sw-6204", "code": "SW 6204", "name": "Sea Salt",        "hex": "#CBD4CC", "family": "blue-green"},
    {"id": "sw-6211", "code": "SW 6211", "name": "Rainwashed",      "hex": "#C2D4CC", "family": "blue-green"},
]

# Lookup dictionaries
SW_BY_ID = {c["id"].lower(): c for c in SW_COLOR_TABLE}
SW_BY_CODE = {c["code"].lower(): c for c in SW_COLOR_TABLE}
SW_BY_NAME = {c["name"].strip().lower(): c for c in SW_COLOR_TABLE}

def resolve_sw_color(color_key: str) -> str:
    """
    Accepts:
      - 'sw-7008'
      - 'SW 7008'
      - 'Alabaster'
    Returns:
      - hex color string like '#EDE6D9'
    """
    if not color_key:
        return "#CCCCCC"

    ck = color_key.strip().lower()

    # Try internal ID form, e.g. 'sw-7008'
    if ck in SW_BY_ID:
        return SW_BY_ID[ck]["hex"]

    # Try code form, e.g. 'sw 7008' or 'SW 7008'
    # Normalize spaces/dash
    ck_normalized = ck.replace("-", " ")
    if ck_normalized in SW_BY_CODE:
        return SW_BY_CODE[ck_normalized]["hex"]

    # Try name form, e.g. 'alabaster'
    if ck in SW_BY_NAME:
        return SW_BY_NAME[ck]["hex"]

    # Fallback if unknown – neutral gray
    return "#CCCCCC"

app = FastAPI(title="Color Rendering Demo API")

origins = [
    "https://rendering.certapropaintersofmissouricity.com",
    "http://localhost:8000",  # optional for local tests
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Auth dependency ----------

def get_api_key(authorization: str = Header(...)):
    """Expect header: Authorization: Bearer <api_key>"""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    token = authorization.split(" ", 1)[1].strip()
    if token not in API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return token


# ---------- Models ----------

class RegionConfig(BaseModel):
    region_id: str
    color_id: str


class RenderingRequest(BaseModel):
    image_id: str
    regions: List[RegionConfig]
    output_format: str = "jpg"


class RenderingJob(BaseModel):
    id: str
    status: str
    output_url: Optional[str] = None
    config: RenderingRequest


# In-memory store for demo
JOBS = {}


# ---------- File serving ----------

@app.get("/files/{folder}/{filename}")
def get_file(folder: str, filename: str):
    if folder not in ("images", "renderings"):
        raise HTTPException(status_code=404, detail="Folder not found")
    path = os.path.join(BASE_DIR, folder, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)


# ---------- Image upload ----------

@app.post("/images")
async def upload_image(
    file: UploadFile = File(...),
    api_key: str = Depends(get_api_key),
):
    # Generate unique filename
    ext = os.path.splitext(file.filename)[1].lower() or ".jpg"
    image_id = str(uuid.uuid4())
    filename = f"{image_id}{ext}"
    filepath = os.path.join(IMAGES_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(await file.read())

    file_url = f"/files/images/{filename}"

    return {
        "id": image_id,
        "file_url": file_url,
    }


# ---------- Rendering ----------

def hex_to_rgb(hex_str: str):
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 3:
        hex_str = "".join(c * 2 for c in hex_str)
    return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))


def simple_tint(input_path: str, output_path: str, hex_color: str):
    """Very simple tint for demo: blends original with a solid color."""
    base = Image.open(input_path).convert("RGB")
    overlay_color = hex_to_rgb(hex_color)
    overlay = Image.new("RGB", base.size, overlay_color)
    # alpha controls how strong the tint is (0.0 – 1.0)
    tinted = Image.blend(base, overlay, alpha=0.35)
    tinted.save(output_path)


@app.post("/renderings")
def create_rendering(
    req: RenderingRequest,
    api_key: str = Depends(get_api_key),
):
    # Check image exists
    # For simplicity we assume .jpg, but you can search for any ext
    possible_files = [f for f in os.listdir(IMAGES_DIR) if f.startswith(req.image_id)]
    if not possible_files:
        raise HTTPException(status_code=404, detail="Image not found")
    input_filename = possible_files[0]
    input_path = os.path.join(IMAGES_DIR, input_filename)

    if not req.regions:
        raise HTTPException(status_code=400, detail="At least one region is required")

    # Use the first region's color_id for demo
       color_key = req.regions[0].color_id
    hex_color = resolve_sw_color(color_key)


    job_id = str(uuid.uuid4())
    ext = ".jpg" if req.output_format.lower() == "jpg" else ".png"
    output_filename = f"{job_id}{ext}"
    output_path = os.path.join(RENDER_DIR, output_filename)

    # Synchronous "render"
    simple_tint(input_path, output_path, hex_color)

    output_url = f"/files/renderings/{output_filename}"

    job = RenderingJob(
        id=job_id,
        status="completed",
        output_url=output_url,
        config=req
    )
    JOBS[job_id] = job

    # For demo we return the completed job immediately
    return job


@app.get("/renderings/{job_id}")
def get_rendering(job_id: str, api_key: str = Depends(get_api_key)):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
