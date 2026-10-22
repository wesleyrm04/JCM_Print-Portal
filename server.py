import os
import shutil
import uuid
import re
import json
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import pypdfium2 as pdfium
from PIL import Image

app = FastAPI(title="Local Print Portal - JC Hub")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- NEW CONFIGURATIONS ---
UPLOAD_BASE_DIR = "/mnt/nas_spool/customer_orders"
MAX_FILE_SIZE_MB = 50
MAX_FILES_PER_ORDER = 50
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

os.makedirs(UPLOAD_BASE_DIR, exist_ok=True)

# Mount static folder
app.mount("/static", StaticFiles(directory="static"), name="static")

def sanitize_folder_name(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def get_safe_order_number(date_folder: str) -> int:
    """
    Safely acquire a unique daily order number using atomic file creation.
    Creates a hidden .locks folder inside the date directory to store locks.
    """
    locks_dir = os.path.join(date_folder, ".locks")
    os.makedirs(locks_dir, exist_ok=True)
    
    order_num = 1
    while True:
        lock_file = os.path.join(locks_dir, f"{order_num:03d}.lock")
        try:
            # os.O_CREAT | os.O_EXCL ensures the file is created atomically.
            # If the file already exists, it raises FileExistsError.
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.close(fd)
            return order_num
        except FileExistsError:
            order_num += 1
            if order_num > 9999: # Safety limit
                raise Exception("Exceeded maximum orders for the day")
        except OSError as e:
            # Fallback for unexpected OS errors
            raise Exception(f"Failed to acquire order number: {str(e)}")

# --- NEW WEB ROUTES ---

@app.get("/", response_class=FileResponse)
async def serve_index():
    return "static/index.html" # The Hub

@app.get("/print", response_class=FileResponse)
async def serve_print():
    return "static/print.html" # Your Original Print Portal

@app.get("/souvenirs", response_class=FileResponse)
async def serve_souvenirs():
    return "static/souvenirs.html" # Future Souvenirs Page

@app.get("/repair", response_class=FileResponse)
async def serve_repair():
    return "static/repair.html" # Future Repair Page

# --- YOUR ORIGINAL API ENDPOINTS (ENHANCED) ---

@app.post("/api/estimate")
async def estimate_print(file: UploadFile = File(...)):
    """Receives a PDF, counts pages, and performs fast pixel analysis to detect Color vs B&W."""
    if not file.filename.lower().endswith('.pdf'):
        return {"page_count": 1, "bw_pages": 1, "color_pages": 0}

    temp_path = f"/tmp/est_{uuid.uuid4().hex[:8]}.pdf"
    
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        doc = pdfium.PdfDocument(temp_path)
        total_pages = len(doc)
        bw_pages = 0
        color_pages = 0
        
        for i in range(total_pages):
            page = doc[i]
            bitmap = page.render(scale=0.5) 
            pil_img = bitmap.to_pil().convert('HSV')
            
            _, s, _ = pil_img.split()
            min_sat, max_sat = s.getextrema()
            
            if max_sat > 15:
                color_pages += 1
            else:
                bw_pages += 1
                
            page.close()
        doc.close()
        
        return {
            "page_count": total_pages,
            "bw_pages": bw_pages,
            "color_pages": color_pages
        }
    except Exception as e:
        print(f"Error analyzing PDF: {e}")
        return {"page_count": 1, "bw_pages": 1, "color_pages": 0}
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass

@app.post("/api/submit_order")
async def submit_order(
    customer_name: str = Form(...),
    preset_request: str = Form(...),
    instructions: str = Form(""),
    order_meta: Optional[str] = Form(None),
    files: List[UploadFile] = File(...)
):
    if not files:
        raise HTTPException(status_code=400, detail="Please select at least one file to upload.")

    if len(files) > MAX_FILES_PER_ORDER:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_FILES_PER_ORDER} files allowed per order.")

    # 1. NEW DATE FOLDER
    current_date_str = datetime.now().strftime("%Y-%m-%d")
    date_folder_path = os.path.join(UPLOAD_BASE_DIR, current_date_str)
    os.makedirs(date_folder_path, exist_ok=True)

    # 2 & 21. DAILY ORDER NUMBER (ATOMIC)
    try:
        order_number = get_safe_order_number(date_folder_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Order numbering error: {str(e)}")

    # 3. ORDER ID
    date_compact = datetime.now().strftime("%Y%m%d")
    order_id = f"{date_compact}-{order_number:03d}"

    clean_name = sanitize_folder_name(customer_name)
    clean_preset = sanitize_folder_name(preset_request)
    
    # NEW FOLDER HIERARCHY: DATE -> ORDER -> FILES
    folder_name = f"{order_number:03d}_[{clean_name}] - {clean_preset}"
    order_folder_path = os.path.join(date_folder_path, folder_name)

    try:
        os.makedirs(order_folder_path, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not create order directory: {str(e)}")

    saved_files = []
    
    try:
        for index, file in enumerate(files, 1):
            if not file.filename:
                continue
            
            # File Validation
            file.file.seek(0, os.SEEK_END)
            file_size = file.file.tell()
            file.file.seek(0)
            
            if file_size == 0:
                raise HTTPException(status_code=400, detail=f"File {file.filename} is empty.")
            if file_size > MAX_FILE_SIZE_BYTES:
                raise HTTPException(status_code=400, detail=f"File {file.filename} exceeds {MAX_FILE_SIZE_MB}MB limit.")

            # 13. Duplicate filenames are safe because of index prefixing
            filename = f"{index:02d}_{sanitize_folder_name(file.filename)}"
            file_dest = os.path.join(order_folder_path, filename)

            with open(file_dest, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            saved_files.append(filename)

        # 5. PRESERVE instructions.txt
        instructions_file_path = os.path.join(order_folder_path, "instructions.txt")
        current_time_str = datetime.now().strftime('%Y-%m-%d %I:%M %p')
        
        with open(instructions_file_path, "w", encoding="utf-8") as f:
            f.write("==========================================" + "\n")
            f.write("    PRINT SHOP CUSTOMER ORDER SUMMARY    " + "\n")
            f.write("==========================================" + "\n")
            f.write(f"Order ID:          {order_id}\n")
            f.write(f"Order Number:      {order_number}\n")
            f.write(f"Date Folder:       {current_date_str}\n")
            f.write(f"Date & Time:       {current_time_str}\n")
            f.write(f"Customer Name:     {customer_name}\n")
            f.write(f"Preset Request:    {preset_request}\n")
            f.write(f"Total Files Sent:  {len(saved_files)}\n")
            f.write("------------------------------------------\n")
            f.write("CUSTOMER INSTRUCTIONS / NOTES:\n")
            f.write(instructions.strip() if instructions.strip() else "None provided.")
            f.write("\n------------------------------------------\n")
            f.write("ATTACHED FILES:\n")
            for fname in saved_files:
                f.write(f" - {fname}\n")
            f.write("==========================================\n")

        # 6. ADD order.json
        meta_data = {}
        if order_meta:
            try:
                meta_data = json.loads(order_meta)
            except:
                pass

        # Merge core backend values with frontend metadata
        order_json_data = {
            "order_id": order_id,
            "order_number": order_number,
            "date": current_date_str,
            "time": datetime.now().strftime("%I:%M:%S %p"),
            "customer_name": customer_name,
            "preset_request": preset_request,
            "instructions": instructions.strip(),
            "total_files": len(saved_files),
            "files": saved_files,
            "status": meta_data.get("status", "Pending")
        }
        
        # Inject additional metadata provided by frontend estimates
        for k, v in meta_data.items():
            if k not in order_json_data:
                order_json_data[k] = v

        order_json_path = os.path.join(order_folder_path, "order.json")
        with open(order_json_path, "w", encoding="utf-8") as jf:
            json.dump(order_json_data, jf, indent=4)

    except Exception as e:
        # 22. FAILED UPLOADS - Clean up incomplete folder for THIS request only
        if os.path.exists(order_folder_path):
            shutil.rmtree(order_folder_path, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "status": "success",
        "message": "Order uploaded successfully!",
        "folder": order_folder_path,
        "total_files": len(saved_files),
        "order_id": order_id,
        "order_number": order_number,
        "date": current_date_str
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=80)