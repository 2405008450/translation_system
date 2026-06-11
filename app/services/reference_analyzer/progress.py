"""参考分析进度管理器 - 支持 SSE 实时推送"""

import asyncio
import json
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Callable
from enum import Enum


class AnalysisStage(Enum):
    """分析阶段"""
    INIT = "init"
    PARSING_FILES = "parsing_files"
    ALIGNING = "aligning"
    EXTRACTING = "extracting"
    DEEP_ANALYSIS = "deep_analysis"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class ProgressInfo:
    """进度信息"""
    stage: str
    stage_label: str
    progress: float  # 0-100
    message: str
    detail: Optional[str] = None


# 全局进度存储
_progress_store: Dict[str, ProgressInfo] = {}
_progress_callbacks: Dict[str, Callable] = {}


def get_progress(task_id: str) -> Optional[ProgressInfo]:
    """获取任务进度"""
    return _progress_store.get(task_id)


def set_progress(
    task_id: str,
    stage: AnalysisStage,
    progress: float,
    message: str,
    detail: Optional[str] = None,
):
    """设置任务进度"""
    stage_labels = {
        AnalysisStage.INIT: "初始化",
        AnalysisStage.PARSING_FILES: "解析文件",
        AnalysisStage.ALIGNING: "对齐文本",
        AnalysisStage.EXTRACTING: "提取术语",
        AnalysisStage.DEEP_ANALYSIS: "深度分析",
        AnalysisStage.COMPLETED: "完成",
        AnalysisStage.ERROR: "错误",
    }
    
    info = ProgressInfo(
        stage=stage.value,
        stage_label=stage_labels.get(stage, stage.value),
        progress=min(100, max(0, progress)),
        message=message,
        detail=detail,
    )
    _progress_store[task_id] = info
    
    # 触发回调
    if task_id in _progress_callbacks:
        try:
            _progress_callbacks[task_id](info)
        except Exception:
            pass


def clear_progress(task_id: str):
    """清除任务进度"""
    _progress_store.pop(task_id, None)
    _progress_callbacks.pop(task_id, None)


def register_callback(task_id: str, callback: Callable):
    """注册进度回调"""
    _progress_callbacks[task_id] = callback


def unregister_callback(task_id: str):
    """注销进度回调"""
    _progress_callbacks.pop(task_id, None)


def progress_to_dict(info: ProgressInfo) -> dict:
    """转换为字典"""
    return asdict(info)


class ProgressReporter:
    """进度报告器 - 在分析过程中使用"""
    
    def __init__(self, task_id: str):
        self.task_id = task_id
        self._current_stage = AnalysisStage.INIT
        self._stage_weights = {
            AnalysisStage.INIT: 5,
            AnalysisStage.PARSING_FILES: 15,
            AnalysisStage.ALIGNING: 40,
            AnalysisStage.EXTRACTING: 20,
            AnalysisStage.DEEP_ANALYSIS: 20,
        }
        self._stage_start_progress = {
            AnalysisStage.INIT: 0,
            AnalysisStage.PARSING_FILES: 5,
            AnalysisStage.ALIGNING: 20,
            AnalysisStage.EXTRACTING: 60,
            AnalysisStage.DEEP_ANALYSIS: 80,
        }
    
    def start(self):
        """开始分析"""
        set_progress(
            self.task_id,
            AnalysisStage.INIT,
            0,
            "准备开始分析...",
        )
    
    def init_progress(self, message: str, detail: Optional[str] = None):
        """初始化阶段进度（0-5%）"""
        set_progress(
            self.task_id,
            AnalysisStage.INIT,
            2,
            message,
            detail,
        )
    
    def parsing_files(self, current: int, total: int, filename: str):
        """解析文件阶段（5-20%）"""
        self._current_stage = AnalysisStage.PARSING_FILES
        # 从5%到20%，共15%的权重
        base = 5
        weight = 15
        progress = base + (current / max(total, 1)) * weight
        
        set_progress(
            self.task_id,
            AnalysisStage.PARSING_FILES,
            progress,
            f"解析文件 ({current}/{total})",
            filename,
        )
    
    def aligning(self, current: int, total: int, detail: Optional[str] = None):
        """对齐阶段（20-60%）"""
        self._current_stage = AnalysisStage.ALIGNING
        base = 20
        weight = 40
        progress = base + (current / max(total, 1)) * weight
        
        set_progress(
            self.task_id,
            AnalysisStage.ALIGNING,
            progress,
            f"对齐文本 ({current}/{total})",
            detail,
        )
    
    def extracting(self, message: str, detail: Optional[str] = None):
        """提取阶段（60-80%）"""
        self._current_stage = AnalysisStage.EXTRACTING
        progress = 65  # 提取阶段中间值
        
        set_progress(
            self.task_id,
            AnalysisStage.EXTRACTING,
            progress,
            message,
            detail,
        )
    
    def deep_analysis(self, step: str, detail: Optional[str] = None):
        """深度分析阶段（80-95%）"""
        self._current_stage = AnalysisStage.DEEP_ANALYSIS
        progress = 85  # 深度分析阶段中间值
        
        set_progress(
            self.task_id,
            AnalysisStage.DEEP_ANALYSIS,
            progress,
            step,
            detail,
        )
    
    def complete(self, terms_count: int, tm_count: int):
        """完成"""
        set_progress(
            self.task_id,
            AnalysisStage.COMPLETED,
            100,
            "分析完成",
            f"提取术语 {terms_count} 条，翻译记忆 {tm_count} 条",
        )
    
    def error(self, message: str):
        """错误"""
        set_progress(
            self.task_id,
            AnalysisStage.ERROR,
            0,
            "分析失败",
            message,
        )
