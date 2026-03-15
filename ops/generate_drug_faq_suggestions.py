import csv
import os
import re


ROOT_DIR = os.path.join(os.path.dirname(__file__), "..")
INPUT_FILE = os.path.join(ROOT_DIR, "data", "drugbank_vi.csv")
OUTPUT_FILE = os.path.join(ROOT_DIR, "data", "faq_drug_suggestions.csv")


MAX_ANSWER_LEN = 300
MAX_DRUGS = 60


def normalize_text(value: str):
    text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def shorten(text: str, max_len: int = MAX_ANSWER_LEN):
    src = normalize_text(text)
    if len(src) <= max_len:
        return src
    cut = src[:max_len]
    if ". " in cut:
        cut = cut.rsplit(". ", 1)[0]
    return cut.rstrip(" ,;:") + "..."


def clean_summary(text: str):
    src = normalize_text(text)
    src = re.sub(r"^[A-Za-z0-9 ._-]+\.\s*", "", src)
    src = src.replace("Chi dinh:", "")
    src = src.replace("Chi định:", "")
    src = src.replace("Co che:", "")
    src = src.replace("Co chế:", "")
    src = src.replace("Duoc luc hoc:", "")
    src = src.replace("Duoc lực học:", "")
    src = src.strip(" -:")
    return shorten(src)


def add_qa(rows_out, question: str, answer: str, tag: str):
    q = normalize_text(question)
    a = shorten(answer)
    if not q or not a:
        return
    rows_out.append({"question": q, "answer": a, "tag": tag})


def is_db_code_heavy(text: str):
    src = normalize_text(text)
    if not src:
        return False
    tokens = src.split(" ")
    if not tokens:
        return False
    db_tokens = [tok for tok in tokens if re.fullmatch(r"DB\d{5}", tok)]
    return len(db_tokens) >= 8 or (len(db_tokens) / max(1, len(tokens))) > 0.45


def build_suggestions(input_file: str, output_file: str):
    if not os.path.isfile(input_file):
        raise FileNotFoundError(f"Missing input file: {input_file}")

    rows_out = []
    used_drugs = 0
    seen_questions = set()

    with open(input_file, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if used_drugs >= MAX_DRUGS:
                break

            name = normalize_text(row.get("name", ""))
            if not name:
                continue

            summary = clean_summary(row.get("tom-tat-vi", ""))
            indication = shorten(row.get("chi-dinh-vi", ""))
            mechanism = shorten(row.get("co-che-vi", ""))
            toxicity = shorten(row.get("doc-tinh-vi", ""))
            interactions = shorten(row.get("tuong-tac-thuoc-vi", ""))
            if is_db_code_heavy(interactions):
                interactions = "Du lieu co thong tin tuong tac thuoc, ban nen cung cap them thuoc dang dung de tra cuu chinh xac hon."

            qas = [
                (
                    f"{name} dung de lam gi?",
                    f"{name}: {summary or indication or 'Du lieu cho thay thuoc co thong tin chi dinh dieu tri.'}",
                    "drug-usage",
                ),
                (
                    f"Co che tac dung cua {name} la gi?",
                    f"{name}: {mechanism or 'Du lieu co thong tin co che tac dung duoc ly, ban co the hoi them theo benh cu the.'}",
                    "drug-mechanism",
                ),
                (
                    f"{name} co tac dung phu hay doc tinh nao?",
                    f"{name}: {toxicity or 'Du lieu co thong tin an toan va tac dung khong mong muon, can tham khao bac si khi dung thuc te.'}",
                    "drug-safety",
                ),
                (
                    f"{name} co tuong tac thuoc nao can luu y?",
                    f"{name}: {interactions or 'Du lieu co thong tin tuong tac thuoc, ban nen cung cap them thuoc dang dung de tra cuu chinh xac hon.'}",
                    "drug-interaction",
                ),
            ]

            added_any = False
            for question, answer, tag in qas:
                if question in seen_questions:
                    continue
                add_qa(rows_out, question, answer, tag)
                seen_questions.add(question)
                added_any = True

            if added_any:
                used_drugs += 1

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["question", "answer", "tag"])
        writer.writeheader()
        writer.writerows(rows_out)

    return len(rows_out), used_drugs


if __name__ == "__main__":
    total_qas, total_drugs = build_suggestions(INPUT_FILE, OUTPUT_FILE)
    print(f"Da tao file: {OUTPUT_FILE}")
    print(f"So cau hoi: {total_qas}")
    print(f"So thuoc su dung: {total_drugs}")