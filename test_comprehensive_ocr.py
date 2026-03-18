#!/usr/bin/env python3
"""
Comprehensive test for OCR improvements and prescription formatting
"""
import sys
sys.path.insert(0, '.')

from backend.app import has_scannable_text, format_prescription_output, apply_medical_glossary
from ocr.ocr_engine import _post_process_text

print("=" * 80)
print("COMPREHENSIVE OCR IMPROVEMENTS TEST")
print("=" * 80)

# Test 1: has_scannable_text improvements
print("\n[TEST 1] has_scannable_text function")
print("-" * 80)

test_cases_scannable = [
    ("Đơn thuốc", True, "Short prescription keyword"),
    ("họ tên", True, "Medical keyword"),
    ("abc", False, "Only 3 chars, no medical keyword"),
    ("Đơn thuốc: Cảm cơ", True, "Prescription header"),
    ("Uống 2 viên sáng chiều tối", True, "Medical dosage"),
    ("", False, "Empty text"),
    ("aa bb", False, "Very short tokens"),
    ("thuốc mg/viên", True, "Has medical keywords"),
]

passed = 0
for text, expected, desc in test_cases_scannable:
    result = has_scannable_text(text)
    status = "✓" if result == expected else "✗"
    if result == expected:
        passed += 1
    print(f"  {status} {desc}: {result} (expected {expected})")

print(f"  → {passed}/{len(test_cases_scannable)} passed")

# Test 2: format_prescription_output  
print("\n[TEST 2] format_prescription_output function")
print("-" * 80)

format_tests = [
    ("Đơn thuốc:  Cảm cơ", "Đơn thuốc: Cảm cơ"),
    ("500  mg x  3 lần/ngày", "500 mg x 3 lần/ngày"),
    ("Sáng  1 viên  ml", "Sáng 1 viên ml"),
    ("Họ tên  :  Nguyễn Văn A", "Họ tên : Nguyễn Văn A"),
]

passed_format = 0
for input_text, expected in format_tests:
    result = format_prescription_output(input_text)
    # Normalize for comparison
    result_norm = result.strip()
    status = "✓" if result_norm == expected else "✗"
    if result_norm == expected:
        passed_format += 1
    print(f"  {status}")
    print(f"     Input:    '{input_text}'")
    print(f"     Expected: '{expected}'")
    print(f"     Got:      '{result}'")

print(f"  → {passed_format}/{len(format_tests)} passed")

# Test 3: OCR post-processing with format
print("\n[TEST 3] Full OCR pipeline (post-process + format)")
print("-" * 80)

pipeline_tests = [
    {
        "input": "Uéng 2 vién sáng chiều tối, 2 lần/ngay",
        "should_contain": ["uống", "viên", "lần/ngày"],
        "desc": "Vietnamese character fixes"
    },
    {
        "input": "Don thuoc: 250mg x 3 lần/ngay",
        "should_contain": ["đơn thuốc", "250 mg", "lần/ngày"],
        "desc": "Medication dosage"
    },
    {
        "input": "Ho ten: Bệnh nhân  Tuoi:  30",
        "should_contain": ["họ tên", "tuổi"],
        "desc": "Patient information"
    },
]

passed_pipeline = 0
for test in pipeline_tests:
    # Step 1: Post-process
    processed = _post_process_text(test["input"])
    
    # Step 2: Format
    formatted = format_prescription_output(processed)
    
    # Step 3: Check
    all_found = all(item.lower() in formatted.lower() for item in test["should_contain"])
    status = "✓" if all_found else "✗"
    if all_found:
        passed_pipeline += 1
    
    print(f"  {status} {test['desc']}")
    print(f"     Input:  '{test['input']}'")
    print(f"     Output: '{formatted}'")
    for item in test["should_contain"]:
        found = "✓" if item.lower() in formatted.lower() else "✗"
        print(f"       {found} Contains '{item}'")
    print()

print(f"  → {passed_pipeline}/{len(pipeline_tests)} passed")

# Summary
print("=" * 80)
print("SUMMARY")
print("=" * 80)
total_passed = passed + passed_format + passed_pipeline
total_tests = len(test_cases_scannable) + len(format_tests) + len(pipeline_tests)
print(f"✓ Total: {total_passed}/{total_tests} tests passed")

if total_passed == total_tests:
    print("✓ All improvements working correctly!")
    sys.exit(0)
else:
    print(f"✗ {total_tests - total_passed} tests failed")
    sys.exit(1)
