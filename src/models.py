"""
半导体车间调度 — 工业级核心数据模型

建模层次：
    SchedulingProblem（问题实例）
        ├── Job（晶圆 Lot / 批次）
        │     └── Operation（工序，最小调度单元）
        ├── Machine（加工设备）
        └── ScheduleResult（调度输出，供甘特图与 KPI 使用）

时间单位默认为「分钟」。所有非负时间字段在构造时做基本校验。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# 枚举：生命周期状态
# ---------------------------------------------------------------------------


class OperationStatus(str, Enum):
    """工序在调度/执行流程中的状态。"""

    WAITING = "waiting"  # 等待前序完成或未排程
    SCHEDULED = "scheduled"  # 已排程，尚未开工
    IN_PROGRESS = "in_progress"  # 正在加工
    COMPLETED = "completed"  # 已完成


class JobStatus(str, Enum):
    """工件（Lot）在产线中的状态。"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class MachineStatus(str, Enum):
    """设备运行状态。"""

    IDLE = "idle"  # 空闲，可接单
    BUSY = "busy"  # 加工中
    DOWN = "down"  # 停机（PM / 故障 / 不可用）


# ---------------------------------------------------------------------------
# 核心实体
# ---------------------------------------------------------------------------


@dataclass
class Operation:
    """
    工序：Lot 在指定设备上的一次加工任务（调度最小单元）。

    同一 Job 内工序按 operation_index 递增形成工艺先后约束；
    前序工序 scheduled_end 之后，后序方可开始。

    Attributes:
        job_id: 所属 Lot 标识。
        operation_index: 工序在工艺路线中的序号，从 0 起。
        machine_id: 目标设备 ID；Job Shop 中各工序可对应不同设备。
        processing_time: 纯加工时间（分钟），不含 setup / 搬运。
        setup_time: 换型 / 配方切换时间（分钟），默认 0。
        recipe_id: 工艺配方 ID，用于换型判定与设备能力匹配。
        scheduled_start: 排程开始时刻；未排程时为 None。
        scheduled_end: 排程结束时刻；未排程时为 None。
        status: 工序当前状态。
    """

    job_id: str
    operation_index: int
    machine_id: str
    processing_time: float
    setup_time: float = 0.0
    recipe_id: str = ""
    scheduled_start: float | None = None
    scheduled_end: float | None = None
    status: OperationStatus = OperationStatus.WAITING

    def __post_init__(self) -> None:
        if self.operation_index < 0:
            raise ValueError(f"operation_index 不能为负: {self.operation_index}")
        if self.processing_time < 0:
            raise ValueError(f"processing_time 不能为负: {self.processing_time}")
        if self.setup_time < 0:
            raise ValueError(f"setup_time 不能为负: {self.setup_time}")
        if self.scheduled_start is not None and self.scheduled_start < 0:
            raise ValueError(f"scheduled_start 不能为负: {self.scheduled_start}")
        if (
            self.scheduled_start is not None
            and self.scheduled_end is not None
            and self.scheduled_end < self.scheduled_start
        ):
            raise ValueError(
                f"scheduled_end ({self.scheduled_end}) "
                f"不能早于 scheduled_start ({self.scheduled_start})"
            )

    @property
    def total_time(self) -> float:
        """换型 + 加工的总占用时间。"""
        return self.setup_time + self.processing_time

    @property
    def is_scheduled(self) -> bool:
        return self.scheduled_start is not None and self.scheduled_end is not None


@dataclass
class Job:
    """
    工件：待加工的晶圆批次（Lot / FOUP）。

    Attributes:
        job_id: Lot 唯一标识。
        operations: 按工艺顺序排列的工序列表。
        release_time: Lot 到达产线、可开始首道工序的最早时刻（分钟）。
        due_date: 交期；None 表示无硬交期约束。
        priority: 优先级，数值越大越紧急（Hot Lot / 工程批等）。
        lot_size: 批次晶圆片数，默认 25（标准 FOUP）。
        product_id: 产品 / 工艺流程标识。
        status: Lot 当前状态。
    """

    job_id: str
    operations: list[Operation] = field(default_factory=list)
    release_time: float = 0.0
    due_date: float | None = None
    priority: int = 0
    lot_size: int = 25
    product_id: str = ""
    status: JobStatus = JobStatus.PENDING

    def __post_init__(self) -> None:
        if self.release_time < 0:
            raise ValueError(f"release_time 不能为负: {self.release_time}")
        if self.due_date is not None and self.due_date < self.release_time:
            raise ValueError(
                f"due_date ({self.due_date}) 不能早于 release_time ({self.release_time})"
            )
        if self.lot_size <= 0:
            raise ValueError(f"lot_size 必须为正: {self.lot_size}")
        self._validate_operations()

    def _validate_operations(self) -> None:
        """校验工序序号连续且 job_id 与 Lot 一致。"""
        for i, op in enumerate(self.operations):
            if op.job_id != self.job_id:
                raise ValueError(
                    f"工序 job_id ({op.job_id}) 与 Lot ({self.job_id}) 不一致"
                )
            if op.operation_index != i:
                raise ValueError(
                    f"工序序号不连续: 期望 {i}, 实际 {op.operation_index}"
                )

    @property
    def total_processing_time(self) -> float:
        """所有工序加工时间之和（不含 setup）。"""
        return sum(op.processing_time for op in self.operations)

    @property
    def total_time(self) -> float:
        """所有工序 setup + 加工时间之和。"""
        return sum(op.total_time for op in self.operations)

    @property
    def completion_time(self) -> float | None:
        """末道工序结束时刻；未全部排程时返回 None。"""
        if not self.operations or not all(op.is_scheduled for op in self.operations):
            return None
        return self.operations[-1].scheduled_end

    @property
    def tardiness(self) -> float:
        """拖期量：max(0, completion_time - due_date)；无交期或未完工时为 0。"""
        if self.due_date is None:
            return 0.0
        ct = self.completion_time
        if ct is None:
            return 0.0
        return max(0.0, ct - self.due_date)

    @property
    def next_operation(self) -> Operation | None:
        """返回下一道待调度/待执行工序。"""
        for op in self.operations:
            if op.status != OperationStatus.COMPLETED:
                return op
        return None


@dataclass
class Machine:
    """
    机器：车间加工设备（光刻、刻蚀、薄膜、CMP 等）。

    idle_time 表示设备 timeline 上下次可开工的最早时刻；
    last_recipe_id 用于估算换型时间（与 Operation.setup_time 配合）。

    Attributes:
        machine_id: 设备唯一标识。
        machine_type: 设备类型 / 工艺段（如 "LITHO", "ETCH"）。
        idle_time: 下次可用时刻（分钟），初始为 0。
        status: 设备当前状态。
        last_recipe_id: 上一道已加工工序的配方 ID，用于换型判定。
    """

    machine_id: str
    machine_type: str = ""
    idle_time: float = 0.0
    status: MachineStatus = MachineStatus.IDLE
    last_recipe_id: str = ""

    def __post_init__(self) -> None:
        if self.idle_time < 0:
            raise ValueError(f"idle_time 不能为负: {self.idle_time}")

    @property
    def is_available(self) -> bool:
        return self.status in (MachineStatus.IDLE, MachineStatus.BUSY)

    def effective_setup(self, operation: Operation) -> float:
        """若配方相同则无需换型，否则返回工序声明的 setup_time。"""
        if not operation.recipe_id or operation.recipe_id == self.last_recipe_id:
            return 0.0
        return operation.setup_time

    def occupy(self, start: float, operation: Operation) -> None:
        """
        将设备占用至 start + setup + processing，并更新 idle_time 与 last_recipe_id。

        调度器在确定 (machine, start) 后调用，保持设备状态与排程一致。
        """
        if start < 0:
            raise ValueError(f"start 不能为负: {start}")
        setup = self.effective_setup(operation)
        end = start + setup + operation.processing_time
        operation.scheduled_start = start + setup
        operation.scheduled_end = end
        operation.status = OperationStatus.SCHEDULED
        self.idle_time = end
        self.last_recipe_id = operation.recipe_id
        self.status = MachineStatus.BUSY


# ---------------------------------------------------------------------------
# 调度问题与输出
# ---------------------------------------------------------------------------


@dataclass
class ScheduleEntry:
    """
    单条排程记录，直接对应甘特图上的一个色块。

    Attributes:
        job_id: Lot 标识。
        operation_index: 工序序号。
        machine_id: 占用设备。
        start: 实际加工开始时刻（setup 结束之后）。
        end: 加工结束时刻。
        setup_start: 换型开始时刻；无换型时等于 start。
    """

    job_id: str
    operation_index: int
    machine_id: str
    start: float
    end: float
    setup_start: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def setup_duration(self) -> float:
        return self.start - self.setup_start


@dataclass
class ScheduleResult:
    """
    调度方案输出，聚合排程条目与 KPI。

    Attributes:
        entries: 按 start 排序的甘特图条目。
        makespan: 最大完工时间 C_max。
    """

    entries: list[ScheduleEntry] = field(default_factory=list)
    makespan: float = 0.0

    def total_tardiness(self, jobs: list[Job]) -> float:
        """所有 Lot 拖期之和。"""
        return sum(job.tardiness for job in jobs)

    def sorted_entries(self) -> list[ScheduleEntry]:
        return sorted(self.entries, key=lambda e: (e.machine_id, e.setup_start))


@dataclass
class SchedulingProblem:
    """
    完整调度问题实例：输入侧容器。

    Attributes:
        jobs: 待调度 Lot 列表。
        machines: 可用设备列表。
        name: 问题名称 / 场景标识（可选）。
    """

    jobs: list[Job] = field(default_factory=list)
    machines: list[Machine] = field(default_factory=list)
    name: str = ""

    def __post_init__(self) -> None:
        self._validate_unique_ids()

    def _validate_unique_ids(self) -> None:
        job_ids = [j.job_id for j in self.jobs]
        if len(job_ids) != len(set(job_ids)):
            raise ValueError("jobs 中存在重复的 job_id")
        machine_ids = [m.machine_id for m in self.machines]
        if len(machine_ids) != len(set(machine_ids)):
            raise ValueError("machines 中存在重复的 machine_id")

    def get_machine(self, machine_id: str) -> Machine:
        for machine in self.machines:
            if machine.machine_id == machine_id:
                return machine
        raise KeyError(f"未找到设备: {machine_id}")

    def get_job(self, job_id: str) -> Job:
        for job in self.jobs:
            if job.job_id == job_id:
                return job
        raise KeyError(f"未找到 Lot: {job_id}")

    @property
    def machine_map(self) -> dict[str, Machine]:
        return {m.machine_id: m for m in self.machines}

    @property
    def job_map(self) -> dict[str, Job]:
        return {j.job_id: j for j in self.jobs}
