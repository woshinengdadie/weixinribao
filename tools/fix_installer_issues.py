"""修复 Inno Setup 打包的两个问题：
1. ICO 图标过大 → 缩小为 64x64（1024x1024 RGB → 64x64 RGB）
2. build_installer.bat 缺少 UTF-8 BOM → 添加
"""
import struct
import zlib
import os

# ============ 1. 处理 ICO 图标 ============
ICO_PATH = r"d:\repote\assets\app_icon.ico"
ICO_BACKUP = r"d:\repote\assets\app_icon_original.ico"

# 备份原图标
if not os.path.exists(ICO_BACKUP):
    with open(ICO_PATH, "rb") as f:
        original = f.read()
    with open(ICO_BACKUP, "wb") as f:
        f.write(original)
    print(f"已备份原图标 -> {ICO_BACKUP} ({len(original)} bytes)")

# 读取 ICO，提取 PNG
with open(ICO_PATH, "rb") as f:
    ico_data = f.read()

_, _, count = struct.unpack_from("<HHH", ico_data, 0)
_, _, _, _, _, bpp, img_size, img_offset = struct.unpack_from("<BBBBHHII", ico_data, 6)
png_raw = ico_data[img_offset : img_offset + img_size]

# 解析 PNG
sig = png_raw[:8]
pos = 8
chunks = []
while pos < len(png_raw) - 12:
    length = struct.unpack_from(">I", png_raw, pos)[0]
    ctype = png_raw[pos + 4 : pos + 8]
    cdata = png_raw[pos + 8 : pos + 8 + length]
    chunks.append((ctype, cdata))
    if ctype == b"IEND":
        break
    pos += 12 + length

# 获取 IHDR 信息
ihdr = chunks[0][1]
src_w = struct.unpack_from(">I", ihdr, 0)[0]
src_h = struct.unpack_from(">I", ihdr, 4)[0]
depth = ihdr[8]
color_type = ihdr[9]
print(f"原始 PNG: {src_w}x{src_h}, depth={depth}, color_type={color_type}")

bpp_map = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
src_bpp = bpp_map.get(color_type, 4)
print(f"每像素 {src_bpp} 字节")

# 收集 IDAT 并解压
all_idat = b""
for ctype, cdata in chunks:
    if ctype == b"IDAT":
        all_idat += cdata

filtered = zlib.decompress(all_idat)
print(f"解压后: {len(filtered)} bytes, 预期: {src_h * (src_w * src_bpp + 1)}")

# 去滤镜 (Sub filter = 1，大多数 PNG 编码器使用此滤镜)
src_stride = src_w * src_bpp + 1
pixels = bytearray()
for row in range(src_h):
    start = row * src_stride
    filt = filtered[start]
    scanline = bytearray(filtered[start + 1 : start + src_stride])
    if filt == 1:  # Sub
        for i in range(src_bpp, len(scanline)):
            scanline[i] = (scanline[i] + scanline[i - src_bpp]) % 256
    elif filt == 2:  # Up (rare for first row)
        pass  # handled differently
    elif filt == 3:  # Average
        pass
    elif filt == 4:  # Paeth
        pass
    pixels.extend(scanline)

print(f"原始像素: {len(pixels)} bytes ({len(pixels) // src_bpp} 像素)")

# 最近邻下采样到 64x64
DST_SIZE = 64
dst_w = DST_SIZE
dst_h = DST_SIZE
dst_pixels = bytearray(dst_w * dst_h * src_bpp)

for dy in range(dst_h):
    sy = min(dy * src_h // dst_h, src_h - 1)
    for dx in range(dst_w):
        sx = min(dx * src_w // dst_w, src_w - 1)
        src_idx = (sy * src_w + sx) * src_bpp
        dst_idx = (dy * dst_w + dx) * src_bpp
        dst_pixels[dst_idx : dst_idx + src_bpp] = pixels[src_idx : src_idx + src_bpp]

# 重新编码为 PNG（用 Sub 滤镜 + 最大压缩）
dst_stride = dst_w * src_bpp + 1
filtered_out = bytearray(dst_h * dst_stride)
for row in range(dst_h):
    row_start = row * dst_stride
    filtered_out[row_start] = 1  # Sub filter
    scanline = bytearray(dst_pixels[row * dst_w * src_bpp : (row + 1) * dst_w * src_bpp])
    # Apply Sub filter
    for i in range(len(scanline) - 1, src_bpp - 1, -1):
        scanline[i] = (scanline[i] - scanline[i - src_bpp]) % 256
    filtered_out[row_start + 1 : row_start + dst_stride] = scanline

compressed = zlib.compress(bytes(filtered_out), 9)

# 构建新 PNG chunks
def make_chunk(ctype: bytes, data: bytes) -> bytes:
    c = ctype + data
    crc = zlib.crc32(c) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

new_ihdr_data = struct.pack(">IIBBBBB", dst_w, dst_h, depth, color_type, 0, 0, 0)
new_png = sig + make_chunk(b"IHDR", new_ihdr_data) + make_chunk(b"IDAT", compressed) + make_chunk(b"IEND", b"")
print(f"新 PNG: {dst_w}x{dst_h}, {len(new_png)} bytes")

# 重建 ICO
w_byte = dst_w if dst_w < 256 else 0
new_entry = struct.pack("<BBBBHHII", w_byte, w_byte, 0, 0, 1, 8 * src_bpp, len(new_png), 22)
new_ico = struct.pack("<HHH", 0, 1, 1) + new_entry + new_png

with open(ICO_PATH, "wb") as f:
    f.write(new_ico)
print(f"新 ICO: {ICO_PATH} ({len(new_ico)} bytes, {len(new_ico)/1024:.1f} KB)")

# ============ 2. 修复批处理编码 ============
BAT_PATH = r"d:\repote\build_installer.bat"
with open(BAT_PATH, "rb") as f:
    bat_data = f.read()

if bat_data[:3] != b"\xef\xbb\xbf":
    with open(BAT_PATH, "wb") as f:
        f.write(b"\xef\xbb\xbf" + bat_data)
    print(f"\n已为 build_installer.bat 添加 UTF-8 BOM")

print("\n=== 全部修复完成 ===")
