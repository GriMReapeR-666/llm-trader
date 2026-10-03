import pdfplumber
import sys

def extract_text_from_pdf(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = []
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    full_text.append(f"--- Page {i+1} ---")
                    full_text.append(text)
                    full_text.append("")  # empty line between pages
            return "\n".join(full_text)
    except Exception as e:
        return f"Error reading PDF: {e}"

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract_pdf.py <path_to_pdf>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    text = extract_text_from_pdf(pdf_path)
    
    # Write to file to avoid encoding issues
    output_file = "extracted_pdf_text.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Text extracted to {output_file}")
    print(f"Total pages: {text.count('--- Page')}")
    # Print first 2000 chars as preview
    print("\n--- PREVIEW (first 2000 chars) ---")
    print(text[:2000])