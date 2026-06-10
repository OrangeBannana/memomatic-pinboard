import socket, base64, json, struct, os, sys, time, subprocess

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

# Reset menu
r = cdp('Runtime.evaluate', {'expression': 'hideMenu(); String(menuVisible)', 'returnByValue': True}, 1)
print('hideMenu:', r.get('result', {}).get('value'))

env = os.environ.copy()
env['DISPLAY'] = ':0'
env['XAUTHORITY'] = '/root/.Xauthority'

# Move cursor to center and click
result = subprocess.run(['xdotool', 'mousemove', '240', '160'], env=env, capture_output=True)
print('mousemove:', result.returncode, result.stderr.decode())
time.sleep(0.1)
result = subprocess.run(['xdotool', 'click', '1'], env=env, capture_output=True)
print('click:', result.returncode, result.stderr.decode())

time.sleep(0.5)
r = cdp('Runtime.evaluate', {'expression': 'String(menuVisible)', 'returnByValue': True}, 2)
print('menuVisible after xdotool click:', r.get('result', {}).get('value'))

# Screenshot
r = cdp('Page.captureScreenshot', {'format': 'png', 'quality': 80}, 3)
if 'data' in r:
    with open('/tmp/xdotool_test.png', 'wb') as f:
        f.write(base64.b64decode(r['data']))
    print('screenshot saved')
else:
    print('screenshot failed')

sock.close()
