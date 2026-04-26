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
    # These characters often replace vowel marks (matras)
    artifacts = {
        'Ǔ': 'ि',
        'ȣ': 'ी',
        'Ĥ': 'प्र',
        'ǽ': 'रु',
        'ȶ': 'े',
        'Đ': 'क्र',
        'ͧ': '', # Zero-width or combining marks
        'नाव[ ': 'नाव',
    }
    for art, repl in artifacts.items():
        text = text.replace(art, repl)
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
async def view_pdf(filename: str, page: int, q: str):
    pdf_path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(pdf_path):
        return {"error": "File not found"}
    
    try:
        doc = fitz.open(pdf_path)
        # Handle page bounds
        total_pages = len(doc)
        safe_page = max(1, min(page, total_pages))
        
        # Create a new single-page document for the requested page
        new_doc = fitz.open()
        new_doc.insert_pdf(doc, from_page=safe_page-1, to_page=safe_page-1)
        
        target_page = new_doc[0]
        
        # Find and highlight the text
        text_instances = target_page.search_for(q)
        for inst in text_instances:
            annot = target_page.add_highlight_annot(inst)
            annot.update()
            
        # Save to memory stream
        pdf_stream = io.BytesIO()
        new_doc.save(pdf_stream)
        new_doc.close()
        doc.close()
        pdf_stream.seek(0)
        
        return StreamingResponse(
            pdf_stream, 
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename={filename}",
                "X-Total-Pages": str(total_pages),
                "X-Current-Page": str(safe_page)
            }
        )
    except Exception as e:
        print(f"Error highlighting PDF: {e}")
        return {"error": str(e)}

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
    results = search_in_pdfs(q)
    return {"results": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
