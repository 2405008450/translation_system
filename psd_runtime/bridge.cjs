'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {
  getCompositeImageData,
  getLayerImageData,
  readPsd,
  writePsdBuffer,
} = require('ag-psd');
const { renderTranslatedTextLayers } = require('./text_renderer.cjs');
const {
  exportImageTextCandidates,
  hashImageData,
  isGeneratedOcrLayer,
  renderImageTranslations,
} = require('./image_ocr.cjs');

const PSD_SIGNATURE = '8BPS';
const PSD_VERSION = 1;
const RGB_COLOR_MODE = 3;
const MAX_DIMENSION = 30000;
const MAX_CANVAS_PIXELS = 50_000_000;
const MAX_TOTAL_LAYER_PIXELS = 100_000_000;
const MAX_LAYERS = 2000;
const MAX_DEPTH = 50;
const MAX_TEXT_LENGTH = 1_000_000;
const MEMORY_LIMIT_BYTES = 512 * 1024 * 1024;

function fail(message) {
  throw new Error(message);
}

function assertPsdHeader(buffer) {
  if (buffer.length < 26 || buffer.toString('ascii', 0, 4) !== PSD_SIGNATURE) {
    fail('文件头无效，不是 PSD 文件');
  }
  const version = buffer.readUInt16BE(4);
  if (version !== PSD_VERSION) {
    fail(version === 2 ? '暂不支持 PSB 大型文档格式' : `不支持的 PSD 版本：${version}`);
  }
}

function readDocument(inputPath, { preserveLinkedFiles = false } = {}) {
  const buffer = fs.readFileSync(inputPath);
  assertPsdHeader(buffer);
  const psd = readPsd(buffer, {
    useRawData: true,
    useRawThumbnail: true,
    skipLinkedFilesData: !preserveLinkedFiles,
    totalMemoryLimit: MEMORY_LIMIT_BYTES,
    throwForMissingFeatures: true,
    logMissingFeatures: false,
  });
  validateDocument(psd);
  return psd;
}

function validateDocument(psd) {
  if (!Number.isInteger(psd.width) || !Number.isInteger(psd.height) || psd.width <= 0 || psd.height <= 0) {
    fail('PSD 画布尺寸无效');
  }
  if (psd.width > MAX_DIMENSION || psd.height > MAX_DIMENSION) {
    fail(`PSD 画布尺寸 ${psd.width} × ${psd.height} 超过限制（单边最大 ${MAX_DIMENSION} 像素）`);
  }
  if (psd.width * psd.height > MAX_CANVAS_PIXELS) {
    fail(`PSD 画布尺寸 ${psd.width} × ${psd.height} 的总像素超过限制（最多 ${MAX_CANVAS_PIXELS} 像素）`);
  }
  if ((psd.bitsPerChannel ?? 8) !== 8) {
    fail('当前仅支持 8 位/通道 PSD');
  }
  if ((psd.colorMode ?? RGB_COLOR_MODE) !== RGB_COLOR_MODE) {
    fail('当前仅支持 RGB 颜色模式 PSD');
  }

  let layerCount = 0;
  let totalLayerPixels = 0;
  walkLayers(psd.children || [], ({ layer, depth }) => {
    layerCount += 1;
    if (layerCount > MAX_LAYERS) {
      fail(`PSD 图层数量超过限制（最多 ${MAX_LAYERS} 层）`);
    }
    if (depth > MAX_DEPTH) {
      fail(`PSD 图层嵌套超过限制（最多 ${MAX_DEPTH} 层）`);
    }
    const width = Math.abs(Number(layer.right ?? 0) - Number(layer.left ?? 0));
    const height = Math.abs(Number(layer.bottom ?? 0) - Number(layer.top ?? 0));
    if (width > MAX_DIMENSION || height > MAX_DIMENSION) {
      fail(`PSD 图层“${String(layer.name || '未命名图层')}”尺寸 ${width} × ${height} 超过限制（单边最大 ${MAX_DIMENSION} 像素）`);
    }
    if (!Array.isArray(layer.children) || layer.children.length === 0) {
      totalLayerPixels += width * height;
      if (totalLayerPixels > MAX_TOTAL_LAYER_PIXELS) {
        fail(`PSD 图层总像素超过限制（最多 ${MAX_TOTAL_LAYER_PIXELS} 像素）`);
      }
    }
    if (layer.text && String(layer.text.text || '').length > MAX_TEXT_LENGTH) {
      fail(`PSD 文本图层内容过长：${String(layer.name || '未命名图层')}`);
    }
  });
}

function walkLayers(
  layers,
  visitor,
  parentIndices = [],
  parentNames = [],
  depth = 0,
  hiddenByParent = false,
  lockedByParent = false,
) {
  for (let index = 0; index < layers.length; index += 1) {
    const layer = layers[index];
    const indices = [...parentIndices, index];
    const names = [...parentNames, String(layer.name || `图层 ${index + 1}`)];
    const hidden = hiddenByParent || Boolean(layer.hidden);
    const locked = lockedByParent || Boolean(
      layer.protected?.composite
      || layer.protected?.position
      || layer.protected?.transparency
    );
    visitor({ layer, indices, names, depth, effectiveHidden: hidden, effectiveLocked: locked });
    if (Array.isArray(layer.children)) {
      walkLayers(layer.children, visitor, indices, names, depth + 1, hidden, locked);
    }
  }
}

function walkOriginalLayers(layers, visitor, parentIndices = [], parentNames = [], depth = 0) {
  let originalIndex = 0;
  for (const layer of layers || []) {
    if (isGeneratedOcrLayer(layer)) continue;
    const indices = [...parentIndices, originalIndex];
    const names = [...parentNames, String(layer.name || `图层 ${originalIndex + 1}`)];
    visitor({ layer, indices, names, depth });
    if (Array.isArray(layer.children)) {
      walkOriginalLayers(layer.children, visitor, indices, names, depth + 1);
    }
    originalIndex += 1;
  }
}

function findOriginalLayerLocationByPath(psd, layerPath) {
  let children = psd.children || [];
  let current = null;
  const parts = String(layerPath).split('.').map(Number);
  for (let depth = 0; depth < parts.length; depth += 1) {
    const originalChildren = children.filter((layer) => !isGeneratedOcrLayer(layer));
    current = originalChildren[parts[depth]];
    if (!current) return null;
    const index = children.indexOf(current);
    if (depth === parts.length - 1) return { layer: current, children, index };
    if (!Array.isArray(current.children)) return null;
    children = current.children;
  }
  return null;
}

function findOriginalLayerByPath(psd, layerPath) {
  return findOriginalLayerLocationByPath(psd, layerPath)?.layer || null;
}

function findLayerByName(psd, name) {
  let found = null;
  walkLayers(psd.children || [], ({ layer }) => {
    if (!found && String(layer.name || '') === name) found = layer;
  });
  return found;
}

function findLayerByPath(psd, layerPath) {
  let current = { children: psd.children || [] };
  for (const value of String(layerPath).split('.')) {
    if (!Array.isArray(current.children)) return null;
    current = current.children[Number(value)];
    if (!current) return null;
  }
  return current;
}

function layerPixelHash(layer) {
  if (!layer) return null;
  try {
    const imageData = layer.imageData || getLayerImageData(layer);
    return hashBytes(imageData?.data);
  } catch {
    return null;
  }
}

function plainColor(color) {
  if (!color || typeof color !== 'object') return null;
  const result = {};
  for (const key of ['r', 'g', 'b', 'a', 'fr', 'fg', 'fb', 'c', 'm', 'y', 'k', 'l']) {
    if (Number.isFinite(color[key])) result[key] = color[key];
  }
  return Object.keys(result).length ? result : null;
}

function unitsBounds(bounds) {
  if (!bounds || typeof bounds !== 'object') return null;
  const result = {};
  for (const key of ['top', 'left', 'bottom', 'right']) {
    const value = bounds[key];
    if (value && typeof value === 'object' && Number.isFinite(value.value)) {
      result[key] = { value: value.value, units: String(value.units || '') };
    }
  }
  return Object.keys(result).length ? result : null;
}

function extractTextLayers(psd) {
  const textLayers = [];
  walkLayers(psd.children || [], ({ layer, indices, names, effectiveHidden, effectiveLocked }) => {
    if (!layer.text || typeof layer.text.text !== 'string' || !layer.text.text.trim()) return;
    const text = layer.text;
    const style = text.style || (Array.isArray(text.styleRuns) ? text.styleRuns[0]?.style : null) || {};
    const paragraphStyle = text.paragraphStyle
      || (Array.isArray(text.paragraphStyleRuns) ? text.paragraphStyleRuns[0]?.style : null)
      || {};
    textLayers.push({
      layer_path: indices.join('.'),
      layer_id: Number.isInteger(layer.id) ? layer.id : null,
      layer_name: String(layer.name || ''),
      layer_names: names,
      text: text.text,
      hidden: effectiveHidden,
      locked: effectiveLocked,
      bounds: {
        top: Number(layer.top ?? 0),
        left: Number(layer.left ?? 0),
        bottom: Number(layer.bottom ?? 0),
        right: Number(layer.right ?? 0),
      },
      text_bounds: unitsBounds(text.bounds || text.boundingBox),
      orientation: String(text.orientation || 'horizontal'),
      shape_type: String(text.shapeType || 'point'),
      transform: Array.isArray(text.transform) ? text.transform.slice(0, 6) : null,
      font_name: style.font?.name ? String(style.font.name) : '',
      font_size: Number.isFinite(style.fontSize) ? style.fontSize : null,
      fill_color: plainColor(style.fillColor),
      justification: paragraphStyle.justification ? String(paragraphStyle.justification) : '',
      has_mixed_styles: Array.isArray(text.styleRuns) && text.styleRuns.length > 1,
    });
  });
  return textLayers;
}

function normalizeText(value) {
  return String(value ?? '').replace(/\r\n|\n/g, '\r');
}

function normalizeXmpText(value) {
  return String(value ?? '').replace(/\r\n|\r/g, '\n');
}

function decodeXmlText(value) {
  return String(value ?? '')
    .replace(/&#x([0-9a-f]+);/gi, (_match, code) => String.fromCodePoint(Number.parseInt(code, 16)))
    .replace(/&#([0-9]+);/g, (_match, code) => String.fromCodePoint(Number.parseInt(code, 10)))
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, '&');
}

function encodeXmlText(value) {
  return normalizeXmpText(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function extractXmpTextLayerEntries(xmpMetadata) {
  if (typeof xmpMetadata !== 'string') return { present: false, entries: [] };
  const block = xmpMetadata.match(/<photoshop:TextLayers\b[^>]*>[\s\S]*?<\/photoshop:TextLayers>/);
  if (!block) return { present: false, entries: [] };

  const entries = [];
  for (const match of block[0].matchAll(/<rdf:li\b[^>]*>[\s\S]*?<\/rdf:li>/g)) {
    const layerName = match[0].match(/<photoshop:LayerName\b[^>]*>([\s\S]*?)<\/photoshop:LayerName>/);
    const layerText = match[0].match(/<photoshop:LayerText\b[^>]*>([\s\S]*?)<\/photoshop:LayerText>/);
    if (!layerName || !layerText) continue;
    entries.push({
      layer_name: decodeXmlText(layerName[1]),
      text: decodeXmlText(layerText[1]),
    });
  }
  return { present: true, entries };
}

function synchronizeXmpTextLayers(psd, updatedLayers) {
  const xmpMetadata = psd.imageResources?.xmpMetadata;
  if (typeof xmpMetadata !== 'string') return [];
  const xmpTextLayers = extractXmpTextLayerEntries(xmpMetadata);
  if (!xmpTextLayers.present) return [];

  const updatedByPath = new Map(updatedLayers.map((item) => [item.layer_path, item]));
  const layerCandidates = [];
  walkLayers(psd.children || [], ({ layer, indices }) => {
    if (!layer.text || typeof layer.text.text !== 'string') return;
    const layerPath = indices.join('.');
    const updated = updatedByPath.get(layerPath);
    layerCandidates.push({
      layer_path: layerPath,
      layer_name: String(layer.name || ''),
      source_text: updated?.source_text ?? layer.text.text,
      updated,
      consumed: false,
    });
  });

  const xmpLayerNames = new Set(xmpTextLayers.entries.map((entry) => entry.layer_name));
  const synchronizedPaths = [];
  const nextMetadata = xmpMetadata.replace(
    /<photoshop:TextLayers\b[^>]*>[\s\S]*?<\/photoshop:TextLayers>/,
    (textLayersBlock) => textLayersBlock.replace(
      /<rdf:li\b[^>]*>[\s\S]*?<\/rdf:li>/g,
      (entry) => {
        const layerNameMatch = entry.match(/<photoshop:LayerName\b[^>]*>([\s\S]*?)<\/photoshop:LayerName>/);
        const layerTextMatch = entry.match(/<photoshop:LayerText\b[^>]*>([\s\S]*?)<\/photoshop:LayerText>/);
        if (!layerNameMatch || !layerTextMatch) return entry;

        const layerName = decodeXmlText(layerNameMatch[1]);
        const currentText = normalizeXmpText(decodeXmlText(layerTextMatch[1]));
        const namedCandidates = layerCandidates.filter(
          (item) => !item.consumed && item.layer_name === layerName,
        );
        if (!namedCandidates.length) return entry;

        const candidate = namedCandidates.find(
          (item) => normalizeXmpText(item.source_text) === currentText,
        ) || namedCandidates[0];
        candidate.consumed = true;
        if (!candidate.updated) return entry;

        synchronizedPaths.push(candidate.layer_path);
        return entry.replace(
          /(<photoshop:LayerText\b[^>]*>)[\s\S]*?(<\/photoshop:LayerText>)/,
          (_match, opening, closing) => `${opening}${encodeXmlText(candidate.updated.text)}${closing}`,
        );
      },
    ),
  );

  const synchronized = new Set(synchronizedPaths);
  const unmatched = updatedLayers.find(
    (item) => !synchronized.has(item.layer_path) && xmpLayerNames.has(item.layer_name),
  );
  if (unmatched) {
    fail(`PSD XMP 文本层无法安全匹配：${unmatched.layer_name}`);
  }

  psd.imageResources.xmpMetadata = nextMetadata;
  return synchronizedPaths;
}

function assertXmpTranslations(psd, updatedLayers, synchronizedPaths) {
  if (!synchronizedPaths.length) return;
  const synchronized = new Set(synchronizedPaths);
  const { present, entries } = extractXmpTextLayerEntries(psd.imageResources?.xmpMetadata);
  if (!present) fail('PSD 导出校验失败，XMP 文本层索引丢失');

  for (const item of updatedLayers) {
    if (!synchronized.has(item.layer_path)) continue;
    const found = entries.some(
      (entry) => entry.layer_name === item.layer_name
        && normalizeXmpText(entry.text) === normalizeXmpText(item.text),
    );
    if (!found) {
      fail(`PSD 导出校验失败，XMP 译文未正确写入：${item.layer_path}`);
    }
  }
}

function resetStyleRuns(textData, textLength) {
  if (Array.isArray(textData.styleRuns) && textData.styleRuns.length) {
    const firstStyle = textData.styleRuns[0]?.style || textData.style || {};
    textData.styleRuns = [{ length: textLength, style: firstStyle }];
  }
  if (Array.isArray(textData.paragraphStyleRuns) && textData.paragraphStyleRuns.length) {
    const firstStyle = textData.paragraphStyleRuns[0]?.style || textData.paragraphStyle || {};
    textData.paragraphStyleRuns = [{ length: textLength, style: firstStyle }];
  }
}

function buildTranslationIndexes(items) {
  const byPath = new Map();
  const byId = new Map();
  for (const item of items) {
    const target = String(item.target_text ?? '');
    if (!target.trim()) continue;
    const normalized = { ...item, target_text: normalizeText(target) };
    if (item.layer_path !== undefined && item.layer_path !== null) {
      byPath.set(String(item.layer_path), normalized);
    }
    if (Number.isInteger(item.layer_id) && !byId.has(item.layer_id)) {
      byId.set(item.layer_id, normalized);
    }
  }
  return { byPath, byId };
}

function applyTranslations(psd, items) {
  const indexes = buildTranslationIndexes(items);
  const updated = [];
  const skipped = [];

  walkLayers(psd.children || [], ({ layer, indices }) => {
    if (!layer.text || typeof layer.text.text !== 'string') return;
    const layerPath = indices.join('.');
    const item = indexes.byPath.get(layerPath)
      || (Number.isInteger(layer.id) ? indexes.byId.get(layer.id) : null);
    if (!item) return;

    const sourceText = layer.text.text;
    const currentSource = normalizeText(sourceText);
    const expectedSource = normalizeText(item.source_text ?? '');
    if (expectedSource && currentSource !== expectedSource) {
      skipped.push({ layer_path: layerPath, reason: 'source_mismatch' });
      return;
    }
    if (String(layer.text.orientation || 'horizontal') === 'vertical') {
      fail(`暂不支持垂直文本图层回写：${String(layer.name || layerPath)}`);
    }

    const target = item.target_text;
    layer.text.text = target;
    resetStyleRuns(layer.text, target.length);
    updated.push({
      layer_path: layerPath,
      layer_id: Number.isInteger(layer.id) ? layer.id : null,
      layer_name: String(layer.name || ''),
      source_text: sourceText,
      text: target,
      layer,
    });
  });

  if (skipped.length) {
    fail(`有 ${skipped.length} 个文本图层因原文不一致而未写回`);
  }
  return updated;
}

function hashBytes(value) {
  if (!value) return null;
  return crypto.createHash('sha256').update(Buffer.from(value.buffer, value.byteOffset, value.byteLength)).digest('hex');
}

function canonicalize(value, seen = new WeakSet()) {
  if (value === null || value === undefined || typeof value !== 'object') return value;
  if (ArrayBuffer.isView(value)) {
    return { type: value.constructor.name, length: value.byteLength, sha256: hashBytes(value) };
  }
  if (seen.has(value)) return '[Circular]';
  seen.add(value);
  if (Array.isArray(value)) return value.map((item) => canonicalize(item, seen));
  const result = {};
  for (const key of Object.keys(value).sort()) {
    if (key === 'canvas' || key === 'imageData' || key === 'rawData') continue;
    result[key] = canonicalize(value[key], seen);
  }
  return result;
}

function hashValue(value) {
  return crypto.createHash('sha256').update(JSON.stringify(canonicalize(value))).digest('hex');
}

function normalizeXmpForIntegrity(xmpMetadata, allowedLayerNames) {
  if (typeof xmpMetadata !== 'string' || !allowedLayerNames.size) return xmpMetadata;
  return xmpMetadata.replace(
    /<photoshop:TextLayers\b[^>]*>[\s\S]*?<\/photoshop:TextLayers>/,
    (textLayersBlock) => textLayersBlock.replace(
      /<rdf:li\b[^>]*>[\s\S]*?<\/rdf:li>/g,
      (entry) => {
        const layerNameMatch = entry.match(/<photoshop:LayerName\b[^>]*>([\s\S]*?)<\/photoshop:LayerName>/);
        if (!layerNameMatch || !allowedLayerNames.has(decodeXmlText(layerNameMatch[1]))) return entry;
        return entry.replace(
          /(<photoshop:LayerText\b[^>]*>)[\s\S]*?(<\/photoshop:LayerText>)/,
          '$1[translation-system-updated-text]$2',
        );
      },
    ),
  );
}

function hashImageResources(imageResources, allowedXmpLayerNames) {
  if (!imageResources || typeof imageResources !== 'object') return hashValue(null);
  const resources = { ...imageResources };
  // 导出会重新生成缩略图，并同步目标图层对应的 XMP 文本；其他资源必须不变。
  delete resources.thumbnail;
  delete resources.thumbnailRaw;
  if (resources.xmpMetadata !== undefined) {
    resources.xmpMetadata = normalizeXmpForIntegrity(resources.xmpMetadata, allowedXmpLayerNames);
  }
  return hashValue(resources);
}

function hashLinkedFiles(linkedFiles) {
  if (!Array.isArray(linkedFiles)) return hashValue(null);
  const normalized = linkedFiles.map((file) => {
    const result = { ...file };
    // ag-psd 将缺省的四字节 creator/type 签名写成 NUL 或空格，重读后
    // 可能从“字段不存在”变为仅含 NUL 的字符串；两者在 PSD 中语义相同。
    for (const key of ['type', 'creator']) {
      const signature = String(result[key] || '').replace(/\0/g, '').trim();
      if (signature) result[key] = signature;
      else delete result[key];
    }
    return result;
  });
  return hashValue(normalized);
}

function layerStateForIntegrity(layer, allowTextChange) {
  const text = layer.text && typeof layer.text === 'object'
    ? {
      ...layer.text,
      text: allowTextChange ? '[translation-system-updated-text]' : layer.text.text,
      styleRuns: allowTextChange && Array.isArray(layer.text.styleRuns)
        ? layer.text.styleRuns.map((run) => ({ ...run, length: 0 }))
        : layer.text.styleRuns,
      paragraphStyleRuns: allowTextChange && Array.isArray(layer.text.paragraphStyleRuns)
        ? layer.text.paragraphStyleRuns.map((run) => ({ ...run, length: 0 }))
        : layer.text.paragraphStyleRuns,
    }
    : null;
  return {
    id: Number.isInteger(layer.id) ? layer.id : null,
    name: String(layer.name || ''),
    top: allowTextChange ? null : Number(layer.top ?? 0),
    left: allowTextChange ? null : Number(layer.left ?? 0),
    bottom: allowTextChange ? null : Number(layer.bottom ?? 0),
    right: allowTextChange ? null : Number(layer.right ?? 0),
    opacity: Number(layer.opacity ?? 1),
    hidden: Boolean(layer.hidden),
    blendMode: String(layer.blendMode || ''),
    clipping: Boolean(layer.clipping),
    protected: layer.protected || null,
    mask: layer.mask || null,
    realMask: layer.realMask || null,
    vectorMask: layer.vectorMask || null,
    vectorFill: layer.vectorFill || null,
    effects: layer.effects || null,
    adjustment: layer.adjustment || null,
    placedLayer: layer.placedLayer || null,
    text: null,
  };
}

function buildIntegritySnapshot(psd, targetPaths) {
  const layers = [];
  const nonTargetRawData = [];
  const allowedXmpLayerNames = new Set();
  walkOriginalLayers(psd.children || [], ({ layer, indices }) => {
    const layerPath = indices.join('.');
    layers.push({
      layer_path: layerPath,
      layer_id: Number.isInteger(layer.id) ? layer.id : null,
      name: String(layer.name || ''),
      kind: Array.isArray(layer.children)
        ? 'group'
        : layer.text
          ? 'text'
          : layer.placedLayer
            ? 'placed'
            : layer.adjustment
              ? 'adjustment'
              : 'pixel',
      children: Array.isArray(layer.children)
        ? layer.children.filter((child) => !isGeneratedOcrLayer(child)).length
        : 0,
      hidden: Boolean(layer.hidden),
      blend_mode: String(layer.blendMode || ''),
      placed_id: layer.placedLayer?.id ? String(layer.placedLayer.id) : '',
      state_sha256: targetPaths.has(layerPath) && Boolean(layer.text)
        ? null
        : hashValue(layerStateForIntegrity(layer, false)),
    });
    if (targetPaths.has(layerPath) && layer.text) {
      allowedXmpLayerNames.add(String(layer.name || ''));
    }
    if (targetPaths.has(layerPath) || !layer.rawData?.channels) return;
    nonTargetRawData.push({
      layer_path: layerPath,
      channels: layer.rawData.channels.map((channel) => ({
        id: channel.id,
        compression: channel.compression,
        length: channel.data?.byteLength || 0,
        sha256: hashBytes(channel.data),
      })),
    });
  });
  return {
    document: {
      width: psd.width,
      height: psd.height,
      channels: psd.channels ?? null,
      bits_per_channel: psd.bitsPerChannel ?? null,
      color_mode: psd.colorMode ?? null,
    },
    layers,
    non_target_raw_data: nonTargetRawData,
    has_raw_composite: Boolean(psd.rawCompositeData?.byteLength),
    engine_data_sha256: hashValue(psd.engineData || null),
    image_resources_sha256: hashImageResources(psd.imageResources, allowedXmpLayerNames),
    linked_files_sha256: hashLinkedFiles(psd.linkedFiles),
  };
}

function assertIntegrityUnchanged(before, after) {
  for (const key of [
    'document',
    'layers',
    'non_target_raw_data',
    'has_raw_composite',
    'engine_data_sha256',
    'image_resources_sha256',
    'linked_files_sha256',
  ]) {
    if (JSON.stringify(before[key]) !== JSON.stringify(after[key])) {
      if (key === 'layers') {
        const maximum = Math.max(before.layers.length, after.layers.length);
        for (let index = 0; index < maximum; index += 1) {
          if (JSON.stringify(before.layers[index]) !== JSON.stringify(after.layers[index])) {
            fail(
              `PSD 导出完整性校验失败：layers（${before.layers[index]?.layer_path || after.layers[index]?.layer_path || index}）`,
            );
          }
        }
      }
      fail(`PSD 导出完整性校验失败：${key}`);
    }
  }
}

function writeDocument(psd) {
  if (!psd.imageData) fail('PSD 中文合成预览未生成');
  if (psd.imageResources) {
    // useRawThumbnail 读取到的旧缩略图优先级更高，写出前移除后重新生成中文缩略图。
    delete psd.imageResources.thumbnailRaw;
    delete psd.imageResources.thumbnail;
  }

  const messages = [];
  const originalLog = console.log;
  const originalWarn = console.warn;
  console.log = (...args) => messages.push(args.map(String).join(' '));
  console.warn = (...args) => messages.push(args.map(String).join(' '));
  let output;
  try {
    output = writePsdBuffer(psd, {
      // Txt2 保存全局字体资源和文本路径。删除它会让 Photoshop 打开后重排
      // 富文本与路径文本；目标层的 TySh 仍会根据 layer.text 写入译文。
      invalidateTextLayers: false,
      generateThumbnail: true,
      trimImageData: false,
      logMissingFeatures: true,
      compress: false,
    });
  } finally {
    console.log = originalLog;
    console.warn = originalWarn;
  }
  if (messages.length) {
    fail(`PSD 包含当前运行时无法安全写回的特性：${messages.slice(0, 3).join('；')}`);
  }
  return output;
}

function readPsdHeader(inputPath) {
  const fileDescriptor = fs.openSync(inputPath, 'r');
  try {
    const header = Buffer.alloc(26);
    const bytesRead = fs.readSync(fileDescriptor, header, 0, header.length, 0);
    const availableHeader = header.subarray(0, bytesRead);
    assertPsdHeader(availableHeader);
    return {
      height: availableHeader.readUInt32BE(14),
      width: availableHeader.readUInt32BE(18),
      bitsPerChannel: availableHeader.readUInt16BE(22),
      colorMode: availableHeader.readUInt16BE(24),
    };
  } finally {
    fs.closeSync(fileDescriptor);
  }
}

function validateSourceDimensions(header) {
  if (!header.width || !header.height) fail('PSD 画布尺寸无效');
  if (header.width > MAX_DIMENSION || header.height > MAX_DIMENSION) {
    fail(`PSD 画布尺寸 ${header.width} × ${header.height} 超过限制（单边最大 ${MAX_DIMENSION} 像素）`);
  }
  if (header.width * header.height > MAX_CANVAS_PIXELS) {
    fail(`PSD 画布尺寸 ${header.width} × ${header.height} 的总像素超过限制（最多 ${MAX_CANVAS_PIXELS} 像素）`);
  }
}

function getImageMagickTimeoutMs() {
  const outerTimeout = Number(process.env.PSD_PROCESS_TIMEOUT_SECONDS || 120);
  const maximumSeconds = Number.isFinite(outerTimeout)
    ? Math.max(Math.min(outerTimeout - 5, 1800), 1)
    : 115;
  const requested = Number(process.env.PSD_IMAGEMAGICK_TIMEOUT_SECONDS || 90);
  const seconds = Number.isFinite(requested)
    ? Math.min(Math.max(requested, 1), maximumSeconds)
    : Math.min(90, maximumSeconds);
  return Math.round(seconds * 1000);
}

function getImageMagickCandidates() {
  const configured = String(process.env.PSD_IMAGEMAGICK_PATH || '').trim();
  if (configured) return [configured];
  // Windows 自带的 convert.exe 不是 ImageMagick，不能作为回退命令。
  return process.platform === 'win32' ? ['magick'] : ['magick', 'convert'];
}

function convertCmykPsdToRgb(inputPath, outputPath) {
  const { spawnSync } = require('node:child_process');
  const configured = Boolean(String(process.env.PSD_IMAGEMAGICK_PATH || '').trim());
  const args = [
    '-limit', 'memory', '512MiB',
    '-limit', 'map', '1GiB',
    '-limit', 'disk', '2GiB',
    '-limit', 'thread', '2',
    inputPath,
    '-colorspace', 'sRGB',
    '-depth', '8',
    outputPath,
  ];

  for (const executable of getImageMagickCandidates()) {
    const result = spawnSync(executable, args, {
      encoding: 'utf8',
      windowsHide: true,
      timeout: getImageMagickTimeoutMs(),
      maxBuffer: 1024 * 1024,
    });
    if (result.error?.code === 'ENOENT' && !configured) continue;
    if (result.error?.code === 'ETIMEDOUT') {
      fail(`CMYK PSD 转 RGB 超时（${getImageMagickTimeoutMs() / 1000} 秒）`);
    }
    if (result.error) {
      fail(`无法启动 ImageMagick（${executable}）：${result.error.message}`);
    }
    if (result.status !== 0) {
      const detail = String(result.stderr || result.stdout || `退出码 ${result.status}`)
        .trim()
        .slice(0, 1000);
      fail(`CMYK PSD 转 RGB 失败：${detail}`);
    }
    if (!fs.existsSync(outputPath) || fs.statSync(outputPath).size < 26) {
      fail('CMYK PSD 转 RGB 失败：ImageMagick 未生成有效的 PSD 文件');
    }
    if (readPsdHeader(outputPath).colorMode !== RGB_COLOR_MODE) {
      fail('CMYK PSD 转 RGB 失败：转换结果仍不是 RGB 颜色模式');
    }
    return;
  }

  fail(
    '检测到 CMYK PSD，但未找到 ImageMagick；'
    + '请安装 ImageMagick（Windows 推荐 7，Linux 可使用发行版软件包）并确保命令在 PATH 中，'
    + '或通过 PSD_IMAGEMAGICK_PATH 配置可执行文件路径',
  );
}

function parseCommand(inputPath, candidateDirectory) {
  const sourceHeader = readPsdHeader(inputPath);
  if (sourceHeader.colorMode !== 4) {
    return parseRgbCommand(inputPath, candidateDirectory);
  }
  validateSourceDimensions(sourceHeader);

  // 与输入文件放在同一个请求级临时目录中；即使父进程强制终止 bridge，
  // Python TemporaryDirectory 仍会回收可能遗留的转换文件。
  const temporaryDirectory = fs.mkdtempSync(
    path.join(path.dirname(inputPath), '.translation-psd-cmyk-'),
  );
  const convertedPath = path.join(temporaryDirectory, 'converted-rgb.psd');
  try {
    convertCmykPsdToRgb(inputPath, convertedPath);
    return {
      ...parseRgbCommand(convertedPath, candidateDirectory),
      source_color_mode: 4,
      color_mode_converted: true,
    };
  } finally {
    fs.rmSync(temporaryDirectory, {
      recursive: true,
      force: true,
      maxRetries: 3,
      retryDelay: 100,
    });
  }
}

function parseRgbCommand(inputPath, candidateDirectory) {
  const psd = readDocument(inputPath);
  const textLayers = extractTextLayers(psd);
  const imageTextCandidates = exportImageTextCandidates(psd, candidateDirectory);
  return {
    width: psd.width,
    height: psd.height,
    bits_per_channel: psd.bitsPerChannel ?? 8,
    color_mode: psd.colorMode ?? RGB_COLOR_MODE,
    text_layers: textLayers,
    text_layer_count: textLayers.length,
    image_text_candidates: imageTextCandidates,
  };
}

function exportCommand(inputPath, translationsPath, outputPath) {
  const psd = readDocument(inputPath, { preserveLinkedFiles: true });
  const payload = JSON.parse(fs.readFileSync(translationsPath, 'utf8'));
  const translations = Array.isArray(payload) ? payload : payload.translations;
  if (!Array.isArray(translations)) fail('PSD 译文数据格式无效');

  const textTranslations = translations.filter((item) => item.entity_type !== 'PSD_IMAGE_TEXT');
  const imageTranslations = translations.filter((item) => item.entity_type === 'PSD_IMAGE_TEXT');
  const expectedTextPaths = new Set(
    textTranslations
      .filter((item) => String(item.target_text ?? '').trim() && item.layer_path !== undefined)
      .map((item) => String(item.layer_path)),
  );
  const expectedImagePaths = new Set(
    imageTranslations
      .filter((item) => String(item.target_text ?? '').trim() && item.layer_path !== undefined)
      .map((item) => String(item.layer_path)),
  );
  // 仅原生文本层允许发生内容变化。OCR 源像素层必须作为非目标层逐通道校验。
  const integrityBefore = buildIntegritySnapshot(psd, expectedTextPaths);
  const updated = applyTranslations(psd, textTranslations);
  const synchronizedXmpPaths = synchronizeXmpTextLayers(psd, updated);
  const renderedImages = renderImageTranslations(psd, imageTranslations);
  if (updated.length !== expectedTextPaths.size || renderedImages.length !== expectedImagePaths.size) {
    fail(
      `PSD 译文写回数量不一致：文本 ${updated.length}/${expectedTextPaths.size}，`
      + `图片文字 ${renderedImages.length}/${expectedImagePaths.size}`,
    );
  }
  if (!updated.length && !renderedImages.length) {
    fail('没有找到可写回的 PSD 文本或图片文字译文');
  }
  const rendering = renderTranslatedTextLayers(psd, updated, renderedImages);
  const expectedCompositeSha256 = hashBytes(psd.imageData?.data);
  const output = writeDocument(psd);
  assertPsdHeader(output);
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, output);

  const verification = readDocument(outputPath, { preserveLinkedFiles: true });
  assertIntegrityUnchanged(integrityBefore, buildIntegritySnapshot(verification, expectedTextPaths));
  assertXmpTranslations(verification, updated, synchronizedXmpPaths);
  for (const item of updated) {
    const verifiedLayer = findOriginalLayerByPath(verification, item.layer_path);
    if (!verifiedLayer?.text || normalizeText(verifiedLayer.text.text) !== item.text) {
      fail(`PSD 导出校验失败，文本图层未正确写入：${item.layer_path}`);
    }
  }
  for (const item of renderedImages) {
    const sourceLocation = findOriginalLayerLocationByPath(verification, item.layer_path);
    const sourceLayer = sourceLocation?.layer;
    const sourceIndex = sourceLocation?.index ?? -1;
    const cleanupLayer = sourceIndex >= 0 ? sourceLocation.children[sourceIndex + 1] : null;
    const textLayer = sourceIndex >= 0 ? sourceLocation.children[sourceIndex + 2] : null;
    if (
      String(cleanupLayer?.name || '') !== item.cleanup_name
      || String(textLayer?.name || '') !== item.text_name
    ) {
      fail(
        `PSD 导出校验失败，layer records 必须为“原图片 → 原文清除 → 译文文字”，`
        + `以确保 Photopea 面板显示为“译文文字 → 原文清除 → 原图片”：${item.layer_path}`,
      );
    }
    if (!sourceLayer || layerPixelHash(sourceLayer) !== item.source_pixel_sha256) {
      fail(`PSD 导出校验失败，OCR 原始图片层发生变化：${item.layer_path}`);
    }
    if (!cleanupLayer || layerPixelHash(cleanupLayer) !== item.cleanup_pixel_sha256) {
      fail(`PSD 导出校验失败，OCR 清除层写入异常：${item.layer_path}`);
    }
    if (
      !textLayer?.text
      || normalizeText(textLayer.text.text) !== item.text
      || layerPixelHash(textLayer) !== item.text_pixel_sha256
      || String(textLayer.text.shapeType || '') !== 'box'
    ) {
      fail(`PSD 导出校验失败，OCR 可编辑译文层写入异常：${item.layer_path}`);
    }
  }
  const verifiedComposite = getCompositeImageData(verification);
  if (!verifiedComposite || hashBytes(verifiedComposite.data) !== expectedCompositeSha256) {
    fail('PSD 导出校验失败，最终合成预览与预期不一致');
  }
  return {
    updated_count: updated.length + renderedImages.length,
    text_updated_count: updated.length,
    image_text_updated_count: renderedImages.length,
    ocr_cleanup_layer_count: renderedImages.length,
    ocr_editable_text_layer_count: renderedImages.length,
    ocr_record_order: 'source-cleanup-text',
    ocr_photopea_layer_order: 'text-cleanup-source',
    rendered_count: rendering.rendered_count,
    xmp_updated_count: synchronizedXmpPaths.length,
    output_size: output.length,
  };
}

async function main() {
  const [command, ...args] = process.argv.slice(2);
  let result;
  if (command === 'health') {
    result = {
      ok: true,
      runtime: 'ag-psd',
      version: require('ag-psd/package.json').version,
      ocr_bridge: 'candidate-png',
    };
  } else if (command === 'parse' && args.length === 2) {
    result = parseCommand(args[0], args[1]);
  } else if (command === 'export' && args.length === 3) {
    result = exportCommand(args[0], args[1], args[2]);
  } else {
    fail('用法：bridge.cjs health | parse <input.psd> <candidate-directory> | export <input.psd> <translations.json> <output.psd>');
  }
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
