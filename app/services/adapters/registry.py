"""
适配器注册表模块 - 管理所有格式适配器的注册和发现

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6
"""
import logging
from pathlib import Path
from typing import Dict, List

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import UnsupportedFormatError


logger = logging.getLogger(__name__)


class AdapterRegistry:
    """适配器注册表
    
    管理文件扩展名到适配器实例的映射，支持运行时注册新适配器。
    """

    def __init__(self):
        self._adapters: Dict[str, FormatAdapter] = {}

    def register(self, adapter: FormatAdapter) -> None:
        """注册适配器
        
        Args:
            adapter: 实现 FormatAdapter 接口的适配器实例
            
        Raises:
            TypeError: 当 adapter 未实现 FormatAdapter 接口时
        """
        # 验证接口合规性
        if not isinstance(adapter, FormatAdapter):
            raise TypeError(
                f"Adapter must implement FormatAdapter interface, "
                f"got {type(adapter).__name__}"
            )
        
        # 注册所有支持的扩展名
        for ext in adapter.supported_extensions():
            ext_lower = ext.lower()
            if ext_lower in self._adapters:
                logger.info(
                    f"Overriding adapter for extension {ext_lower}: "
                    f"{type(self._adapters[ext_lower]).__name__} -> "
                    f"{type(adapter).__name__}"
                )
            self._adapters[ext_lower] = adapter
            logger.debug(f"Registered adapter {type(adapter).__name__} for {ext_lower}")

    def get_adapter(self, filename: str) -> FormatAdapter:
        """根据文件名获取适配器
        
        Args:
            filename: 文件名或文件路径
            
        Returns:
            FormatAdapter: 对应的适配器实例
            
        Raises:
            UnsupportedFormatError: 当没有适配器支持该扩展名时
        """
        ext = Path(filename).suffix.lower()
        if ext not in self._adapters:
            raise UnsupportedFormatError(ext)
        return self._adapters[ext]

    def list_supported_extensions(self) -> List[str]:
        """列出所有支持的扩展名
        
        Returns:
            List[str]: 已注册的扩展名列表
        """
        return list(self._adapters.keys())

    def is_supported(self, filename: str) -> bool:
        """检查文件是否被支持
        
        Args:
            filename: 文件名或文件路径
            
        Returns:
            bool: 如果有适配器支持该扩展名返回 True
        """
        ext = Path(filename).suffix.lower()
        return ext in self._adapters


# 全局注册表实例
_global_registry: AdapterRegistry | None = None


def get_registry() -> AdapterRegistry:
    """获取全局注册表实例（单例模式）"""
    global _global_registry
    if _global_registry is None:
        _global_registry = AdapterRegistry()
    return _global_registry


def register_adapter(adapter: FormatAdapter) -> None:
    """向全局注册表注册适配器的便捷函数"""
    get_registry().register(adapter)
