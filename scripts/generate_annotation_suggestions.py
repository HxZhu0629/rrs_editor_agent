#!/usr/bin/env python
import argparse
import csv
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Generate Chinese suggestion labels for manual annotation review.")
    parser.add_argument("input_csv", help="Path to the annotation CSV file")
    parser.add_argument(
        "--output-csv",
        default="",
        help="Optional output CSV path. Defaults to input filename with _suggested suffix.",
    )
    return parser.parse_args()


def split_pipe_text(value: str):
    if not value:
        return []
    return [part.strip() for part in value.split("|") if part.strip()]


def suggest_label(score: float, matched_count: int, token_overlap: int):
    if matched_count >= 4 and score >= 0.45 and token_overlap >= 8:
        return 2, "相关", "关键词命中较多，综合得分较高，文本重叠明显，建议优先判为相关。"
    if matched_count >= 2 and score >= 0.30 and token_overlap >= 4:
        return 1, "一般相关", "有一定关键词与文本匹配，但强度中等，建议判为一般相关。"
    return 0, "不相关", "关键词命中较少或综合得分偏低，建议判为不相关。"


def main():
    args = parse_args()
    input_path = Path(args.input_csv).resolve()
    if not input_path.exists():
        raise SystemExit(f"Input CSV not found: {input_path}")

    if args.output_csv:
        output_path = Path(args.output_csv).resolve()
    else:
        output_path = input_path.with_name(f"{input_path.stem}_suggested{input_path.suffix}")

    with input_path.open("r", encoding="utf-8-sig", newline="") as src:
        reader = csv.DictReader(src)
        fieldnames = list(reader.fieldnames or [])
        for extra in ["suggested_label", "suggested_label_cn", "suggested_reason_zh"]:
            if extra not in fieldnames:
                fieldnames.append(extra)

        rows = []
        for row in reader:
            matched_keywords = split_pipe_text(row.get("matched_keywords", ""))
            matched_count = len(matched_keywords)
            score = float((row.get("reviewer_score") or "0").strip() or "0")
            token_overlap = int((row.get("token_overlap_count") or "0").strip() or "0")

            label, label_cn, reason_zh = suggest_label(score, matched_count, token_overlap)
            row["suggested_label"] = str(label)
            row["suggested_label_cn"] = label_cn
            row["suggested_reason_zh"] = (
                f"{reason_zh} 命中关键词数={matched_count}，文本重叠数={token_overlap}，综合得分={score:.3f}。"
            )
            rows.append(row)

    with output_path.open("w", encoding="utf-8-sig", newline="") as dst:
        writer = csv.DictWriter(dst, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"input_csv={input_path}")
    print(f"output_csv={output_path}")
    print(f"rows={len(rows)}")


if __name__ == "__main__":
    main()
