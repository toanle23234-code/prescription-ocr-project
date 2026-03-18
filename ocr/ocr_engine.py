import pytesseract
import cv2
import numpy as np
import os
import shutil
import re
import unicodedata

def find_tesseract():
    env_path = os.environ.get("TESSERACT_CMD")
    if env_path and os.path.isfile(env_path):
        return env_path
    found = shutil.which("tesseract")
    if found:
        return found
    common_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for p in common_paths:
        if os.path.isfile(p):
            return p
    return None

tesseract_path = find_tesseract()
if tesseract_path:
    pytesseract.pytesseract.tesseract_cmd = tesseract_path

def _resize_for_ocr(gray):
    h, w = gray.shape[:2]
    max_side = max(h, w)
    # Cap 900px: nhanh nhất cho Render free tier 0.1 vCPU
    cap = 900
    if max_side > cap:
        scale = cap / max_side
        new_w = int(w * scale)
        new_h = int(h * scale)
        return cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_AREA)
    if max_side < 500:
        scale = 500 / max_side
        new_w = int(w * scale)
        new_h = int(h * scale)
        return cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
    return gray


def _deskew(gray):
    # Estimate skew angle from text foreground and rotate back.
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.bitwise_not(th)
    coords = np.column_stack(np.where(th > 0))
    if coords.size == 0:
        return gray

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    # Ignore tiny rotation noise.
    if abs(angle) < 0.5:
        return gray

    h, w = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(gray, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _auto_crop_document(img):
    h, w = img.shape[:2]
    if h < 180 or w < 180:
        return img

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 60, 180)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img

    min_area = h * w * 0.22
    best_rect = None
    best_area = 0

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue
        x, y, cw, ch = cv2.boundingRect(cnt)
        if cw < 0.35 * w or ch < 0.35 * h:
            continue
        rect_area = cw * ch
        if rect_area > best_area:
            best_area = rect_area
            best_rect = (x, y, cw, ch)

    if not best_rect:
        return img

    x, y, cw, ch = best_rect
    pad_x = int(cw * 0.02)
    pad_y = int(ch * 0.02)
    x1 = max(0, x - pad_x)
    y1 = max(0, y - pad_y)
    x2 = min(w, x + cw + pad_x)
    y2 = min(h, y + ch + pad_y)

    cropped = img[y1:y2, x1:x2]
    if cropped.size == 0:
        return img
    return cropped


def preprocess_variants(img):
    """Generate multiple preprocessing variants for OCR testing"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = _resize_for_ocr(gray)
    
    variants = []
    
    # Variant 1: CLAHE + Otsu (original)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    otsu = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    variants.append(("clahe_otsu", otsu))
    
    # Variant 2: More aggressive CLAHE
    clahe_strong = cv2.createCLAHE(clipLimit=3.5, tileGridSize=(6, 6)).apply(gray)
    otsu_strong = cv2.threshold(clahe_strong, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    variants.append(("clahe_strong_otsu", otsu_strong))
    
    # Variant 3: Simple Otsu without CLAHE
    _, otsu_simple = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(("simple_otsu", otsu_simple))
    
    # Variant 4: Adaptive threshold
    adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                      cv2.THRESH_BINARY, 11, 2)
    variants.append(("adaptive", adaptive))
    
    # Variant 5: Dialate + Erode to connect text
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    dilated = cv2.dilate(otsu, kernel, iterations=1)
    variants.append(("dilated", dilated))
    
    return variants


def _post_process_text(text):
    replacements = {
        "®": "", "€": "", "—": "-", "|": "", "_": "", "[": "", "]": "",
        "l?n/ng?y": "lần/ngày", "v?ên": "viên", "tuíp": "tuýp",
        "Ho ten": "Họ tên", "Họ tên": "Họ tên", "Hoten": "Họ tên",
        "Huyet ap": "Huyết áp", "Than nhiet": "Thân nhiệt", "Dien thoai": "Điện thoại",
        "Chan doan": "Chẩn đoán", "Chuan doan": "Chuẩn đoán", "Dieu tri": "Điều trị",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    # --- Fix Vietnamese OCR character errors ---
    vi_ocr_fixes = [
        (r"\bUéng\b", "Uống"),
        (r"\buéng\b", "uống"),
        (r"\bUêng\b", "Uống"),
        (r"\buêng\b", "uống"),
        (r"\bUong\b", "Uống"),
        (r"\buong\b", "uống"),
        (r"\bUñng\b", "Uống"),
        (r"\buñng\b", "uống"),
        (r"\bUệng\b", "Uống"),
        (r"\buệng\b", "uống"),
        (r"\bVién\b", "Viên"),
        (r"\bvién\b", "viên"),
        (r"\bViẻn\b", "Viên"),
        (r"\bviẻn\b", "viên"),
        (r"\bVien\b", "Viên"),
        (r"\bvien\b", "viên"),
        (r"\bViền\b", "Viên"),
        (r"\bviền\b", "viên"),
        (r"\bVỉen\b", "Viên"),
        (r"\bvỉen\b", "viên"),
        (r"\bSang\b(?=\s+\d|$)", "Sáng"),
        (r"\bSáng\b(?=\s+\d|$)", "Sáng"),
        (r"\bChiéu\b", "Chiều"),
        (r"\bchiéu\b", "chiều"),
        (r"\bChieu\b", "Chiều"),
        (r"\bchieu\b", "chiều"),
        (r"\bChie\b", "Chiều"),
        (r"\bchie\b", "chiều"),
        (r"\bTrua\b", "Trưa"),
        (r"\btrua\b", "trưa"),
        (r"\bTrưa\b", "Trưa"),
        (r"\btrưa\b", "trưa"),
        (r"\bTrừa\b", "Trưa"),
        (r"\btrừa\b", "trưa"),
        (r"\bTôi\b(?=\s+\d|$)", "Tối"),
        (r"\btôi\b(?=\s+\d|$)", "tối"),
        (r"\bToi\b(?=\s+\d|$)", "Tối"),
        (r"\btoi\b(?=\s+\d|$)", "tối"),
        (r"\bTỏi\b(?=\s+\d|$)", "Tối"),
        (r"\btỏi\b(?=\s+\d|$)", "tối"),
        (r"\bNgay\b(?=\s*\d)", "Ngày"),
        (r"\bngay\b(?=\s*\d)", "ngày"),
        (r"\bNgày\b", "Ngày"),
        (r"\bngày\b", "ngày"),
        (r"\bNghay\b", "Ngày"),
        (r"\bnghay\b", "ngày"),
        (r"\blan/ngay\b", "lần/ngày"),
        (r"\blan/ngày\b", "lần/ngày"),
        (r"\blần/ngay\b", "lần/ngày"),
        (r"\bLan/ngay\b", "Lần/ngày"),
        (r"\bLan/ngày\b", "Lần/ngày"),
        (r"\bLần/ngay\b", "Lần/ngày"),
        (r"\bduoi\s+dang\b", "dưới dạng"),
        (r"\bdưoi\s+dạng\b", "dưới dạng"),
        (r"\bDuoi\s+dang\b", "Dưới dạng"),
        (r"\bDưoi\s+dạng\b", "Dưới dạng"),
        (r"\bduom\b", "dưới"),
        (r"\bDon\s+thuoc\b", "Đơn thuốc"),
        (r"\bdon\s+thuoc\b", "đơn thuốc"),
        (r"\bDon\s+Thuoc\b", "Đơn thuốc"),
        (r"\bĐon\s+thuoc\b", "Đơn thuốc"),
        (r"\bđon\s+thuoc\b", "đơn thuốc"),
        (r"\bHo\s+ten\b", "Họ tên"),
        (r"\bho\s+ten\b", "họ tên"),
        (r"\bHo\s+Ten\b", "Họ tên"),
        (r"\bHô\s+tên\b", "Họ tên"),
        (r"\bhô\s+tên\b", "họ tên"),
        (r"\bTuoi\b", "Tuổi"),
        (r"\btuoi\b", "tuổi"),
        (r"\bTuôi\b", "Tuổi"),
        (r"\btuôi\b", "tuổi"),
        (r"\bTuỏi\b", "Tuổi"),
        (r"\btuỏi\b", "tuổi"),
        (r"\bChan\s+doan\b", "Chẩn đoán"),
        (r"\bchan\s+doan\b", "chẩn đoán"),
        (r"\bChan\s+Doan\b", "Chẩn đoán"),
        (r"\bChuan\s+doan\b", "Chuẩn đoán"),
        (r"\bchuan\s+doan\b", "chuẩn đoán"),
        (r"\bChuan\s+Doan\b", "Chuẩn đoán"),
        (r"\bHuyet\s+ap\b", "Huyết áp"),
        (r"\bhuyet\s+ap\b", "huyết áp"),
        (r"\bThan\s+nhiet\b", "Thân nhiệt"),
        (r"\bthan\s+nhiet\b", "thân nhiệt"),
        (r"\bThan\s+Nhiet\b", "Thân nhiệt"),
        (r"\bLieu\b", "Liều"),
        (r"\blieu\b", "liều"),
        (r"\bLiều\b", "Liều"),
        (r"\bliều\b", "liều"),
        (r"\bLuong\b", "Liều"),
        (r"\bluong\b", "liều"),
        (r"\bSo\s+luong\b", "Số lượng"),
        (r"\bso\s+luong\b", "số lượng"),
        (r"\bSo\s+Luong\b", "Số lượng"),
        (r"\bDia\s+chi\b", "Địa chỉ"),
        (r"\bdia\s+chi\b", "địa chỉ"),
        (r"\bDia\s+Chi\b", "Địa chỉ"),
        (r"\bDieu\s+tri\b", "Điều trị"),
        (r"\bdieu\s+tri\b", "điều trị"),
        (r"\bDieu\s+Tri\b", "Điều trị"),
        (r"\bOng\b", "Ống"),
        (r"\bong\b", "ống"),
        (r"\bTuip\b", "Tuýp"),
        (r"\btuip\b", "tuýp"),
        (r"\bTup\b", "Tuýp"),
        (r"\btup\b", "tuýp"),
    ]
    for pattern, replacement in vi_ocr_fixes:
        text = re.sub(pattern, replacement, text)

    # --- Fix spaced thousands: "400. 000" -> "400.000" ---
    text = re.sub(r"(\d+)\.\s+(\d{3})\b", r"\1.\2", text)

    # --- Clean OCR noise patterns ---
    text = re.sub(r"\s*-\s*\d+\s*-\.?", "", text)
    text = re.sub(r"\s*>>\s*", " ", text)
    text = re.sub(r"\s*<<\s*", " ", text)
    text = re.sub(r"\s*\|\s*", " ", text)

    text = re.sub(r"\bmg\s*/\s*vien\b", "mg/viên", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(\d+)\s*([mM][gG]|[mM][lL]|[uU][iI])\b", r"\1 \2", text)
    text = re.sub(r"\b([xX])\s*(\d+)\s*(lan|lần)\s*/\s*(ngay|ngày)\b", r"x \2 lần/ngày", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(So|Số)\s*luong\b", "Số lượng", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(Don|Đơn)\s*thuoc\b", "Đơn thuốc", text, flags=re.IGNORECASE)

    # Fix repeated characters (OCR noise) - keep max 2
    text = re.sub(r"([a-z])\1{5,}", r"\1\1", text, flags=re.IGNORECASE)
    
    # Normalize whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    text = re.sub(r" +\n", "\n", text)
    text = re.sub(r"\n +", "\n", text)
    
    return text.strip()


def _extract_with_confidence(image, lang, config):
    raw_text = pytesseract.image_to_string(image, lang=lang, config=config, timeout=45)
    text = _post_process_text(raw_text)
    score = _score_ocr_text(text, 0.0)
    return text, score


def _score_ocr_text(text, avg_conf):
    if not text:
        return 0.0

    alnum_count = len(re.findall(r"[A-Za-z0-9À-ỹ]", text))
    alpha_count = len(re.findall(r"[A-Za-zÀ-ỹ]", text))
    vi_count = len(re.findall(r"[À-ỹ]", text))
    noisy_symbol_count = len(re.findall(r"[@#$%^*_~=<>`{}\\]", text))

    alnum_ratio = alnum_count / max(len(text), 1)
    vi_ratio = vi_count / max(alpha_count, 1)
    line_count = len([ln for ln in text.splitlines() if len(ln.strip()) >= 3])

    medical_keywords = [
        "đơn thuốc", "đơn thuocs", "ho ten", "họ tên", "tuoi", "tuổi", "chẩn đoán", "chuẩn đoán",
        "điều trị", "liều", "ngày", "uống", "sáng", "chiều", "tối", "huyết áp",
        "thân nhiệt", "địa chỉ", "điện thoại", "viên", "ống", "ml", "mg", "ui",
        "lần/ngày", "lần/ngay", "dưới dạng", "duoi dang",
        "sang", "chiều", "tối", "trưa", "trua",
        "bác sĩ", "bác si", "kê đơn", "ke don", "cơ sở", "co so",
        "thuốc", "thuoc", "bệnh", "benh", "nhân", "nhan", "bệnh nhân", "benh nhan",
        "toa thuoc", "tòa thuốc", "toá thuốc", "đơn", "don",
        "sáng", "sang", "chiều", "chieu", "tối", "toi", "trưa", "trua",
        "số", "so", "lượng", "luong", "liều lượng", "don thuoc", "thuoc",
    ]
    normalized = unicodedata.normalize("NFD", text.lower())
    normalized = re.sub(r"[\u0300-\u036f]", "", normalized)
    keyword_hits = sum(1 for kw in medical_keywords if kw in normalized)

    repeated_noise_penalty = 0
    if re.search(r"(.)\1{4,}", text):
        repeated_noise_penalty += 3  # Reduced penalty
    if noisy_symbol_count > max(3, len(text) * 0.05):  # More lenient
        repeated_noise_penalty += 4

    conf_score = avg_conf * 0.40  # Lower confidence weight
    # More aggressive scoring - accept more text
    quality_score = alnum_ratio * 15 + vi_ratio * 5 + min(line_count, 15) * 0.8 + min(keyword_hits, 10) * 1.5
    score = conf_score + quality_score - repeated_noise_penalty
    return float(score)


def _build_language_list():
    try:
        available = set(pytesseract.get_languages(config=""))
    except Exception:
        available = set()

    if "vie" in available and "eng" in available:
        return ["vie+eng", "eng+vie"]
    if "vie" in available:
        return ["vie"]
    if "eng" in available:
        return ["eng"]
    return ["eng"]

def extract_text(image_path):
    import logging
    logger = logging.getLogger(__name__)
    img_array = np.fromfile(image_path, dtype=np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    if img is None:
        return "Không thể đọc ảnh. Kiểm tra lại đường dẫn file."
    logger.info("OCR: image loaded, shape=%s", img.shape)

    try:
        languages = _build_language_list()
        lang = languages[0] if languages else "vie+eng"
        logger.info("OCR: lang=%s, languages_available=%s", lang, languages)

        # Try different PSM modes
        psm_configs = [
            (11, r"--oem 1 --psm 11 -c preserve_interword_spaces=1 -c user_defined_dpi=300"),
            (6, r"--oem 1 --psm 6 -c preserve_interword_spaces=1 -c user_defined_dpi=300"),
            (3, r"--oem 1 --psm 3 -c preserve_interword_spaces=1 -c user_defined_dpi=300"),
        ]
        
        best_text = None
        best_score = 0

        # Phase 1: Try all preprocessing variants with PSM 11
        variants = preprocess_variants(img)
        logger.info("OCR: trying %d preprocessing variants", len(variants))
        
        for variant_name, processed in variants:
            for psm_mode, config in psm_configs[:1]:  # Try PSM 11 with each variant first
                try:
                    text, score = _extract_with_confidence(processed, lang=lang, config=config)
                    logger.info("OCR variant=%s psm=%d: score=%.1f, len=%d", variant_name, psm_mode, score, len(text or ''))
                    
                    if text and score >= 5:  # Lower threshold to accept more text
                        return text
                    
                    if text and score > best_score:
                        best_score = score
                        best_text = text
                except RuntimeError as e:
                    logger.warning("OCR variant=%s psm=%d error: %s", variant_name, psm_mode, e)

        # Phase 2: If no good result yet, try crop + variants with multiple PSM
        if not best_text or best_score < 5:
            try:
                cropped = _auto_crop_document(img)
                if cropped.shape[:2] != img.shape[:2]:
                    logger.info("OCR: document cropped, new shape=%s", cropped.shape)
                    variants_c = preprocess_variants(cropped)
                    
                    for variant_name, processed in variants_c:
                        for psm_mode, config in psm_configs:
                            try:
                                text, score = _extract_with_confidence(processed, lang=lang, config=config)
                                logger.info("OCR crop variant=%s psm=%d: score=%.1f, len=%d", variant_name, psm_mode, score, len(text or ''))
                                
                                if text and score >= 5:
                                    return text
                                
                                if text and score > best_score:
                                    best_score = score
                                    best_text = text
                            except RuntimeError as e:
                                logger.warning("OCR crop variant=%s psm=%d error: %s", variant_name, psm_mode, e)
            except Exception as e:
                logger.warning("OCR crop phase error: %s", e)

        # Phase 3: Direct gray OCR with different PSM
        try:
            gray_fb = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            gray_fb = _resize_for_ocr(gray_fb)
            
            for psm_mode, config in psm_configs:
                try:
                    raw = pytesseract.image_to_string(gray_fb, lang=lang, config=config, timeout=45)
                    raw = _post_process_text(raw)
                    if raw and len(raw.strip()) >= 5:
                        logger.info("OCR gray psm=%d: len=%d", psm_mode, len(raw))
                        return raw
                except Exception as e:
                    logger.warning("OCR gray psm=%d error: %s", psm_mode, e)
        except Exception as e:
            logger.warning("OCR gray phase error: %s", e)

        # Return best result found, even if score is low
        if best_text:
            logger.info("OCR: returning best result with score=%.1f", best_score)
            return best_text

        return "Không thể trích xuất văn bản rõ ràng từ ảnh. Hãy thử ảnh rõ hơn hoặc chụp thẳng góc."
    except pytesseract.TesseractNotFoundError:
        return "Lỗi: Hệ thống chưa được cài đặt phần mềm Tesseract OCR!"
    except Exception as e:
        logger.exception("OCR error: %s", e)
        return f"Đã xảy ra lỗi trong quá trình đọc ảnh: {str(e)}"

