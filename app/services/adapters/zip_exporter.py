"""
ZIP 导出器 - 将翻译后的内容导出为 ZIP 格式

递归处理压缩包中的文件并重新打包。
"""
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Dict


class ZipExporter:
    """ZIP 导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        file_translations: Dict[str, Dict[str, str]] = None,
    ) -> bytes:
        """导出翻译后的 ZIP 文件
        
        Args:
            original_bytes: 原始 ZIP 文件字节
            translations: 全局翻译映射 source -> target
            file_translations: 按文件路径的翻译映射 {path: {source: target}}
            
        Returns:
            bytes: 翻译后的 ZIP 文件字节
        """
        file_translations = file_translations or {}
        
        input_zip = zipfile.ZipFile(BytesIO(original_bytes), 'r')
        output_buffer = BytesIO()
        output_zip = zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED)
        
        # 延迟导入
        from app.services.adapters import get_registry
        registry = get_registry()
        
        for name in input_zip.namelist():
            file_bytes = input_zip.read(name)
            
            # 目录或不支持的文件，直接复制
            if name.endswith('/') or not registry.is_supported(name):
                output_zip.writestr(name, file_bytes)
                continue
            
            # 获取该文件的翻译
            file_trans = file_translations.get(name, {})
            combined_trans = {**translations, **file_trans}
            
            if not combined_trans:
                output_zip.writestr(name, file_bytes)
                continue
            
            # 尝试导出翻译后的文件
            try:
                exported = self._export_file(name, file_bytes, combined_trans)
                output_zip.writestr(name, exported)
            except Exception:
                # 导出失败，保留原文件
                output_zip.writestr(name, file_bytes)
        
        input_zip.close()
        output_zip.close()
        
        return output_buffer.getvalue()

    def _export_file(self, name: str, file_bytes: bytes, translations: Dict[str, str]) -> bytes:
        """导出单个文件"""
        ext = Path(name).suffix.lower()
        
        # 根据扩展名选择导出器
        exporters = {
            '.txt': self._export_txt,
            '.html': self._export_html,
            '.htm': self._export_html,
            '.properties': self._export_properties,
            '.po': self._export_po,
            '.pot': self._export_po,
            '.strings': self._export_strings,
            '.md': self._export_markdown,
            '.markdown': self._export_markdown,
            '.srt': self._export_srt,
            '.csv': self._export_csv,
            '.json': self._export_json,
            '.yaml': self._export_yaml,
            '.yml': self._export_yaml,
        }
        
        exporter = exporters.get(ext)
        if exporter:
            return exporter(file_bytes, translations)
        
        # 不支持的格式，返回原文件
        return file_bytes

    def _export_txt(self, data: bytes, trans: Dict[str, str]) -> bytes:
        content = data.decode('utf-8', errors='replace')
        for source, target in trans.items():
            content = content.replace(source, target)
        return content.encode('utf-8')

    def _export_html(self, data: bytes, trans: Dict[str, str]) -> bytes:
        from app.services.adapters.html_exporter import HtmlExporter
        return HtmlExporter().export(data, trans)

    def _export_properties(self, data: bytes, trans: Dict[str, str]) -> bytes:
        from app.services.adapters.properties_exporter import PropertiesExporter
        return PropertiesExporter().export(data, trans)

    def _export_po(self, data: bytes, trans: Dict[str, str]) -> bytes:
        from app.services.adapters.po_exporter import PoExporter
        return PoExporter().export(data, trans)

    def _export_strings(self, data: bytes, trans: Dict[str, str]) -> bytes:
        from app.services.adapters.strings_exporter import StringsExporter
        return StringsExporter().export(data, trans)

    def _export_markdown(self, data: bytes, trans: Dict[str, str]) -> bytes:
        from app.services.adapters.markdown_exporter import MarkdownExporter
        return MarkdownExporter().export(data, trans)

    def _export_srt(self, data: bytes, trans: Dict[str, str]) -> bytes:
        from app.services.adapters.srt_exporter import SrtExporter
        return SrtExporter().export(data, trans)

    def _export_csv(self, data: bytes, trans: Dict[str, str]) -> bytes:
        from app.services.adapters.csv_exporter import CsvExporter
        return CsvExporter().export(data, trans)

    def _export_json(self, data: bytes, trans: Dict[str, str]) -> bytes:
        import json
        content = data.decode('utf-8', errors='replace')
        obj = json.loads(content)
        self._translate_json(obj, trans)
        return json.dumps(obj, ensure_ascii=False, indent=2).encode('utf-8')

    def _export_yaml(self, data: bytes, trans: Dict[str, str]) -> bytes:
        import yaml
        content = data.decode('utf-8', errors='replace')
        obj = yaml.safe_load(content)
        self._translate_json(obj, trans)
        return yaml.dump(obj, allow_unicode=True, default_flow_style=False).encode('utf-8')

    def _translate_json(self, obj, trans: Dict[str, str]) -> None:
        """递归翻译 JSON/YAML 对象"""
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, str) and value in trans:
                    obj[key] = trans[value]
                else:
                    self._translate_json(value, trans)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, str) and item in trans:
                    obj[i] = trans[item]
                else:
                    self._translate_json(item, trans)
