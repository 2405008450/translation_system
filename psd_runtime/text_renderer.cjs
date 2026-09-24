'use strict';

const agPsd = require('ag-psd');
const {
  createCanvas,
  GlobalFonts,
  ImageData,
} = require('@napi-rs/canvas');

const MIN_FONT_SIZE = 4;
const MAX_FONT_SIZE = 512;
const PATCH_MARGIN = 4;
const CJK_FONT_FALLBACKS = [
  'Microsoft YaHei',
  'Noto Sans CJK SC',
  'Noto Sans SC',
  'Source Han Sans SC',
  'SimHei',
  'Arial Unicode MS',
];

agPsd.initializeCanvas(createCanvas, (width, height) => new ImageData(width, height));
try {
  GlobalFonts.loadSystemFonts();
} catch {
  // 部分平台在模块加载时已经完成系统字体扫描。
}

function clamp(value, minimum, maximum) {
  return Math.min(Math.max(value, minimum), maximum);
}

function byte(value, fallback = 0) {
  return Math.round(clamp(Number.isFinite(value) ? value : fallback, 0, 255));
}

function cssColor(color, fallback = 'rgba(0, 0, 0, 1)') {
  if (!color || typeof color !== 'object') return fallback;
  const alphaValue = Number.isFinite(color.a) ? color.a : 1;
  const alpha = alphaValue > 1 ? alphaValue / 255 : alphaValue;
  return `rgba(${byte(color.r)}, ${byte(color.g)}, ${byte(color.b)}, ${clamp(alpha, 0, 1)})`;
}

function textStyle(layer) {
  const text = layer.text || {};
  return text.style || text.styleRuns?.[0]?.style || {};
}

function paragraphStyle(layer) {
  const text = layer.text || {};
  return text.paragraphStyle || text.paragraphStyleRuns?.[0]?.style || {};
}

function normalizeFontName(value) {
  const aliases = {
    ArialMT: 'Arial',
    'Arial-BoldMT': 'Arial',
    'Tahoma-Bold': 'Tahoma',
    'TimesNewRomanPSMT': 'Times New Roman',
    'TrebuchetMS-Bold': 'Trebuchet MS',
  };
  const name = String(value || '').trim();
  return aliases[name] || name.replace(/-(Bold|Italic|Regular|Roman|MT)$/i, '').replace(/PSMT$/i, '') || 'Arial';
}

function fontFamilies(layer) {
  const requested = normalizeFontName(textStyle(layer).font?.name);
  const candidates = [requested, ...CJK_FONT_FALLBACKS];
  const unique = [...new Set(candidates.filter(Boolean))];
  const available = unique.filter((family) => {
    try {
      return GlobalFonts.has(family);
    } catch {
      return true;
    }
  });
  const selected = available.length ? available : unique;
  return [...selected, 'sans-serif'].map((family) => `"${family.replaceAll('"', '')}"`).join(', ');
}

function fontCss(layer, size) {
  const style = textStyle(layer);
  const originalName = String(style.font?.name || '');
  const italic = style.fauxItalic || /italic/i.test(originalName) ? 'italic' : 'normal';
  const weight = style.fauxBold || /bold/i.test(originalName) ? '700' : '400';
  return `${italic} ${weight} ${size}px ${fontFamilies(layer)}`;
}

function transformedFontSize(layer) {
  const style = textStyle(layer);
  const originalSize = Number(style.fontSize) || 16;
  const transform = Array.isArray(layer.text?.transform) ? layer.text.transform : null;
  if (!transform || transform.length < 4) return originalSize;
  const scaleX = Math.hypot(Number(transform[0]) || 0, Number(transform[1]) || 0);
  const scaleY = Math.hypot(Number(transform[2]) || 0, Number(transform[3]) || 0);
  const scale = Math.max(scaleX, scaleY, 0.01);
  return originalSize * scale;
}

function isQuarterTurn(layer) {
  const transform = layer.text?.transform;
  if (!Array.isArray(transform) || transform.length < 4) return false;
  const [a, b, c, d] = transform.map(Number);
  return Math.abs(b) > Math.abs(a) * 2 && Math.abs(c) > Math.abs(d) * 2;
}

function wrapParagraph(context, paragraph, maximumWidth) {
  if (!paragraph) return [''];
  const characters = Array.from(paragraph);
  const lines = [];
  let current = '';
  for (const character of characters) {
    const candidate = current + character;
    if (current && context.measureText(candidate).width > maximumWidth) {
      lines.push(current);
      current = character;
    } else {
      current = candidate;
    }
  }
  lines.push(current);
  return lines;
}

function layoutText(context, layer, text, size, maximumWidth, boxText) {
  context.font = fontCss(layer, size);
  const paragraphs = String(text).replace(/\r\n|\n/g, '\r').split('\r');
  const lines = boxText
    ? paragraphs.flatMap((paragraph) => wrapParagraph(context, paragraph, maximumWidth))
    : paragraphs;
  const metrics = lines.map((line) => context.measureText(line || ' '));
  const ascent = Math.max(...metrics.map((item) => item.actualBoundingBoxAscent || size * 0.8));
  const descent = Math.max(...metrics.map((item) => item.actualBoundingBoxDescent || size * 0.2));
  const configuredLeading = Number(textStyle(layer).leading);
  const lineHeight = Math.max(
    ascent + descent,
    Number.isFinite(configuredLeading) && configuredLeading > 0
      ? Math.min(configuredLeading, size * 1.5)
      : size * 1.08,
  );
  return {
    lines,
    metrics,
    ascent,
    descent,
    lineHeight,
    width: Math.max(...metrics.map((item) => item.width), 0),
    height: ascent + descent + Math.max(lines.length - 1, 0) * lineHeight,
  };
}

function fitText(context, layer, text, width, height) {
  const boxText = String(layer.text?.shapeType || 'point') === 'box';
  const preferred = clamp(transformedFontSize(layer), MIN_FONT_SIZE, MAX_FONT_SIZE);
  let low = MIN_FONT_SIZE;
  let high = Math.max(low, preferred);
  let best = layoutText(context, layer, text, low, width, boxText);
  let bestSize = low;
  for (let iteration = 0; iteration < 18; iteration += 1) {
    const size = (low + high) / 2;
    const layout = layoutText(context, layer, text, size, width, boxText);
    if (layout.width <= width && layout.height <= height) {
      best = layout;
      bestSize = size;
      low = size;
    } else {
      high = size;
    }
  }
  return { ...best, size: bestSize };
}

function effectEnabled(effect) {
  const values = Array.isArray(effect) ? effect : [effect];
  return values.some((value) => value && (typeof value !== 'object' || value.enabled !== false));
}

function drawTextLayer(layer, targetText) {
  const width = Math.abs(Number(layer.right ?? 0) - Number(layer.left ?? 0));
  const height = Math.abs(Number(layer.bottom ?? 0) - Number(layer.top ?? 0));
  if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0) {
    throw new Error(`PSD 文本图层尺寸无效：${String(layer.name || '未命名图层')}`);
  }

  const quarterTurn = isQuarterTurn(layer);
  const logicalWidth = quarterTurn ? height : width;
  const logicalHeight = quarterTurn ? width : height;
  const effects = layer.effects || {};
  const hasShadow = effectEnabled(effects.dropShadow);
  const hasStroke = effectEnabled(effects.stroke);
  const padding = Math.min(hasShadow || hasStroke ? 3 : 1, Math.floor(Math.min(logicalWidth, logicalHeight) / 4));
  const availableWidth = Math.max(logicalWidth - padding * 2, 1);
  const availableHeight = Math.max(logicalHeight - padding * 2, 1);
  const logicalCanvas = createCanvas(logicalWidth, logicalHeight);
  const context = logicalCanvas.getContext('2d');
  const layout = fitText(context, layer, targetText, availableWidth, availableHeight);
  const style = textStyle(layer);
  const paragraph = paragraphStyle(layer);
  const justification = String(paragraph.justification || 'left');

  context.font = fontCss(layer, layout.size);
  context.textBaseline = 'alphabetic';
  context.textAlign = justification === 'center' ? 'center' : justification === 'right' ? 'right' : 'left';
  context.fillStyle = cssColor(style.fillColor);
  if (hasShadow) {
    context.shadowColor = 'rgba(0, 0, 0, 0.45)';
    context.shadowBlur = Math.max(1, layout.size * 0.12);
    context.shadowOffsetX = Math.max(1, layout.size * 0.06);
    context.shadowOffsetY = Math.max(1, layout.size * 0.06);
  }

  const x = context.textAlign === 'center'
    ? logicalWidth / 2
    : context.textAlign === 'right'
      ? logicalWidth - padding
      : padding;
  let baseline = (logicalHeight - layout.height) / 2 + layout.ascent;
  for (const line of layout.lines) {
    if (hasStroke) {
      context.lineJoin = 'round';
      context.lineWidth = Math.max(1, layout.size * 0.06);
      context.strokeStyle = cssColor(style.strokeColor, 'rgba(0, 0, 0, 0.75)');
      context.strokeText(line, x, baseline);
    }
    context.fillText(line, x, baseline);
    baseline += layout.lineHeight;
  }

  let finalCanvas = logicalCanvas;
  if (quarterTurn) {
    finalCanvas = createCanvas(width, height);
    const finalContext = finalCanvas.getContext('2d');
    const direction = Number(layer.text?.transform?.[1]) < 0 ? -1 : 1;
    if (direction < 0) {
      finalContext.translate(0, height);
      finalContext.rotate(-Math.PI / 2);
    } else {
      finalContext.translate(width, 0);
      finalContext.rotate(Math.PI / 2);
    }
    finalContext.drawImage(logicalCanvas, 0, 0);
  }

  return {
    canvas: finalCanvas,
    imageData: finalCanvas.getContext('2d').getImageData(0, 0, width, height),
    fontSize: layout.size,
  };
}

const BLEND_MODES = {
  normal: 'source-over',
  multiply: 'multiply',
  screen: 'screen',
  overlay: 'overlay',
  darken: 'darken',
  lighten: 'lighten',
  colorDodge: 'color-dodge',
  colorBurn: 'color-burn',
  hardLight: 'hard-light',
  softLight: 'soft-light',
  difference: 'difference',
  exclusion: 'exclusion',
  hue: 'hue',
  saturation: 'saturation',
  color: 'color',
  luminosity: 'luminosity',
};

function canvasFromImageData(imageData) {
  const canvas = createCanvas(imageData.width, imageData.height);
  const context = canvas.getContext('2d');
  const nativeImageData = imageData instanceof ImageData
    ? imageData
    : new ImageData(new Uint8ClampedArray(
      imageData.data.buffer,
      imageData.data.byteOffset,
      imageData.data.byteLength,
    ), imageData.width, imageData.height);
  context.putImageData(nativeImageData, 0, 0);
  return canvas;
}

function canvasFromLayerImageData(layer, imageData) {
  const sourceCanvas = layer.canvas || canvasFromImageData(imageData);
  // 形状图层的保存 Alpha 已包含矢量轮廓；普通图片层的独立矢量蒙版
  // 则需要在中文预览合成时按灰度通道应用，但不能改写原始图层像素。
  if (!layer.mask || !layer.vectorMask || layer.vectorFill) return sourceCanvas;

  const maskImage = layer.mask.imageData || agPsd.getLayerMaskImageData(layer);
  const width = Number(imageData.width);
  const height = Number(imageData.height);
  const maskWidth = Number(maskImage.width);
  const maskHeight = Number(maskImage.height);
  const offsetX = Number(layer.mask.left) - Number(layer.left);
  const offsetY = Number(layer.mask.top) - Number(layer.top);
  const defaultAlpha = clamp(Math.round(Number(layer.mask.defaultColor ?? 0)), 0, 255);
  const alphaData = new Uint8ClampedArray(width * height * 4);

  for (let pixel = 0; pixel < width * height; pixel += 1) {
    alphaData[pixel * 4] = 255;
    alphaData[pixel * 4 + 1] = 255;
    alphaData[pixel * 4 + 2] = 255;
    alphaData[pixel * 4 + 3] = defaultAlpha;
  }
  for (let y = 0; y < maskHeight; y += 1) {
    const targetY = y + offsetY;
    if (targetY < 0 || targetY >= height) continue;
    for (let x = 0; x < maskWidth; x += 1) {
      const targetX = x + offsetX;
      if (targetX < 0 || targetX >= width) continue;
      const sourcePixel = y * maskWidth + x;
      const targetPixel = targetY * width + targetX;
      alphaData[targetPixel * 4 + 3] = maskImage.data[sourcePixel * 4];
    }
  }

  const maskCanvas = createCanvas(width, height);
  maskCanvas.getContext('2d').putImageData(new ImageData(alphaData, width, height), 0, 0);
  const maskedCanvas = createCanvas(width, height);
  const maskedContext = maskedCanvas.getContext('2d');
  maskedContext.drawImage(sourceCanvas, 0, 0);
  maskedContext.globalCompositeOperation = 'destination-in';
  maskedContext.drawImage(maskCanvas, 0, 0);
  return maskedCanvas;
}

function drawLayer(context, layer, inheritedOpacity) {
  if (layer.hidden) return;
  const opacity = inheritedOpacity * clamp(Number(layer.opacity ?? 1), 0, 1);
  if (Array.isArray(layer.children)) {
    drawLayers(context, layer.children, opacity, new Set());
    return;
  }
  if (!layer.rawData && !layer.imageData && !layer.canvas) return;
  const imageData = layer.imageData || agPsd.getLayerImageData(layer);
  if (!imageData?.width || !imageData?.height) return;
  const canvas = canvasFromLayerImageData(layer, imageData);
  context.save();
  context.globalAlpha = opacity;
  context.globalCompositeOperation = BLEND_MODES[layer.blendMode] || 'source-over';
  context.drawImage(canvas, Number(layer.left ?? 0), Number(layer.top ?? 0));
  context.restore();
}

function drawLayers(
  context,
  layers,
  inheritedOpacity,
  excludedPaths,
  parentPath = '',
  excludedLayers = new Set(),
) {
  for (let index = layers.length - 1; index >= 0; index -= 1) {
    const layer = layers[index];
    const layerPath = parentPath ? `${parentPath}.${index}` : String(index);
    if (excludedLayers.has(layer) || excludedPaths.has(layerPath) || layer.hidden) continue;
    const opacity = inheritedOpacity * clamp(Number(layer.opacity ?? 1), 0, 1);
    if (Array.isArray(layer.children)) {
      drawLayers(context, layer.children, opacity, excludedPaths, layerPath, excludedLayers);
    } else {
      drawLayer(context, layer, inheritedOpacity);
    }
  }
}

function buildBackgroundCanvas(psd, excludedPaths, excludedLayers = new Set()) {
  const canvas = createCanvas(psd.width, psd.height);
  drawLayers(canvas.getContext('2d'), psd.children || [], 1, excludedPaths, '', excludedLayers);
  return canvas;
}

function assertPreviewMaskSupported(layer) {
  if (!layer.mask && !layer.realMask && !layer.vectorMask) return;

  const layerName = String(layer.name || '未命名图层');
  const mask = layer.mask;
  const vectorMask = layer.vectorMask;
  const unsupported = () => {
    throw new Error(`PSD 中文预览暂不支持图层“${layerName}”的此类蒙版组合`);
  };

  // Photoshop 形状图层通常只有 vectorFill + vectorMask。只在 PSD 同时保存了
  // 可直接合成的 RGBA 图层像素时放行；预览使用这些像素，不栅格化或改写矢量路径。
  if (
    layer.vectorFill
    && vectorMask
    && !mask
    && !layer.realMask
    && !vectorMask.disable
    && !vectorMask.invert
    && !Array.isArray(layer.children)
    && !layer.text
  ) {
    let layerImage;
    try {
      layerImage = layer.imageData || agPsd.getLayerImageData(layer);
    } catch {
      unsupported();
    }
    if (
      layerImage?.data
      && Number(layerImage.width) > 0
      && Number(layerImage.height) > 0
      && layerImage.data.length >= Number(layerImage.width) * Number(layerImage.height) * 4
    ) {
      return;
    }
    unsupported();
  }

  // 只处理由 PSD 同时保存为像素通道的矢量蒙版，不栅格化矢量路径。
  // 形状图层使用已保存的 Alpha；普通图片层在临时预览画布上按位置应用蒙版。
  if (
    layer.realMask
    || !mask
    || !vectorMask
    || !mask.fromVectorData
    || mask.disabled
    || vectorMask.disable
    || vectorMask.invert
    || mask.positionRelativeToLayer
    || Array.isArray(layer.children)
    || layer.text
  ) {
    unsupported();
  }

  if (
    mask.userMaskDensity !== undefined
    || mask.userMaskFeather !== undefined
    || mask.vectorMaskDensity !== undefined
    || mask.vectorMaskFeather !== undefined
    || ![0, 255].includes(Number(mask.defaultColor ?? 0))
  ) {
    unsupported();
  }

  let layerImage;
  let maskImage;
  try {
    layerImage = layer.imageData || agPsd.getLayerImageData(layer);
    maskImage = mask.imageData || agPsd.getLayerMaskImageData(layer);
  } catch {
    unsupported();
  }

  const layerBounds = [layer.left, layer.top, layer.right, layer.bottom].map(Number);
  const maskBounds = [mask.left, mask.top, mask.right, mask.bottom].map(Number);
  const [layerLeft, layerTop, layerRight, layerBottom] = layerBounds;
  const [maskLeft, maskTop, maskRight, maskBottom] = maskBounds;
  if (
    [...layerBounds, ...maskBounds].some((value) => !Number.isInteger(value))
    || !layerImage?.data
    || !maskImage?.data
    || layerImage.width !== layerRight - layerLeft
    || layerImage.height !== layerBottom - layerTop
    || maskImage.width !== maskRight - maskLeft
    || maskImage.height !== maskBottom - maskTop
  ) {
    unsupported();
  }

  // vectorFill 形状的 Alpha 已包含保存时的矢量覆盖，重复应用蒙版会让
  // 抗锯齿边缘被乘两次；其蒙版必须与图层边界一致。普通图片允许蒙版
  // 使用较小或偏移后的边界，由 canvasFromLayerImageData 按画布坐标合成。
  if (
    layer.vectorFill
    && layerBounds.some((value, index) => value !== maskBounds[index])
  ) {
    unsupported();
  }
}

function assertPreviewCompositionSupported(psd) {
  function inspect(layers, hiddenByParent = false) {
    for (const layer of layers || []) {
      const hidden = hiddenByParent || Boolean(layer.hidden);
      if (!hidden) {
        if (layer.adjustment) {
          throw new Error(`PSD 中文预览暂不支持调整图层：${String(layer.name || '未命名图层')}`);
        }
        if (layer.clipping) {
          throw new Error(`PSD 中文预览暂不支持剪贴图层：${String(layer.name || '未命名图层')}`);
        }
        assertPreviewMaskSupported(layer);
        const blendMode = String(layer.blendMode || 'normal');
        const passThrough = ['passThrough', 'pass through'].includes(blendMode);
        if (Array.isArray(layer.children) && !passThrough) {
          throw new Error(
            `PSD 中文预览暂不支持非穿透组混合模式 ${blendMode}：${String(layer.name || '未命名图层')}`,
          );
        }
        if (!passThrough && !BLEND_MODES[blendMode]) {
          throw new Error(`PSD 中文预览暂不支持混合模式 ${blendMode}：${String(layer.name || '未命名图层')}`);
        }
      }
      inspect(layer.children, hidden);
    }
  }
  inspect(psd.children || []);
}

function renderTranslatedTextLayers(psd, updatedLayers, additionalRenderedLayers = []) {
  if (psd.width * psd.height > 12_000_000) {
    throw new Error('PSD 画布超过中文可见预览渲染限制（最多 12000000 像素）');
  }
  assertPreviewCompositionSupported(psd);
  const sourceComposite = agPsd.getCompositeImageData(psd);
  if (!sourceComposite) throw new Error('PSD 缺少可渲染的合成图数据');
  const compositeCanvas = canvasFromImageData(sourceComposite);
  const compositeContext = compositeCanvas.getContext('2d');
  const nativeRendered = [];

  for (const item of updatedLayers) {
    const result = drawTextLayer(item.layer, item.text);
    item.layer.imageData = result.imageData;
    delete item.layer.canvas;
    delete item.layer.rawData;
    nativeRendered.push({ ...item, canvas: result.canvas, font_size: result.fontSize });
  }

  // 原生文字层按完整树重建其局部预览，保留其上方遮挡关系。
  const translatedCompositeCanvas = buildBackgroundCanvas(psd, new Set());
  for (const item of nativeRendered) {
    if (item.layer.hidden) continue;
    const left = Math.max(Math.floor(Number(item.layer.left ?? 0)) - PATCH_MARGIN, 0);
    const top = Math.max(Math.floor(Number(item.layer.top ?? 0)) - PATCH_MARGIN, 0);
    const right = Math.min(Math.ceil(Number(item.layer.right ?? left)) + PATCH_MARGIN, psd.width);
    const bottom = Math.min(Math.ceil(Number(item.layer.bottom ?? top)) + PATCH_MARGIN, psd.height);
    const width = Math.max(right - left, 0);
    const height = Math.max(bottom - top, 0);
    if (!width || !height) continue;
    compositeContext.clearRect(left, top, width, height);
    compositeContext.drawImage(
      translatedCompositeCanvas,
      left,
      top,
      width,
      height,
      left,
      top,
      width,
      height,
    );
  }

  // OCR 预览严格按与 Photoshop 相同的可见顺序绘制：先清除原文，再绘制
  // 可编辑文字层。真实 PSD 的 sibling 顺序会在写后回读时另行强制校验。
  for (const item of additionalRenderedLayers) {
    if (item.cleanup_layer?.hidden || item.layer.hidden) continue;
    drawLayer(compositeContext, item.cleanup_layer, 1);
    drawLayer(compositeContext, item.layer, 1);
  }

  const rendered = [...nativeRendered, ...additionalRenderedLayers];
  psd.imageData = compositeContext.getImageData(0, 0, psd.width, psd.height);
  return {
    rendered_count: rendered.length,
    rendered: rendered.map((item) => ({
      layer_path: item.layer_path,
      font_size: item.font_size,
    })),
  };
}

module.exports = {
  renderTranslatedTextLayers,
};
