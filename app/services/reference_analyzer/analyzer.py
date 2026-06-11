"""深度分析器 - 从参考文件中提取全面的翻译规范分析报告"""

import re
import json
from typing import List, Optional
from collections import Counter

from .schema import (
    AnalysisReport, IndustryDomain, TranslationStrategy,
    TermEntry, SentencePair, Abbreviation, TermConflict,
    RiskPoint, FormatSpec, TranslationProfile
)


class DeepAnalyzer:
    """深度分析器 - 使用 LLM 进行全面分析"""
    
    def __init__(self, llm_helper=None):
        self.llm_helper = llm_helper
    
    def analyze(self, profile: TranslationProfile) -> AnalysisReport:
        """执行深度分析，生成分析报告"""
        report = AnalysisReport()
        
        # 收集所有文本用于分析
        all_sources = [t.source for t in profile.constraints.terminology]
        all_targets = [t.target for t in profile.constraints.terminology]
        all_sources.extend([p.source for p in profile.references.translation_memory])
        all_targets.extend([p.target for p in profile.references.translation_memory])
        
        print(f"[ReferenceAnalyzer] 深度分析开始: 术语={len(profile.constraints.terminology)}, TM={len(profile.references.translation_memory)}")
        
        # 1. 规则分析（不依赖 LLM）
        report.term_conflicts = self._detect_term_conflicts(profile)
        report.abbreviations = self._extract_abbreviations(all_sources, all_targets)
        report.format_spec = self._analyze_format(all_sources, all_targets)
        report.risk_points = self._detect_risk_points(all_sources, all_targets)
        report.brand_terms = self._identify_brand_terms(profile.constraints.terminology)
        report.fixed_patterns = self._identify_fixed_patterns(profile.references.translation_memory)
        
        print(f"[ReferenceAnalyzer] 规则分析完成: 冲突={len(report.term_conflicts)}, 缩写={len(report.abbreviations)}, 风险={len(report.risk_points)}")
        
        # 2. LLM 深度分析
        if self.llm_helper and (all_sources or all_targets):
            print(f"[ReferenceAnalyzer] 开始LLM深度分析...")
            llm_analysis = self._llm_deep_analysis(profile, all_sources, all_targets)
            if llm_analysis:
                report.industry = llm_analysis.get('industry', IndustryDomain.GENERAL)
                report.industry_confidence = llm_analysis.get('industry_confidence', 0.0)
                report.industry_signals = llm_analysis.get('industry_signals', [])
                report.strategy = llm_analysis.get('strategy', TranslationStrategy.BALANCED)
                report.strategy_reasoning = llm_analysis.get('strategy_reasoning', '')
                report.preserve_structure = llm_analysis.get('preserve_structure', False)
                report.client_profile = llm_analysis.get('client_profile', '')
                report.formality_level = llm_analysis.get('formality_level', 3)
                report.analysis_notes = llm_analysis.get('notes', [])
                
                # LLM 可能发现更多风险点
                if llm_analysis.get('additional_risks'):
                    report.risk_points.extend(llm_analysis['additional_risks'])
                
                print(f"[ReferenceAnalyzer] LLM分析完成: 行业={report.industry.value}, 策略={report.strategy.value}")
            else:
                print(f"[ReferenceAnalyzer] LLM分析未返回结果，使用默认值")
        else:
            if not self.llm_helper:
                print(f"[ReferenceAnalyzer] 未配置LLM Helper，跳过深度分析")
            else:
                print(f"[ReferenceAnalyzer] 无文本内容，跳过深度分析")
        
        # 3. 计算整体置信度
        report.overall_confidence = self._calculate_confidence(report, profile)
        
        print(f"[ReferenceAnalyzer] 深度分析完成: 置信度={report.overall_confidence:.2f}")
        
        return report
    
    def _detect_term_conflicts(self, profile: TranslationProfile) -> List[TermConflict]:
        """检测同一原文有多种译法的冲突"""
        conflicts = []
        
        # 收集所有术语和 TM
        all_pairs = [(t.source.lower().strip(), t.target.strip()) 
                     for t in profile.constraints.terminology]
        all_pairs.extend([(p.source.lower().strip(), p.target.strip()) 
                          for p in profile.references.translation_memory])
        
        # 按原文分组
        source_to_targets = {}
        for source, target in all_pairs:
            # 提取短语（3-5个词）
            words = source.split()
            if len(words) <= 5:
                if source not in source_to_targets:
                    source_to_targets[source] = []
                if target not in source_to_targets[source]:
                    source_to_targets[source].append(target)
        
        # 找出有多种译法的
        for source, targets in source_to_targets.items():
            if len(targets) > 1:
                conflicts.append(TermConflict(
                    source=source,
                    translations=targets,
                    note=f"发现 {len(targets)} 种不同译法，请确认统一"
                ))
        
        return conflicts[:20]  # 最多返回20个
    
    def _extract_abbreviations(self, sources: List[str], targets: List[str]) -> List[Abbreviation]:
        """提取缩略语"""
        abbrs = []
        seen = set()
        
        # 匹配大写缩写
        abbr_pattern = re.compile(r'\b([A-Z]{2,6})\b')
        # 匹配括号中的全称
        full_pattern = re.compile(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*\(([A-Z]{2,6})\)')
        
        all_text = ' '.join(sources)
        
        # 从 "Full Name (ABBR)" 格式提取
        for match in full_pattern.finditer(all_text):
            full_form, abbr = match.groups()
            if abbr not in seen:
                seen.add(abbr)
                abbrs.append(Abbreviation(abbr=abbr, full_form=full_form))
        
        # 单独的缩写
        for match in abbr_pattern.finditer(all_text):
            abbr = match.group(1)
            if abbr not in seen and len(abbr) >= 2:
                # 排除常见非缩写词
                if abbr not in {'THE', 'AND', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL'}:
                    seen.add(abbr)
                    abbrs.append(Abbreviation(abbr=abbr, full_form=""))
        
        return abbrs[:30]
    
    def _analyze_format(self, sources: List[str], targets: List[str]) -> FormatSpec:
        """分析格式规范"""
        spec = FormatSpec()
        all_text = ' '.join(sources + targets)
        
        # 数字格式
        if re.search(r'\d{1,3},\d{3}', all_text):
            spec.number_format = "千位分隔符（1,000）"
        elif re.search(r'\d{4,}', all_text):
            spec.number_format = "无分隔符（1000）"
        
        # 日期格式
        if re.search(r'\d{4}年\d{1,2}月\d{1,2}日', all_text):
            spec.date_format = "YYYY年M月D日"
        elif re.search(r'\d{1,2}/\d{1,2}/\d{4}', all_text):
            spec.date_format = "MM/DD/YYYY"
        elif re.search(r'\d{4}-\d{2}-\d{2}', all_text):
            spec.date_format = "YYYY-MM-DD"
        
        # 货币格式
        if re.search(r'\$[\d,]+', all_text):
            spec.currency_format = "$金额"
        if re.search(r'USD\s*[\d,]+', all_text):
            spec.currency_format = "USD 金额"
        if re.search(r'人民币|RMB|CNY|￥', all_text):
            if spec.currency_format:
                spec.currency_format += " / 人民币/RMB"
            else:
                spec.currency_format = "人民币/RMB"
        
        return spec

    def _detect_risk_points(self, sources: List[str], targets: List[str]) -> List[RiskPoint]:
        """检测易错点"""
        risks = []
        all_source = ' '.join(sources)
        all_target = ' '.join(targets)
        
        # 法律责任表达
        legal_patterns = [
            r'shall\s+(not\s+)?be\s+(liable|responsible)',
            r'indemnif',
            r'warrant',
            r'represent',
            r'covenant',
            r'承担.*责任',
            r'免责',
            r'赔偿',
        ]
        legal_examples = []
        for pattern in legal_patterns:
            matches = re.findall(f'.{{0,30}}{pattern}.{{0,30}}', all_source + all_target, re.IGNORECASE)
            legal_examples.extend(matches[:2])
        if legal_examples:
            risks.append(RiskPoint(
                category="legal",
                description="包含法律责任相关表达，需确保译法准确",
                examples=legal_examples[:5],
                suggestion="法律术语建议参考标准法律词典或客户已有译法"
            ))
        
        # 数字金额
        money_patterns = re.findall(r'[\$￥]\s*[\d,]+(?:\.\d+)?|\d+(?:,\d{3})*(?:\.\d+)?\s*(?:美元|元|USD|RMB)', 
                                     all_source + all_target)
        if money_patterns:
            risks.append(RiskPoint(
                category="number",
                description="包含金额数字，需核对数字准确性",
                examples=money_patterns[:5],
                suggestion="翻译后务必核对金额数字是否一致"
            ))
        
        # 中式英语检测
        chinglish_patterns = [
            (r'do\s+good\s+to', "do good to → benefit/help"),
            (r'open\s+the\s+door\s+of', "open the door of → lead to"),
            (r'more\s+and\s+more\s+\w+er', "more and more Xer → increasingly"),
        ]
        for pattern, suggestion in chinglish_patterns:
            if re.search(pattern, all_source, re.IGNORECASE):
                risks.append(RiskPoint(
                    category="chinglish",
                    description="可能存在中式英语表达",
                    examples=[suggestion],
                    suggestion="建议使用更地道的英语表达"
                ))
                break
        
        return risks
    
    def _identify_brand_terms(self, terms: List[TermEntry]) -> List[TermEntry]:
        """识别品牌/产品专有名词"""
        brand_terms = []
        
        for term in terms:
            source = term.source
            # 特征：首字母大写、全大写、包含®™、短词
            if (source.istitle() or source.isupper() or 
                '®' in source or '™' in source or
                (len(source.split()) <= 2 and source[0].isupper())):
                brand_term = TermEntry(
                    source=term.source,
                    target=term.target,
                    context=term.context,
                    category="brand"
                )
                brand_terms.append(brand_term)
        
        return brand_terms[:20]
    
    def _identify_fixed_patterns(self, tm: List[SentencePair]) -> List[SentencePair]:
        """识别不可改写的固定句型"""
        fixed = []
        
        # 特征：包含法律用语、声明、免责等
        fixed_keywords = [
            'hereby', 'whereas', 'notwithstanding', 'pursuant to',
            'in witness whereof', 'shall be deemed',
            '特此', '鉴于', '尽管如此', '根据', '兹证明'
        ]
        
        for pair in tm:
            text = (pair.source + pair.target).lower()
            if any(kw in text for kw in fixed_keywords):
                fixed.append(pair)
        
        return fixed[:20]
    
    def _llm_deep_analysis(self, profile: TranslationProfile, 
                           sources: List[str], targets: List[str]) -> Optional[dict]:
        """使用 LLM 进行深度分析"""
        if not self.llm_helper:
            return None
        
        # 准备样本
        sample_pairs = []
        for t in profile.constraints.terminology[:10]:
            sample_pairs.append(f"术语: {t.source} → {t.target}")
        for p in profile.references.translation_memory[:10]:
            sample_pairs.append(f"句对: {p.source[:100]} → {p.target[:100]}")
        
        if not sample_pairs:
            return None
        
        samples_text = '\n'.join(sample_pairs)
        
        system = "你是资深翻译项目经理，擅长分析翻译参考文件并制定翻译策略。"
        user = f"""分析以下从客户参考文件中提取的翻译样本，给出专业分析：

{samples_text}

请分析并输出 JSON：
{{
    "industry": "领域代码(legal/finance/medical/tech/marketing/general)",
    "industry_confidence": 0.0-1.0的置信度,
    "industry_signals": ["判断依据1", "判断依据2"],
    "strategy": "翻译策略(literal/free/balanced)",
    "strategy_reasoning": "策略建议理由",
    "preserve_structure": true/false是否需要保留原句结构,
    "formality_level": 1-5的正式程度,
    "client_profile": "客户风格画像总结（50字内）",
    "notes": ["其他注意事项"],
    "additional_risks": [
        {{"category": "类别", "description": "描述", "suggestion": "建议"}}
    ]
}}

只输出JSON："""

        try:
            result = self.llm_helper._call(system, user, temperature=0.2)
            result = result.strip()
            
            if "```" in result:
                result = re.sub(r'```json\s*', '', result)
                result = re.sub(r'```\s*', '', result)
            
            data = json.loads(result)
            
            # 转换枚举
            industry_map = {
                'legal': IndustryDomain.LEGAL,
                'finance': IndustryDomain.FINANCE,
                'medical': IndustryDomain.MEDICAL,
                'tech': IndustryDomain.TECH,
                'marketing': IndustryDomain.MARKETING,
                'general': IndustryDomain.GENERAL,
            }
            data['industry'] = industry_map.get(data.get('industry', 'general'), IndustryDomain.GENERAL)
            
            strategy_map = {
                'literal': TranslationStrategy.LITERAL,
                'free': TranslationStrategy.FREE,
                'balanced': TranslationStrategy.BALANCED,
            }
            data['strategy'] = strategy_map.get(data.get('strategy', 'balanced'), TranslationStrategy.BALANCED)
            
            # 转换 additional_risks
            if data.get('additional_risks'):
                data['additional_risks'] = [
                    RiskPoint(
                        category=r.get('category', 'other'),
                        description=r.get('description', ''),
                        suggestion=r.get('suggestion', '')
                    )
                    for r in data['additional_risks']
                ]
            
            return data
            
        except Exception as e:
            print(f"[ReferenceAnalyzer] LLM 分析失败: {e}")
            return None
    
    def _calculate_confidence(self, report: AnalysisReport, profile: TranslationProfile) -> float:
        """计算分析置信度"""
        score = 0.0
        
        # 有足够的术语
        term_count = len(profile.constraints.terminology)
        if term_count >= 20:
            score += 0.3
        elif term_count >= 10:
            score += 0.2
        elif term_count >= 5:
            score += 0.1
        
        # 有 TM
        tm_count = len(profile.references.translation_memory)
        if tm_count >= 10:
            score += 0.2
        elif tm_count >= 5:
            score += 0.1
        
        # 有行业判断
        if report.industry != IndustryDomain.GENERAL:
            score += 0.2
        
        # 有客户画像
        if report.client_profile:
            score += 0.15
        
        # 有格式规范
        if report.format_spec.number_format or report.format_spec.date_format:
            score += 0.1
        
        # 识别了缩略语
        if report.abbreviations:
            score += 0.05
        
        return min(score, 1.0)
