#!/usr/bin/env python
import argparse
import csv
import random
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Generate scheme-C experiment folders for Top-20 reviewer recommendation evaluation.")
    project_root = Path(__file__).resolve().parent.parent
    parser.add_argument("--project-root", default=str(project_root), help="Project root path")
    parser.add_argument("--container", default="rrs-backend", help="Running backend container name")
    parser.add_argument("--final-sample-size", type=int, default=100, help="Sample size for the final hybrid experiment")
    parser.add_argument("--comparison-sample-size", type=int, default=20, help="Sample size for the comparison experiment")
    parser.add_argument("--top-k", type=int, default=20, help="Recommendation cutoff")
    parser.add_argument("--seed", type=int, default=314159, help="Random seed for the final 100-paper sample")
    parser.add_argument(
        "--comparison-seed",
        type=int,
        default=271828,
        help="Random seed for selecting the 20-paper comparison subset from the final set",
    )
    parser.add_argument(
        "--exclude-manuscripts-csv",
        default="",
        help="Optional manuscripts CSV whose manuscript_id column should be excluded from the new 100-paper sample",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Optional output folder under data/experiments. Defaults to a timestamped scheme_c folder.",
    )
    return parser.parse_args()


def run(command, cwd=None):
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def ensure_clean_dir(path: Path):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def write_ids(path: Path, manuscript_ids):
    path.write_text("\n".join(manuscript_ids) + "\n", encoding="utf-8")


def read_ids_from_manuscripts_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        ids = [str(row["manuscript_id"]).strip() for row in reader if str(row.get("manuscript_id", "")).strip()]
    if not ids:
        raise RuntimeError(f"No manuscript ids found in {path}")
    return ids


def find_single_csv(path: Path, keyword: str, exclude_keyword: str = ""):
    matches = sorted(path.glob(f"*{keyword}*.csv"))
    if exclude_keyword:
        matches = [item for item in matches if exclude_keyword not in item.name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one CSV containing '{keyword}' under {path}, found {len(matches)}")
    return matches[0]


def find_latest_previous_manuscripts_csv(experiments_dir: Path):
    candidates = [
        path for path in experiments_dir.glob("experiment_*_n30_k10_*_manuscripts.csv")
        if path.is_file()
    ]
    if not candidates:
        raise RuntimeError("No previous 30-paper experiment manuscripts CSV found for exclusion")
    return max(candidates, key=lambda item: item.stat().st_mtime)


def docker_cp_to(container: str, host_path: Path, container_path: str):
    run(["docker", "cp", str(host_path), f"{container}:{container_path}"])


def docker_cp_from(container: str, container_path: str, host_path: Path):
    host_path.mkdir(parents=True, exist_ok=True)
    run(["docker", "cp", f"{container}:{container_path}/.", str(host_path)])


def export_dataset(container: str, container_ids_path: str, container_exclude_ids_path: str, sample_size: int, top_k: int, seed: int, method: str, container_output_dir: str):
    command = [
        "docker",
        "exec",
        container,
        "python",
        "manage.py",
        "export_experiment_dataset",
        "--sample-size",
        str(sample_size),
        "--top-k",
        str(top_k),
        "--seed",
        str(seed),
        "--method",
        method,
        "--output-dir",
        container_output_dir,
    ]
    if container_ids_path:
        command.extend(["--manuscript-ids-file", container_ids_path])
    if container_exclude_ids_path:
        command.extend(["--exclude-manuscript-ids-file", container_exclude_ids_path])
    return run(command)


def generate_suggested_annotation(script_path: Path, csv_path: Path):
    run([sys.executable, str(script_path), str(csv_path)])


def write_manifest(path: Path, final_dir: Path, comparison_dir: Path, results_dir: Path, exclude_csv: Path, seed: int, comparison_seed: int, top_k: int):
    text = f"""# 方案 C 实验清单

## 1. 实验结构

- `final_100_hybrid/`：100 篇新论文、Top-{top_k}、最终混合方法，用于实际业务设置验证。
- `comparison_20_methods/`：从上述 100 篇中再抽取 20 篇，分别用 lexical、vector、hybrid 三种方法导出，用于对照实验。
- `results/`：指标汇总表、分析模板和后续论文材料。
- `shared/`：抽样 id 文件与排除列表，保证实验可复现。

## 2. 采样说明

- 100 篇最终实验随机种子：`{seed}`
- 20 篇对照实验子集随机种子：`{comparison_seed}`
- Top-K：`{top_k}`
- 排除旧实验稿件来源：`{exclude_csv.name}`

## 3. 使用顺序

1. 先标注 `final_100_hybrid/` 下的 `annotations_suggested.csv`。
2. 再标注 `comparison_20_methods/` 下三种方法的 `annotations_suggested.csv`。
3. 标注完成后，使用 `scripts/compute_experiment_metrics.py` 计算指标。
4. 将结果填入 `results/metrics_summary_template.csv`，并补充 `results/analysis_summary_template.md`。

## 4. 目录

- 最终实验目录：`{final_dir.name}`
- 对照实验目录：`{comparison_dir.name}`
- 结果目录：`{results_dir.name}`
"""
    path.write_text(text, encoding="utf-8-sig")


def write_results_templates(results_dir: Path, top_k: int):
    metrics_csv = results_dir / "metrics_summary_template.csv"
    analysis_md = results_dir / "analysis_summary_template.md"
    metrics_csv.write_text(
        "\n".join(
            [
                "experiment_group,method,sample_size,top_k,total_annotations,relevant_count,partially_relevant_count,irrelevant_count,precision_strict,precision_relaxed,hit_rate,mrr,ndcg,status,notes",
                f"final_100,hybrid,100,{top_k},,,,,,,,,,pending_annotation,等待人工标注后填写",
                f"comparison_20,lexical,20,{top_k},,,,,,,,,,pending_annotation,等待人工标注后填写",
                f"comparison_20,vector,20,{top_k},,,,,,,,,,pending_annotation,等待人工标注后填写",
                f"comparison_20,hybrid,20,{top_k},,,,,,,,,,pending_annotation,等待人工标注后填写",
            ]
        ),
        encoding="utf-8-sig",
    )
    analysis_md.write_text(
        f"""# Top-{top_k} 实验分析模板

## 1. 最终实验结果

待补充：100 篇新论文在 hybrid 方法下的 Precision@{top_k}、HitRate@{top_k}、MRR、nDCG。

## 2. 三方法对照结果

待补充：20 篇子集在 lexical、vector、hybrid 三种方法下的横向对比分析。

## 3. 可写入论文的分析方向

- Top-{top_k} 场景下系统实际可用性是否满足业务需求。
- hybrid 方法在真实输出 20 位审稿人的设置下，是否仍保持较好的排序质量。
- vector 方法是否能提升覆盖面，但可能带来部分泛相关候选前移。
- lexical 方法是否在严格相关性上更强，但整体排序稳定性不如 hybrid。
""",
        encoding="utf-8-sig",
    )


def main():
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    experiments_dir = project_root / "data" / "experiments"
    experiments_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = Path(args.output_dir).resolve() if args.output_dir else experiments_dir / f"scheme_c_top20_{stamp}"
    shared_dir = output_root / "shared"
    final_dir = output_root / "final_100_hybrid"
    comparison_dir = output_root / "comparison_20_methods"
    results_dir = output_root / "results"
    ensure_clean_dir(output_root)
    shared_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    comparison_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    exclude_csv = Path(args.exclude_manuscripts_csv).resolve() if args.exclude_manuscripts_csv else find_latest_previous_manuscripts_csv(experiments_dir)
    exclude_ids = read_ids_from_manuscripts_csv(exclude_csv)
    exclude_ids_path = shared_dir / "excluded_previous_30_ids.txt"
    write_ids(exclude_ids_path, exclude_ids)

    container_root = f"/tmp/rrs_scheme_c_{stamp}"
    run(["docker", "exec", args.container, "mkdir", "-p", container_root])
    docker_cp_to(args.container, exclude_ids_path, f"{container_root}/excluded_previous_30_ids.txt")

    final_container_dir = f"{container_root}/final_100_hybrid"
    export_dataset(
        container=args.container,
        container_ids_path="",
        container_exclude_ids_path=f"{container_root}/excluded_previous_30_ids.txt",
        sample_size=args.final_sample_size,
        top_k=args.top_k,
        seed=args.seed,
        method="hybrid",
        container_output_dir=final_container_dir,
    )
    docker_cp_from(args.container, final_container_dir, final_dir)

    final_manuscripts_csv = find_single_csv(final_dir, "manuscripts")
    final_annotation_csv = find_single_csv(final_dir, "annotations", exclude_keyword="suggested")
    final_ids = read_ids_from_manuscripts_csv(final_manuscripts_csv)
    final_ids_path = shared_dir / "final_100_manuscript_ids.txt"
    write_ids(final_ids_path, final_ids)

    comparison_ids = random.Random(args.comparison_seed).sample(final_ids, args.comparison_sample_size)
    comparison_ids_path = shared_dir / "comparison_20_manuscript_ids.txt"
    write_ids(comparison_ids_path, comparison_ids)
    docker_cp_to(args.container, comparison_ids_path, f"{container_root}/comparison_20_manuscript_ids.txt")

    for method in ("lexical", "vector", "hybrid"):
        method_host_dir = comparison_dir / method
        method_container_dir = f"{container_root}/comparison_20_{method}"
        export_dataset(
            container=args.container,
            container_ids_path=f"{container_root}/comparison_20_manuscript_ids.txt",
            container_exclude_ids_path="",
            sample_size=args.comparison_sample_size,
            top_k=args.top_k,
            seed=args.seed,
            method=method,
            container_output_dir=method_container_dir,
        )
        docker_cp_from(args.container, method_container_dir, method_host_dir)

    suggestion_script = project_root / "scripts" / "generate_annotation_suggestions.py"
    generate_suggested_annotation(suggestion_script, final_annotation_csv)
    for method in ("lexical", "vector", "hybrid"):
        comparison_annotation_csv = find_single_csv(comparison_dir / method, "annotations", exclude_keyword="suggested")
        generate_suggested_annotation(suggestion_script, comparison_annotation_csv)

    write_manifest(
        output_root / "README.md",
        final_dir=final_dir,
        comparison_dir=comparison_dir,
        results_dir=results_dir,
        exclude_csv=exclude_csv,
        seed=args.seed,
        comparison_seed=args.comparison_seed,
        top_k=args.top_k,
    )
    write_results_templates(results_dir, args.top_k)

    print(f"output_root={output_root}")
    print(f"exclude_csv={exclude_csv}")
    print(f"final_annotation_csv={final_annotation_csv}")
    print(f"comparison_dir={comparison_dir}")
    print(f"results_dir={results_dir}")


if __name__ == "__main__":
    main()
