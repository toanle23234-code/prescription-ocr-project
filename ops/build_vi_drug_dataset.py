import csv
import os
import re
from medical_glossary_vi import apply_medical_glossary


ROOT_DIR = os.path.join(os.path.dirname(__file__), "..")
INPUT_FILE = os.path.join(ROOT_DIR, "data", "drugbank_clean.csv")
OUTPUT_FILE = os.path.join(ROOT_DIR, "data", "drugbank_vi.csv")


MAX_FIELD_LEN = 520


def normalize_text(value: str):
    text = str(value or "")
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def shorten(text: str, max_len: int = MAX_FIELD_LEN):
    source = normalize_text(text)
    if len(source) <= max_len:
        return source
    cut = source[:max_len]
    if ". " in cut:
        cut = cut.rsplit(". ", 1)[0]
    return cut.rstrip(" ,;:") + "..."


def translate_light_to_vi(text: str):
    result = apply_medical_glossary(normalize_text(text))
    return normalize_text(result)


def summary_vi(row: dict):
    name = normalize_text(row.get("name"))
    indication_vi = translate_light_to_vi(row.get("indication", ""))
    moa_vi = translate_light_to_vi(row.get("mechanism-of-action", ""))
    pd_vi = translate_light_to_vi(row.get("pharmacodynamics", ""))

    clauses = []
    if indication_vi:
        clauses.append(f"Chỉ định: {shorten(indication_vi, 260)}")
    if moa_vi:
        clauses.append(f"Cơ chế: {shorten(moa_vi, 220)}")
    elif pd_vi:
        clauses.append(f"Dược lực học: {shorten(pd_vi, 220)}")

    if not clauses:
        return ""
    if name:
        return f"{name}. " + " ".join(clauses)
    return " ".join(clauses)


def build_keywords_vi(row: dict):
    tokens = []
    for key in ["name", "indication", "mechanism-of-action", "pharmacodynamics", "food-interactions", "drug-interactions"]:
        text = translate_light_to_vi(row.get(key, ""))
        text_tokens = re.findall(r"[0-9a-zA-Zà-ỹ]+", text.lower())
        for token in text_tokens:
            if len(token) < 3:
                continue
            tokens.append(token)

    unique = []
    seen = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        unique.append(token)
        if len(unique) >= 24:
            break
    return " ".join(unique)


def build_dataset(input_file: str, output_file: str):
    if not os.path.isfile(input_file):
        raise FileNotFoundError(f"Không tìm thấy file nguồn: {input_file}")

    rows_out = []
    with open(input_file, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = normalize_text(row.get("name"))
            drugbank_id = normalize_text(row.get("drugbank-id"))
            if not name or not drugbank_id:
                continue

            indication_vi = shorten(translate_light_to_vi(row.get("indication", "")))
            moa_vi = shorten(translate_light_to_vi(row.get("mechanism-of-action", "")))
            pd_vi = shorten(translate_light_to_vi(row.get("pharmacodynamics", "")))
            tox_vi = shorten(translate_light_to_vi(row.get("toxicity", "")))
            food_vi = shorten(translate_light_to_vi(row.get("food-interactions", "")), 240)
            interaction_vi = shorten(translate_light_to_vi(row.get("drug-interactions", "")), 320)
            summary = summary_vi(row)
            keywords = build_keywords_vi(row)

            # Keep only clinically useful rows to reduce retrieval noise.
            if not (summary or indication_vi or moa_vi):
                continue

            rows_out.append({
                "drugbank-id": drugbank_id,
                "name": name,
                "tom-tat-vi": summary,
                "chi-dinh-vi": indication_vi,
                "co-che-vi": moa_vi,
                "duoc-luc-hoc-vi": pd_vi,
                "doc-tinh-vi": tox_vi,
                "tuong-tac-thuc-an-vi": food_vi,
                "tuong-tac-thuoc-vi": interaction_vi,
                "keywords-vi": keywords,
                "tags": "thuoc drugbank",
            })

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    fieldnames = [
        "drugbank-id",
        "name",
        "tom-tat-vi",
        "chi-dinh-vi",
        "co-che-vi",
        "duoc-luc-hoc-vi",
        "doc-tinh-vi",
        "tuong-tac-thuc-an-vi",
        "tuong-tac-thuoc-vi",
        "keywords-vi",
        "tags",
    ]

    with open(output_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)

    return len(rows_out)


if __name__ == "__main__":
    count = build_dataset(INPUT_FILE, OUTPUT_FILE)
    print(f"Da tao: {OUTPUT_FILE}")
    print(f"So ban ghi: {count}")