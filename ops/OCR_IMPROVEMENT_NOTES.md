# OCR Improvement Notes

## What was improved
- Auto-crop document region before OCR to reduce noisy background.
- Stronger preprocessing variants (adaptive threshold, morphology, border padding).
- Multi-pass OCR with multiple PSM modes and quality scoring.
- Medical-domain post-processing to normalize common OCR mistakes.

## How to measure real accuracy
1. Create folder `benchmark_samples` in project root.
2. Put image + ground truth text with same filename stem:
   - `sample_01.jpg`
   - `sample_01.txt`
3. Run:

```powershell
python ops/evaluate_ocr.py --dataset benchmark_samples
```

4. Check report:
- `ops/autofix_artifacts/ocr_eval_report.json`

## Interpreting results
- `average_accuracy` close to `0.90` means roughly 90% character-level accuracy.
- `exact_match_rate` is strict and usually lower than average accuracy.
