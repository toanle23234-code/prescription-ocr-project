import argparse
import json
import re
import unicodedata
from pathlib import Path

from ocr.ocr_engine import extract_text


def normalize_text(text):
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def levenshtein_distance(a, b):
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            insert_cost = curr[j - 1] + 1
            delete_cost = prev[j] + 1
            replace_cost = prev[j - 1] + (0 if ca == cb else 1)
            curr.append(min(insert_cost, delete_cost, replace_cost))
        prev = curr
    return prev[-1]


def cer(pred, truth):
    pred_n = normalize_text(pred)
    truth_n = normalize_text(truth)
    if not truth_n:
        return 0.0 if not pred_n else 1.0
    return levenshtein_distance(pred_n, truth_n) / max(len(truth_n), 1)


def collect_image_files(folder):
    patterns = ["*.png", "*.jpg", "*.jpeg", "*.bmp", "*.tiff"]
    files = []
    for pattern in patterns:
        files.extend(folder.glob(pattern))
    return sorted(files)


def evaluate(dataset_dir):
    dataset = Path(dataset_dir)
    if not dataset.exists():
        raise FileNotFoundError(f"Dataset folder not found: {dataset}")

    images = collect_image_files(dataset)
    if not images:
        raise RuntimeError("No image files found in dataset folder")

    results = []
    for image_path in images:
        truth_path = image_path.with_suffix(".txt")
        if not truth_path.exists():
            continue

        truth = truth_path.read_text(encoding="utf-8", errors="replace")
        pred = extract_text(str(image_path))
        sample_cer = cer(pred, truth)
        results.append(
            {
                "image": image_path.name,
                "truth": truth_path.name,
                "cer": round(sample_cer, 4),
                "accuracy": round(max(0.0, 1.0 - sample_cer), 4),
            }
        )

    if not results:
        raise RuntimeError("No matching .txt ground-truth files were found")

    avg_cer = sum(item["cer"] for item in results) / len(results)
    avg_acc = max(0.0, 1.0 - avg_cer)
    em_count = sum(1 for item in results if item["cer"] == 0.0)

    return {
        "samples": len(results),
        "average_cer": round(avg_cer, 4),
        "average_accuracy": round(avg_acc, 4),
        "exact_match_count": em_count,
        "exact_match_rate": round(em_count / len(results), 4),
        "details": results,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate OCR quality on a labeled dataset.")
    parser.add_argument("--dataset", default="benchmark_samples", help="Folder containing images and same-name .txt ground truth")
    parser.add_argument("--output", default="ops/autofix_artifacts/ocr_eval_report.json", help="Output report path")
    args = parser.parse_args()

    report = evaluate(args.dataset)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Samples: {report['samples']}")
    print(f"Average accuracy: {report['average_accuracy'] * 100:.2f}%")
    print(f"Average CER: {report['average_cer'] * 100:.2f}%")
    print(f"Exact match rate: {report['exact_match_rate'] * 100:.2f}%")
    print(f"Saved report: {out_path}")


if __name__ == "__main__":
    main()
