"""
从 JSON 生产计划加载 SchedulingProblem，并做基本连通性验证。

用法（在项目根目录执行）：
    python -m src.main
    python src/main.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 保证无论从根目录还是 src/ 运行，都能正确 import
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models import Job, Machine, Operation, SchedulingProblem

DEFAULT_DATA_PATH = ROOT / "data" / "production_plan.json"


def load_scheduling_problem(json_path: Path) -> SchedulingProblem:
    """读取 JSON 并实例化为 SchedulingProblem。"""
    with json_path.open(encoding="utf-8") as f:
        data = json.load(f)

    machines = [
        Machine(
            machine_id=item["machine_id"],
            machine_type=item.get("machine_type", ""),
        )
        for item in data["machines"]
    ]

    jobs: list[Job] = []
    for job_data in data["jobs"]:
        job_id = job_data["job_id"]
        operations = [
            Operation(
                job_id=job_id,
                operation_index=op["operation_index"],
                machine_id=op["machine_id"],
                processing_time=op["processing_time"],
                setup_time=op.get("setup_time", 0.0),
                recipe_id=op.get("recipe_id", ""),
            )
            for op in job_data["operations"]
        ]
        jobs.append(
            Job(
                job_id=job_id,
                operations=operations,
                release_time=job_data.get("release_time", 0.0),
                due_date=job_data.get("due_date"),
                priority=job_data.get("priority", 0),
                product_id=job_data.get("product_id", ""),
                lot_size=job_data.get("lot_size", 25),
            )
        )

    return SchedulingProblem(
        name=data.get("scenario_name", ""),
        jobs=jobs,
        machines=machines,
    )


def main() -> None:
    data_path = DEFAULT_DATA_PATH
    if not data_path.exists():
        raise FileNotFoundError(f"未找到数据文件: {data_path}")

    problem = load_scheduling_problem(data_path)

    total_operations = sum(len(job.operations) for job in problem.jobs)

    print("数据加载成功")
    print(f"  场景名称 : {problem.name}")
    print(f"  机器数量 : {len(problem.machines)}")
    print(f"  工件数量 : {len(problem.jobs)}")
    print(f"  工序总数 : {total_operations}")


if __name__ == "__main__":
    main()
