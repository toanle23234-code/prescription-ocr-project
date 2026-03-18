#!/usr/bin/env python3
"""
Test script for OCR improvements
"""
import sys
sys.path.insert(0, '.')

from ocr.ocr_engine import _post_process_text

# Test cases with common OCR errors
test_cases = [
    # Vietnamese character errors
    {
        "input": "Uéng 2 viên sáng chiều tối, 2 lần/ngày dưới dạng viên",
        "expected": "uống 2 viên sáng chiều tối, 2 lần/ngày dưới dạng viên"
    },
    {
        "input": "Ho ten: Nguyễn Văn A\nTuoi: 30\nChẩn đoán: Cảm cơ",
        "expected": "Họ tên: Nguyễn Văn A\nTuổi: 30\nChẩn đoán: Cảm cơ"
    },
    {
        "input": "Đơn thuốc: 500 mg x 3 lần/ngay",
        "expected": "Đơn thuốc: 500 mg x 3 lần/ngày"
    },
    {
        "input": "Duoi dang tablet, 2 vien sáng, 2 vien chiều",
        "expected": "dưới dạng tablet, 2 viên sáng, 2 viên chiều"
    },
    {
        "input": "Sáng 1 viên, Trua 1 viên, Chiều 1 viên, Tôi 1 viên",
        "expected": "sáng 1 viên, trưa 1 viên, chiều 1 viên, tối 1 viên"
    },
    {
        "input": "Huyết áp: 120/80  Thân nhiệt: 37°C",
        "expected": "Huyết áp: 120/80 Thân nhiệt: 37°C"
    },
    {
        "input": "Liều: 250mg/viên  ml x 3 lần",
        "expected": "Liều: 250 mg/viên ml x 3 lần"
    },
]

print("=" * 70)
print("Testing OCR Post-Processing Improvements")
print("=" * 70)

passed = 0
failed = 0

for i, test in enumerate(test_cases, 1):
    result = _post_process_text(test["input"])
    expected = test["expected"].lower().strip()
    actual = result.lower().strip()
    
    is_pass = expected == actual or test["expected"] in result
    
    if is_pass:
        passed += 1
        print(f"\n✓ Test {i}: PASS")
    else:
        failed += 1
        print(f"\n✗ Test {i}: FAIL")
        print(f"  Input:    {test['input']}")
        print(f"  Expected: {test['expected']}")
        print(f"  Got:      {result}")

print("\n" + "=" * 70)
print(f"Results: {passed} passed, {failed} failed out of {len(test_cases)} tests")
print("=" * 70)

sys.exit(0 if failed == 0 else 1)
