import socket, base64, json, struct, os, sys, time

PAGE_ID = sys.argv[1]
sock = socket.create_connection(('127.0.0.1', 9222), timeout=10)
key = base64.b64encode(os.urandom(16)).decode()
hs = (
    'GET /devtools/page/' + PAGE_ID + ' HTTP/1.1\r\n'
    'Host: 127.0.0.1:9222\r\n'
    'Upgrade: websocket\r\n'
    'Connection: Upgrade\r\n'
    'Sec-WebSocket-Key: ' + key + '\r\n'
    'Sec-WebSocket-Version: 13\r\n\r\n'
)
sock.sendall(hs.encode())
resp = sock.recv(4096)
assert b'101' in resp

def ws_send(data):
    data = data.encode()
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    hdr = b'\x81'
    if len(data) < 126:
        hdr += bytes([0x80 | len(data)])
    else:
        hdr += b'\xfe' + struct.pack('>H', len(data))
    sock.sendall(hdr + mask + masked)

def ws_recv_raw():
    hdr = sock.recv(2)
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack('>H', sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack('>Q', sock.recv(8))[0]
    data = b''
    while len(data) < length:
        chunk = sock.recv(min(65536, length - len(data)))
        if not chunk:
            break
        data += chunk
    return data

def cdp(method, params, mid):
    ws_send(json.dumps({'id': mid, 'method': method, 'params': params}))
    for _ in range(200):
        try:
            raw = ws_recv_raw()
            obj = json.loads(raw.decode(errors='replace'))
        except Exception:
            continue
        if obj.get('id') == mid:
            return obj.get('result', {})
    return {}

# Reset menu state
r = cdp('Runtime.evaluate', {'expression': 'hideMenu(); String(menuVisible)', 'returnByValue': True}, 1)
print('hideMenu:', r.get('result', {}).get('value'))

# Inject a mouse click at (160, 160) via CDP Input.dispatchMouseEvent
r = cdp('Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': 160, 'y': 160, 'button': 'left', 'clickCount': 1}, 3)
print('mousePressed:', r)
time.sleep(0.05)
r = cdp('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': 160, 'y': 160, 'button': 'left', 'clickCount': 1}, 4)
print('mouseReleased:', r)

time.sleep(0.3)
r = cdp('Runtime.evaluate', {'expression': 'String(menuVisible)', 'returnByValue': True}, 6)
print('menuVisible after CDP click:', r.get('result', {}).get('value'))

# Take screenshot
r = cdp('Page.captureScreenshot', {'format': 'png', 'quality': 80}, 7)
if 'data' in r:
    import base64 as b64
    with open('/tmp/click_test.png', 'wb') as f:
        f.write(b64.b64decode(r['data']))
    print('screenshot ok')
else:
    print('screenshot failed:', r)

sock.close()
