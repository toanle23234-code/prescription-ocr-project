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
    # Cap tối đa 1600px để giảm thời gian Tesseract trên server yếu
    target = 1600
    if max_side < target:
        scale = target / max_side
        new_w = int(w * scale)
        new_h = int(h * scale)
        return cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
    elif max_side > 2400:
        scale = 2400 / max_side
        new_w = int(w * scale)
        new_h = int(h * scale)
        return cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_AREA)
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
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = _resize_for_ocr(gray)
    gray = _deskew(gray)

    denoise = cv2.fastNlMeansDenoising(gray, None, 9, 7, 21)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(denoise)

    sharpen_kernel = np.array([[0, -1, 0],
                               [-1, 5, -1],
                               [0, -1, 0]])
    sharp = cv2.filter2D(clahe, -1, sharpen_kernel)

    otsu = cv2.threshold(sharp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    adaptive_mean = cv2.adaptiveThreshold(
        sharp,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY,
        31,
        11,
    )
    adaptive_gauss = cv2.adaptiveThreshold(
        sharp,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        9,
    )
    morph_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    morph_close = cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, morph_kernel, iterations=1)
    bordered = cv2.copyMakeBorder(sharp, 16, 16, 16, 16, cv2.BORDER_REPLICATE)

    return [
        ("otsu", otsu),
        ("adaptive_gauss", adaptive_gauss),
        ("clahe", clahe),
        ("bordered", bordered),
    ]


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
        (r"\bVién\b", "Viên"),
        (r"\bvién\b", "viên"),
        (r"\bViẻn\b", "Viên"),
        (r"\bviẻn\b", "viên"),
        (r"\bSang\b(?=\s*\d)", "Sáng"),
        (r"\bChiéu\b", "Chiều"),
        (r"\bchiéu\b", "chiều"),
        (r"\bTrua\b", "Trưa"),
        (r"\btrua\b", "trưa"),
        (r"\bTôi\b(?=\s*\d)", "Tối"),
        (r"\bNgay\b(?=\s*\d)", "Ngày"),
        (r"\bngay\b(?=\s*\d)", "ngày"),
        (r"\blan/ngay\b", "lần/ngày"),
        (r"\blan/ngày\b", "lần/ngày"),
        (r"\blần/ngay\b", "lần/ngày"),
        (r"\bduoi\s+dang\b", "dưới dạng"),
        (r"\bdưoi\s+dạng\b", "dưới dạng"),
    ]
    for pattern, replacement in vi_ocr_fixes:
        text = re.sub(pattern, replacement, text)

    # --- Fix spaced thousands: "400. 000" -> "400.000" ---
    text = re.sub(r"(\d+)\.\s+(\d{3})\b", r"\1.\2", text)

    # --- Clean OCR noise patterns ---
    text = re.sub(r"\s*-\s*\d+\s*-\.?", "", text)
    text = re.sub(r"\s*>>\s*", " ", text)
    text = re.sub(r"\s*<<\s*", " ", text)

    text = re.sub(r"\bmg\s*/\s*vien\b", "mg/viên", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(\d+)\s*([mM][gG]|[mM][lL]|[uU][iI])\b", r"\1 \2", text)
    text = re.sub(r"\b([xX])\s*(\d+)\s*(lan|lần)\s*/\s*(ngay|ngày)\b", r"x \2 lần/ngày", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(So|Số)\s*luong\b", "Số lượng", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(Don|Đơn)\s*thuoc\b", "Đơn thuốc", text, flags=re.IGNORECASE)

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _extract_with_confidence(image, lang, config):
    raw_text = pytesseract.image_to_string(image, lang=lang, config=config, timeout=5)
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
        "đơn thuốc", "ho ten", "họ tên", "tuoi", "tuổi", "chẩn đoán", "chuẩn đoán",
        "điều trị", "liều", "ngày", "uống", "sáng", "chiều", "tối", "huyết áp",
        "thân nhiệt", "địa chỉ", "điện thoại", "viên", "ống", "ml", "mg"
    ]
    normalized = unicodedata.normalize("NFD", text.lower())
    normalized = re.sub(r"[\u0300-\u036f]", "", normalized)
    keyword_hits = sum(1 for kw in medical_keywords if kw in normalized)

    repeated_noise_penalty = 0
    if re.search(r"(.)\1{4,}", text):
        repeated_noise_penalty += 6
    if noisy_symbol_count > max(3, len(text) * 0.03):
        repeated_noise_penalty += 8

    conf_score = avg_conf * 0.55
    quality_score = alnum_ratio * 26 + vi_ratio * 8 + min(line_count, 18) * 1.1 + min(keyword_hits, 10) * 2.2
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
    img_array = np.fromfile(image_path, dtype=np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    if img is None:
        return "Không thể đọc ảnh. Kiểm tra lại đường dẫn file."

    try:
        cropped_img = _auto_crop_document(img)
        source_images = [img]
        if cropped_img.shape[:2] != img.shape[:2]:
            source_images.append(cropped_img)

        languages = _build_language_list()
        # Giảm xuống 2 config tốt nhất để chạy nhanh trên free tier
        configs = [
            r"--oem 1 --psm 6 -c preserve_interword_spaces=1 -c user_defined_dpi=300",
            r"--oem 1 --psm 4 -c preserve_interword_spaces=1 -c user_defined_dpi=300",
        ]

        best_text = ""
        best_score = -1.0
        attempts = 0
        max_attempts = 4

        for source in source_images:
            variants = preprocess_variants(source)
            for _, processed in variants:
                for lang in languages:
                    for config in configs:
                        attempts += 1
                        if attempts > max_attempts:
                            break
                        try:
                            text, score = _extract_with_confidence(processed, lang=lang, config=config)
                        except RuntimeError:
                            # Tesseract timeout for this pass; continue with remaining strategies.
                            continue

                        if score > best_score and text:
                            best_score = score
                            best_text = text
                            # Fast early-stop when text quality is already good.
                            if best_score >= 70:
                                return best_text
                    if attempts > max_attempts:
                        break
                if attempts > max_attempts:
                    break
            if attempts > max_attempts:
                break

        if best_text:
            return best_text

        return "Không thể trích xuất văn bản rõ ràng từ ảnh. Hãy thử ảnh rõ hơn hoặc chụp thẳng góc."
    except pytesseract.TesseractNotFoundError:
        return "Lỗi: Hệ thống chưa được cài đặt phần mềm Tesseract OCR!"
    except Exception as e:
        return f"Đã xảy ra lỗi trong quá trình đọc ảnh: {str(e)}"

