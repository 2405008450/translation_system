<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import Modal from './base/Modal.vue'

export interface ExportStyleSettings {
  enabled: boolean
  defaults: Record<string, string>
  styles: Record<string, Record<string, string>>
  hyphenation: Record<string, string>
}

type FieldType = 'text' | 'number' | 'select'

interface FieldOption {
  value: string
  label: string
}

interface FieldDef {
  key: string
  label: string
  type: FieldType
  options?: FieldOption[]
  placeholder?: string
  step?: string
  hint: string
}

interface StyleDef {
  key: string
  label: string
  note: string
  groups: Array<'run' | 'para' | 'table'>
}

interface CategoryDef {
  key: string
  label: string
  kind: 'styles' | 'defaults' | 'hyphenation'
  styles?: StyleDef[]
}

const props = withDefaults(defineProps<{
  open: boolean
  modelValue: ExportStyleSettings
}>(), {})

const emit = defineEmits<{
  close: []
  save: [payload: ExportStyleSettings]
}>()

const { t } = useI18n()

// ── 复用的下拉选项 ──
const BOOL_OPTIONS: FieldOption[] = [
  { value: '', label: '继承 / 不修改' },
  { value: 'true', label: '是' },
  { value: 'false', label: '否' },
]
const UNDERLINE_OPTIONS: FieldOption[] = [
  { value: '', label: '继承 / 不修改' },
  { value: 'none', label: '无' },
  { value: 'single', label: '单线' },
  { value: 'double', label: '双线' },
  { value: 'dotted', label: '点线' },
  { value: 'dash', label: '虚线' },
]
const ALIGN_OPTIONS: FieldOption[] = [
  { value: '', label: '继承 / 不修改' },
  { value: 'left', label: '左对齐' },
  { value: 'center', label: '居中' },
  { value: 'right', label: '右对齐' },
  { value: 'both', label: '两端对齐' },
  { value: 'distribute', label: '分散对齐' },
]
const TBL_ALIGN_OPTIONS: FieldOption[] = [
  { value: '', label: '继承 / 不修改' },
  { value: 'left', label: '左对齐' },
  { value: 'center', label: '居中' },
  { value: 'right', label: '右对齐' },
]
const LINE_RULE_OPTIONS: FieldOption[] = [
  { value: '', label: '继承 / 不修改' },
  { value: 'auto', label: '倍数 (auto)' },
  { value: 'exact', label: '固定值 (exact)' },
  { value: 'atLeast', label: '最小值 (atLeast)' },
]
const VALIGN_OPTIONS: FieldOption[] = [
  { value: '', label: '继承 / 不修改' },
  { value: 'baseline', label: '基线（正常）' },
  { value: 'superscript', label: '上标' },
  { value: 'subscript', label: '下标' },
]
const HIGHLIGHT_OPTIONS: FieldOption[] = [
  { value: '', label: '继承 / 不修改' },
  { value: 'none', label: '无' },
  { value: 'yellow', label: '黄' },
  { value: 'green', label: '绿' },
  { value: 'cyan', label: '青' },
  { value: 'red', label: '红' },
  { value: 'magenta', label: '品红' },
  { value: 'blue', label: '蓝' },
  { value: 'darkBlue', label: '深蓝' },
]
const TBL_LAYOUT_OPTIONS: FieldOption[] = [
  { value: '', label: '继承 / 不修改' },
  { value: 'autofit_content', label: '根据内容自动调整' },
  { value: 'autofit_window', label: '根据窗口自动调整' },
  { value: 'fixed', label: '固定列宽' },
]
const TBL_BORDER_OPTIONS: FieldOption[] = [
  { value: '', label: '继承 / 不修改' },
  { value: 'single', label: '单线' },
  { value: 'none', label: '无' },
  { value: 'double', label: '双线' },
  { value: 'thick', label: '粗线' },
  { value: 'dotted', label: '点线' },
  { value: 'dashed', label: '虚线' },
]
const TBL_TEXT_DIR_OPTIONS: FieldOption[] = [
  { value: '', label: '继承 / 不修改' },
  { value: 'lrTb', label: '水平（默认横排）' },
  { value: 'tbRl', label: '垂直·从右往左' },
  { value: 'btLr', label: '垂直·从左往右' },
  { value: 'lrTbV', label: '文字顺时针 90°' },
  { value: 'tbRlV', label: '文字逆时针 90°' },
  { value: 'tbLrV', label: '中文逆时针 90°' },
]

const FONT_PH = '如 宋体 / Times New Roman'
const COLOR_PH = '十六进制不带#，如 FF0000'

// ── 字符格式字段（Run 级）──
const RUN_FIELDS: FieldDef[] = [
  { key: 'font_ascii', label: '西文字体', type: 'text', placeholder: FONT_PH, hint: '西文/数字使用的字体，如 Times New Roman。' },
  { key: 'font_east_asia', label: '中文字体', type: 'text', placeholder: FONT_PH, hint: '东亚/中文使用的字体，如 宋体、黑体。' },
  { key: 'font_hAnsi', label: 'hAnsi 字体', type: 'text', placeholder: FONT_PH, hint: '高位字符字体，一般与西文字体保持一致。' },
  { key: 'font_cs', label: '复杂文种字体', type: 'text', placeholder: FONT_PH, hint: '复杂文种(cs)字体，一般与中文字体一致。' },
  { key: 'font_size', label: '字号（磅）', type: 'number', step: '0.5', hint: '磅值。10.5=五号，12=小四，14=四号，16=三号。' },
  { key: 'font_size_cs', label: '复杂文种字号（磅）', type: 'number', step: '0.5', hint: '复杂文种字号，通常与字号相同。' },
  { key: 'bold', label: '加粗', type: 'select', options: BOOL_OPTIONS, hint: '是否加粗。' },
  { key: 'italic', label: '斜体', type: 'select', options: BOOL_OPTIONS, hint: '是否斜体。' },
  { key: 'strike', label: '单删除线', type: 'select', options: BOOL_OPTIONS, hint: '单条删除线。' },
  { key: 'dstrike', label: '双删除线', type: 'select', options: BOOL_OPTIONS, hint: '双条删除线。' },
  { key: 'underline', label: '下划线', type: 'select', options: UNDERLINE_OPTIONS, hint: '下划线样式。' },
  { key: 'small_caps', label: '小型大写', type: 'select', options: BOOL_OPTIONS, hint: '小型大写字母效果。' },
  { key: 'all_caps', label: '全部大写', type: 'select', options: BOOL_OPTIONS, hint: '强制显示为大写字母。' },
  { key: 'vanish', label: '隐藏文字', type: 'select', options: BOOL_OPTIONS, hint: '隐藏文字（不打印/不显示）。' },
  { key: 'color', label: '字体颜色', type: 'text', placeholder: COLOR_PH, hint: '十六进制颜色不带#。FF0000=红，0000FF=蓝，444444=深灰。' },
  { key: 'highlight', label: '突出显示', type: 'select', options: HIGHLIGHT_OPTIONS, hint: '荧光笔式高亮底色。' },
  { key: 'shading', label: '字符底纹', type: 'text', placeholder: COLOR_PH, hint: '字符底纹颜色，十六进制不带#，如 D9D9D9。' },
  { key: 'char_spacing', label: '字符间距（磅）', type: 'number', step: '0.1', hint: '正值加宽，负值紧缩。' },
  { key: 'char_scale', label: '字符缩放（%）', type: 'number', hint: '横向缩放百分比，100=正常，80=扁，150=宽。' },
  { key: 'kern', label: '字距调整起始字号（磅）', type: 'number', step: '0.5', hint: '大于该字号时自动调整字距，0=关闭。' },
  { key: 'position', label: '字符升降（磅）', type: 'number', step: '0.5', hint: '正值上升，负值下降。' },
  { key: 'vertical_align', label: '上标/下标', type: 'select', options: VALIGN_OPTIONS, hint: '设置为上标或下标。' },
]

// ── 段落格式字段 ──
const PARA_FIELDS: FieldDef[] = [
  { key: 'alignment', label: '对齐方式', type: 'select', options: ALIGN_OPTIONS, hint: '段落水平对齐方式。' },
  { key: 'line_spacing', label: '行间距', type: 'number', step: '0.1', hint: '规则为倍数时填倍数(如1.5)；exact/atLeast 时填磅值。' },
  { key: 'line_spacing_rule', label: '行距规则', type: 'select', options: LINE_RULE_OPTIONS, hint: 'auto=倍数，exact=固定磅值，atLeast=最小磅值。' },
  { key: 'space_before', label: '段前间距（磅）', type: 'number', step: '0.5', hint: '段落上方的间距。' },
  { key: 'space_after', label: '段后间距（磅）', type: 'number', step: '0.5', hint: '段落下方的间距。' },
  { key: 'indent_left', label: '左缩进（磅）', type: 'number', step: '0.5', hint: '整段左侧缩进。' },
  { key: 'indent_right', label: '右缩进（磅）', type: 'number', step: '0.5', hint: '整段右侧缩进。' },
  { key: 'indent_first_line', label: '首行缩进（磅）', type: 'number', step: '0.5', hint: '首行缩进。21磅≈五号2字符，24磅≈小四2字符。与悬挂缩进互斥。' },
  { key: 'indent_hanging', label: '悬挂缩进（磅）', type: 'number', step: '0.5', hint: '除首行外的行缩进，与首行缩进互斥。' },
  { key: 'keep_next', label: '与下段同页', type: 'select', options: BOOL_OPTIONS, hint: '防止标题与正文被分页断开，标题建议开启。' },
  { key: 'keep_lines', label: '段落内不分页', type: 'select', options: BOOL_OPTIONS, hint: '整段保持在同一页。' },
  { key: 'page_break_before', label: '段前分页', type: 'select', options: BOOL_OPTIONS, hint: '该段落前强制分页。' },
]

// ── 表格格式字段 ──
const TABLE_FIELDS: FieldDef[] = [
  { key: 'tbl_style_name', label: '表格样式名', type: 'text', placeholder: '如 Table Grid', hint: '应用的表格样式名称。' },
  { key: 'tbl_layout_type', label: '自动调整方式', type: 'select', options: TBL_LAYOUT_OPTIONS, hint: '对应 Word 表格属性→自动调整的三种方式。' },
  { key: 'tbl_width_pct', label: '表格宽度（%）', type: 'number', hint: '仅“根据窗口自动调整”时生效，100=占满页面宽度。' },
  { key: 'tbl_indent', label: '表格左缩进（磅）', type: 'number', step: '0.5', hint: '整个表格的左缩进。' },
  { key: 'tbl_align', label: '表格对齐', type: 'select', options: TBL_ALIGN_OPTIONS, hint: '表格在页面中的对齐方式。' },
  { key: 'tbl_border_style', label: '边框样式', type: 'select', options: TBL_BORDER_OPTIONS, hint: '表格边框线型。' },
  { key: 'tbl_border_size', label: '边框粗细（1/8磅）', type: 'number', hint: '1/8磅为单位：4=0.5pt，8=1pt，16=2pt。' },
  { key: 'tbl_border_color', label: '边框颜色', type: 'text', placeholder: COLOR_PH, hint: '十六进制不带#，如 000000 黑、BFBFBF 灰。' },
  { key: 'tbl_cell_margin_top', label: '单元格上边距（磅）', type: 'number', step: '0.1', hint: '单元格内容与上边框的间距。' },
  { key: 'tbl_cell_margin_bottom', label: '单元格下边距（磅）', type: 'number', step: '0.1', hint: '单元格内容与下边框的间距。' },
  { key: 'tbl_cell_margin_left', label: '单元格左边距（磅）', type: 'number', step: '0.1', hint: 'Word 默认约 5.4 磅。' },
  { key: 'tbl_cell_margin_right', label: '单元格右边距（磅）', type: 'number', step: '0.1', hint: 'Word 默认约 5.4 磅。' },
  { key: 'tbl_para_line_spacing', label: '单元格行间距', type: 'number', step: '0.1', hint: '规则为倍数时填倍数，exact/atLeast 时填磅值。' },
  { key: 'tbl_para_line_spacing_rule', label: '单元格行距规则', type: 'select', options: LINE_RULE_OPTIONS, hint: 'auto=倍数，exact=固定磅值，atLeast=最小磅值。' },
  { key: 'tbl_para_space_before', label: '单元格段前（磅）', type: 'number', step: '0.5', hint: '单元格内段落段前间距。' },
  { key: 'tbl_para_space_after', label: '单元格段后（磅）', type: 'number', step: '0.5', hint: '单元格内段落段后间距。' },
  { key: 'tbl_para_alignment', label: '单元格段落对齐', type: 'select', options: ALIGN_OPTIONS, hint: '单元格内文字的对齐方式。' },
  { key: 'tbl_run_font_ascii', label: '单元格西文字体', type: 'text', placeholder: FONT_PH, hint: '单元格内西文字体。' },
  { key: 'tbl_run_font_east_asia', label: '单元格中文字体', type: 'text', placeholder: FONT_PH, hint: '单元格内中文字体。' },
  { key: 'tbl_run_font_size', label: '单元格字号（磅）', type: 'number', step: '0.5', hint: '单元格内文字字号。' },
  { key: 'tbl_run_bold', label: '单元格加粗', type: 'select', options: BOOL_OPTIONS, hint: '单元格内文字是否加粗。' },
  { key: 'tbl_run_color', label: '单元格字体颜色', type: 'text', placeholder: COLOR_PH, hint: '十六进制不带#，如 000000。' },
  { key: 'tbl_run_char_spacing', label: '单元格字符间距（磅）', type: 'number', step: '0.1', hint: '单元格内文字字符间距。' },
  { key: 'tbl_cell_text_direction', label: '单元格文字方向', type: 'select', options: TBL_TEXT_DIR_OPTIONS, hint: '单元格内文字的排版方向。' },
]

// ── 默认字体字段（docDefaults）──
const DEFAULTS_FIELDS: FieldDef[] = [
  { key: 'font_ascii', label: '西文字体', type: 'text', placeholder: FONT_PH, hint: '文档默认西文字体，影响所有未单独指定字体的样式。' },
  { key: 'font_east_asia', label: '中文字体', type: 'text', placeholder: FONT_PH, hint: '文档默认中文字体。' },
  { key: 'font_hAnsi', label: 'hAnsi 字体', type: 'text', placeholder: FONT_PH, hint: '高位字符字体，一般与西文字体一致。' },
  { key: 'font_cs', label: '复杂文种字体', type: 'text', placeholder: FONT_PH, hint: '复杂文种字体，一般与中文字体一致。' },
  { key: 'font_size', label: '字号（磅）', type: 'number', step: '0.5', hint: '文档默认字号，10.5=五号，12=小四。' },
  { key: 'font_size_cs', label: '复杂文种字号（磅）', type: 'number', step: '0.5', hint: '复杂文种默认字号。' },
]

// ── 自动断字字段（word/settings.xml）──
const HYPHEN_FIELDS: FieldDef[] = [
  { key: 'auto', label: '自动断字', type: 'select', options: BOOL_OPTIONS, hint: '对应 Word 布局→断字→自动/无。' },
  { key: 'consecutive_limit', label: '连续断字行数上限', type: 'number', hint: '最多连续多少行以连字符结尾，0 或留空=不限制。' },
  { key: 'zone_pt', label: '断字区宽度（磅）', type: 'number', step: '0.5', hint: '值越小断字越频繁，Word 默认 18 磅。' },
  { key: 'do_not_hyphenate_caps', label: '全大写单词不断字', type: 'select', options: BOOL_OPTIONS, hint: '勾选后全大写的单词不参与断字。' },
]

// ── 样式定义 ──
const STYLE_NORMAL: StyleDef = { key: 'Normal', label: '正文 Normal', note: '所有段落的基础样式，其它样式大多继承自此。修改这里会影响全文未单独设置样式的段落。', groups: ['run', 'para'] }

const STYLE_HEADINGS: StyleDef[] = Array.from({ length: 9 }, (_, i) => ({
  key: `heading ${i + 1}`,
  label: `标题 ${i + 1}`,
  note: `对应 Word「标题${i + 1}」。标题建议开启“与下段同页”，并将首行缩进设为 0。`,
  groups: ['run', 'para'] as Array<'run' | 'para' | 'table'>,
}))

const STYLE_TOCS: StyleDef[] = [1, 2, 3].map((i) => ({
  key: `toc ${i}`,
  label: `目录 ${i}`,
  note: `自动目录第 ${i} 级条目。用左缩进控制各级层次感。`,
  groups: ['run', 'para'] as Array<'run' | 'para' | 'table'>,
}))

const STYLE_HEADER_FOOTER: StyleDef[] = [
  { key: 'Header', label: '页眉 Header', note: '页眉文字格式，对齐方式控制左/中/右位置。', groups: ['run', 'para'] },
  { key: 'Footer', label: '页脚 Footer', note: '页脚文字格式，页码通常在页脚中。', groups: ['run', 'para'] },
  { key: 'page number', label: '页码 page number', note: '页脚中的页码数字样式。', groups: ['run'] },
]

const STYLE_TABLE: StyleDef = { key: 'Table Grid', label: '表格 Table Grid', note: '该配置会应用到文档里的每一个表格，不要求表格本身引用了此样式名。表格走直接改写 XML，会覆盖已有的直接格式。', groups: ['table'] }

const STYLE_OTHERS: StyleDef[] = [
  { key: 'caption', label: '题注 caption', note: '图表标题，如「图1-1」「表2-1」，通常居中、字号略小。', groups: ['run', 'para'] },
  { key: 'footnote text', label: '脚注文本 footnote text', note: '页面底部的脚注正文，字号通常比正文小 1~2 磅。', groups: ['run', 'para'] },
  { key: 'footnote reference', label: '脚注引用 footnote reference', note: '正文中的上标脚注标记。', groups: ['run'] },
  { key: 'endnote text', label: '尾注文本 endnote text', note: '文档末尾的尾注正文。', groups: ['run', 'para'] },
  { key: 'endnote reference', label: '尾注引用 endnote reference', note: '正文中的上标尾注标记。', groups: ['run'] },
  { key: 'Hyperlink', label: '超链接 Hyperlink', note: '控制所有超链接文字外观，默认蓝色带下划线。', groups: ['run'] },
  { key: 'List Paragraph', label: '列表段落 List Paragraph', note: '项目符号/编号列表段落，用左缩进控制整体缩进。', groups: ['run', 'para'] },
  { key: 'Block Text', label: '块引用 Block Text', note: '整段缩进的块引用效果。', groups: ['run', 'para'] },
  { key: 'Intense Quote', label: '强调引用 Intense Quote', note: '强调型引用，通常带颜色。', groups: ['run', 'para'] },
  { key: 'Emphasis', label: '强调 Emphasis', note: '字符样式，通常斜体。', groups: ['run'] },
  { key: 'Strong', label: '加强 Strong', note: '字符样式，通常加粗。', groups: ['run'] },
  { key: 'Intense Emphasis', label: '明显强调 Intense Emphasis', note: '字符样式，斜体+颜色。', groups: ['run'] },
  { key: 'Subtle Emphasis', label: '轻微强调 Subtle Emphasis', note: '字符样式，斜体+浅灰。', groups: ['run'] },
]

const CATEGORIES: CategoryDef[] = [
  { key: 'defaults', label: '默认字体', kind: 'defaults' },
  { key: 'body', label: '正文', kind: 'styles', styles: [STYLE_NORMAL] },
  { key: 'headings', label: '标题', kind: 'styles', styles: STYLE_HEADINGS },
  { key: 'toc', label: '目录', kind: 'styles', styles: STYLE_TOCS },
  { key: 'headerFooter', label: '页眉页脚', kind: 'styles', styles: STYLE_HEADER_FOOTER },
  { key: 'table', label: '表格', kind: 'styles', styles: [STYLE_TABLE] },
  { key: 'others', label: '其他文本', kind: 'styles', styles: STYLE_OTHERS },
  { key: 'hyphenation', label: '自动断字', kind: 'hyphenation' },
]

const ALL_STYLE_KEYS: string[] = [
  STYLE_NORMAL,
  ...STYLE_HEADINGS,
  ...STYLE_TOCS,
  ...STYLE_HEADER_FOOTER,
  STYLE_TABLE,
  ...STYLE_OTHERS,
].map((s) => s.key)

function emptySettings(): ExportStyleSettings {
  return { enabled: false, defaults: {}, styles: {}, hyphenation: {} }
}

const form = reactive<ExportStyleSettings>(emptySettings())
const activeCategory = ref<string>(CATEGORIES[0].key)

const activeCategoryDef = computed<CategoryDef>(
  () => CATEGORIES.find((c) => c.key === activeCategory.value) || CATEGORIES[0],
)

function fieldsForGroup(group: 'run' | 'para' | 'table'): FieldDef[] {
  if (group === 'run') return RUN_FIELDS
  if (group === 'para') return PARA_FIELDS
  return TABLE_FIELDS
}

function groupLabel(group: 'run' | 'para' | 'table'): string {
  if (group === 'run') return t('workbench.exportStyleSettings.groupRun')
  if (group === 'para') return t('workbench.exportStyleSettings.groupPara')
  return t('workbench.exportStyleSettings.groupTable')
}

function syncForm() {
  const source = props.modelValue || emptySettings()
  form.enabled = Boolean(source.enabled)
  form.defaults = { ...(source.defaults || {}) }
  form.hyphenation = { ...(source.hyphenation || {}) }
  const styles: Record<string, Record<string, string>> = {}
  ALL_STYLE_KEYS.forEach((key) => {
    styles[key] = { ...((source.styles || {})[key] || {}) }
  })
  form.styles = styles
  activeCategory.value = CATEGORIES[0].key
}

function pruneEmpty(values: Record<string, string>): Record<string, string> {
  const next: Record<string, string> = {}
  Object.entries(values || {}).forEach(([key, value]) => {
    if (value !== '' && value !== null && value !== undefined) {
      next[key] = value
    }
  })
  return next
}

function buildPayload(): ExportStyleSettings {
  const styles: Record<string, Record<string, string>> = {}
  Object.entries(form.styles).forEach(([name, values]) => {
    const cleaned = pruneEmpty(values)
    if (Object.keys(cleaned).length > 0) {
      styles[name] = cleaned
    }
  })
  return {
    enabled: form.enabled,
    defaults: pruneEmpty(form.defaults),
    styles,
    hyphenation: pruneEmpty(form.hyphenation),
  }
}

function submit() {
  emit('save', buildPayload())
}

function resetActiveCategory() {
  const def = activeCategoryDef.value
  if (def.kind === 'defaults') {
    form.defaults = {}
  } else if (def.kind === 'hyphenation') {
    form.hyphenation = {}
  } else {
    (def.styles || []).forEach((style) => {
      form.styles[style.key] = {}
    })
  }
}

function resetAll() {
  form.enabled = false
  form.defaults = {}
  form.hyphenation = {}
  ALL_STYLE_KEYS.forEach((key) => {
    form.styles[key] = {}
  })
}

function countConfigured(values: Record<string, string> | undefined): number {
  return Object.keys(pruneEmpty(values || {})).length
}

const configuredCount = computed(() => {
  let count = countConfigured(form.defaults) + countConfigured(form.hyphenation)
  Object.values(form.styles).forEach((values) => {
    count += countConfigured(values)
  })
  return count
})

function categoryCount(def: CategoryDef): number {
  if (def.kind === 'defaults') return countConfigured(form.defaults)
  if (def.kind === 'hyphenation') return countConfigured(form.hyphenation)
  return (def.styles || []).reduce((sum, style) => sum + countConfigured(form.styles[style.key]), 0)
}

watch(() => props.open, (open) => {
  if (open) {
    syncForm()
  }
})

watch(() => props.modelValue, () => {
  if (props.open) {
    syncForm()
  }
}, { deep: true })
</script>

<template>
  <Modal
    :open="open"
    :title="t('workbench.exportStyleSettings.title')"
    width="min(960px, calc(100vw - 32px))"
    @close="emit('close')"
  >
    <div class="export-style">
      <label class="export-style__enable">
        <input v-model="form.enabled" type="checkbox">
        <span>
          <strong>{{ t('workbench.exportStyleSettings.enable') }}</strong>
          <small>{{ t('workbench.exportStyleSettings.enableHint') }}</small>
        </span>
      </label>

      <p class="export-style__hint">
        {{ t('workbench.exportStyleSettings.onlyDocxHint') }}
        {{ t('workbench.exportStyleSettings.unchangedHint') }}
      </p>

      <div class="export-style__layout" :class="{ 'is-disabled': !form.enabled }">
        <nav class="export-style__nav">
          <button
            v-for="cat in CATEGORIES"
            :key="cat.key"
            type="button"
            class="export-style__nav-item"
            :class="{ 'is-active': activeCategory === cat.key }"
            @click="activeCategory = cat.key"
          >
            <span>{{ cat.label }}</span>
            <span v-if="categoryCount(cat) > 0" class="export-style__nav-badge">{{ categoryCount(cat) }}</span>
          </button>
        </nav>

        <div class="export-style__content">
          <!-- 默认字体 -->
          <section v-if="activeCategoryDef.kind === 'defaults'" class="style-block">
            <header class="style-block__head">
              <strong>文档默认字体</strong>
              <code>docDefaults</code>
            </header>
            <p class="style-block__note">影响所有未单独指定字体的样式，是整份文档字体的兜底设置。</p>
            <div class="fields">
              <div v-for="field in DEFAULTS_FIELDS" :key="`def-${field.key}`" class="field-row">
                <div class="field-row__head">
                  <span class="field-row__label">{{ field.label }}</span>
                  <span class="field-row__hint">{{ field.hint }}</span>
                </div>
                <select v-if="field.options" v-model="form.defaults[field.key]" class="field-row__control">
                  <option v-for="opt in field.options" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
                </select>
                <input
                  v-else
                  v-model="form.defaults[field.key]"
                  class="field-row__control"
                  :type="field.type === 'number' ? 'number' : 'text'"
                  :step="field.step"
                  :placeholder="field.placeholder"
                >
              </div>
            </div>
          </section>

          <!-- 自动断字 -->
          <section v-else-if="activeCategoryDef.kind === 'hyphenation'" class="style-block">
            <header class="style-block__head">
              <strong>{{ t('workbench.exportStyleSettings.hyphenation') }}</strong>
              <code>settings.xml</code>
            </header>
            <p class="style-block__note">文档级设置。值越小断字越频繁；不需要断字时把“自动断字”留空即可。</p>
            <div class="fields">
              <div v-for="field in HYPHEN_FIELDS" :key="`hyphen-${field.key}`" class="field-row">
                <div class="field-row__head">
                  <span class="field-row__label">{{ field.label }}</span>
                  <span class="field-row__hint">{{ field.hint }}</span>
                </div>
                <select v-if="field.options" v-model="form.hyphenation[field.key]" class="field-row__control">
                  <option v-for="opt in field.options" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
                </select>
                <input
                  v-else
                  v-model="form.hyphenation[field.key]"
                  class="field-row__control"
                  :type="field.type === 'number' ? 'number' : 'text'"
                  :step="field.step"
                  :placeholder="field.placeholder"
                >
              </div>
            </div>
          </section>

          <!-- 样式类目 -->
          <template v-else>
            <section
              v-for="style in (activeCategoryDef.styles || [])"
              :key="style.key"
              class="style-block"
            >
              <header class="style-block__head">
                <strong>{{ style.label }}</strong>
                <code>{{ style.key }}</code>
              </header>
              <p class="style-block__note">{{ style.note }}</p>
              <div v-for="group in style.groups" :key="`${style.key}-${group}`" class="style-block__group">
                <div class="style-block__group-title">{{ groupLabel(group) }}</div>
                <div class="fields">
                  <div
                    v-for="field in fieldsForGroup(group)"
                    :key="`${style.key}-${field.key}`"
                    class="field-row"
                  >
                    <div class="field-row__head">
                      <span class="field-row__label">{{ field.label }}</span>
                      <span class="field-row__hint">{{ field.hint }}</span>
                    </div>
                    <select v-if="field.options" v-model="form.styles[style.key][field.key]" class="field-row__control">
                      <option v-for="opt in field.options" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
                    </select>
                    <input
                      v-else
                      v-model="form.styles[style.key][field.key]"
                      class="field-row__control"
                      :type="field.type === 'number' ? 'number' : 'text'"
                      :step="field.step"
                      :placeholder="field.placeholder"
                    >
                  </div>
                </div>
              </div>
            </section>
          </template>
        </div>
      </div>

      <div class="export-style__actions">
        <span class="export-style__count">已配置 {{ configuredCount }} 项</span>
        <div class="export-style__actions-buttons">
          <button class="button ghost" type="button" @click="resetActiveCategory">清空当前类目</button>
          <button class="button ghost" type="button" @click="resetAll">{{ t('workbench.exportStyleSettings.reset') }}</button>
          <button class="button secondary" type="button" @click="emit('close')">{{ t('common.actions.cancel') }}</button>
          <button class="button primary" type="button" @click="submit">{{ t('workbench.exportStyleSettings.save') }}</button>
        </div>
      </div>
    </div>
  </Modal>
</template>

<style scoped>
.export-style {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.export-style__enable {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  align-items: flex-start;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--line-soft);
}

.export-style__enable input {
  width: 16px;
  height: 16px;
  margin-top: 2px;
}

.export-style__enable span {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.export-style__enable strong {
  color: var(--text-primary);
  font-size: 13px;
}

.export-style__enable small,
.export-style__hint {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.5;
}

.export-style__hint {
  margin: 0;
}

.export-style__layout {
  display: grid;
  grid-template-columns: 148px minmax(0, 1fr);
  gap: 14px;
  min-height: 0;
}

.export-style__layout.is-disabled .export-style__content,
.export-style__layout.is-disabled .export-style__nav {
  opacity: 0.5;
  pointer-events: none;
}

.export-style__nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: min(56vh, 540px);
  overflow-y: auto;
  padding-right: 4px;
}

.export-style__nav-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  cursor: pointer;
  text-align: left;
}

.export-style__nav-item:hover {
  background: var(--surface-muted);
}

.export-style__nav-item.is-active {
  background: var(--surface-muted);
  border-color: var(--line-soft);
  color: var(--text-primary);
  font-weight: 600;
}

.export-style__nav-badge {
  min-width: 18px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--brand, #2563eb);
  color: #fff;
  font-size: 11px;
  line-height: 18px;
  text-align: center;
}

.export-style__content {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-height: min(56vh, 540px);
  overflow-y: auto;
  padding-right: 4px;
}

.style-block {
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: var(--surface-muted);
  padding: 12px 14px;
}

.style-block__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.style-block__head strong {
  color: var(--text-primary);
  font-size: 14px;
}

.style-block__head code {
  font-size: 11px;
  color: var(--text-muted);
}

.style-block__note {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-muted);
}

.style-block__group {
  margin-top: 12px;
}

.style-block__group-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  padding-bottom: 8px;
  border-bottom: 1px dashed var(--line-soft);
}

.fields {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
  margin-top: 10px;
}

.field-row {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.field-row__head {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.field-row__label {
  font-size: 13px;
  color: var(--text-primary);
  font-weight: 500;
}

.field-row__hint {
  font-size: 11px;
  line-height: 1.45;
  color: var(--text-muted);
}

.field-row__control {
  min-height: 34px;
  padding: 6px 9px;
  border: 1px solid var(--line-soft);
  border-radius: 7px;
  background: var(--surface);
  color: var(--text-primary);
  font-size: 13px;
}

.field-row__control:focus {
  outline: none;
  border-color: var(--brand, #2563eb);
}

.export-style__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding-top: 12px;
  border-top: 1px solid var(--line-soft);
}

.export-style__count {
  font-size: 12px;
  color: var(--text-muted);
}

.export-style__actions-buttons {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

@media (max-width: 720px) {
  .export-style__layout {
    grid-template-columns: 1fr;
  }

  .export-style__nav {
    flex-direction: row;
    flex-wrap: wrap;
    max-height: none;
  }

  .fields {
    grid-template-columns: 1fr;
  }
}
</style>
