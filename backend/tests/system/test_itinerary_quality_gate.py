"""使用固定数据集执行不访问付费服务的行程质量门禁。"""

from pathlib import Path

from evals.itinerary_feasibility import run_dataset


def test_beijing_fixed_dataset_meets_quality_thresholds() -> None:
    """北京三日数据集应达到路线、时间轴和游览覆盖验收阈值。"""
    dataset_path = (
        Path(__file__).resolve().parents[2]
        / "evals"
        / "datasets"
        / "beijing_3_day_v1.json"
    )

    report = run_dataset(dataset_path)

    assert report["passed"] is True
    assert report["metrics"]["timeline_overlap_count"] == 0
