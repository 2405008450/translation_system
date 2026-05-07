"""
HTML 导出器 - 将翻译后的内容导出为 HTML 格式

保留原始 HTML 结构，仅替换文本内容。
"""
import re
from html.parser import HTMLParser
from typing import Dict, List, Tuple, Optional


# 块级元素
BLOCK_ELEMENTS = {
    'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'li', 'td', 'th', 'dt', 'dd', 'blockquote', 'figcaption',
    'article', 'section', 'header', 'footer', 'main', 'aside',
    'address', 'pre', 'caption', 'summary',
}

# 需要跳过的元素（不替换文本）
SKIP_ELEMENTS = {
    'script', 'style', 'code', 'pre', 'textarea',
    'noscript', 'template', 'svg', 'math',
}

# 内联元素
INLINE_ELEMENTS = {
    'a', 'span', 'strong', 'b', 'em', 'i', 'u', 's',
    'mark', 'small', 'sub', 'sup', 'abbr', 'cite', 'q',
    'br', 'wbr', 'img', 'input', 'label',
}


class TextBlock:
    """表示一个可翻译的文本块"""
    def __init__(self, start_pos: int, end_pos: int, text: str, normalized_text: str):
        self.start_pos = start_pos  # 在原始HTML中的起始位置
        self.end_pos = end_pos      # 在原始HTML中的结束位置
        self.text = text            # 原始文本（包含标签）
        self.normalized_text = normalized_text  # 规范化后的纯文本


class HtmlTextExtractor(HTMLParser):
    """HTML 文本提取器 - 提取可翻译的文本块及其位置"""
    
    def __init__(self, content: str):
        super().__init__()
        self.content = content
        self.text_blocks: List[TextBlock] = []
        
        # 当前块的状态
        self.current_block_start: Optional[int] = None
        self.current_texts: List[str] = []  # 当前块内的文本片段
        self.skip_depth = 0
        self.tag_stack: List[str] = []
        self.block_depth = 0
        
    def get_pos(self) -> int:
        """获取当前解析位置在原始内容中的字节位置"""
        line, col = self.getpos()
        pos = 0
        for i, l in enumerate(self.content.split('\n')):
            if i < line - 1:
                pos += len(l) + 1  # +1 for newline
            else:
                pos += col
                break
        return pos
    
    def handle_starttag(self, tag: str, attrs: list):
        tag_lower = tag.lower()
        self.tag_stack.append(tag_lower)
        
        if tag_lower in SKIP_ELEMENTS:
            self._flush_block()
            self.skip_depth += 1
            return
        
        if self.skip_depth > 0:
            return
        
        # 块级元素开始
        if tag_lower in BLOCK_ELEMENTS:
            self._flush_block()
            self.block_depth += 1
    
    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        
        if self.tag_stack and self.tag_stack[-1] == tag_lower:
            self.tag_stack.pop()
        
        if tag_lower in SKIP_ELEMENTS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        
        if self.skip_depth > 0:
            return
        
        # 块级元素结束
        if tag_lower in BLOCK_ELEMENTS:
            self._flush_block()
            self.block_depth = max(0, self.block_depth - 1)
    
    def handle_data(self, data: str):
        if self.skip_depth > 0:
            return
        
        # 记录文本
        if data.strip():
            if self.current_block_start is None:
                self.current_block_start = self.get_pos()
            self.current_texts.append(data)
    
    def _flush_block(self):
        """保存当前文本块"""
        if self.current_texts and self.current_block_start is not None:
            # 合并所有文本片段
            combined_text = ''.join(self.current_texts)
            normalized = self._normalize_whitespace(combined_text)
            
            if normalized:
                # 计算结束位置
                end_pos = self.get_pos()
                
                self.text_blocks.append(TextBlock(
                    start_pos=self.current_block_start,
                    end_pos=end_pos,
                    text=combined_text,
                    normalized_text=normalized,
                ))
        
        self.current_block_start = None
        self.current_texts = []
    
    def _normalize_whitespace(self, text: str) -> str:
        """规范化空白字符"""
        return ' '.join(text.split())
    
    def close(self):
        """解析结束时刷新剩余内容"""
        self._flush_block()
        super().close()


class HtmlExporter:
    """HTML 导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
    ) -> bytes:
        """导出翻译后的 HTML
        
        Args:
            original_bytes: 原始 HTML 文件字节
            translations: source_text -> target_text 的映射
            
        Returns:
            bytes: 翻译后的 HTML 文件字节
        """
        content = self._decode_content(original_bytes)
        
        # 构建规范化后的源文到译文映射
        normalized_translations = self._build_normalized_translations(translations)
        
        # 使用智能替换
        result = self._smart_replace(content, normalized_translations)
        
        # 后处理：清理括号内的多余空格
        result = self._fix_bracket_spacing(result)
        
        return result.encode('utf-8')

    def _fix_bracket_spacing(self, content: str) -> str:
        """修复括号内的多余空格
        
        将 "( 2 )" 这样的格式修复为 "(2)"
        处理各种括号类型：() [] {} （）【】「」『』《》〈〉
        """
        # 定义括号对
        bracket_pairs = [
            (r'\(\s+', '('),      # ( 后的空格
            (r'\s+\)', ')'),      # ) 前的空格
            (r'\[\s+', '['),      # [ 后的空格
            (r'\s+\]', ']'),      # ] 前的空格
            (r'\{\s+', '{'),      # { 后的空格
            (r'\s+\}', '}'),      # } 前的空格
            (r'（\s+', '（'),     # （ 后的空格
            (r'\s+）', '）'),     # ） 前的空格
            (r'【\s+', '【'),     # 【 后的空格
            (r'\s+】', '】'),     # 】 前的空格
            (r'「\s+', '「'),     # 「 后的空格
            (r'\s+」', '」'),     # 」 前的空格
            (r'『\s+', '『'),     # 『 后的空格
            (r'\s+』', '』'),     # 』 前的空格
            (r'《\s+', '《'),     # 《 后的空格
            (r'\s+》', '》'),     # 》 前的空格
            (r'〈\s+', '〈'),     # 〈 后的空格
            (r'\s+〉', '〉'),     # 〉 前的空格
        ]
        
        result = content
        for pattern, replacement in bracket_pairs:
            result = re.sub(pattern, replacement, result)
        
        return result

    def _decode_content(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "iso-8859-1"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode('utf-8', errors='replace')

    def _normalize_whitespace(self, text: str) -> str:
        """规范化空白字符，与解析时保持一致"""
        return ' '.join(text.split())

    def _build_normalized_translations(self, translations: Dict[str, str]) -> Dict[str, str]:
        """构建规范化后的翻译映射"""
        result: Dict[str, str] = {}
        
        for source, target in translations.items():
            if not source or not target:
                continue
            
            # 保留原始映射
            result[source] = target
            
            # 添加规范化后的映射
            normalized_source = self._normalize_whitespace(source)
            if normalized_source and normalized_source not in result:
                result[normalized_source] = target
        
        return result

    def _smart_replace(self, content: str, translations: Dict[str, str]) -> str:
        """智能替换 HTML 中的文本内容
        
        使用多种策略，每种策略只处理前一种没有处理的内容：
        1. 对于简单的标签间文本，直接替换
        2. 对于包含内联标签的复杂文本，提取纯文本进行匹配
        3. 对于块内的部分句子，进行子字符串替换
        4. 对于跨多个span的句子，合并后替换
        """
        # 跟踪已替换的源文
        remaining_translations = dict(translations)
        
        # 策略1：简单的标签间文本替换
        result, replaced = self._replace_simple_text(content, remaining_translations)
        for key in replaced:
            remaining_translations.pop(key, None)
        
        if not remaining_translations:
            return result
        
        # 策略2：处理跨内联标签的文本（整个块匹配）
        result, replaced = self._replace_across_inline_tags(result, remaining_translations)
        for key in replaced:
            remaining_translations.pop(key, None)
        
        if not remaining_translations:
            return result
        
        # 策略3：处理块内的部分句子替换
        result, replaced = self._replace_partial_sentences(result, remaining_translations)
        for key in replaced:
            remaining_translations.pop(key, None)
        
        if not remaining_translations:
            return result
        
        # 策略4：处理跨多个span的句子
        result, replaced = self._replace_across_spans(result, remaining_translations)
        
        return result

    def _replace_simple_text(self, content: str, translations: Dict[str, str]) -> Tuple[str, List[str]]:
        """替换简单的标签间文本
        
        Returns:
            Tuple[替换后的内容, 已替换的源文列表]
        """
        replaced_sources = []
        
        def replace_text_node(match: re.Match) -> str:
            text = match.group(1)
            if not text.strip():
                return match.group(0)
            
            # 规范化 HTML 中的文本以进行匹配
            normalized_text = self._normalize_whitespace(text.strip())
            
            # 尝试规范化匹配
            if normalized_text in translations:
                leading = text[:len(text) - len(text.lstrip())]
                trailing = text[len(text.rstrip()):]
                replaced_sources.append(normalized_text)
                return f">{leading}{translations[normalized_text]}{trailing}<"
            
            # 尝试 strip 后直接匹配
            stripped_text = text.strip()
            if stripped_text in translations:
                leading = text[:len(text) - len(text.lstrip())]
                trailing = text[len(text.rstrip()):]
                replaced_sources.append(stripped_text)
                return f">{leading}{translations[stripped_text]}{trailing}<"
            
            return match.group(0)
        
        # 匹配标签之间的文本
        result = re.sub(r'>([^<]+)<', replace_text_node, content)
        return result, replaced_sources

    def _extract_text_like_parser(self, inner_content: str) -> str:
        """提取文本，模拟HTML解析器的行为
        
        HTML解析器在处理内联标签时会在标签边界添加空格，
        这个方法模拟这种行为以确保匹配成功。
        """
        # 在标签前后添加空格标记，然后去除标签
        # 这模拟了解析器的行为
        text_parts = []
        last_end = 0
        
        for match in re.finditer(r'<[^>]+>', inner_content):
            # 添加标签前的文本
            before_text = inner_content[last_end:match.start()]
            if before_text:
                text_parts.append(before_text)
            
            # 标签位置添加空格（模拟解析器行为）
            # 但只在有实际文本的情况下添加
            if text_parts and text_parts[-1].strip():
                text_parts.append(' ')
            
            last_end = match.end()
        
        # 添加最后一段文本
        if last_end < len(inner_content):
            remaining = inner_content[last_end:]
            if remaining:
                text_parts.append(remaining)
        
        combined = ''.join(text_parts)
        return self._normalize_whitespace(combined)

    def _replace_across_inline_tags(self, content: str, translations: Dict[str, str]) -> Tuple[str, List[str]]:
        """处理跨内联标签的文本替换
        
        例如: <p>Hello <b>World</b>!</p> 中的 "Hello World !" 需要整体替换
        
        Returns:
            Tuple[替换后的内容, 已替换的源文列表]
        """
        replaced_sources = []
        
        # 匹配块级元素的内容
        block_pattern = r'(<(?:p|div|h[1-6]|li|td|th|dt|dd|blockquote|figcaption|article|section|header|footer|main|aside|address|caption|summary)[^>]*>)(.*?)(</(?:p|div|h[1-6]|li|td|th|dt|dd|blockquote|figcaption|article|section|header|footer|main|aside|address|caption|summary)>)'
        
        def replace_block_content(match: re.Match) -> str:
            open_tag = match.group(1)
            inner_content = match.group(2)
            close_tag = match.group(3)
            
            # 使用模拟解析器行为的方法提取文本
            normalized_text = self._extract_text_like_parser(inner_content)
            
            if not normalized_text:
                return match.group(0)
            
            # 检查是否有对应的翻译
            if normalized_text in translations:
                translation = translations[normalized_text]
                
                # 检查内容是否包含内联标签
                if re.search(r'<[^>]+>', inner_content):
                    # 有内联标签，需要智能替换
                    new_content = self._replace_preserving_structure(inner_content, normalized_text, translation)
                    replaced_sources.append(normalized_text)
                    return f"{open_tag}{new_content}{close_tag}"
            
            # 也尝试不带空格的版本（简单去除标签）
            simple_text = re.sub(r'<[^>]+>', '', inner_content)
            simple_normalized = self._normalize_whitespace(simple_text)
            
            if simple_normalized in translations:
                translation = translations[simple_normalized]
                if re.search(r'<[^>]+>', inner_content):
                    new_content = self._replace_preserving_structure(inner_content, simple_normalized, translation)
                    replaced_sources.append(simple_normalized)
                    return f"{open_tag}{new_content}{close_tag}"
            
            return match.group(0)
        
        result = re.sub(block_pattern, replace_block_content, content, flags=re.DOTALL | re.IGNORECASE)
        return result, replaced_sources

    def _replace_preserving_structure(self, inner_content: str, source_text: str, translation: str) -> str:
        """替换内容同时保留HTML结构
        
        策略：将翻译放在第一个文本节点位置，清空其他文本节点，保留所有标签
        """
        # 找出所有文本节点的位置
        text_nodes = []
        
        for match in re.finditer(r'>([^<]*)<', inner_content):
            text = match.group(1)
            text_nodes.append({
                'start': match.start() + 1,  # +1 跳过 >
                'end': match.end() - 1,      # -1 跳过 <
                'text': text,
                'has_content': bool(text.strip()),
            })
        
        # 也检查开头文本（在第一个标签之前）
        first_tag = re.search(r'<', inner_content)
        if first_tag and first_tag.start() > 0:
            text = inner_content[:first_tag.start()]
            text_nodes.insert(0, {
                'start': 0,
                'end': first_tag.start(),
                'text': text,
                'has_content': bool(text.strip()),
            })
        
        # 检查结尾文本（在最后一个标签之后）
        last_tag_end = None
        for m in re.finditer(r'>', inner_content):
            last_tag_end = m.end()
        if last_tag_end and last_tag_end < len(inner_content):
            text = inner_content[last_tag_end:]
            text_nodes.append({
                'start': last_tag_end,
                'end': len(inner_content),
                'text': text,
                'has_content': bool(text.strip()),
            })
        
        if not text_nodes:
            return inner_content
        
        # 在保留HTML结构的情况下替换
        result = inner_content
        offset = 0
        first_content_replaced = False
        
        for node in text_nodes:
            start = node['start'] + offset
            end = node['end'] + offset
            original_text = node['text']
            
            if not node['has_content']:
                # 空白节点保持不变
                continue
            
            # 保留原始的前后空白
            leading = ''
            trailing = ''
            if original_text and original_text[0] in ' \t\n\r':
                for ch in original_text:
                    if ch in ' \t\n\r':
                        leading += ch
                    else:
                        break
            if original_text and original_text[-1] in ' \t\n\r':
                for ch in reversed(original_text):
                    if ch in ' \t\n\r':
                        trailing = ch + trailing
                    else:
                        break
            
            if not first_content_replaced:
                # 第一个有内容的节点：放入翻译
                new_text = leading + translation + trailing
                first_content_replaced = True
            else:
                # 后续节点：只保留空白
                new_text = leading + trailing
            
            result = result[:start] + new_text + result[end:]
            offset += len(new_text) - len(original_text)
        
        return result

    def _replace_partial_sentences(self, content: str, translations: Dict[str, str]) -> Tuple[str, List[str]]:
        """处理块内的部分句子替换
        
        当一个块级元素内包含多个句子时，需要替换其中的部分句子。
        这种情况下，我们直接在文本内容中进行子字符串替换。
        
        Returns:
            Tuple[替换后的内容, 已替换的源文列表]
        """
        # 按源文长度降序排序，优先匹配长文本
        sorted_sources = sorted(translations.keys(), key=len, reverse=True)
        replaced_sources = []
        
        result = content
        
        # 在标签间的文本中查找并替换
        def replace_in_text(match: re.Match) -> str:
            text = match.group(1)
            modified_text = text
            
            # 对每个翻译进行替换
            for source in sorted_sources:
                target = translations[source]
                if not source or not target:
                    continue
                
                if source in modified_text:
                    modified_text = modified_text.replace(source, target)
                    if source not in replaced_sources:
                        replaced_sources.append(source)
            
            if modified_text != text:
                return '>' + modified_text + '<'
            return match.group(0)
        
        result = re.sub(r'>([^<]+)<', replace_in_text, result)
        
        return result, replaced_sources

    def _replace_across_spans(self, content: str, translations: Dict[str, str]) -> Tuple[str, List[str]]:
        """处理跨多个span标签的句子替换，保留HTML结构
        
        例如: <span>5) </span><span>您使用第三方...</span>
        解析器会将这些合并成 "5) 您使用第三方..."
        
        策略：只替换文本节点内容，不改变HTML结构
        
        Returns:
            Tuple[替换后的内容, 已替换的源文列表]
        """
        replaced_sources = []
        
        # 匹配块级元素的内容
        block_pattern = r'(<(?:p|div|h[1-6]|li|td|th|dt|dd|blockquote|figcaption|article|section|header|footer|main|aside|address|caption|summary)[^>]*>)(.*?)(</(?:p|div|h[1-6]|li|td|th|dt|dd|blockquote|figcaption|article|section|header|footer|main|aside|address|caption|summary)>)'
        
        def replace_block_content(match: re.Match) -> str:
            open_tag = match.group(1)
            inner_content = match.group(2)
            close_tag = match.group(3)
            
            # 提取所有文本（模拟解析器行为）
            full_text = self._extract_text_like_parser(inner_content)
            
            if not full_text:
                return match.group(0)
            
            # 检查是否有任何翻译的源文是这个文本的子串
            replacements_needed = []
            for source, target in translations.items():
                if not source or not target:
                    continue
                if source in full_text:
                    replacements_needed.append((source, target))
            
            if not replacements_needed:
                return match.group(0)
            
            # 按长度降序排序，优先处理长文本
            replacements_needed.sort(key=lambda x: len(x[0]), reverse=True)
            
            # 在保留HTML结构的情况下替换文本
            new_inner = self._replace_text_in_nodes(inner_content, replacements_needed)
            
            if new_inner == inner_content:
                return match.group(0)
            
            # 记录已替换的源文
            for source, _ in replacements_needed:
                if source not in replaced_sources:
                    replaced_sources.append(source)
            
            return f"{open_tag}{new_inner}{close_tag}"
        
        result = re.sub(block_pattern, replace_block_content, content, flags=re.DOTALL | re.IGNORECASE)
        return result, replaced_sources

    def _replace_text_in_nodes(self, inner_content: str, replacements: List[Tuple[str, str]]) -> str:
        """在文本节点中进行替换，保留HTML结构
        
        Args:
            inner_content: 块级元素的内部HTML
            replacements: [(source, target), ...] 替换列表
        
        Returns:
            替换后的HTML，保留所有标签和属性
        """
        # 找出所有文本节点及其位置
        text_nodes = []
        for match in re.finditer(r'>([^<]*)<', inner_content):
            text = match.group(1)
            text_nodes.append({
                'start': match.start() + 1,
                'end': match.end() - 1,
                'text': text,
            })
        
        # 也处理开头的文本（在第一个标签之前）
        first_tag = re.search(r'<', inner_content)
        if first_tag and first_tag.start() > 0:
            text_nodes.insert(0, {
                'start': 0,
                'end': first_tag.start(),
                'text': inner_content[:first_tag.start()],
            })
        
        # 处理结尾的文本（在最后一个标签之后）
        last_tag_end = None
        for m in re.finditer(r'>', inner_content):
            last_tag_end = m.end()
        if last_tag_end and last_tag_end < len(inner_content):
            text_nodes.append({
                'start': last_tag_end,
                'end': len(inner_content),
                'text': inner_content[last_tag_end:],
            })
        
        if not text_nodes:
            return inner_content
        
        # 构建合并后的文本（模拟解析器行为）
        combined_parts = []
        for node in text_nodes:
            if node['text'].strip():
                if combined_parts and combined_parts[-1].strip():
                    combined_parts.append(' ')
                combined_parts.append(node['text'])
        combined_text = ''.join(combined_parts)
        normalized_combined = self._normalize_whitespace(combined_text)
        
        # 执行替换
        modified_combined = normalized_combined
        for source, target in replacements:
            if source in modified_combined:
                modified_combined = modified_combined.replace(source, target)
        
        if modified_combined == normalized_combined:
            return inner_content
        
        # 将替换后的文本分配回各个文本节点
        # 策略：将所有翻译后的内容放在第一个非空文本节点，其他节点清空
        result = inner_content
        offset = 0
        first_non_empty_replaced = False
        
        for node in text_nodes:
            start = node['start'] + offset
            end = node['end'] + offset
            original_text = node['text']
            
            if not original_text.strip():
                # 空白节点保持不变
                continue
            
            if not first_non_empty_replaced:
                # 第一个非空节点：放入所有翻译后的内容
                # 保留原始的前后空白
                leading = ''
                trailing = ''
                if original_text and original_text[0] in ' \t\n\r':
                    for ch in original_text:
                        if ch in ' \t\n\r':
                            leading += ch
                        else:
                            break
                if original_text and original_text[-1] in ' \t\n\r':
                    for ch in reversed(original_text):
                        if ch in ' \t\n\r':
                            trailing = ch + trailing
                        else:
                            break
                
                new_text = leading + modified_combined + trailing
                first_non_empty_replaced = True
            else:
                # 后续节点：保留空白，清空内容
                leading = ''
                trailing = ''
                if original_text and original_text[0] in ' \t\n\r':
                    for ch in original_text:
                        if ch in ' \t\n\r':
                            leading += ch
                        else:
                            break
                if original_text and original_text[-1] in ' \t\n\r':
                    for ch in reversed(original_text):
                        if ch in ' \t\n\r':
                            trailing = ch + trailing
                        else:
                            break
                new_text = leading + trailing
            
            result = result[:start] + new_text + result[end:]
            offset += len(new_text) - len(original_text)
        
        return result
