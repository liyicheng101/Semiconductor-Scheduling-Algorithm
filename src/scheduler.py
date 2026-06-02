"""
规则调度器（Rule-Based Scheduler）

本模块提供两种经典的启发式排程策略：
1) FIFO（First In First Out）：
   - 按问题输入中的 Job 列表顺序依次排程；
   - 每个 Job 内按工序顺序排程；
   - 每道工序的开工约束严格满足：
       start = max(machine.idle_time, prev_op_end)
2) SPT（Shortest Processing Time）：
   - 每一步都在“各 Job 下一道可排工序”中选择 processing_time 最短者优先；
   - 同样遵守机器可用与工艺前序完工约束。

注意：
- 该调度器会直接更新传入问题中的机器与工序状态（调用 machine.occupy）。
- 若希望复用同一个问题实例做多次实验，建议每次传入前先深拷贝。
"""

from __future__ import annotations

from typing import Iterable

from src.models import Job, Operation, ScheduleEntry, ScheduleResult, SchedulingProblem


class RuleBasedScheduler:
    """基于规则的简单调度器，实现 FIFO 与 SPT 两种基础策略。"""

    def schedule_fifo(self, problem: SchedulingProblem) -> ScheduleResult:
        """
        按 Job 列表顺序进行 FIFO 排程。

        核心逻辑：
        - 外层固定按 problem.jobs 的顺序遍历；
        - 内层按 Job 工序顺序遍历；
        - 每道工序使用统一约束公式计算可开始时刻：
              start = max(machine.idle_time, prev_op_end)
          其中 prev_op_end 对首道工序取 job.release_time。
        """
        entries: list[ScheduleEntry] = []

        for job in problem.jobs:
            # prev_op_end 表示该 Job 上一道工序的完工时刻；
            # 对首道工序而言，等价于 release_time（最早可开工时刻）。
            prev_op_end = job.release_time

            for operation in job.operations:
                machine = problem.get_machine(operation.machine_id)

                # 严格应用用户要求的约束公式：
                # 机器要空闲 + 前序要完成，两者取较大值作为 setup 开始时刻。
                start = max(machine.idle_time, prev_op_end)

                # 调用模型层机器占用接口，统一更新：
                # - operation.scheduled_start / scheduled_end / status
                # - machine.idle_time / last_recipe_id / status
                machine.occupy(start=start, operation=operation)

                # 记录排程条目，供甘特图与 KPI 计算使用。
                entries.append(
                    ScheduleEntry(
                        job_id=job.job_id,
                        operation_index=operation.operation_index,
                        machine_id=operation.machine_id,
                        start=operation.scheduled_start or start,
                        end=operation.scheduled_end or start,
                        setup_start=start,
                    )
                )

                # 更新该 Job 的前序完工时刻，供下一道工序使用。
                prev_op_end = operation.scheduled_end or prev_op_end

        return self._build_result(entries)

    def schedule_spt(self, problem: SchedulingProblem) -> ScheduleResult:
        """
        按 SPT（Shortest Processing Time）规则排程。

        实现要点：
        - 对每个 Job，只追踪“下一道待排工序”；
        - 每一轮从所有候选工序中选择 processing_time 最短者；
        - 选中后仍严格使用约束公式：
              start = max(machine.idle_time, prev_op_end)
        - 直到所有工序被排完。
        """
        entries: list[ScheduleEntry] = []

        # next_index[job_id]：该 Job 下一道待排工序在 operations 中的下标。
        next_index: dict[str, int] = {job.job_id: 0 for job in problem.jobs}
        # prev_end[job_id]：该 Job 前一道已排工序的完工时刻（首道工序用 release_time）。
        prev_end: dict[str, float] = {job.job_id: job.release_time for job in problem.jobs}

        total_operations = sum(len(job.operations) for job in problem.jobs)
        scheduled_count = 0

        while scheduled_count < total_operations:
            candidates = list(self._collect_candidates(problem.jobs, next_index))
            if not candidates:
                # 正常情况下不会发生。若出现，多半是输入数据缺失或不一致。
                raise RuntimeError("SPT 排程失败：未找到可调度候选工序。")

            # 选择规则（主关键字）：
            # 1) processing_time 越短优先（SPT 核心）
            # 2) earliest_start 越早优先（减少明显可开工时差）
            # 3) job_id / operation_index 作为稳定 tie-break，保证结果可复现
            selected_job, selected_op, selected_start = min(
                (
                    (
                        job,
                        op,
                        max(problem.get_machine(op.machine_id).idle_time, prev_end[job.job_id]),
                    )
                    for job, op in candidates
                ),
                key=lambda item: (
                    item[1].processing_time,
                    item[2],
                    item[0].job_id,
                    item[1].operation_index,
                ),
            )

            machine = problem.get_machine(selected_op.machine_id)
            machine.occupy(start=selected_start, operation=selected_op)

            entries.append(
                ScheduleEntry(
                    job_id=selected_job.job_id,
                    operation_index=selected_op.operation_index,
                    machine_id=selected_op.machine_id,
                    start=selected_op.scheduled_start or selected_start,
                    end=selected_op.scheduled_end or selected_start,
                    setup_start=selected_start,
                )
            )

            prev_end[selected_job.job_id] = selected_op.scheduled_end or prev_end[selected_job.job_id]
            next_index[selected_job.job_id] += 1
            scheduled_count += 1

        return self._build_result(entries)

    @staticmethod
    def _collect_candidates(
        jobs: list[Job], next_index: dict[str, int]
    ) -> Iterable[tuple[Job, Operation]]:
        """收集每个 Job 当前“下一道可排工序”作为候选集合。"""
        for job in jobs:
            idx = next_index[job.job_id]
            if idx < len(job.operations):
                yield job, job.operations[idx]

    @staticmethod
    def _build_result(entries: list[ScheduleEntry]) -> ScheduleResult:
        """
        统一构建 ScheduleResult。

        - entries 按 start（实际加工开始时刻）排序；
        - makespan 取所有条目 end 的最大值。
        """
        sorted_entries = sorted(entries, key=lambda e: (e.start, e.machine_id, e.operation_index))
        makespan = max((entry.end for entry in sorted_entries), default=0.0)
        return ScheduleResult(entries=sorted_entries, makespan=makespan)

