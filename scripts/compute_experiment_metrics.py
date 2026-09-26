#!/usr/bin/env python
import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path


def dcg(labels):
    total = 0.0
    for idx, label in enumerate(labels, start=1):
        gain = (2 ** label) - 1
        total += gain / math.log2(idx + 1)
    return total


def parse_args():
    parser = argparse.ArgumentParser(description="Compute experiment metrics from the annotated recommendation CSV.")
    parser.add_argument("csv_file", help="Path to the annotated CSV file")
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Recommendation cutoff used during annotation (default: 10)",
    )
    return parser.parse_args()


def open_csv_with_fallback(path: Path):
    encodings = ["utf-8-sig", "utf-8", "gb18030", "gbk", "cp936", "utf-16"]
    last_error = None
    for encoding in encodings:
        try:
            fp = path.open("r", encoding=encoding, newline="")
            reader = csv.DictReader(fp)
            _ = reader.fieldnames
            if not reader.fieldnames:
                fp.close()
                continue
            return fp, reader, encoding
        except UnicodeDecodeError as exc:
            last_error = exc
        except Exception:
            raise
    raise SystemExit(f"Failed to decode CSV file {path}: {last_error}")


def main():
    args = parse_args()
    csv_path = Path(args.csv_file).resolve()
    if not csv_path.exists():
        raise SystemExit(f"CSV file not found: {csv_path}")

    grouped = defaultdict(list)
    total_rows = 0
    labeled_rows = 0

    fp, reader, used_encoding = open_csv_with_fallback(csv_path)
    try:
        for row in reader:
            total_rows += 1
            label_raw = (row.get("human_label") or "").strip()
            label = None
            if label_raw != "":
                try:
                    label = int(label_raw)
                except ValueError as exc:
                    raise SystemExit(f"Invalid human_label '{label_raw}' in row {total_rows + 1}") from exc
                if label not in (0, 1, 2):
                    raise SystemExit(f"human_label must be 0, 1, or 2. Found {label} in row {total_rows + 1}")
                labeled_rows += 1

            key = row["sample_order"]
            grouped[key].append(
                {
                    "rank": int(row["reviewer_rank"]),
                    "label": label,
                    "title": row["manuscript_title"],
                }
            )
    finally:
        fp.close()

    complete_groups = []
    incomplete_groups = []
    for sample_order, rows in grouped.items():
        rows.sort(key=lambda item: item["rank"])
        if len(rows) < args.top_k or any(item["label"] is None for item in rows[: args.top_k]):
            incomplete_groups.append((sample_order, rows[0]["title"], len(rows)))
            continue
        complete_groups.append((sample_order, rows[: args.top_k]))

    if not complete_groups:
        print(f"csv_file={csv_path}")
        print(f"total_rows={total_rows}")
        print(f"labeled_rows={labeled_rows}")
        print("complete_manuscripts=0")
        print("message=No fully annotated manuscripts yet. Fill the human_label column first.")
        return

    strict_precision_values = []
    relaxed_precision_values = []
    hit_values = []
    mrr_values = []
    ndcg_values = []

    for _, rows in complete_groups:
        labels = [item["label"] for item in rows]
        strict_hits = sum(1 for label in labels if label == 2)
        relaxed_hits = sum(1 for label in labels if label >= 1)

        strict_precision_values.append(strict_hits / args.top_k)
        relaxed_precision_values.append(relaxed_hits / args.top_k)
        hit_values.append(1.0 if strict_hits > 0 else 0.0)

        reciprocal_rank = 0.0
        for item in rows:
            if item["label"] == 2:
                reciprocal_rank = 1.0 / item["rank"]
                break
        mrr_values.append(reciprocal_rank)

        ideal_labels = sorted(labels, reverse=True)
        ideal_dcg = dcg(ideal_labels)
        ndcg_values.append((dcg(labels) / ideal_dcg) if ideal_dcg else 0.0)

    print(f"csv_file={csv_path}")
    print(f"encoding={used_encoding}")
    print(f"total_rows={total_rows}")
    print(f"labeled_rows={labeled_rows}")
    print(f"unlabeled_rows={total_rows - labeled_rows}")
    print(f"complete_manuscripts={len(complete_groups)}")
    print(f"incomplete_manuscripts={len(incomplete_groups)}")
    print(f"precision_at_{args.top_k}_strict={sum(strict_precision_values) / len(strict_precision_values):.4f}")
    print(f"precision_at_{args.top_k}_relaxed={sum(relaxed_precision_values) / len(relaxed_precision_values):.4f}")
    print(f"hit_rate_at_{args.top_k}={sum(hit_values) / len(hit_values):.4f}")
    print(f"mrr={sum(mrr_values) / len(mrr_values):.4f}")
    print(f"ndcg_at_{args.top_k}={sum(ndcg_values) / len(ndcg_values):.4f}")

    if incomplete_groups:
        preview = ", ".join(sample_order for sample_order, _, _ in incomplete_groups[:5])
        print(f"incomplete_sample_orders={preview}")


if __name__ == "__main__":
    main()
