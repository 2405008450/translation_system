# Requirements Document

## Introduction

本文档定义了翻译系统多格式文档适配器的需求规格。该功能旨在通过统一的适配器模式支持多种文档格式（DOCX、TXT、XLSX、PDF、PPTX）的解析，将不同格式的文档转换为统一的 Document AST 和 Segment 模型，以便后续的翻译匹配、审校和导出流程能够以一致的方式处理所有格式。

根据项目实施计划，V1 阶段以 DOCX/TXT 为主，XLSX 用于 TM 导入，PDF/PPTX 在架构层面预留接口。

## Glossary

- **Document_AST**: 文档抽象语法树，表达文档的结构化信息，包括段落、表格、单元格、文本块等层级关系
- **Segment**: 最小翻译单元，表示一个可独立翻译的文本片段，如句子或短语
- **Format_Adapter**: 格式适配器，负责将特定格式的文档解析为统一的 Document AST 结构
- **Adapter_Registry**: 适配器注册表，管理所有已注册的格式适配器，根据文件扩展名分发解析请求
- **Block_Node**: 块级节点，Document AST 中的顶层结构单元，如段落、表格、标题等
- **Segment_ID**: 片段唯一标识符，基于文档版本、块路径和顺序生成的稳定 ID
- **Parse_Result**: 解析结果，包含 Document AST、Segment 列表和解析元数据

## Requirements

### Requirement 1: 统一适配器接口

**User Story:** As a 开发者, I want 所有格式适配器遵循统一的接口规范, so that 我可以以一致的方式处理不同格式的文档解析。

#### Acceptance Criteria

1. THE Format_Adapter SHALL define a `parse(raw_bytes: bytes) -> Parse_Result` method for document parsing
2. THE Format_Adapter SHALL define a `supported_extensions() -> list[str]` method to declare supported file extensions
3. THE Format_Adapter SHALL define a `can_parse(filename: str) -> bool` method to check file compatibility
4. WHEN a Format_Adapter encounters an unsupported file, THEN THE Format_Adapter SHALL raise `UnsupportedFormatError` with the file extension
5. WHEN a Format_Adapter encounters corrupted file content, THEN THE Format_Adapter SHALL raise `ParseError` with error details

### Requirement 2: 适配器注册与发现

**User Story:** As a 系统, I want 自动发现和注册所有可用的格式适配器, so that 新增格式支持时无需修改核心代码。

#### Acceptance Criteria

1. THE Adapter_Registry SHALL maintain a mapping from file extensions to Format_Adapter instances
2. WHEN a new Format_Adapter is registered, THEN THE Adapter_Registry SHALL validate interface compliance
3. WHEN `get_adapter(filename: str)` is called, THEN THE Adapter_Registry SHALL return the appropriate adapter based on file extension
4. IF no adapter is found for a given extension, THEN THE Adapter_Registry SHALL raise `UnsupportedFormatError`
5. THE Adapter_Registry SHALL provide `list_supported_extensions() -> list[str]` method

6. WHEN multiple adapters support the same extension, THEN THE Adapter_Registry SHALL use the most recently registered adapter

### Requirement 3: Document AST 统一模型

**User Story:** As a 翻译工作台, I want 所有文档格式解析后产生统一的 AST 结构, so that 后续处理逻辑无需关心原始格式差异。

#### Acceptance Criteria

1. THE Document_AST SHALL contain a list of Block_Node elements representing document structure
2. THE Block_Node SHALL support types: `paragraph`, `table`, `table_row`, `table_cell`, `heading`, `list_item`
3. THE Block_Node SHALL contain `node_type`, `children`, and `metadata` fields
4. WHEN a Block_Node is of type `table`, THEN THE Block_Node SHALL contain `rows` and `columns` count in metadata
5. THE Document_AST SHALL be serializable to JSON format
6. THE Document_AST SHALL be deserializable from JSON format

### Requirement 4: Segment 提取与 ID 生成

**User Story:** As a 翻译系统, I want 从 Document AST 中提取稳定的 Segment 列表, so that 每个翻译单元都有可追踪的唯一标识。

#### Acceptance Criteria

1. THE Segment SHALL contain fields: `segment_id`, `source_text`, `display_text`, `block_path`, `position`
2. WHEN extracting segments, THE System SHALL generate stable Segment_ID based on block path and position
3. THE Segment_ID SHALL remain consistent across multiple parses of the same document content
4. WHEN the same source text appears in different positions, THEN THE System SHALL assign different Segment_IDs
5. THE System SHALL preserve segment order matching the document reading order

### Requirement 5: DOCX 适配器

**User Story:** As a 用户, I want 上传 DOCX 文件并获得结构化解析结果, so that 我可以在工作台中进行翻译审校。

#### Acceptance Criteria

1. THE DOCX_Adapter SHALL parse paragraphs into `paragraph` Block_Nodes
2. THE DOCX_Adapter SHALL parse tables into nested `table`, `table_row`, `table_cell` Block_Nodes
3. THE DOCX_Adapter SHALL preserve heading levels as `heading` Block_Nodes with level metadata
4. WHEN a DOCX contains empty paragraphs, THEN THE DOCX_Adapter SHALL skip them in the AST
5. THE DOCX_Adapter SHALL extract text content preserving reading order

### Requirement 6: TXT 适配器

**User Story:** As a 用户, I want 上传纯文本文件并获得段落级解析, so that 简单文本也能进入翻译流程。

#### Acceptance Criteria

1. THE TXT_Adapter SHALL split text by blank lines into separate `paragraph` Block_Nodes
2. THE TXT_Adapter SHALL support UTF-8, UTF-8-BOM, and GB18030 encodings
3. WHEN encoding detection fails, THEN THE TXT_Adapter SHALL raise `ParseError` with encoding hint
4. WHEN the file is empty or contains only whitespace, THEN THE TXT_Adapter SHALL return an empty Document_AST

### Requirement 7: XLSX 适配器（TM 导入专用）

**User Story:** As a 管理员, I want 从 XLSX 文件导入翻译记忆, so that 我可以批量导入历史翻译数据。

#### Acceptance Criteria

1. THE XLSX_Adapter SHALL parse each row as a translation memory entry
2. THE XLSX_Adapter SHALL support configurable column mapping for source and target text
3. WHEN a row contains empty source text, THEN THE XLSX_Adapter SHALL skip that row
4. THE XLSX_Adapter SHALL return a specialized TM_Import_Result instead of Document_AST

### Requirement 8: PDF 适配器（V2 预留）

**User Story:** As a 用户, I want 上传 PDF 文件并获得可翻译的文本内容, so that 我可以处理 PDF 格式的翻译任务。

#### Acceptance Criteria

1. THE PDF_Adapter SHALL extract text content from PDF files preserving reading order
2. THE PDF_Adapter SHALL identify paragraph boundaries based on layout analysis
3. WHEN a PDF contains tables, THEN THE PDF_Adapter SHALL attempt to reconstruct table structure as `table` Block_Nodes
4. THE PDF_Adapter SHALL preserve page number metadata for each Block_Node
5. WHEN a PDF is scanned or image-based without embedded text, THEN THE PDF_Adapter SHALL raise `OCRRequiredError`

### Requirement 9: PPTX 适配器（V2 预留）

**User Story:** As a 用户, I want 上传 PowerPoint 文件并翻译幻灯片内容, so that 我可以处理演示文稿的本地化。

#### Acceptance Criteria

1. THE PPTX_Adapter SHALL parse each slide as a container with slide number metadata
2. THE PPTX_Adapter SHALL extract text from text boxes, titles, and shapes as `paragraph` Block_Nodes
3. THE PPTX_Adapter SHALL preserve speaker notes as separate translatable segments with `notes` metadata
4. WHEN a slide contains tables, THEN THE PPTX_Adapter SHALL parse them as nested `table` Block_Nodes
5. THE PPTX_Adapter SHALL maintain slide order in the Document_AST

### Requirement 10: DITA 适配器

**User Story:** As a 技术文档工程师, I want 上传 DITA 格式文件并保留其结构化标签, so that 翻译后可以保持 DITA 规范的完整性。

#### Acceptance Criteria

1. THE DITA_Adapter SHALL parse DITA topic files (.dita, .xml) into Document_AST
2. THE DITA_Adapter SHALL preserve DITA elements as Block_Nodes: `title`, `shortdesc`, `p`, `ul`, `ol`, `table`, `note`, `codeblock`
3. THE DITA_Adapter SHALL extract translatable text while preserving inline tags like `ph`, `xref`, `codeph` in metadata
4. WHEN a DITA file contains conref references, THEN THE DITA_Adapter SHALL mark them as placeholder nodes
5. WHEN a DITA file is malformed, THEN THE DITA_Adapter SHALL raise `ParseError` with XML validation details
6. THE DITA_Adapter SHALL support DITA maps (.ditamap) to identify topic relationships

### Requirement 11: SVG 适配器

**User Story:** As a 设计师, I want 上传 SVG 文件并翻译其中的文本内容, so that 我可以本地化矢量图形中的文字。

#### Acceptance Criteria

1. THE SVG_Adapter SHALL extract text content from `text` and `tspan` elements
2. THE SVG_Adapter SHALL preserve text position and styling information in metadata
3. WHEN an SVG contains nested text elements, THEN THE SVG_Adapter SHALL maintain parent-child relationships
4. THE SVG_Adapter SHALL ignore non-translatable elements like paths, shapes, and styles
5. WHEN an SVG file is malformed, THEN THE SVG_Adapter SHALL raise `ParseError` with XML validation details

### Requirement 12: 文档导出

**User Story:** As a 用户, I want 将翻译完成的内容导出为原始格式, so that 我可以获得可交付的翻译文档。

#### Acceptance Criteria

1. THE Export_Service SHALL support exporting translated Document_AST back to DOCX format
2. THE Export_Service SHALL support exporting translated Document_AST back to TXT format
3. WHEN exporting to DOCX, THEN THE Export_Service SHALL preserve original document structure and basic formatting
4. WHEN exporting to TXT, THEN THE Export_Service SHALL output plain text with paragraph separators
5. THE Export_Service SHALL support bilingual export with source and target text side-by-side
6. WHEN export fails, THEN THE Export_Service SHALL raise `ExportError` with failure details
7. THE Export_Service SHALL support batch export of multiple documents as ZIP archive

### Requirement 13: DITA 导出

**User Story:** As a 技术文档工程师, I want 将翻译后的 DITA 内容导出为有效的 DITA 文件, so that 翻译结果可以直接用于文档发布流程。

#### Acceptance Criteria

1. THE DITA_Exporter SHALL generate valid DITA XML from translated Document_AST
2. THE DITA_Exporter SHALL preserve all DITA structural elements and inline tags
3. WHEN exporting DITA, THEN THE DITA_Exporter SHALL maintain conref placeholders unchanged
4. THE DITA_Exporter SHALL validate output against DITA DTD before completion
5. WHEN validation fails, THEN THE DITA_Exporter SHALL raise `ExportError` with validation details

### Requirement 14: SVG 导出

**User Story:** As a 设计师, I want 将翻译后的 SVG 文本导出回 SVG 文件, so that 本地化后的图形可以直接使用。

#### Acceptance Criteria

1. THE SVG_Exporter SHALL replace text content in original SVG with translated text
2. THE SVG_Exporter SHALL preserve all non-text elements unchanged
3. WHEN text length changes significantly, THEN THE SVG_Exporter SHALL log a warning about potential layout issues
4. THE SVG_Exporter SHALL generate valid SVG XML output

### Requirement 15: 格式扩展预留

**User Story:** As a 架构师, I want 系统架构支持灵活扩展新格式, so that 未来可以无缝添加更多文档类型支持。

#### Acceptance Criteria

1. THE Adapter_Registry SHALL accept registration of new adapters at runtime
2. THE Document_AST model SHALL be extensible to support new node types via metadata
3. WHEN an unimplemented adapter is called, THEN THE System SHALL raise `NotImplementedError` with format name
4. THE System SHALL log a warning when attempting to use a placeholder adapter

### Requirement 16: 错误处理

**User Story:** As a 运维人员, I want 清晰的错误信息, so that 我可以快速定位文档解析问题。

#### Acceptance Criteria

1. WHEN parsing fails, THEN THE Format_Adapter SHALL include file name and failure reason in the error
2. WHEN a document exceeds size limits, THEN THE System SHALL raise `FileTooLargeError` before attempting parse
3. THE System SHALL support configurable maximum file size per format type
