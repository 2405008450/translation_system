# Implementation Plan: Multi-Format Adapter

## Overview

本实施计划将多格式适配器功能分解为可执行的编码任务。按照 V1 优先（DOCX/TXT/XLSX）、V2 扩展（PDF/PPTX/DITA/SVG）的顺序实施。

## Tasks

- [x] 1. 核心基础设施
  - [x] 1.1 创建异常类模块 `app/services/adapters/exceptions.py`
    - 实现 AdapterError, UnsupportedFormatError, ParseError, FileTooLargeError, OCRRequiredError, ExportError
    - _Requirements: 16.1, 16.2_
  - [x] 1.2 创建数据模型模块 `app/services/adapters/models.py`
    - 实现 NodeType 枚举、Block_Node、Document_AST、Segment、Parse_Result
    - 实现 to_dict/from_dict 和 to_json/from_json 方法
    - _Requirements: 3.1, 3.2, 3.3, 4.1_
  - [x] 1.3 编写 AST 序列化往返属性测试
    - **Property 4: AST Serialization Round-Trip**
    - **Validates: Requirements 3.5, 3.6**
  - [x] 1.4 创建适配器基类 `app/services/adapters/base.py`
    - 实现 Format_Adapter 抽象基类
    - _Requirements: 1.1, 1.2, 1.3_
  - [x] 1.5 创建适配器注册表 `app/services/adapters/registry.py`
    - 实现 Adapter_Registry 类
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_
  - [x] 1.6 编写注册表属性测试
    - **Property 2: Registry Extension Mapping**
    - **Property 3: Unsupported Format Error**
    - **Validates: Requirements 2.1, 2.3, 2.4, 1.4**

- [x] 2. Segment 提取器
  - [x] 2.1 创建 Segment 提取模块 `app/services/adapters/segment_extractor.py`
    - 实现从 Document_AST 提取 Segment 列表
    - 实现稳定 Segment ID 生成算法
    - _Requirements: 4.1, 4.2, 4.4, 4.5_
  - [x] 2.2 编写 Segment ID 稳定性属性测试
    - **Property 6: Segment ID Stability**
    - **Validates: Requirements 4.2, 4.3**
  - [x] 2.3 编写 Segment 位置唯一性属性测试
    - **Property 7: Segment Position Uniqueness**
    - **Validates: Requirements 4.4**
  - [x] 2.4 编写 Segment 顺序保持属性测试
    - **Property 8: Segment Order Preservation**
    - **Validates: Requirements 4.5**

- [x] 3. Checkpoint - 核心基础设施验证
  - 确保所有测试通过，如有问题请询问用户

- [x] 4. TXT 适配器（V1）
  - [x] 4.1 创建 TXT 适配器 `app/services/adapters/txt_adapter.py`
    - 实现多编码支持（UTF-8, UTF-8-BOM, GB18030）
    - 实现空行分段逻辑
    - _Requirements: 6.1, 6.2, 6.3, 6.4_
  - [x] 4.2 编写 TXT 编码支持属性测试
    - **Property 9: TXT Encoding Support**
    - **Validates: Requirements 6.2**
  - [x] 4.3 编写 TXT 段落分割属性测试
    - **Property 10: TXT Paragraph Splitting**
    - **Validates: Requirements 6.1**
  - [x] 4.4 编写空文档处理属性测试
    - **Property 11: Empty Document Handling**
    - **Validates: Requirements 6.4**

- [x] 5. DOCX 适配器（V1）
  - [x] 5.1 创建 DOCX 适配器 `app/services/adapters/docx_adapter.py`
    - 重构现有 document_workspace.py 中的解析逻辑
    - 实现段落、表格、标题解析
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_
  - [x] 5.2 编写 DOCX 结构保持属性测试
    - **Property 14: DOCX Structure Preservation**
    - **Validates: Requirements 5.1, 5.2, 5.3**
  - [x] 5.3 编写 DOCX 空段落过滤属性测试
    - **Property 15: DOCX Empty Paragraph Filtering**
    - **Validates: Requirements 5.4**

- [x] 6. XLSX 适配器（V1 - TM 导入）
  - [x] 6.1 创建 XLSX 适配器 `app/services/adapters/xlsx_adapter.py`
    - 实现 TM_Import_Result 数据类
    - 实现可配置列映射
    - _Requirements: 7.1, 7.2, 7.3, 7.4_
  - [x] 6.2 编写 XLSX 行过滤属性测试
    - **Property 12: XLSX Row Filtering**
    - **Validates: Requirements 7.3**
  - [x] 6.3 编写 XLSX 列映射属性测试
    - **Property 13: XLSX Column Mapping**
    - **Validates: Requirements 7.2**

- [x] 7. Checkpoint - V1 适配器验证
  - 确保 TXT、DOCX、XLSX 适配器测试通过
  - 验证与现有 document_workspace 的集成

- [x] 8. 导出服务（V1）
  - [x] 8.1 创建导出服务 `app/services/adapters/export_service.py`
    - 实现 DOCX 导出
    - 实现 TXT 导出
    - 实现双语导出
    - 实现批量 ZIP 导出
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7_
  - [x] 8.2 编写导出往返完整性属性测试
    - **Property 20: Export Round-Trip Integrity**
    - **Validates: Requirements 12.1, 12.2, 12.3, 12.4**
  - [x] 8.3 编写双语导出完整性属性测试
    - **Property 21: Bilingual Export Completeness**
    - **Validates: Requirements 12.5**

- [x] 9. Checkpoint - V1 完整验证
  - 确保所有 V1 功能测试通过
  - 验证解析-翻译-导出完整流程

- [x] 10. PDF 适配器（V2）
  - [x] 10.1 创建 PDF 适配器 `app/services/adapters/pdf_adapter.py`
    - 使用 pdfplumber 或 PyMuPDF 提取文本
    - 实现表格检测和重建
    - 实现页码元数据
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

- [x] 11. PPTX 适配器（V2）
  - [x] 11.1 创建 PPTX 适配器 `app/services/adapters/pptx_adapter.py`
    - 使用 python-pptx 解析幻灯片
    - 提取文本框、标题、形状文本
    - 提取演讲者备注
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

- [x] 12. DITA 适配器（V2）
  - [x] 12.1 创建 DITA 适配器 `app/services/adapters/dita_adapter.py`
    - 使用 lxml 解析 DITA XML
    - 映射 DITA 元素到 Block_Node
    - 保留内联标签到 metadata
    - 处理 conref 占位符
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_
  - [x] 12.2 编写 DITA 元素映射属性测试
    - **Property 16: DITA Element Mapping**
    - **Validates: Requirements 10.2**
  - [x] 12.3 编写 DITA 内联标签保留属性测试
    - **Property 17: DITA Inline Tag Preservation**
    - **Validates: Requirements 10.3**
  - [x] 12.4 创建 DITA 导出器 `app/services/adapters/dita_exporter.py`
    - 生成有效 DITA XML
    - 保留结构元素和内联标签
    - DTD 验证
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5_
  - [x] 12.5 编写 DITA 导出有效性属性测试
    - **Property 22: DITA Export Validity**
    - **Validates: Requirements 13.1, 13.2**

- [x] 13. SVG 适配器（V2）
  - [x] 13.1 创建 SVG 适配器 `app/services/adapters/svg_adapter.py`
    - 使用 lxml 解析 SVG XML
    - 提取 text/tspan 元素
    - 保留位置和样式到 metadata
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_
  - [x] 13.2 编写 SVG 文本提取属性测试
    - **Property 18: SVG Text Extraction**
    - **Validates: Requirements 11.1**
  - [x] 13.3 编写 SVG 非文本过滤属性测试
    - **Property 19: SVG Non-Text Filtering**
    - **Validates: Requirements 11.4**
  - [x] 13.4 创建 SVG 导出器 `app/services/adapters/svg_exporter.py`
    - 替换文本内容
    - 保留非文本元素
    - 文本长度变化警告
    - _Requirements: 14.1, 14.2, 14.3, 14.4_
  - [x] 13.5 编写 SVG 导出完整性属性测试
    - **Property 23: SVG Export Integrity**
    - **Validates: Requirements 14.1, 14.2**

- [x] 14. 错误处理增强
  - [x] 14.1 实现文件大小验证
    - 在解析前检查文件大小
    - 支持按格式配置大小限制
    - _Requirements: 16.2, 16.3_
  - [x] 14.2 编写文件大小验证属性测试
    - **Property 25: File Size Validation**
    - **Validates: Requirements 16.2**
  - [x] 14.3 编写错误消息完整性属性测试
    - **Property 24: Error Message Completeness**
    - **Validates: Requirements 16.1**

- [x] 15. 集成与注册
  - [x] 15.1 创建适配器初始化模块 `app/services/adapters/__init__.py`
    - 自动注册所有适配器
    - 导出公共接口
    - _Requirements: 15.1, 15.2_
  - [x] 15.2 更新现有 API 路由使用新适配器
    - 修改文件上传接口
    - 修改工作台构建接口
    - 添加通用解析接口 `/parser/parse`
    - 添加格式列表接口 `/parser/formats`
    - _Requirements: 1.1, 2.3_

- [x] 16. Final Checkpoint - 全功能验证
  - 所有 139 个测试通过
  - 验证所有格式的解析和导出
  - API 路由已更新支持多格式

## Notes

- 所有任务（包括测试任务）都必须完成
- 每个任务引用具体需求以便追溯
- Checkpoint 任务用于阶段性验证
- 属性测试验证通用正确性属性
- 单元测试验证具体示例和边界情况
