#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from ocr.ocr_engine import _format_prescription_layout

sample = (
    "DON THUOC Ho ten: NGUYEN HOAI THUONG NS: 6 tuoi 1 thang (Nam) "
    "Dia chi: Dien thoai: Sinh hieu: Than nhiet: 0C Huyet ap: mmHg Can nang: 19 Kg "
    "Chan doan: Cham Da Dieu tri: 1/ Amox Ngot 09 Vien Sang 1; Trua 1; Chieu 1 "
    "2/ PRED 5mg 06 Vien Sang 2"
)

out = _format_prescription_layout(sample)
print(out)

assert "Họ tên:" in out
assert "NS:" in out
assert "Chẩn đoán:" in out
assert "Điều trị:" in out
assert "\n1/" in out
assert "\n2/" in out
assert "HỌTÊN:" not in out
assert "tên:" in out  # keep actual field content, not duplicated synthetic label

print("\nOK: prescription layout formatter")
