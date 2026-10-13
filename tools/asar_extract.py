import json, os, struct, sys
src, dst = sys.argv[1], sys.argv[2]
b = open(src, 'rb').read()
# asar: pickle(uint32 size=4, uint32 header_size) + pickle(uint32 payload, uint32 strlen, json)
hsize = struct.unpack_from('<I', b, 4)[0]
jlen = struct.unpack_from('<I', b, 12)[0]
header = json.loads(b[16:16+jlen])
base = 8 + hsize
def walk(node, path):
    for name, n in node.get('files', {}).items():
        p = os.path.join(path, name)
        if os.path.normpath(p).startswith('..') or os.path.isabs(name): continue
        if 'files' in n:
            os.makedirs(os.path.join(dst, p), exist_ok=True); walk(n, p)
        elif n.get('unpacked'):
            continue
        else:
            off = base + int(n['offset']); sz = n['size']
            os.makedirs(os.path.dirname(os.path.join(dst, p)) or dst, exist_ok=True)
            open(os.path.join(dst, p), 'wb').write(b[off:off+sz])
walk(header, '')
