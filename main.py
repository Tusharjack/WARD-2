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

def search_in_pdfs(query: str) -> List[Dict]:
    results = []
    # Use absolute path to find PDFs
    pdf_files = [f for f in os.listdir(BASE_DIR) if f.lower().endswith('.pdf')]
    print(f"Searching for '{query}' in {len(pdf_files)} PDF files at {BASE_DIR}")
    
    for pdf_file in pdf_files:
        pdf_path = os.path.join(BASE_DIR, pdf_file)
        try:
            doc = fitz.open(pdf_path)
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                text = page.get_text()
                
                if query.lower() in text.lower():
                    # Find snippets
                    lines = [line.strip() for line in text.split('\n') if line.strip()]
                    for i, line in enumerate(lines):
                        if query.lower() in line.lower():
                            # Get a bit of context (current line and next line usually contains the name/ID)
                            snippet = line
                            if i + 1 < len(lines):
                                snippet += " " + lines[i+1]
                            
                            results.append({
                                "filename": pdf_file,
                                "page": page_num + 1,
                                "snippets": [snippet]
                            })
                            # Once we find a match on a page, we could continue or break. 
                            # For voter lists, multiple people might match (e.g. same surname).
                            # Let's not break, but let's avoid too many duplicate snippets for the same ID.
            doc.close()
        except Exception as e:
            print(f"Error processing {pdf_file}: {e}")
            
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
        # We only highlight the specific page to keep it fast
        target_page = doc[page - 1]
        
        # Find and highlight the text
        text_instances = target_page.search_for(q)
        for inst in text_instances:
            annot = target_page.add_highlight_annot(inst)
            annot.update()
            
        # Save to memory stream
        pdf_stream = io.BytesIO()
        doc.save(pdf_stream)
        doc.close()
        pdf_stream.seek(0)
        
        return StreamingResponse(
            pdf_stream, 
            media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename={filename}"}
        )
    except Exception as e:
        print(f"Error highlighting PDF: {e}")
        return {"error": str(e)}

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
