"""反推：s1_g0_1 这个框(W11.14 H0.77寸)要装下这段文字，理论上字号该缩到多少？
   跟 XML 缓存的 normAutofit fontScale(63.088%->25.2pt) 对比，看谁更接近实际需要值(约22pt->55%)"""
from common import find_shape_and_xform_by_uid, get_line_spacing_info, estimate_required_height
from pptx import Presentation

prs = Presentation(r"测试布局.pptx")
shape, xform = find_shape_and_xform_by_uid(prs, "s1_g0_1")
ls_info = get_line_spacing_info(shape)
text = shape.text_frame.text

box_w, box_h = 11.14, 0.77   # 已知的绝对尺寸(英寸)

print(f"文本: {text!r}")
print(f"框: W={box_w} H={box_h}寸")
print(f"行距信息: {ls_info}\n")

print(f"{'名义字号(pt)':>12} {'对应scale':>10} {'估算需要高度(寸)':>16} {'vs框H0.77':>10}")
for nominal in [40.0]:
    for scale_pct in [70, 65, 63.088, 60, 58, 56, 55, 54, 52, 50]:
        s = scale_pct / 100.0
        real_pt = nominal * s
        rec = {"text": text, "width": box_w, "height": box_h,
               "font_sizes": [real_pt], "line_spacing_info": ls_info}
        needed = estimate_required_height(rec, font_pt=real_pt)
        fit = "✅刚好/有余" if needed <= box_h else "❌超出"
        print(f"{real_pt:>10.2f}pt {scale_pct:>9.1f}% {needed:>16.3f} {fit:>10}")
