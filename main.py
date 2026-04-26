import os
import fitz
from typing import List, Dict
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from fastapi.staticfiles import StaticFiles

app = FastAPI()

# Get absolute path of the current directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Serve PDF files as static files
app.mount("/static_pdfs", StaticFiles(directory=BASE_DIR), name="static_pdfs")

import re

def normalize_text(text: str) -> str:
    # Remove common PDF encoding artifacts for Marathi/Hindi
    artifacts = {
        'Ǔ': 'ि',
        'ȣ': 'ी',
        'Ĥ': 'प्र',
        'ǽ': 'रु',
        'ȶ': 'े',
        'Đ': 'क्र',
        'ͧ': '', 
        'नाव[ ': 'नाव',
        '७वŮ': ' ', # Seen in screenshot
        'नाल४': ' ', # Seen in screenshot
        'Ů': '',
        '४': '',
        'Ó': 'ो',
        'Ò': 'ो',
        'Ô': 'ो',
        'Ö': 'ौ',
        '×': 'ु',
    }
    for art, repl in artifacts.items():
        text = text.replace(art, repl)
    
    # Remove any remaining non-Marathi/ASCII characters that might be artifacts
    text = re.sub(r'[^\u0900-\u097F\s\w]', '', text)
    return text

# Global cache for PDF data
PDF_CACHE = []

def preload_pdfs():
    global PDF_CACHE
    PDF_CACHE = []
    pdf_files = [f for f in os.listdir(BASE_DIR) if f.lower().endswith('.pdf')]
    print(f"Preloading {len(pdf_files)} PDF(s)...")
    
    for pdf_file in pdf_files:
        pdf_path = os.path.join(BASE_DIR, pdf_file)
        try:
            doc = fitz.open(pdf_path)
            file_data = {"filename": pdf_file, "pages": []}
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                text = page.get_text()
                lines = [line.strip() for line in text.split('\n') if line.strip()]
                
                # Store original and normalized text for fast searching
                page_data = {
                    "number": page_num + 1,
                    "original_lines": lines,
                    "clean_text": normalize_text(text.lower()),
                    "clean_lines": [normalize_text(line.lower()) for line in lines]
                }
                file_data["pages"].append(page_data)
            PDF_CACHE.append(file_data)
            doc.close()
            print(f"Loaded {pdf_file}")
        except Exception as e:
            print(f"Error preloading {pdf_file}: {e}")

def search_in_pdfs(query: str) -> List[Dict]:
    results = []
    clean_query = normalize_text(query.lower())
    
    for file_data in PDF_CACHE:
        for page in file_data["pages"]:
            if clean_query in page["clean_text"]:
                for i, clean_line in enumerate(page["clean_lines"]):
                    if clean_query in clean_line:
                        snippet = page["original_lines"][i]
                        if i + 1 < len(page["original_lines"]):
                            snippet += " " + page["original_lines"][i+1]
                        
                        results.append({
                            "filename": file_data["filename"],
                            "page": page["number"],
                            "snippets": [snippet]
                        })
    return results

from fastapi.responses import HTMLResponse, StreamingResponse
import io

@app.get("/view_pdf")
async def view_pdf(filename: str, page: int, q: str = None):
    pdf_path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(pdf_path):
        print(f"File not found: {pdf_path}")
        return HTMLResponse(content=f"File {filename} not found on server", status_code=404)
    
    doc = None
    new_doc = None
    try:
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        safe_page = max(1, min(page, total_pages))
        
        # Create a new document for the single page
        new_doc = fitz.open()
        new_doc.insert_pdf(doc, from_page=safe_page-1, to_page=safe_page-1)
        target_page = new_doc[0]
        
        # Find and highlight the text
        if q and len(q) >= 2:
            try:
                # search_for can be slow or fail on some PDFs
                text_instances = target_page.search_for(q)
                for inst in text_instances:
                    annot = target_page.add_highlight_annot(inst)
                    annot.update()
            except Exception as e:
                print(f"Highlighting failed (skipping): {e}")
            
        # Get PDF bytes with compression
        pdf_bytes = new_doc.tobytes(garbage=3, deflate=True)
        
        return StreamingResponse(
            io.BytesIO(pdf_bytes), 
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename={filename}",
                "Cache-Control": "public, max-age=3600",
                "X-Total-Pages": str(total_pages)
            }
        )
    except Exception as e:
        print(f"Error processing PDF {filename} page {page}: {e}")
        import traceback
        traceback.print_exc()
        return HTMLResponse(content=f"PDF Processing Error: {str(e)}", status_code=500)
    finally:
        if new_doc:
            new_doc.close()
        if doc:
            doc.close()

@app.on_event("startup")
async def startup_event():
    preload_pdfs()

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    pdf_files = [f for f in os.listdir(BASE_DIR) if f.lower().endswith('.pdf')]
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={"pdf_count": len(pdf_files), "pdf_names": pdf_files}
    )

@app.get("/search")
async def search(q: str = Query(...)):
    if not q or len(q) < 2:
        return {"results": []}
    
    global PDF_CACHE
    if not PDF_CACHE:
        print("PDF_CACHE is empty, preloading...")
        preload_pdfs()
        
    results = search_in_pdfs(q)
    return {"results": results}

@app.get("/health")
async def health():
    pdf_files = [f for f in os.listdir(BASE_DIR) if f.lower().endswith('.pdf')]
    return {
        "status": "ok",
        "pdfs_found": pdf_files,
        "cache_size": len(PDF_CACHE),
        "os": os.name
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
