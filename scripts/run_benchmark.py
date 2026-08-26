"""命令行运行 M6 L0/L1 Benchmark 并输出 JSON 报告。"""

import argparse
import json
from uuid import UUID

from etl_agent.benchmark import BenchmarkLevel, BenchmarkRequest, run_benchmark


def main() -> None:
    """解析命令行参数并打印可保存的 Benchmark 报告。"""
    parser = argparse.ArgumentParser(description="运行 ETL-Agent 合成 Benchmark")
    parser.add_argument("--project-id", required=True, help="项目 UUID")
    parser.add_argument("--level", choices=[level.value for level in BenchmarkLevel], default="l0")
    parser.add_argument("--rows", type=int, default=1000, help="合成数据行数")
    parser.add_argument("--seed", type=int, default=20260826, help="固定随机种子")
    parser.add_argument("--repeat", type=int, default=1, help="重复次数")
    parser.add_argument("--artifact-digest", default="synthetic-etl-plan-v1")
    parser.add_argument("--policy-version", default="pdp-v1")
    parser.add_argument("--environment", default="development")
    args = parser.parse_args()
    report = run_benchmark(
        BenchmarkRequest(
            project_id=UUID(args.project_id),
            level=BenchmarkLevel(args.level),
            dataset_rows=args.rows,
            seed=args.seed,
            repeat=args.repeat,
            artifact_digest=args.artifact_digest,
            policy_version=args.policy_version,
            environment=args.environment,
        )
    )
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
