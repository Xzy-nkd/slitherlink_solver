"""
从 Slitherlink 谜题图片中提取数字网格。

本模块仅依赖 Pillow（PIL），无需 OpenCV 或 Tesseract。实现思路：
1. 灰度化、二值化。
2. 提取所有连通组件，根据圆度筛选出黑点。
3. 对黑点坐标聚类，得到网格线位置。
4. 对每个单元格，识别数字 0-3；空白格记为 None。

适用于高质量截图（黑点清晰、数字清晰、无网格线）。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageOps


class ImageParseError(Exception):
    """图片解析失败。"""
    pass


# 支持的图片扩展名
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.tif', '.tiff'}


def is_image_path(path: Path) -> bool:
    """根据扩展名判断路径是否为图片文件。"""
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _auto_threshold(img: Image.Image) -> int:
    """使用 Otsu 阈值法的简化版估算二值化阈值。"""
    hist = img.histogram()
    total = sum(hist)
    if total == 0:
        return 128

    sum_total = sum(i * hist[i] for i in range(256))
    sum_bg = 0
    weight_bg = 0
    max_var = 0.0
    threshold = 128

    for t in range(256):
        weight_bg += hist[t]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += t * hist[t]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_total - sum_bg) / weight_fg
        var = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if var > max_var:
            max_var = var
            threshold = t
    return threshold


def _binarize(img: Image.Image) -> Image.Image:
    """二值化并反转，使线条和数字变为白色（255），背景为黑色（0）。"""
    # 先转为灰度
    gray = img.convert('L')
    threshold = _auto_threshold(gray)
    binary = gray.point(lambda p: 255 if p > threshold else 0, mode='1')
    return ImageOps.invert(binary.convert('L'))


def _content_bbox(img: Image.Image) -> Optional[Tuple[int, int, int, int]]:
    """返回非零像素的包围盒 (left, top, right, bottom)。"""
    width, height = img.size
    pixels = img.load()

    top = None
    for y in range(height):
        for x in range(width):
            if pixels[x, y] > 0:
                top = y
                break
        if top is not None:
            break
    if top is None:
        return None

    bottom = None
    for y in range(height - 1, -1, -1):
        for x in range(width):
            if pixels[x, y] > 0:
                bottom = y
                break
        if bottom is not None:
            break

    left = None
    for x in range(width):
        for y in range(height):
            if pixels[x, y] > 0:
                left = x
                break
        if left is not None:
            break

    right = None
    for x in range(width - 1, -1, -1):
        for y in range(height):
            if pixels[x, y] > 0:
                right = x
                break
        if right is not None:
            break

    return left, top, right + 1, bottom + 1


def _find_components(
    img: Image.Image,
) -> List[Tuple[int, float, float, int, int]]:
    """
    提取二值图像中的所有连通组件。
    返回列表，每个元素为 (area, cx, cy, bw, bh)。
    """
    width, height = img.size
    pixels = img.load()

    visited = [[False] * width for _ in range(height)]
    components: List[Tuple[int, float, float, int, int]] = []

    for y in range(height):
        for x in range(width):
            if pixels[x, y] > 0 and not visited[y][x]:
                stack = [(x, y)]
                visited[y][x] = True
                xs, ys = [], []
                while stack:
                    cx, cy = stack.pop()
                    xs.append(cx)
                    ys.append(cy)
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < width and 0 <= ny < height:
                            if pixels[nx, ny] > 0 and not visited[ny][nx]:
                                visited[ny][nx] = True
                                stack.append((nx, ny))
                area = len(xs)
                if area < 3:
                    continue
                components.append((
                    area,
                    sum(xs) / area,
                    sum(ys) / area,
                    max(xs) - min(xs) + 1,
                    max(ys) - min(ys) + 1,
                ))

    return components


def _is_circular(bw: int, bh: int, max_aspect: float = 1.2) -> bool:
    """判断组件是否为圆形（宽高比接近 1）。"""
    if bw == 0 or bh == 0:
        return False
    return max(bw, bh) / min(bw, bh) <= max_aspect


def _separate_dots_and_numbers(
    components: List[Tuple[int, float, float, int, int]],
) -> Tuple[List[Tuple[float, float]], List[Tuple[int, float, float, int, int]]]:
    """
    将连通组件区分为黑点（圆形）和数字（非圆形）。
    返回 (dots, numbers)：
      dots: [(cx, cy), ...]
      numbers: [(area, cx, cy, bw, bh), ...]

    原理：黑点是高宽比接近 1 的小圆形组件；数字笔画通常是非圆形的。
    对于数字 0，其外圈虽是圆形但面积通常比黑点大，通过面积比较可区分。
    """
    if not components:
        return [], []

    # 先按面积排序
    sorted_comp = sorted(components, key=lambda c: c[0])

    # 找出所有圆形组件（aspect <= 1.2）
    circular = [(area, cx, cy, bw, bh) for area, cx, cy, bw, bh in sorted_comp
                if _is_circular(bw, bh)]
    non_circular = [(area, cx, cy, bw, bh) for area, cx, cy, bw, bh in sorted_comp
                    if not _is_circular(bw, bh)]

    if not circular:
        return [], non_circular

    # 从圆形组件中区分黑点和数字 0 的外圈/背景轮廓：
    # 黑点：面积小且集中，bw/bh 通常在 10-40 像素范围内；
    # 数字 0 的外圈或背景轮廓：面积大得多。
    # 策略：取圆形组件面积的中位数，面积小于中位数 2 倍且尺寸合理的是黑点。
    areas = [c[0] for c in circular]
    areas.sort()
    median_area = areas[len(areas) // 2]
    max_dot_area = median_area * 2

    dots_raw = []
    zero_circles = []
    for area, cx, cy, bw, bh in circular:
        # 黑点尺寸应合理（不超过 50 像素宽高，排除背景轮廓）
        if area <= max_dot_area and bw <= 50 and bh <= 50:
            dots_raw.append((cx, cy))
        else:
            zero_circles.append((area, cx, cy, bw, bh))

    # ——— 去重：距离相近（< 5 像素）的圆点合并为一个 ———
    if dots_raw:
        dots_raw.sort(key=lambda p: (p[0], p[1]))
        merged = [dots_raw[0]]
        for cx, cy in dots_raw[1:]:
            last_cx, last_cy = merged[-1]
            if abs(cx - last_cx) < 5 and abs(cy - last_cy) < 5:
                # 太接近，认为是一致点，取均值
                merged[-1] = ((last_cx + cx) / 2, (last_cy + cy) / 2)
            else:
                merged.append((cx, cy))
        dots = merged
    else:
        dots = []

    # 所有非圆形组件都是数字
    numbers = non_circular + zero_circles

    return dots, numbers


def _cluster_coords(
    coords: List[float], tolerance: float = 0.10
) -> List[float]:
    """
    对一维坐标进行聚类。
    tolerance: 允许的误差比例（相对于平均间距）。
    """
    if not coords:
        return []
    sorted_vals = sorted(coords)
    if len(sorted_vals) == 1:
        return [sorted_vals[0]]

    # 先粗聚类：间距显著大于中位间距的视为不同簇
    gaps = [sorted_vals[i + 1] - sorted_vals[i] for i in range(len(sorted_vals) - 1)]
    gaps_sorted = sorted(gaps)
    med_gap = gaps_sorted[len(gaps_sorted) // 2]

    # 间距大于中位间距3倍的视为分簇点
    cluster_threshold = max(med_gap * 3, 5.0)

    clusters = [[sorted_vals[0]]]
    for i in range(1, len(sorted_vals)):
        if sorted_vals[i] - sorted_vals[i - 1] > cluster_threshold:
            clusters.append([sorted_vals[i]])
        else:
            clusters[-1].append(sorted_vals[i])

    centers = [sum(c) / len(c) for c in clusters]

    # 如果簇数太少，可能是间距不均匀，尝试用更小的阈值
    if len(centers) < 2:
        return centers

    # 检查间距是否均匀，如果不均匀，合并相邻近的簇
    while len(centers) >= 3:
        gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
        avg_gap = sum(gaps) / len(gaps)
        # 如果有某个间距显著小于平均间距，合并相邻簇
        merged = False
        for i in range(len(gaps) - 1):
            if gaps[i] < avg_gap * 0.5 and gaps[i + 1] < avg_gap * 0.5:
                # 合并三个簇为两个
                new_centers = centers[:i]
                # 合并 i, i+1, i+2 为两个
                new_centers.append((centers[i] + centers[i + 1]) / 2)
                new_centers.append(centers[i + 2])
                new_centers.extend(centers[i + 3:])
                centers = new_centers
                merged = True
                break
        if not merged:
            break

    return centers


def _regular_spacing(centers: List[float], max_error: float = 0.15) -> bool:
    """检查中心点是否近似等间距。"""
    if len(centers) < 2:
        return False
    gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
    avg = sum(gaps) / len(gaps)
    if avg == 0:
        return False
    for g in gaps:
        if abs(g - avg) / avg > max_error:
            return False
    return True


def _fit_grid_from_dots(
    dots: List[Tuple[float, float]],
    bbox: Tuple[int, int, int, int],
) -> Optional[Tuple[int, int, List[int], List[int]]]:
    """
    从检测到的黑点拟合网格。
    返回 (R, C, h_lines, v_lines)，失败返回 None。
    """
    if len(dots) < 4:
        return None

    xs = [d[0] for d in dots]
    ys = [d[1] for d in dots]

    # 对 x 和 y 坐标分别聚类
    x_centers = _cluster_coords(xs)
    y_centers = _cluster_coords(ys)

    if len(x_centers) < 2 or len(y_centers) < 2:
        return None

    # 检查间距是否均匀
    if not _regular_spacing(x_centers) or not _regular_spacing(y_centers):
        return None

    R = len(y_centers) - 1
    C = len(x_centers) - 1
    if R < 1 or C < 1:
        return None

    # 网格线位置 = 黑点中心位置（取整）
    left, top, right, bottom = bbox
    h_lines = [max(top, int(round(y))) for y in y_centers]
    v_lines = [max(left, int(round(x))) for x in x_centers]

    return R, C, h_lines, v_lines


def _estimate_grid_from_numbers(
    numbers: List[Tuple[int, float, float, int, int]],
    bbox: Tuple[int, int, int, int],
) -> Optional[Tuple[int, int, List[int], List[int]]]:
    """
    从数字组件的位置推断网格（适用于没有黑点的图片）。
    数字组件通常位于单元格中心，通过聚类其中心坐标可推断行列数。
    返回 (R, C, h_lines, v_lines)，失败返回 None。
    """
    # 过滤掉明显过大的组件（如背景轮廓）
    filtered = [n for n in numbers if n[0] < 500 and n[3] < 100 and n[4] < 100]
    if len(filtered) < 4:
        return None

    left, top, right, bottom = bbox

    xs = [n[1] for n in filtered]
    ys = [n[2] for n in filtered]

    # 聚类 x 和 y 坐标
    x_centers = _cluster_coords(xs)
    y_centers = _cluster_coords(ys)

    if len(x_centers) < 2 or len(y_centers) < 2:
        return None

    # 检查间距是否均匀
    if not _regular_spacing(x_centers) or not _regular_spacing(y_centers):
        return None

    C = len(x_centers)
    R = len(y_centers)

    if R < 1 or C < 1:
        return None

    # 计算平均间距
    x_gaps = [x_centers[i + 1] - x_centers[i] for i in range(len(x_centers) - 1)]
    y_gaps = [y_centers[i + 1] - y_centers[i] for i in range(len(y_centers) - 1)]
    avg_x_gap = sum(x_gaps) / len(x_gaps)
    avg_y_gap = sum(y_gaps) / len(y_gaps)

    # 网格线位置 = 数字中心 - 半间距
    h_lines = [max(top, int(round(y_centers[0] - avg_y_gap / 2)))]
    for y in y_centers:
        h_lines.append(max(top, int(round(y + avg_y_gap / 2))))

    v_lines = [max(left, int(round(x_centers[0] - avg_x_gap / 2)))]
    for x in x_centers:
        v_lines.append(max(left, int(round(x + avg_x_gap / 2))))

    # 确保网格线数量正确
    if len(h_lines) != R + 1 or len(v_lines) != C + 1:
        return None

    return R, C, h_lines, v_lines


def _per_cell_otsu(cell_img: Image.Image) -> Image.Image:
    """对单个单元格应用 Otsu 阈值二值化（白字黑底 → 白=255前景）。"""
    hist = cell_img.histogram()
    total = sum(hist)
    if total == 0:
        return Image.new('L', cell_img.size, 0)
    sum_all = sum(i * hist[i] for i in range(256))
    w_bg = 0; sum_bg = 0; max_var = 0; thresh = 128
    for t in range(256):
        w_bg += hist[t]
        if w_bg == 0: continue
        w_fg = total - w_bg
        if w_fg == 0: break
        sum_bg += t * hist[t]
        m_bg = sum_bg / w_bg
        m_fg = (sum_all - sum_bg) / w_fg
        var_between = w_bg * w_fg * (m_bg - m_fg) ** 2
        if var_between > max_var:
            max_var = var_between; thresh = t
    # 白字黑底：高于阈值为前景（白色=255）
    return cell_img.point(lambda p: 255 if p > thresh else 0)


def _clean_cell_otsu(cell_img: Image.Image) -> Image.Image:
    """逐格 Otsu 二值化 + 移除角落孤立像素（网格点残留）。
    
    流程：
    1. 逐格 Otsu 二值化
    2. 仅移除与四角（3px范围内）相邻且面积 < 8 像素的连通组件
       （这些通常是网格交点的残留，而非数字笔画）
    """
    bin_img = _per_cell_otsu(cell_img)
    w, h = bin_img.size
    if w < 6 or h < 6:
        return bin_img

    px = bin_img.load()
    visited = [[False] * w for _ in range(h)]

    # 定义四角区域（距角点3像素以内）
    corners = [
        (0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)
    ]

    def _near_corner(cx: int, cy: int) -> bool:
        for kx, ky in corners:
            if abs(cx - kx) <= 3 and abs(cy - ky) <= 3:
                return True
        return False

    # 找出所有白像素连通组件
    for y in range(h):
        for x in range(w):
            if px[x, y] > 127 and not visited[y][x]:
                stack = [(x, y)]
                visited[y][x] = True
                comp = []
                touches_corner = False
                while stack:
                    cx, cy = stack.pop()
                    comp.append((cx, cy))
                    if _near_corner(cx, cy):
                        touches_corner = True
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < w and 0 <= ny < h:
                            if px[nx, ny] > 127 and not visited[ny][nx]:
                                visited[ny][nx] = True
                                stack.append((nx, ny))

                # 小面积 + 接触角落 → 移除
                if touches_corner and len(comp) < 8:
                    for cx, cy in comp:
                        px[cx, cy] = 0

    return bin_img


def _projection_profile(bin_img: Image.Image):
    """计算二值图像的水平投影（每行白像素数）和垂直投影（每列白像素数）。
    返回 (h_proj, v_proj)，均已归一化（除以最大值）。"""
    w, h = bin_img.size
    px = bin_img.load()
    h_proj = [0] * h
    v_proj = [0] * w
    for y in range(h):
        row_sum = 0
        for x in range(w):
            if px[x, y] > 127:
                row_sum += 1
                v_proj[x] += 1
        h_proj[y] = row_sum
    max_h = max(h_proj) if h_proj else 1
    max_v = max(v_proj) if v_proj else 1
    h_proj = [v / max_h for v in h_proj]
    v_proj = [v / max_v for v in v_proj]
    return h_proj, v_proj


def _compute_x_shift(bin_cell: Image.Image) -> Optional[float]:
    """计算水平重心从上到下的偏移量。
    
    正值 = 底部比顶部偏右；负值 = 底部比顶部偏左。
    对于数字 2，笔画从右上到左下，因此 x_shift 为显著负值（≈-1.75）。
    对于数字 3，上下对称，x_shift ≈ 0。
    """
    w, h = bin_cell.size
    px = bin_cell.load()
    rows_with_content = []
    for y in range(h):
        xs = [x for x in range(w) if px[x, y] > 127]
        if xs:
            rows_with_content.append(sum(xs) / len(xs))
    if len(rows_with_content) < 4:
        return None
    n = len(rows_with_content)
    third = max(1, n // 3)
    top_x = sum(rows_with_content[:third]) / third
    bot_x = sum(rows_with_content[-third:]) / third
    return bot_x - top_x


def _classify_by_shape(bin_cell: Image.Image, features: dict) -> Optional[int]:
    """基于形状特征分类数字 0-3。
    
    决策树（跨谜题验证：5×5 到 50×40，格子 20~114px）：
    1. 有孔洞 → 0
    2. 纵横比 < 0.68 → 1（窄）
    3. x_shift < -0.75 → 2（对角线：右上→左下）
    4. 其余 → 3（上下对称）
    """
    holes = _count_holes(bin_cell)
    aspect = features['aspect']

    # 0: 有孔洞
    if holes >= 1 and aspect > 0.4:
        return 0

    # 1: 窄长形状
    if aspect < 0.68:
        return 1

    # 2 vs 3: 水平重心偏移（-0.75 经验证为最佳全局阈值）
    x_shift = _compute_x_shift(bin_cell)
    if x_shift is not None and x_shift < -0.75:
        return 2

    # 默认为 3
    if aspect >= 0.68:
        return 3

    return None


def _learn_templates(
    cell_images: List[Image.Image],
    labels: List[int],
    size: int = 64,
) -> dict:
    """从已分类的单元格构建真实数字模板。
    对每个数字取所有样本的归一化平均图像。"""
    from collections import defaultdict
    groups = defaultdict(list)
    for img, lbl in zip(cell_images, labels):
        if lbl is not None and 0 <= lbl <= 3:
            norm = _center_content(img, size)
            groups[lbl].append(norm)

    templates = {}
    for digit in range(4):
        imgs = groups.get(digit, [])
        if len(imgs) >= 2:
            # 平均融合
            avg = Image.new('F', (size, size), 0.0)
            for im in imgs:
                px = im.load()
                apx = avg.load()
                for y in range(size):
                    for x in range(size):
                        apx[x, y] += px[x, y] / 255.0
            count = len(imgs)
            out = Image.new('L', (size, size), 0)
            opx = out.load()
            apx = avg.load()
            for y in range(size):
                for x in range(size):
                    opx[x, y] = int(min(255, (apx[x, y] / count) * 255))
            templates[digit] = out
        else:
            # 样本不足，用合成模板兜底
            templates[digit] = _synthetic_template(digit, size)
    return templates


def _synthetic_template(digit: int, size: int = 64) -> Image.Image:
    """生成合成数字模板（兜底）。"""
    big = Image.new('L', (120, 120), 0)
    draw = ImageDraw.Draw(big)
    try:
        draw.text((60, 60), str(digit), fill=255, anchor='mm', font_size=64)
    except TypeError:
        draw.text((35, 25), str(digit), fill=255)
    return big.resize((size, size), Image.Resampling.LANCZOS)


def _similarity(a: Image.Image, b: Image.Image) -> float:
    """
    计算两张相同尺寸灰度图像的归一化互相关系数（NCC）。
    """
    if a.size != b.size:
        return 0.0
    pa = a.load()
    pb = b.load()
    width, height = a.size
    n = width * height

    sum_a = sum_b = sum_a2 = sum_b2 = sum_ab = 0.0
    for y in range(height):
        for x in range(width):
            va = pa[x, y]
            vb = pb[x, y]
            sum_a += va
            sum_b += vb
            sum_a2 += va * va
            sum_b2 += vb * vb
            sum_ab += va * vb

    mean_a = sum_a / n
    mean_b = sum_b / n
    cov = sum_ab - n * mean_a * mean_b
    std_a = math.sqrt(sum_a2 - n * mean_a * mean_a)
    std_b = math.sqrt(sum_b2 - n * mean_b * mean_b)

    if std_a == 0 or std_b == 0:
        return 0.0
    return cov / (std_a * std_b)


def _count_holes(img: Image.Image) -> int:
    """
    计算二值图像中白色前景的内部孔洞数量（黑色背景被白色前景包围的连通块）。
    用于区分 0（有孔）与 1/2/3（无孔）。
    """
    w, h = img.size
    pixels = img.load()

    # 找到所有背景像素，并从边界开始做洪水填充标记外部背景
    visited = [[False] * w for _ in range(h)]
    stack = []
    for x in range(w):
        stack.append((x, 0))
        stack.append((x, h - 1))
    for y in range(h):
        stack.append((0, y))
        stack.append((w - 1, y))

    while stack:
        x, y = stack.pop()
        if not (0 <= x < w and 0 <= y < h):
            continue
        if visited[y][x] or pixels[x, y] > 127:
            continue
        visited[y][x] = True
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            stack.append((x + dx, y + dy))

    # 统计未访问到的背景连通块即为孔洞
    holes = 0
    for y in range(h):
        for x in range(w):
            if pixels[x, y] <= 127 and not visited[y][x]:
                holes += 1
                # BFS 标记整个孔洞
                stack = [(x, y)]
                visited[y][x] = True
                while stack:
                    cx, cy = stack.pop()
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < w and 0 <= ny < h:
                            if pixels[nx, ny] <= 127 and not visited[ny][nx]:
                                visited[ny][nx] = True
                                stack.append((nx, ny))
    return holes


def _digit_features(img: Image.Image) -> Optional[dict]:
    """提取用于区分数字的简单几何特征。"""
    bbox = _content_bbox(img)
    if bbox is None:
        return None
    left, top, right, bottom = bbox
    w = right - left
    h = bottom - top
    if w <= 0 or h <= 0:
        return None

    content = img.crop((left, top, right, bottom))
    cp = content.load()
    cw, ch = content.size

    filled = [(x, y) for y in range(ch) for x in range(cw) if cp[x, y] > 127]
    if not filled:
        return None

    cx = sum(p[0] for p in filled) / len(filled)
    cy = sum(p[1] for p in filled) / len(filled)

    # 归一化重心
    nx = cx / cw
    ny = cy / ch

    # 纵横比
    aspect = w / h

    # 上半部与下半部像素比例
    top_half = sum(1 for p in filled if p[1] < ch / 2)
    bottom_half = len(filled) - top_half
    top_ratio = top_half / len(filled) if filled else 0.5

    # 左半部与右半部像素比例
    left_half = sum(1 for p in filled if p[0] < cw / 2)
    right_ratio = (len(filled) - left_half) / len(filled) if filled else 0.5

    # 四个象限密度
    quads = [0, 0, 0, 0]
    for x, y in filled:
        idx = (0 if x < cw / 2 else 1) + (0 if y < ch / 2 else 2)
        quads[idx] += 1
    quads = [q / len(filled) for q in quads] if filled else [0.25] * 4

    return {
        'aspect': aspect,
        'nx': nx,
        'ny': ny,
        'top_ratio': top_ratio,
        'right_ratio': right_ratio,
        'quads': quads,
        'filled_count': len(filled),
    }


def _is_blank(cell_img: Image.Image, threshold: float = 0.015) -> bool:
    """判断单元格是否为空（二值图中前景像素比例过低）。"""
    pixels = cell_img.load()
    w, h = cell_img.size
    if w <= 0 or h <= 0:
        return True
    filled = sum(1 for y in range(h) for x in range(w) if pixels[x, y] > 127)
    return filled / (w * h) < threshold


def _center_content(img: Image.Image, size: int = 64) -> Image.Image:
    """将图像中的非零内容居中缩放到指定尺寸。"""
    bbox = _content_bbox(img)
    if bbox is None:
        return Image.new('L', (size, size), 0)

    left, top, right, bottom = bbox
    content = img.crop((left, top, right, bottom))
    cw, ch = content.size

    # 保持长宽比缩放到 target，留出边距
    target = int(size * 0.75)
    scale = min(target / cw, target / ch) if cw > 0 and ch > 0 else 1.0
    new_w, new_h = max(1, int(cw * scale)), max(1, int(ch * scale))
    scaled = content.resize((new_w, new_h), Image.Resampling.LANCZOS)

    out = Image.new('L', (size, size), 0)
    x_off = (size - new_w) // 2
    y_off = (size - new_h) // 2
    out.paste(scaled, (x_off, y_off))
    return out


def _recognize_digit(cell_gray: Image.Image,
                     templates: Optional[dict] = None) -> Optional[int]:
    """识别单元格中的数字（0-3）或返回 None（空白格）。

    流程：
    1. 逐格 Otsu 二值化（关键改进：不用全局阈值）
    2. 形状特征分类（孔洞→0, 纵横比→1, 投影→2/3）
    3. 若有自学习模板，用 NCC 作为辅助校验
    """
    if cell_gray.size[0] < 5 or cell_gray.size[1] < 5:
        return None

    # ── 逐格 Otsu 二值化 ──
    bin_cell = _clean_cell_otsu(cell_gray)

    # 检查是否空白
    w, h = bin_cell.size
    px = bin_cell.load()
    white_count = sum(1 for y in range(h) for x in range(w) if px[x, y] > 127)
    fill_ratio = white_count / (w * h) if w * h > 0 else 0
    if fill_ratio < 0.01 or white_count < 15:
        return None

    # ── 提取几何特征 ──
    features = _digit_features(bin_cell)
    if features is None or features['filled_count'] < 15:
        return None

    # ── 形状分类 ──
    shape_result = _classify_by_shape(bin_cell, features)

    # ── 模板匹配辅助 ──
    if templates and len(templates) >= 4:
        normalized = _center_content(cell_gray, size=64)
        scores = {d: _similarity(normalized, tmpl) for d, tmpl in templates.items()}
        best_digit = max(scores, key=scores.get)
        best_score = scores[best_digit]
        second_score = sorted(scores.values(), reverse=True)[1]

        # NCC 有明确优势时采用 NCC 结果
        if best_score > 0.35 and best_score - second_score > 0.05:
            return best_digit

        # 形状结果与 NCC 一致 → 高置信度
        if shape_result is not None and shape_result == best_digit and best_score > 0.2:
            return shape_result

        # NCC 分数不够 → 信任形状分类
        if shape_result is not None:
            return shape_result

        # 都不确定，用 NCC 最佳
        if best_score > 0.15:
            return best_digit
    else:
        # 无模板：纯形状分类
        if shape_result is not None:
            return shape_result

        # 回退：用纵横比粗略判断
        aspect = features['aspect']
        if aspect < 0.68:
            return 1
        # 2 vs 3: x_shift
        x_shift = _compute_x_shift(bin_cell)
        if x_shift is not None and x_shift < -0.75:
            return 2
        if aspect >= 0.68:
            return 3

    return None


def _extract_cell(
    binary: Image.Image,
    x1: int, y1: int, x2: int, y2: int,
    margin: int = 2,
) -> Image.Image:
    """裁剪单元格，并略去边界处的黑点。"""
    left = min(x1 + margin, x2)
    top = min(y1 + margin, y2)
    right = max(x2 - margin, left + 1)
    bottom = max(y2 - margin, top + 1)
    return binary.crop((left, top, right, bottom))


def _estimate_dot_size(
    dots: List[Tuple[float, float]],
    components: List[Tuple[int, float, float, int, int]],
) -> int:
    """
    根据黑点估算黑点直径，用于单元格裁剪边距。
    返回比点半径稍大的边距值，确保裁剪单元格时排除圆点边缘。
    """
    if not dots or not components:
        return 6

    # 取前几个黑点，找最近的组件
    sizes = []
    for cx, cy in dots[:min(8, len(dots))]:
        best_dist = float('inf')
        best_bw = 3
        best_bh = 3
        for area, ccx, ccy, bw, bh in components:
            dist = (ccx - cx) ** 2 + (ccy - cy) ** 2
            if dist < best_dist:
                best_dist = dist
                best_bw = bw
                best_bh = bh
        sizes.append(max(best_bw, best_bh))

    if not sizes:
        return 6
    avg_size = sum(sizes) / len(sizes)
    # 边距取点直径的 80%，确保完全排除圆点
    return max(3, int(avg_size * 0.8))


def parse_image(path: Path, debug: bool = False) -> List[List[Optional[int]]]:
    """
    从图片文件中解析 Slitherlink 谜题，返回数字网格。

    Args:
        path: 图片文件路径。
        debug: 为 True 时，在同目录输出 debug_grid.png 用于检查网格划分。

    Returns:
        二维列表，每个元素为 int（0-3）或 None（空白格）。

    Raises:
        ImageParseError: 解析失败时抛出。
    """
    if not path.exists():
        raise ImageParseError(f'文件不存在：{path}')

    try:
        with Image.open(path) as img:
            img = img.convert('L')
            binary = _binarize(img)
            # 用于数字识别的灰度图：数字为亮色、背景为暗色
            gray = ImageOps.invert(img)
    except Exception as exc:
        raise ImageParseError(f'无法读取或处理图片：{exc}')

    bbox = _content_bbox(binary)
    if bbox is None:
        raise ImageParseError('未在图片中找到任何内容，请检查是否为空白图片。')

    left, top, right, bottom = bbox
    region = binary.crop((left, top, right, bottom))

    # ——— 提取连通组件，分离黑点和数字 ———
    all_components = _find_components(region)
    dots, numbers = _separate_dots_and_numbers(all_components)

    # 将黑点坐标转换回全局坐标
    dots_global = [(left + cx, top + cy) for cx, cy in dots]

    if len(dots_global) >= 4:
        # ——— 方法 1：从黑点拟合网格 ———
        result = _fit_grid_from_dots(dots_global, bbox)
        if result is not None:
            R, C, h_lines, v_lines = result
            dots_region = [(cx - left, cy - top) for cx, cy in dots_global]
            margin = _estimate_dot_size(dots_region, all_components)
        else:
            result = None
    else:
        result = None

    if result is None:
        # ——— 方法 2：从数字组件位置推断网格 ———
        result = _estimate_grid_from_numbers(numbers, bbox)
        if result is not None:
            R, C, h_lines, v_lines = result
            margin = 3
        else:
            raise ImageParseError(
                '无法从图片中检测到规则网格。'
                f'检测到 {len(dots_global)} 个黑点，'
                f'{len(numbers)} 个数字组件，但无法形成规则网格。'
            )

    # ——— 提取每个单元格，两轮识别数字 ———
    # 计算单元格平均尺寸
    avg_v_gap = sum(v_lines[i+1] - v_lines[i] for i in range(len(v_lines)-1)) / (len(v_lines)-1)
    avg_h_gap = sum(h_lines[i+1] - h_lines[i] for i in range(len(h_lines)-1)) / (len(h_lines)-1)
    # 边距：12% 保留足够像素，角落残留由 _clean_cell_otsu 处理
    small_margin = max(2, min(int(avg_v_gap * 0.12), int(avg_h_gap * 0.12)))

    # 第一阶段：从原始灰度图裁剪所有单元格
    cell_crops = []  # (r, c, cell_gray_inverted)
    cell_bins = []   # (r, c, otsu_binary)
    for r in range(R):
        for c in range(C):
            x1, y1 = v_lines[c], h_lines[r]
            x2, y2 = v_lines[c + 1], h_lines[r + 1]
            left_m = min(x1 + small_margin, x2)
            top_m = min(y1 + small_margin, y2)
            right_m = max(x2 - small_margin, left_m + 1)
            bottom_m = max(y2 - small_margin, top_m + 1)
            cell_gray = ImageOps.invert(img.crop((left_m, top_m, right_m, bottom_m)))
            cell_bin = _clean_cell_otsu(cell_gray)
            cell_crops.append((r, c, cell_gray))
            cell_bins.append((r, c, cell_bin))

    # 第一轮：决策树主导 + 合成 NCC 纠正 → 种子分类
    # 策略：DT 对 0（孔洞）、1（窄）、2（x_shift）置信度高，但对 3 不可靠
    # → 仅在 DT=3 时用合成 NCC 纠错，避免 NCC 对小格子的误判
    first_labels: dict[Tuple[int, int], Optional[int]] = {}
    seed_ncc_size = max(int(min(avg_v_gap, avg_h_gap)), 24)
    syn_templates = {d: _synthetic_template(d, seed_ncc_size) for d in range(4)}

    for (r, c, cell_gray), (_, _, bin_cell) in zip(cell_crops, cell_bins):
        w, h = bin_cell.size
        px = bin_cell.load()
        wc = sum(1 for y in range(h) for x in range(w) if px[x, y] > 127)
        if wc < 8:
            first_labels[(r, c)] = None
            continue

        feat = _digit_features(bin_cell)
        if feat is None:
            first_labels[(r, c)] = None
            continue

        # ——— 决策树分类 ———
        holes = _count_holes(bin_cell)
        aspect = feat['aspect']

        # 0: 有孔洞（高置信度）
        if holes >= 1 and aspect > 0.4:
            first_labels[(r, c)] = 0
            continue

        # 1: 窄长形状（高置信度）
        if aspect < 0.68:
            first_labels[(r, c)] = 1
            continue

        # 2 vs 3: x_shift 判断
        x_shift = _compute_x_shift(bin_cell)
        if x_shift is not None and x_shift < -0.75:
            first_labels[(r, c)] = 2
            continue

        # DT 归类为 3（默认类别，最不可靠）→ 用合成 NCC 二次确认
        scaled = cell_gray.resize((seed_ncc_size, seed_ncc_size), Image.Resampling.LANCZOS)
        ncc_scores = {d: _similarity(scaled, tmpl) for d, tmpl in syn_templates.items()}
        ncc_best = max(ncc_scores, key=ncc_scores.get)
        ncc_best_score = ncc_scores[ncc_best]

        if ncc_best != 3 and ncc_best_score > 0.30:
            first_labels[(r, c)] = ncc_best  # NCC 纠正 DT 误判
        else:
            first_labels[(r, c)] = 3

    # 构建原生分辨率模板
    sample_imgs = {0: [], 1: [], 2: [], 3: []}
    for r, c, cell_gray in cell_crops:
        lbl = first_labels.get((r, c))
        if lbl is not None and 0 <= lbl <= 3:
            sample_imgs[lbl].append(cell_gray)

    native_templates = {}
    # 统一模板尺寸：取所有已构建模板中的最大尺寸，确保 NCC 可比较
    tsize_candidates = []
    temp_templates = {}
    for digit in range(4):
        imgs = sample_imgs.get(digit, [])
        if len(imgs) >= 2:
            max_w = max(im.size[0] for im in imgs)
            max_h = max(im.size[1] for im in imgs)
            tsize = max(max_w, max_h, 18)
            tsize_candidates.append(tsize)
            resized = []
            for im in imgs:
                scaled = im.resize((tsize, tsize), Image.Resampling.LANCZOS)
                resized.append(scaled)
            avg = Image.new('F', (tsize, tsize), 0.0)
            for im in resized:
                ipx = im.load()
                apx = avg.load()
                for y in range(tsize):
                    for x in range(tsize):
                        apx[x, y] += ipx[x, y] / 255.0
            n = len(resized)
            out = Image.new('L', (tsize, tsize), 0)
            opx = out.load()
            apx = avg.load()
            for y in range(tsize):
                for x in range(tsize):
                    opx[x, y] = int(min(255, (apx[x, y] / n) * 255))
            temp_templates[digit] = out
        else:
            temp_templates[digit] = None  # 稍后用统一尺寸的合成模板

    # 统一尺寸
    common_tsize = max(tsize_candidates) if tsize_candidates else 24
    for digit in range(4):
        if temp_templates.get(digit) is not None:
            if temp_templates[digit].size[0] != common_tsize:
                native_templates[digit] = temp_templates[digit].resize(
                    (common_tsize, common_tsize), Image.Resampling.LANCZOS)
            else:
                native_templates[digit] = temp_templates[digit]
        else:
            native_templates[digit] = _synthetic_template(digit, common_tsize)

    # 第二轮：NCC 模板匹配主导，决策树作为回退
    grid: List[List[Optional[int]]] = [[None] * C for _ in range(R)]
    for r, c, cell_gray in cell_crops:
        tsize = native_templates[0].size[0]
        scaled = cell_gray.resize((tsize, tsize), Image.Resampling.LANCZOS)
        scores = {d: _similarity(scaled, tmpl) for d, tmpl in native_templates.items()}
        best = max(scores, key=scores.get)
        best_score = scores[best]
        sorted_scores = sorted(scores.values(), reverse=True)
        second_score = sorted_scores[1] if len(sorted_scores) > 1 else 0
        margin = best_score - second_score

        # NCC 极高置信度 + 明显优势 → 直接采用
        if best_score > 0.50 and margin > 0.05:
            grid[r][c] = best
        # NCC 极高置信度但优势小 → 参考第一轮标签
        elif best_score > 0.50:
            lbl = first_labels.get((r, c))
            if lbl is not None and lbl != best:
                grid[r][c] = lbl  # 保护第一轮标签
            else:
                grid[r][c] = best
        # NCC 高置信度 + 明显优势 → 直接采用
        elif best_score > 0.35 and margin > 0.03:
            grid[r][c] = best
        # NCC 高置信度但优势小 → 参考第一轮标签
        elif best_score > 0.35:
            lbl = first_labels.get((r, c))
            if lbl is not None and lbl != best:
                grid[r][c] = lbl  # 保护第一轮标签
            else:
                grid[r][c] = best
        # NCC 中等置信度 → 优先信任第一轮
        elif best_score > 0.25:
            lbl = first_labels.get((r, c))
            if lbl is not None:
                grid[r][c] = lbl
            else:
                grid[r][c] = best
        # NCC 低置信度 → 回退到第一轮标签
        else:
            lbl = first_labels.get((r, c))
            if lbl is not None:
                grid[r][c] = lbl

    if debug:
        _save_debug_image(binary, h_lines, v_lines, grid, path)

    return grid


def _save_debug_image(
    binary: Image.Image,
    h_lines: List[int],
    v_lines: List[int],
    grid: List[List[Optional[int]]],
    src_path: Path,
) -> None:
    """保存带网格线和识别结果的调试图片。"""
    debug = binary.convert('RGB')
    draw = ImageDraw.Draw(debug)

    # 绘制网格线（红色）
    for y in h_lines:
        draw.line([(0, y), (debug.width, y)], fill='red', width=1)
    for x in v_lines:
        draw.line([(x, 0), (x, debug.height)], fill='red', width=1)

    # 绘制识别结果（绿色）
    for r in range(len(grid)):
        for c in range(len(grid[0])):
            x1, y1 = v_lines[c], h_lines[r]
            x2, y2 = v_lines[c + 1], h_lines[r + 1]
            text = str(grid[r][c]) if grid[r][c] is not None else '.'
            draw.text(((x1 + x2) // 2, (y1 + y2) // 2), text, fill='lime', anchor='mm')

    out = src_path.parent / f'{src_path.stem}_debug{src_path.suffix}'
    debug.save(out)


def parse_input(path: Path, debug: bool = False) -> List[List[Optional[int]]]:
    """
    统一入口：根据扩展名自动判断是图片还是文本文件，并解析为数回网格。

    Args:
        path: 文件路径。
        debug: 图片解析时是否输出调试图（仅对图片文件有效）。
    """
    from solver import parse_puzzle

    if is_image_path(path):
        return parse_image(path, debug=debug)
    return parse_puzzle(path.read_text(encoding='utf-8'))