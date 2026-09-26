# 数据目录说明

本目录只提交可复现评测所需的小型样例和配置，例如 evaluation/demo_cases.jsonl、evaluation/README.md 与 evaluation/gates.example.json。

以下内容属于本地或生成产物，不应进入公开仓库：

- backups/：数据库备份。
- experiments/：大体积实验输入、导出数据和结果。
- download*.log：外部数据下载日志。
- evaluation/local_*/、evaluation/demo_report*/：本机回归和评测输出。

真实稿件、作者信息、Agent 对话和标注数据可能包含敏感内容。导出评测案例前请脱敏，并把原始文件保留在受控存储中。评测报告应区分 synthetic、unreviewed 和 human 数据源，不能把演示样例当作线上质量结论。

