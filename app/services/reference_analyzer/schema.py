"""翻译规格的数据结构定义"""

from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum


class FileType(Enum):
    """参考文件类型"""
    TERMINOLOGY = "terminology"       # 术语表
    TRANSLATION_MEMORY = "tm"         # 翻译记忆（双语句对）
    STYLE_GUIDE = "style_guide"       # 风格指南
    BILINGUAL = "bilingual"           # 双语对照文件
    MIXED = "mixed"                   # 混合型
    UNKNOWN = "unknown"


class IndustryDomain(Enum):
    """行业领域"""
    LEGAL = "legal"                   # 法律
    FINANCE = "finance"               # 金融
    MEDICAL = "medical"               # 医疗
    TECH = "tech"                     # 科技
    MARKETING = "marketing"           # 营销
    GENERAL = "general"               # 通用


class TranslationStrategy(Enum):
    """翻译策略"""
    LITERAL = "literal"               # 直译
    FREE = "free"                     # 意译
    BALANCED = "balanced"             # 平衡


@dataclass
class TermEntry:
    """术语条目"""
    source: str
    target: str
    context: Optional[str] = None     # 使用语境说明
    category: Optional[str] = None    # 类别：brand/product/technical/legal/title


@dataclass
class SentencePair:
    """翻译记忆句对"""
    source: str
    target: str
    similarity: float = 0.0           # 与当前待翻译文本的相似度


@dataclass
class StyleGuide:
    """风格指南"""
    tone: Optional[str] = None            # "formal", "casual", "neutral"
    person: Optional[str] = None          # "first", "third"
    preferences: List[str] = field(default_factory=list)   # 风格偏好描述
    avoid: List[str] = field(default_factory=list)         # 禁用词/禁用表达


@dataclass
class Abbreviation:
    """缩略语"""
    abbr: str                         # 缩写
    full_form: str                    # 全称
    translation: Optional[str] = None # 译法


@dataclass
class TermConflict:
    """译法冲突"""
    source: str                       # 原文
    translations: List[str] = field(default_factory=list)  # 多种译法
    recommendation: Optional[str] = None  # 推荐译法
    note: Optional[str] = None        # 说明


@dataclass 
class RiskPoint:
    """易错点/风险提示"""
    category: str                     # 类别：legal/number/format/chinglish/ocr
    description: str                  # 描述
    examples: List[str] = field(default_factory=list)  # 示例
    suggestion: Optional[str] = None  # 建议


@dataclass
class FormatSpec:
    """排版格式规范"""
    number_format: Optional[str] = None      # 数字格式：1,000 vs 1000
    date_format: Optional[str] = None        # 日期格式
    currency_format: Optional[str] = None    # 货币格式
    unit_format: Optional[str] = None        # 单位格式
    heading_style: Optional[str] = None      # 标题风格
    list_style: Optional[str] = None         # 列表风格
    notes: List[str] = field(default_factory=list)


@dataclass
class AnalysisReport:
    """深度分析报告 - 供用户审阅"""
    
    # 领域与策略
    industry: IndustryDomain = IndustryDomain.GENERAL
    industry_confidence: float = 0.0          # 置信度
    industry_signals: List[str] = field(default_factory=list)  # 判断依据
    
    strategy: TranslationStrategy = TranslationStrategy.BALANCED
    strategy_reasoning: str = ""              # 策略建议理由
    preserve_structure: bool = False          # 是否保留原句结构
    
    # 客户风格画像
    client_profile: str = ""                  # 客户风格总结
    formality_level: int = 3                  # 正式程度 1-5
    
    # 特殊术语
    brand_terms: List[TermEntry] = field(default_factory=list)      # 品牌/产品固定译名
    abbreviations: List[Abbreviation] = field(default_factory=list) # 缩略语
    
    # 问题检测
    term_conflicts: List[TermConflict] = field(default_factory=list)  # 译法冲突
    risk_points: List[RiskPoint] = field(default_factory=list)        # 易错点
    
    # 格式规范
    format_spec: FormatSpec = field(default_factory=FormatSpec)
    
    # 固定句型（不可改写）
    fixed_patterns: List[SentencePair] = field(default_factory=list)
    
    # 分析置信度
    overall_confidence: float = 0.0
    analysis_notes: List[str] = field(default_factory=list)


@dataclass
class Constraints:
    """硬性约束 - 必须遵守"""
    terminology: List[TermEntry] = field(default_factory=list)
    forbidden_words: List[str] = field(default_factory=list)


@dataclass
class References:
    """软性参考 - 可供借鉴"""
    translation_memory: List[SentencePair] = field(default_factory=list)
    style: Optional[StyleGuide] = None


@dataclass
class TranslationProfile:
    """翻译规格 - 从参考文件中提取的完整规格"""
    source_lang: str = "en"
    target_lang: str = "zh"
    constraints: Constraints = field(default_factory=Constraints)
    references: References = field(default_factory=References)
    source_files: List[str] = field(default_factory=list)
    
    # 深度分析报告
    analysis: Optional[AnalysisReport] = None
