import logging
import mimetypes
import numpy as np
import cv2
import io

log = logging.getLogger(__name__)

# Cache PaddleOCR instance
_ocr_instance = None

def get_ocr():
    global _ocr_instance
    if _ocr_instance is None:
        try:
            import os
            # Disable oneDNN (MKL-DNN) to prevent NotImplementedError in static graph execution on CPU
            os.environ["FLAGS_use_onednn"] = "0"
            os.environ["FLAGS_use_mkldnn"] = "0"
            
            from paddleocr import PaddleOCR
            # Suppress excessive logging from PaddleOCR
            logging.getLogger("ppocr").setLevel(logging.WARNING)
            _ocr_instance = PaddleOCR(
                lang='en',
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_mkldnn=False
            )
            log.info("PaddleOCR successfully initialized.")
        except Exception as e:
            log.error(f"Failed to initialize PaddleOCR: {e}", exc_info=True)
            _ocr_instance = None
    return _ocr_instance

def extract_text_from_image(image_bytes: bytes) -> str:
    """Extract raw text from image bytes using PaddleOCR."""
    ocr = get_ocr()
    if ocr is None:
        log.warning("PaddleOCR instance not available, skipping OCR")
        return ""
    try:
        # Decode image bytes to OpenCV format (numpy array)
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            log.error("Failed to decode image bytes using OpenCV")
            return ""
            
        # Run PaddleOCR on the image using predict() for compatibility with 3.x
        result = ocr.predict(img)
        texts = []
        if result:
            for item in result:
                if isinstance(item, dict):
                    # PaddleOCR 3.x dictionary format
                    if 'rec_texts' in item:
                        texts.extend(item['rec_texts'])
                elif isinstance(item, list):
                    # Legacy PaddleOCR format fallback
                    for element in item:
                        if isinstance(element, (list, tuple)) and len(element) > 1:
                            text_info = element[1]
                            if isinstance(text_info, (list, tuple)) and len(text_info) > 0:
                                texts.append(str(text_info[0]))
        return "\n".join(texts)
    except Exception as e:
        log.error(f"Error during PaddleOCR text extraction: {e}", exc_info=True)
        return ""

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes using pypdf."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        texts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                texts.append(text)
        return "\n".join(texts)
    except Exception as e:
        log.error(f"Error during PDF text extraction: {e}", exc_info=True)
        return ""

def extract_text_from_document(document_bytes: bytes, mime_type: str) -> str:
    """Helper to extract text from a document based on its MIME type."""
    if not document_bytes:
        return ""
    if "pdf" in mime_type.lower():
        return extract_text_from_pdf(document_bytes)
    else:
        return extract_text_from_image(document_bytes)
