"""缩小 ICO 图标，去除超大幅面，适配 Inno Setup SetupIconFile"""
import struct
import os

SRC = r"d:\repote\assets\app_icon.ico"
DST = r"d:\repote\assets\app_icon_small.ico"

MAX_ICO_SIZE = 256  # 只保留 <=256x256 的图标

with open(SRC, "rb") as f:
    data = f.read()

# 解析 ICO 头部
reserved, img_type, count = struct.unpack_from("<HHH", data, 0)
print(f"ICO: type={img_type}, count={count}, total_size={len(data)}")

# 解析目录项
entries = []
for i in range(count):
    offset = 6 + i * 16
    w, h, colors, reserved2, planes, bpp, size, img_offset = struct.unpack_from(
        "<BBBBHHII", data, offset
    )
    actual_w = w if w != 0 else 256  # 0 表示 256
    actual_h = h if h != 0 else 256
    entries.append({
        "index": i,
        "w": actual_w,
        "h": actual_h,
        "size": size,
        "offset": img_offset,
        "bpp": bpp,
    })
    print(f"  [{i}] {actual_w}x{actual_h} @{bpp}bpp, {size} bytes (offset={img_offset})")

# 过滤：只保留 <= MAX_ICO_SIZE 的尺寸
kept = [e for e in entries if e["w"] <= MAX_ICO_SIZE and e["h"] <= MAX_ICO_SIZE]
removed = [e for e in entries if e not in kept]

if not kept:
    print("\n错误：没有 <= 256x256 的图标，保留第一个")
    kept = [entries[0]]

print(f"\n保留 {len(kept)} 个图标，移除 {len(removed)} 个:")
for e in kept:
    print(f"  [{e['index']}] {e['w']}x{e['h']}, {e['size']} bytes")
for e in removed:
    print(f"  移除 [{e['index']}] {e['w']}x{e['h']}")

# 重建 ICO 文件
new_count = len(kept)
new_header = struct.pack("<HHH", 0, 1, new_count)

# 计算新偏移
new_offset = 6 + new_count * 16  # header + directory entries
new_entries_data = b""
new_image_data = b""

for i, e in enumerate(kept):
    w = e["w"] if e["w"] < 256 else 0
    h = e["h"] if e["h"] < 256 else 0
    new_entries_data += struct.pack(
        "<BBBBHHII", w, h, 0, 0, 1, e["bpp"], e["size"], new_offset
    )
    # 复制图像数据
    new_image_data += data[e["offset"] : e["offset"] + e["size"]]
    new_offset += e["size"]

with open(DST, "wb") as f:
    f.write(new_header)
    f.write(new_entries_data)
    f.write(new_image_data)

new_size = os.path.getsize(DST)
print(f"\n新图标: {DST} ({new_size} bytes, {new_size/1024:.1f} KB)")

# 替换原文件
os.replace(DST, SRC)
print(f"已替换 {SRC}")
