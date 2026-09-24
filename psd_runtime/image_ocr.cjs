'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const agPsd = require('ag-psd');
const { createCanvas, GlobalFonts, ImageData } = require('@napi-rs/canvas');

const OCR_SCALE = 2;
const MIN_WIDTH = 48;
const MIN_HEIGHT = 32;
const MAX_LAYER_PIXELS = 4_000_000;
const MAX_CANDIDATE_LAYERS = 24;
const GENERATED_TEXT_PREFIX = '__TS_OCR_TEXT__';
const GENERATED_CLEANUP_PREFIX = '__TS_OCR_CLEANUP__';
const CJK_FONT_FAMILIES = [
  'Microsoft YaHei',
  'Noto Sans CJK SC',
  'Noto Sans SC',
  'Source Han Sans SC',
  'SimHei',
  'Arial Unicode MS',
  'Arial',
  'sans-serif',
];

function clamp(value, minimum, maximum) {
  return Math.min(Math.max(value, minimum), maximum);
}

function toNativeImageData(imageData) {
  if (imageData instanceof ImageData) return imageData;
  return new ImageData(new Uint8ClampedArray(
    imageData.data.buffer,
    imageData.data.byteOffset,
    imageData.data.byteLength,
  ), imageData.width, imageData.height);
}

function layerImageData(layer) {
  try {
    return layer.imageData || agPsd.getLayerImageData(layer);
  } catch {
    return null;
  }
}

function quantizedColorKey(r, g, b, a) {
  const quantize = (value) => Math.round(value / 16) * 16;
  return `${quantize(r)},${quantize(g)},${quantize(b)},${quantize(a)}`;
}

function parseColorKey(key) {
  return key.split(',').map((value) => clamp(Number(value), 0, 255));
}

function analyzeFlatTextImage(imageData) {
  if (!imageData || imageData.width < MIN_WIDTH || imageData.height < MIN_HEIGHT) return null;
  if (imageData.width * imageData.height > MAX_LAYER_PIXELS) return null;

  const { data, width, height } = imageData;
  const borderColors = new Map();
  const step = Math.max(1, Math.floor(Math.min(width, height) / 80));
  const addBorderColor = (x, y) => {
    const offset = (y * width + x) * 4;
    const key = quantizedColorKey(data[offset], data[offset + 1], data[offset + 2], data[offset + 3]);
    borderColors.set(key, (borderColors.get(key) || 0) + 1);
  };
  for (let x = 0; x < width; x += step) {
    addBorderColor(x, 0);
    addBorderColor(x, height - 1);
  }
  for (let y = 0; y < height; y += step) {
    addBorderColor(0, y);
    addBorderColor(width - 1, y);
  }
  const dominant = [...borderColors.entries()].sort((left, right) => right[1] - left[1])[0];
  if (!dominant) return null;
  const background = parseColorKey(dominant[0]);
  if (background[3] < 192) return null;

  let opaque = 0;
  let backgroundPixels = 0;
  let contrastingPixels = 0;
  let darkest = [0, 0, 0, 255];
  let darkestLuminance = 256;
  const pixelStep = Math.max(1, Math.floor(Math.sqrt((width * height) / 120_000)));
  for (let y = 0; y < height; y += pixelStep) {
    for (let x = 0; x < width; x += pixelStep) {
      const offset = (y * width + x) * 4;
      const alpha = data[offset + 3];
      if (alpha < 192) continue;
      opaque += 1;
      const red = data[offset];
      const green = data[offset + 1];
      const blue = data[offset + 2];
      const distance = Math.max(
        Math.abs(red - background[0]),
        Math.abs(green - background[1]),
        Math.abs(blue - background[2]),
      );
      if (distance <= 28) backgroundPixels += 1;
      if (distance >= 72) {
        contrastingPixels += 1;
        const luminance = red * 0.2126 + green * 0.7152 + blue * 0.0722;
        if (luminance < darkestLuminance) {
          darkestLuminance = luminance;
          darkest = [red, green, blue, alpha];
        }
      }
    }
  }
  if (!opaque) return null;
  const backgroundRatio = backgroundPixels / opaque;
  const contrastRatio = contrastingPixels / opaque;
  if (backgroundRatio < 0.55 || contrastRatio < 0.002 || contrastRatio > 0.35) return null;
  return {
    background_color: { r: background[0], g: background[1], b: background[2], a: background[3] },
    foreground_color: { r: darkest[0], g: darkest[1], b: darkest[2], a: darkest[3] },
    background_ratio: backgroundRatio,
    contrast_ratio: contrastRatio,
  };
}

function canvasForOcr(imageData) {
  const source = createCanvas(imageData.width, imageData.height);
  source.getContext('2d').putImageData(toNativeImageData(imageData), 0, 0);
  const scaled = createCanvas(imageData.width * OCR_SCALE, imageData.height * OCR_SCALE);
  const context = scaled.getContext('2d');
  context.imageSmoothingEnabled = true;
  context.drawImage(source, 0, 0, scaled.width, scaled.height);
  return scaled;
}

function isGeneratedOcrLayer(layer) {
  const name = String(layer?.name || '');
  return name.startsWith(GENERATED_TEXT_PREFIX) || name.startsWith(GENERATED_CLEANUP_PREFIX);
}

function collectCandidates(psd) {
  const candidates = [];
  function walk(
    layers,
    parentPath = '',
    parentNames = [],
    hiddenByParent = false,
    lockedByParent = false,
  ) {
    for (let index = 0; index < (layers || []).length; index += 1) {
      const layer = layers[index];
      const layerPath = parentPath ? `${parentPath}.${index}` : String(index);
      const names = [...parentNames, String(layer.name || `图层 ${index + 1}`)];
      const hidden = hiddenByParent || Boolean(layer.hidden);
      const locked = lockedByParent || Boolean(
        layer.protected?.composite
        || layer.protected?.position
        || layer.protected?.transparency
      );
      if (Array.isArray(layer.children)) {
        walk(layer.children, layerPath, names, hidden, locked);
        continue;
      }
      const unsupportedLayer = Boolean(
        isGeneratedOcrLayer(layer)
        || layer.text
        || hidden
        || locked
        || layer.placedLayer
        || layer.adjustment
        || layer.vectorFill
        || layer.mask
        || layer.realMask
        || layer.vectorMask
        || layer.clipping
      );
      if (unsupportedLayer || /^<BG>$/i.test(String(layer.name || '').trim())) continue;
      const imageData = layerImageData(layer);
      const analysis = analyzeFlatTextImage(imageData);
      if (!analysis) continue;
      candidates.push({ layer, layer_path: layerPath, layer_names: names, imageData, analysis });
    }
  }
  walk(psd.children || []);
  if (candidates.length > MAX_CANDIDATE_LAYERS) {
    throw new Error(
      `PSD OCR 候选像素层过多（${candidates.length} 层，最多 ${MAX_CANDIDATE_LAYERS} 层），请关闭 PSD OCR 或简化文件`,
    );
  }
  return candidates;
}

function exportImageTextCandidates(psd, outputDirectory) {
  if (/^(0|false|off|no)$/i.test(String(process.env.PSD_OCR_ENABLED || '').trim())) return [];
  const candidates = collectCandidates(psd);
  if (!candidates.length) return [];
  fs.mkdirSync(outputDirectory, { recursive: true });
  return candidates.map((candidate, index) => {
    const filename = `candidate-${String(index + 1).padStart(3, '0')}.png`;
    fs.writeFileSync(path.join(outputDirectory, filename), canvasForOcr(candidate.imageData).toBuffer('image/png'));
    return {
      entity_type: 'PSD_IMAGE_TEXT',
      candidate_file: filename,
      ocr_scale: OCR_SCALE,
      layer_path: candidate.layer_path,
      layer_id: Number.isInteger(candidate.layer.id) ? candidate.layer.id : null,
      layer_name: String(candidate.layer.name || ''),
      layer_names: candidate.layer_names,
      hidden: Boolean(candidate.layer.hidden),
      locked: Boolean(candidate.layer.protected?.composite || candidate.layer.protected?.position),
      bounds: {
        top: Number(candidate.layer.top ?? 0),
        left: Number(candidate.layer.left ?? 0),
        bottom: Number(candidate.layer.bottom ?? 0),
        right: Number(candidate.layer.right ?? 0),
      },
      image_width: candidate.imageData.width,
      image_height: candidate.imageData.height,
      background_color: candidate.analysis.background_color,
      foreground_color: candidate.analysis.foreground_color,
    };
  });
}

function colorCss(color, fallback) {
  if (!color || typeof color !== 'object') return fallback;
  const alphaValue = Number(color.a);
  const alpha = Number.isFinite(alphaValue) ? (alphaValue > 1 ? alphaValue / 255 : alphaValue) : 1;
  return `rgba(${clamp(Number(color.r || 0), 0, 255)}, ${clamp(Number(color.g || 0), 0, 255)}, ${clamp(Number(color.b || 0), 0, 255)}, ${clamp(alpha, 0, 1)})`;
}

function colorLuminance(color) {
  const channel = (value) => {
    const normalized = clamp(Number(value || 0), 0, 255) / 255;
    return normalized <= 0.04045
      ? normalized / 12.92
      : ((normalized + 0.055) / 1.055) ** 2.4;
  };
  return channel(color?.r) * 0.2126 + channel(color?.g) * 0.7152 + channel(color?.b) * 0.0722;
}

function colorContrast(left, right) {
  const first = colorLuminance(left);
  const second = colorLuminance(right);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

function readableForeground(background, requested) {
  const candidate = requested && typeof requested === 'object'
    ? { r: Number(requested.r || 0), g: Number(requested.g || 0), b: Number(requested.b || 0), a: 255 }
    : { r: 0, g: 0, b: 0, a: 255 };
  if (colorContrast(background, candidate) >= 4.5) return candidate;
  const black = { r: 0, g: 0, b: 0, a: 255 };
  const white = { r: 255, g: 255, b: 255, a: 255 };
  return colorContrast(background, black) >= colorContrast(background, white) ? black : white;
}

function fontFamily() {
  for (const family of CJK_FONT_FAMILIES) {
    try {
      if (family === 'sans-serif' || GlobalFonts.has(family)) return family;
    } catch {
      return family;
    }
  }
  return 'sans-serif';
}

function psdFontName(family) {
  const names = {
    'Microsoft YaHei': 'MicrosoftYaHei',
    'Noto Sans CJK SC': 'NotoSansCJKsc-Regular',
    'Noto Sans SC': 'NotoSansSC-Regular',
    'Source Han Sans SC': 'SourceHanSansSC-Regular',
    SimHei: 'SimHei',
    'Arial Unicode MS': 'ArialUnicodeMS',
    Arial: 'ArialMT',
  };
  return names[family] || 'ArialMT';
}

function splitTokenToFit(context, token, maxWidth) {
  const parts = [];
  let current = '';
  for (const character of token) {
    if (current && context.measureText(current + character).width > maxWidth) {
      parts.push(current);
      current = character;
    } else {
      current += character;
    }
  }
  if (current) parts.push(current);
  return parts;
}

function wrapText(context, text, maxWidth) {
  const lines = [];
  for (const paragraph of String(text || '').split(/\r\n|\r|\n/)) {
    const hasSpaces = /\s/.test(paragraph.trim());
    const tokens = hasSpaces ? paragraph.trim().split(/\s+/) : [...paragraph.trim()];
    let current = '';
    for (const rawToken of tokens) {
      const separator = current && hasSpaces ? ' ' : '';
      const candidate = `${current}${separator}${rawToken}`;
      if (!current || context.measureText(candidate).width <= maxWidth) {
        current = candidate;
        continue;
      }
      lines.push(current);
      const parts = splitTokenToFit(context, rawToken, maxWidth);
      current = parts.pop() || '';
      lines.push(...parts);
    }
    if (current) lines.push(current);
  }
  return lines.length ? lines : [''];
}

function fitImageText(context, text, width, height, preferredSize, family) {
  let size = clamp(Number(preferredSize || 18), 8, 128);
  while (size >= 8) {
    context.font = `400 ${size}px "${family}"`;
    const lines = wrapText(context, text, width);
    const lineHeight = size * 1.25;
    const fitsWidth = lines.every((line) => context.measureText(line).width <= width);
    if (fitsWidth && lines.length * lineHeight <= height) return { size, lines, lineHeight };
    size -= 1;
  }
  throw new Error('PSD 图片译文无法在原文字区域内完整排版');
}

function findLayerLocation(psd, layerPath) {
  let children = psd.children || [];
  let layer = null;
  let hidden = false;
  let locked = false;
  const values = String(layerPath).split('.').map(Number);
  for (let depth = 0; depth < values.length; depth += 1) {
    const index = values[depth];
    layer = children[index];
    if (!layer) return null;
    hidden = hidden || Boolean(layer.hidden);
    locked = locked || Boolean(
      layer.protected?.composite
      || layer.protected?.position
      || layer.protected?.transparency
    );
    if (depth === values.length - 1) return { layer, children, index, hidden, locked };
    if (!Array.isArray(layer.children)) return null;
    children = layer.children;
  }
  return null;
}

function normalizedRegionBounds(rawBounds, width, height, padding = 6) {
  if (!rawBounds || typeof rawBounds !== 'object') return null;
  const left = clamp(Math.floor(Number(rawBounds.left)) - padding, 0, width);
  const top = clamp(Math.floor(Number(rawBounds.top)) - padding, 0, height);
  const right = clamp(Math.ceil(Number(rawBounds.right)) + padding, 0, width);
  const bottom = clamp(Math.ceil(Number(rawBounds.bottom)) + padding, 0, height);
  if (![left, top, right, bottom].every(Number.isFinite) || right <= left || bottom <= top) return null;
  return { left, top, right, bottom };
}

function hashImageData(imageData) {
  if (!imageData?.data) return null;
  return crypto.createHash('sha256').update(Buffer.from(
    imageData.data.buffer,
    imageData.data.byteOffset,
    imageData.data.byteLength,
  )).digest('hex');
}

function createCleanupLayer(item, absoluteBounds, width, height, background) {
  const canvas = createCanvas(width, height);
  const context = canvas.getContext('2d');
  context.fillStyle = colorCss(background, 'rgb(255, 255, 255)');
  context.fillRect(0, 0, width, height);
  return {
    name: `${GENERATED_CLEANUP_PREFIX}${item.layer_path}`,
    left: absoluteBounds.left,
    top: absoluteBounds.top,
    right: absoluteBounds.right,
    bottom: absoluteBounds.bottom,
    imageData: context.getImageData(0, 0, width, height),
  };
}

function createEditableTextLayer(item, target, absoluteBounds, width, height, foreground) {
  const canvas = createCanvas(width, height);
  const context = canvas.getContext('2d');
  const inset = 4;
  const family = fontFamily();
  const fitted = fitImageText(
    context,
    target,
    Math.max(width - inset * 2, 1),
    Math.max(height - inset * 2, 1),
    item.font_size,
    family,
  );
  context.font = `400 ${fitted.size}px "${family}"`;
  context.fillStyle = colorCss(foreground, 'rgb(0, 0, 0)');
  context.textAlign = 'center';
  context.textBaseline = 'alphabetic';
  let baseline = (height - fitted.lines.length * fitted.lineHeight) / 2 + fitted.size;
  for (const line of fitted.lines) {
    context.fillText(line, width / 2, baseline);
    baseline += fitted.lineHeight;
  }

  const unitsBounds = {
    top: { value: 0, units: 'Pixels' },
    left: { value: 0, units: 'Pixels' },
    bottom: { value: height, units: 'Pixels' },
    right: { value: width, units: 'Pixels' },
  };
  const normalizedTarget = target.replace(/\r\n|\n/g, '\r');
  return {
    name: `${GENERATED_TEXT_PREFIX}${item.layer_path}`,
    left: absoluteBounds.left,
    top: absoluteBounds.top,
    right: absoluteBounds.right,
    bottom: absoluteBounds.bottom,
    imageData: context.getImageData(0, 0, width, height),
    text: {
      text: normalizedTarget,
      transform: [1, 0, 0, 1, absoluteBounds.left, absoluteBounds.top],
      antiAlias: 'smooth',
      gridding: 'none',
      orientation: 'horizontal',
      shapeType: 'box',
      boxBounds: [0, 0, width, height],
      bounds: unitsBounds,
      boundingBox: unitsBounds,
      style: {
        font: { name: psdFontName(family) },
        fontSize: fitted.size,
        fillColor: {
          r: clamp(Number(foreground?.r || 0), 0, 255),
          g: clamp(Number(foreground?.g || 0), 0, 255),
          b: clamp(Number(foreground?.b || 0), 0, 255),
          a: 1,
        },
        fillFlag: true,
        strokeFlag: false,
      },
      paragraphStyle: { justification: 'center' },
    },
  };
}

function renderImageTranslations(psd, items) {
  const operations = [];
  for (const item of items || []) {
    if (item.entity_type !== 'PSD_IMAGE_TEXT') continue;
    const target = String(item.target_text || '').trim();
    if (!target || item.layer_path === undefined || item.layer_path === null) continue;
    const layerPath = String(item.layer_path);
    const location = findLayerLocation(psd, layerPath);
    const layer = location?.layer;
    if (!layer || layer.text || Array.isArray(layer.children)) {
      throw new Error(`PSD 图片文字图层不存在：${layerPath}`);
    }
    if (
      isGeneratedOcrLayer(layer)
      || location.hidden
      || location.locked
      || layer.placedLayer
      || layer.adjustment
      || layer.vectorFill
      || layer.mask
      || layer.realMask
      || layer.vectorMask
      || layer.clipping
      || layer.protected?.composite
      || layer.protected?.position
      || layer.protected?.transparency
    ) {
      throw new Error(`PSD 图片文字图层包含不支持的结构：${String(layer.name || layerPath)}`);
    }
    const sourceImage = layerImageData(layer);
    const analysis = analyzeFlatTextImage(sourceImage);
    if (!analysis) {
      throw new Error(`PSD 图片文字图层不再满足安全回写条件：${String(layer.name || layerPath)}`);
    }
    const region = normalizedRegionBounds(item.ocr_bounds, sourceImage.width, sourceImage.height);
    if (!region) {
      throw new Error(`PSD 图片文字缺少有效 OCR 区域：${String(layer.name || layerPath)}`);
    }
    const width = region.right - region.left;
    const height = region.bottom - region.top;
    const absoluteBounds = {
      left: Number(layer.left ?? 0) + region.left,
      top: Number(layer.top ?? 0) + region.top,
      right: Number(layer.left ?? 0) + region.right,
      bottom: Number(layer.top ?? 0) + region.bottom,
    };
    const background = item.background_color || analysis.background_color;
    const foreground = readableForeground(
      background,
      item.foreground_color || analysis.foreground_color,
    );
    const cleanupLayer = createCleanupLayer(item, absoluteBounds, width, height, background);
    const textLayer = createEditableTextLayer(item, target, absoluteBounds, width, height, foreground);
    operations.push({
      item,
      target,
      layerPath,
      sourceLayer: layer,
      sourcePixelSha256: hashImageData(sourceImage),
      children: location.children,
      index: location.index,
      region,
      cleanupLayer,
      textLayer,
    });
  }

  operations.sort((left, right) => {
    if (left.children === right.children) return right.index - left.index;
    return right.layerPath.localeCompare(left.layerPath, undefined, { numeric: true });
  });
  for (const operation of operations) {
    // Photopea 的实际图层面板会反向显示 ag-psd 写出的同级 layer records。
    // 记录按 source → cleanup → text 写入，Photopea 中显示为
    // text → cleanup → source，保证译文位于清除层和原图上方。
    operation.children.splice(
      operation.index,
      1,
      operation.sourceLayer,
      operation.cleanupLayer,
      operation.textLayer,
    );
  }

  return operations.map((operation) => ({
    layer_path: operation.layerPath,
    layer: operation.textLayer,
    cleanup_layer: operation.cleanupLayer,
    source_layer: operation.sourceLayer,
    source_pixel_sha256: operation.sourcePixelSha256,
    cleanup_name: operation.cleanupLayer.name,
    cleanup_pixel_sha256: hashImageData(operation.cleanupLayer.imageData),
    text_name: operation.textLayer.name,
    text_pixel_sha256: hashImageData(operation.textLayer.imageData),
    text: operation.textLayer.text.text,
    ocr_bounds: operation.region,
    font_size: operation.textLayer.text.style.fontSize,
  }));
}

module.exports = {
  GENERATED_CLEANUP_PREFIX,
  GENERATED_TEXT_PREFIX,
  analyzeFlatTextImage,
  exportImageTextCandidates,
  hashImageData,
  isGeneratedOcrLayer,
  renderImageTranslations,
};
