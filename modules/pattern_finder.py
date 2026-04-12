import struct

with open(r"e:\Data\MOD工具\JCNS\binaries\ch03_012_0012.jcns.102", "rb") as f:
    data = f.read()

# Pattern: 25.0, 120.0 (not necessarily those numbers, but float patterns)
# Let's search for Float 1.0 (00 00 80 3F) which is always at the end!
# Actually, the string L_Thigh occurs right after.
import re
offsets = [m.start() for m in re.finditer(b'L\x00_\x00T\x00h\x00i\x00g\x00h\x00', data)]

print("L_Thigh string offsets found:")
for off in offsets:
    print(hex(off))
