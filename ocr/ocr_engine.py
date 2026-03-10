import pytesseract
import cv2
import numpy as np
import os
import shutil

# Tự tìm đường dẫn Tesseract (hoạt động trên mọi máy)
def find_tesseract():
    # Ưu tiên biến môi trường
    env_path = os.environ.get("TESSERACT_CMD")
    if env_path and os.path.isfile(env_path):
        return env_path
    # Tìm trong PATH
    found = shutil.which("tesseract")
    if found:
        return found
    # Các vị trí phổ biến trên Windows
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
else:
    print("CẢNH BÁO: Không tìm thấy Tesseract! Hãy cài đặt Tesseract-OCR.")


def deskew(image):
    """Chỉnh nghiêng ảnh"""
    coords = np.column_stack(np.where(image < 128))
    if len(coords) < 50:
        return image
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.5:
        return image
    h, w = image.shape
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_REPLICATE)
    return rotated


def preprocess_image(img):
    # Chuyển sang grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Phóng to ảnh 3x để Tesseract đọc rõ hơn (tối thiểu 300 DPI)
    h, w = gray.shape
    scale = max(1, 3000 // max(h, w))
    if scale > 1:
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

    # Khử nhiễu giữ cạnh chữ sắc nét
    gray = cv2.bilateralFilter(gray, 9, 75, 75)

    # Tăng độ tương phản bằng CLAHE
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    # Làm sắc nét ảnh
    sharpen_kernel = np.array([[-1, -1, -1],
                                [-1,  9, -1],
                                [-1, -1, -1]])
    gray = cv2.filter2D(gray, -1, sharpen_kernel)

    # Otsu threshold — tự chọn ngưỡng tối ưu
    _, gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Chỉnh nghiêng
    gray = deskew(gray)

    # Loại bỏ nhiễu nhỏ (đốm đen/trắng)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    gray = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
    gray = cv2.morphologyEx(gray, cv2.MORPH_OPEN, kernel)

    return gray


def extract_text(image_path):
    # Dùng numpy để đọc file, tránh lỗi đường dẫn có ký tự Unicode
    img_array = np.fromfile(image_path, dtype=np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    if img is None:
        return "Không thể đọc ảnh. Kiểm tra lại đường dẫn file."

    processed = preprocess_image(img)

    # Cấu hình Tesseract: OEM 1 = LSTM (neural net), PSM 4 = cột văn bản
    config = r"--oem 1 --psm 4"
    text = pytesseract.image_to_string(processed, lang="vie+eng", config=config)
    return text


if __name__ == "__main__":
    test_path = os.path.join(os.path.dirname(__file__), "..", "test.png")
    result = extract_text(test_path)
    print("===== OCR RESULT =====")
    print(result)