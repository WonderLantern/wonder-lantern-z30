"""Write on-camera SD LUT test vectors: .cube text and WL3L binary files.

The device text parser accepts only the documented subset: LUT_3D_SIZE before
DOMAIN_MIN/DOMAIN_MAX, three space-separated decimals per row, at most nine
fractional digits, no exponent, no negative values, no trailing tokens. Every
generated file is re-parsed here with the host reference parser, so a vector
that the camera would reject fails before it reaches a card.

Vectors are diagnostics, not deliverables: identity shows whether the colour
round trip is neutral, the luma vector turns the frame gray when the assumed
matrix/range/plane order is right, and the R/B swap proves pixels changed even
if the colour contract is off. Negatives prove the loader keeps the previous
table instead of activating a partial one.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.host import lut  # noqa: E402

LUMA = (0.299, 0.587, 0.114)


def _clamp(value):
    return min(1.0, max(0.0, value))


def _nodes(size):
    for blue in range(size):
        for green in range(size):
            for red in range(size):
                yield red / (size - 1), green / (size - 1), blue / (size - 1)


def rows_identity(size):
    return list(_nodes(size))


def rows_swap_rb(size):
    """Output R takes input B and output B takes input R."""
    return [(blue, green, red) for red, green, blue in _nodes(size)]


def rows_luma(size):
    """Collapse to BT.601 luma: a neutral frame means matrix, range and plane order agree."""
    rows = []
    for red, green, blue in _nodes(size):
        y = _clamp(LUMA[0] * red + LUMA[1] * green + LUMA[2] * blue)
        rows.append((y, y, y))
    return rows


def rows_warm(size):
    """Mild red lift and blue cut: visible, but still judgeable by eye."""
    return [(_clamp(1.06 * red + 0.02), green, _clamp(0.94 * blue))
            for red, green, blue in _nodes(size)]


def cube_text(title, size, rows):
    lines = [f'TITLE {title}', f'LUT_3D_SIZE {size}']
    lines += [' '.join(f'{_clamp(value):.6f}' for value in row) for row in rows]
    return '\n'.join(lines) + '\n'


def binary_of(text):
    parsed = lut.parse_cube(text)
    return lut.pack_binary(parsed['size'], parsed['domain_min'], parsed['domain_max'], parsed['table'])


def good_vectors():
    """(name, payload bytes, expected LUT size, effect) for files the camera must accept."""
    swap17 = cube_text('Swap R/B 17', 17, rows_swap_rb(17))
    return [
        ('identity-17.cube', cube_text('Identity 17', 17, rows_identity(17)).encode(), 17,
         '输出应与原图一致（无偏色、无亮度变化）'),
        ('swap-rb-17.cube', swap17.encode(), 17, '红蓝互换：证明像素确实经过 LUT'),
        ('luma-17.cube', cube_text('BT601 luma 17', 17, rows_luma(17)).encode(), 17,
         '输出应为中性灰：矩阵/范围/平面顺序正确时才成立'),
        ('warm-17.cube', cube_text('Warm 17', 17, rows_warm(17)).encode(), 17,
         '轻微偏暖：肉眼可判读的温和效果'),
        ('swap-rb-33.cube', cube_text('Swap R/B 33', 33, rows_swap_rb(33)).encode(), 33,
         '同 swap-rb-17，用于比较表尺寸对耗时的影响'),
        ('swap-rb-17.wl3l', binary_of(swap17), 17, '二进制 WL3L 路径，效果同 swap-rb-17'),
        # 最小表：红蓝互换是线性映射，2³ 的三线性插值即为精确互换，因此这几张表
        # 与 17³ 等效，只用于把「读不到内容」与「表太大/读不完」区分开。
        ('tiny-2-swap.cube', cube_text('Swap R/B 2', 2, rows_swap_rb(2)).encode(), 2,
         '最小合法文本表（8 行）：能加载则说明读取与解析都通，问题在大小'),
        ('tiny-5-swap.cube', cube_text('Swap R/B 5', 5, rows_swap_rb(5)).encode(), 5,
         '小表（125 行）：与 tiny-2 配合判断是否与文件长度相关'),
        ('tiny-2-swap.wl3l', binary_of(cube_text('Swap R/B 2', 2, rows_swap_rb(2))), 2,
         '最小二进制表（70 字节）：文本与二进制同时失败则与格式无关'),
        ('tiny-5-swap.wl3l', binary_of(cube_text('Swap R/B 5', 5, rows_swap_rb(5))), 5,
         '小二进制表（772 字节）'),
        # 大小扫描：同样精确的互换，只是表长不同，用来找设备的读取边界。
        ('sweep-9-swap.cube', cube_text('Swap R/B 9', 9, rows_swap_rb(9)).encode(), 9,
         '大小扫描约 30 KB'),
        ('sweep-13-swap.cube', cube_text('Swap R/B 13', 13, rows_swap_rb(13)).encode(), 13,
         '大小扫描约 59 KB'),
        ('sweep-15-swap.cube', cube_text('Swap R/B 15', 15, rows_swap_rb(15)).encode(), 15,
         '大小扫描约 91 KB'),
        ('sweep-3-swap.cube', cube_text('Swap R/B 3', 3, rows_swap_rb(3)).encode(), 3,
         '大小扫描约 730 B'),
        ('sweep-4-swap.cube', cube_text('Swap R/B 4', 4, rows_swap_rb(4)).encode(), 4,
         '大小扫描约 1.7 KB'),
        ('sweep-6-swap.cube', cube_text('Swap R/B 6', 6, rows_swap_rb(6)).encode(), 6,
         '大小扫描约 5.8 KB'),
    ]


def bad_vectors():
    text = cube_text('Swap R/B 17', 17, rows_swap_rb(17))
    header, rows = text.split('\n')[:2], text.split('\n')[2:]
    truncated = '\n'.join(header + rows[:100]) + '\n'
    paired = '\n'.join(header + [f'{rows[i]} {rows[i + 1]}' for i in range(0, 200, 2)]) + '\n'
    return [
        ('bad-truncated-17.cube', truncated, '行数不足：应报「无效的 LUT」并保留旧表'),
        ('bad-six-per-line-17.cube', paired, '一行六个值：应报「无效的 LUT」并保留旧表'),
    ]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path,
                   default=Path('build/research/lut-postprocess/test-vectors'))
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = []
    for name, data, size, effect in good_vectors():
        parsed = lut.unpack_binary(data) if name.endswith('.wl3l') else lut.parse_cube(data.decode())
        if parsed['size'] != size:
            raise SystemExit(f'{name} parsed as size {parsed["size"]}, expected {size}')
        args.output.joinpath(name).write_bytes(data)
        report.append((name, len(data), parsed['size'], 0, effect))
    for name, text, effect in bad_vectors():
        try:
            lut.parse_cube(text)
        except lut.CubeError:
            pass
        else:
            raise SystemExit(f'{name} unexpectedly parses; negative vector is wrong')
        args.output.joinpath(name).write_text(text, encoding='utf-8', newline='\n')
        report.append((name, len(text), '-', 3, effect))
    readme = [
        '机内 SD LUT 测试向量。用法：把要测的文件复制到存储卡根目录并改名为 WL-LUT.CUBE。',
        '一次只放一个；换文件后需在菜单里重新选「加载 SD LUT」。',
        '',
        '文件 / 字节 / 表尺寸 / 预期状态 / 预期效果',
    ]
    for name, size, lut_size, status, effect in report:
        readme.append(f'{name} / {size} / {lut_size} / {status} / {effect}')
    readme += ['', '状态 0 = 成功；3 = 无效的 LUT。负向向量必须在设备上被拒绝且保留旧表。']
    args.output.joinpath('README.txt').write_text('\n'.join(readme) + '\n', encoding='utf-8', newline='\n')
    for name, size, lut_size, status, effect in report:
        print(f'{name:26s} {size:>8d} B  size={lut_size:>2}  expect={status}  {effect}')
    print(f'PASS: {len(report)} vectors in {args.output}; every good vector re-parsed.')


if __name__ == '__main__':
    main()
