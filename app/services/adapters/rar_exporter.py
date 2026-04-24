"""
RAR 导出器 - 将翻译后的内容导出为 ZIP 格式

由于 RAR 格式是专有的，导出时转换为 ZIP 格式。
"""
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Dict


class RarExporter:
    """RAR 导出器（导出为 ZIP）"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        file_translations: Dict[str, Dict[str, str]] = None,
    ) -> bytes:
        """导出翻译后的文件为 ZIP 格式
        
        Args:
            original_bytes: 原始 RAR 文件字节
            translations: 全局翻译映射
            file_translations: 按文件路径的翻译映射
            
        Returns:
            bytes: 翻译后的 ZIP 文件字节
        """
        file_translations = file_translations or {}
        
        try:
            import rarfile
        except ImportError:
            raise ImportError("需要安装 rarfile 库: pip install rarfile")
        
        # 创建临时文件
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.rar') as tmp:
            tmp.write(original_bytes)
            tmp_path = tmp.name
        
        try:
            rf = rarfile.RarFile(tmp_path)
            
            output_buffer = BytesIO()
            output_zip = zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED)
            
            from app.services.adapters import get_registry
            registry = get_registry()
            
            for info in rf.infolist():
                name = info.filename
                
                if info.is_dir():
                    continue
                
                file_bytes = rf.read(name)
                
                # 获取翻译
                file_trans = file_translations.get(name, {})
                combined_trans = {**translations, **file_trans}
                
                if combined_trans and registry.is_supported(name):
                    try:
                        exported = self._export_file(name, file_bytes, combined_trans)
                        output_zip.writestr(name, exported)
                        continue
                    except Exception:
                        pass
                
                output_zip.writestr(name, file_bytes)
            
            rf.close()
            output_zip.close()
            
        finally:
            os.unlink(tmp_path)
        
        return output_buffer.getvalue()

    def _export_file(self, name: str, file_bytes: bytes, translations: Dict[str, str]) -> bytes:
        """导出单个文件"""
        ext = Path(name).suffix.lower()
        
        exporters = {
            '.txt': self._export_txt,
            '.html': self._export_html,
            '.htm': self._export_html,
            '.properties': self._export_properties,
            '.po': self._export_po,
            '.pot': self._export_po,
            '.json': self._export_json,
            '.yaml': self._export_yaml,
            '.yml': self._export_yaml,
        }
        
        exporter = exporters.get(ext)
        if exporter:
            return exporter(file_bytes, translations)
        
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

    def _export_json(self, data: bytes, trans: Dict[str, str]) -> bytes:
        import json
        content = data.decode('utf-8', errors='replace')
        obj = json.loads(content)
        self._translate_obj(obj, trans)
        return json.dumps(obj, ensure_ascii=False, indent=2).encode('utf-8')

    def _export_yaml(self, data: bytes, trans: Dict[str, str]) -> bytes:
        import yaml
        content = data.decode('utf-8', errors='replace')
        obj = yaml.safe_load(content)
        self._translate_obj(obj, trans)
        return yaml.dump(obj, allow_unicode=True).encode('utf-8')

    def _translate_obj(self, obj, trans: Dict[str, str]) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, str) and value in trans:
                    obj[key] = trans[value]
                else:
                    self._translate_obj(value, trans)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, str) and item in trans:
                    obj[i] = trans[item]
                else:
                    self._translate_obj(item, trans)
