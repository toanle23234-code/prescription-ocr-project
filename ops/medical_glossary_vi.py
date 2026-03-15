import re


# Ordered from longer/specific phrases to shorter/general terms.
MEDICAL_GLOSSARY_PATTERNS = [
    (r"\bacute coronary syndromes?\b", "hội chứng mạch vành cấp"),
    (r"\bmyocardial infarction\b", "nhồi máu cơ tim"),
    (r"\bunstable angina\b", "đau thắt ngực không ổn định"),
    (r"\bheparin-induced thrombocytopenia\b", "giảm tiểu cầu do heparin"),
    (r"\bthromboembolic\b", "huyết khối tắc mạch"),
    (r"\bthrombin inhibitor\b", "chất ức chế thrombin"),
    (r"\bblood-clotting cascade\b", "chuỗi đông máu"),
    (r"\bmetastatic colorectal cancer\b", "ung thư đại trực tràng di căn"),
    (r"\bnon-small cell lung cancer\b", "ung thư phổi không tế bào nhỏ"),
    (r"\bsquamous cell carcinoma\b", "ung thư biểu mô tế bào vảy"),
    (r"\bhead and neck cancer\b", "ung thư đầu cổ"),
    (r"\bradiation therapy\b", "xạ trị"),
    (r"\bplatinum-based therapy\b", "liệu pháp nền bạch kim"),
    (r"\bmonoclonal antibody\b", "kháng thể đơn dòng"),
    (r"\bepidermal growth factor receptor\b", "thụ thể yếu tố tăng trưởng biểu bì"),
    (r"\btyrosine kinase\b", "men tyrosine kinase"),
    (r"\bantibody-dependent cellular cytotoxicity\b", "độc tính tế bào phụ thuộc kháng thể"),
    (r"\binterstitial lung disease\b", "bệnh phổi kẽ"),
    (r"\bpulmonary edema\b", "phù phổi"),
    (r"\bcardiopulmonary arrest\b", "ngừng tim phổi"),
    (r"\binfusion reactions?\b", "phản ứng truyền dịch"),
    (r"\bcontraindicated\b", "chống chỉ định"),
    (r"\bindicated for\b", "được chỉ định cho"),
    (r"\bindicated to\b", "được chỉ định để"),
    (r"\bused for\b", "được dùng cho"),
    (r"\bused as\b", "được dùng như"),
    (r"\bused to\b", "được dùng để"),
    (r"\bto treat\b", "để điều trị"),
    (r"\bto prevent\b", "để phòng ngừa"),
    (r"\bmechanism of action\b", "cơ chế tác dụng"),
    (r"\bpharmacodynamics\b", "dược lực học"),
    (r"\bpharmacokinetics\b", "dược động học"),
    (r"\btoxicity\b", "độc tính"),
    (r"\bhalf-life\b", "thời gian bán thải"),
    (r"\bdrug interactions\b", "tương tác thuốc"),
    (r"\bfood interactions\b", "tương tác với thực phẩm"),
    (r"\bside effects\b", "tác dụng phụ"),
    (r"\badverse effects\b", "tác dụng không mong muốn"),
    (r"\badverse events\b", "biến cố bất lợi"),
    (r"\broute of elimination\b", "đường thải trừ"),
    (r"\bvolume of distribution\b", "thể tích phân bố"),
    (r"\bprotein binding\b", "tỷ lệ gắn protein"),
    (r"\bclearance\b", "độ thanh thải"),
    (r"\babsorption\b", "hấp thu"),
    (r"\bmetabolism\b", "chuyển hóa"),
    (r"\bintravenous\b", "đường tiêm tĩnh mạch"),
    (r"\bsubcutaneous\b", "đường tiêm dưới da"),
    (r"\bintramuscular\b", "đường tiêm bắp"),
    (r"\boral\b", "đường uống"),
    (r"\btopical\b", "đường bôi ngoài da"),
    (r"\bdiagnostic aid\b", "hỗ trợ chẩn đoán"),
    (r"\bclinical trial\b", "thử nghiệm lâm sàng"),
    (r"\bdouble-blind\b", "mù đôi"),
    (r"\bplacebo\b", "giả dược"),
    (r"\bhypertension\b", "tăng huyết áp"),
    (r"\bhypotension\b", "hạ huyết áp"),
    (r"\bhypoglycemia\b", "hạ đường huyết"),
    (r"\bdiabetes\b", "đái tháo đường"),
    (r"\binfection\b", "nhiễm trùng"),
    (r"\binflammation\b", "viêm"),
    (r"\bsevere\b", "nặng"),
    (r"\bmild\b", "nhẹ"),
    (r"\bchronic\b", "mạn tính"),
    (r"\bacute\b", "cấp tính"),
    (r"\bmetastatic\b", "di căn"),
    (r"\brecurrent\b", "tái phát"),
    (r"\btherapy\b", "liệu pháp"),
    (r"\btreatment\b", "điều trị"),
    (r"\banticoagulant\b", "thuốc chống đông"),
    (r"\banti[- ]?platelet\b", "chống kết tập tiểu cầu"),
    (r"\banticancer\b", "chống ung thư"),
    (r"\bpatient\b", "bệnh nhân"),
    (r"\bpatients\b", "bệnh nhân"),
]


def apply_medical_glossary(text: str):
    result = str(text or "")
    for pattern, replacement in MEDICAL_GLOSSARY_PATTERNS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    result = re.sub(r"\bis a\b", "là", result, flags=re.IGNORECASE)
    result = re.sub(r"\bis an\b", "là", result, flags=re.IGNORECASE)
    result = re.sub(r"\bare\b", "là", result, flags=re.IGNORECASE)
    result = re.sub(r"\s+", " ", result).strip()
    return result