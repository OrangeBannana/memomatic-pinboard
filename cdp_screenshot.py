import socket, base64, json, struct, os, sys, time

PAGE_ID = sys.argv[1]
host = '127.0.0.1'
port = 9222
path = '/devtools/page/' + PAGE_ID

sock = socket.create_connection((host, port), timeout=10)
key = base64.b64encode(os.urandom(16)).decode()
hs = (
    'GET ' + path + ' HTTP/1.1\r\n'
    'Host: 127.0.0.1:9222\r\n'
    'Upgrade: websocket\r\n'
    'Connection: Upgrade\r\n'
    'Sec-WebSocket-Key: ' + key + '\r\n'
    'Sec-WebSocket-Version: 13\r\n\r\n'
)
sock.sendall(hs.encode())
resp = sock.recv(4096)
assert b'101' in resp, 'WS handshake failed'

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
    for _ in range(100):
        try:
            raw = ws_recv_raw()
            obj = json.loads(raw.decode(errors='replace'))
        except Exception:
            continue
        if obj.get('id') == mid:
            return obj.get('result', {})
    return {}

# Call showMenu()
r = cdp('Runtime.evaluate', {'expression': 'showMenu(); String(menuVisible)', 'returnByValue': True}, 1)
print('showMenu result:', r.get('result', {}).get('value'))

# Wait for CSS transition to complete
time.sleep(0.5)

# Check opacity now
r = cdp('Runtime.evaluate', {'expression': 'getComputedStyle(document.getElementById("menu-overlay")).opacity', 'returnByValue': True}, 2)
print('opacity after 500ms:', r.get('result', {}).get('value'))

# Check if visible class still there
r = cdp('Runtime.evaluate', {'expression': 'document.getElementById("menu-overlay").className', 'returnByValue': True}, 3)
print('classes after 500ms:', r.get('result', {}).get('value'))

# Take a screenshot
r = cdp('Page.captureScreenshot', {'format': 'png', 'quality': 80}, 4)
if 'data' in r:
    img_data = base64.b64decode(r['data'])
    with open('/tmp/menu_screenshot.png', 'wb') as f:
        f.write(img_data)
    print('screenshot saved to /tmp/menu_screenshot.png, size:', len(img_data))
else:
    print('screenshot failed:', r)

sock.close()
