import socket, base64, json, struct, os, sys

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
assert b'101' in resp, 'WS handshake failed: ' + repr(resp)

def ws_send(data):
    data = data.encode()
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    hdr = b'\x81'
    if len(data) < 126:
        hdr += bytes([0x80 | len(data)])
    elif len(data) < 65536:
        hdr += b'\xfe' + struct.pack('>H', len(data))
    sock.sendall(hdr + mask + masked)

def ws_recv():
    hdr = sock.recv(2)
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack('>H', sock.recv(2))[0]
    data = b''
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            break
        data += chunk
    return data.decode(errors='replace')

def evaluate(expr, mid):
    ws_send(json.dumps({'id': mid, 'method': 'Runtime.evaluate',
                        'params': {'expression': expr, 'returnByValue': True}}))
    for _ in range(50):
        try:
            obj = json.loads(ws_recv())
        except Exception:
            continue
        if obj.get('id') == mid:
            res = obj.get('result', {}).get('result', {})
            exc = obj.get('result', {}).get('exceptionDetails')
            if exc:
                return 'EXCEPTION: ' + exc.get('text', str(exc))
            return res.get('value', str(res))
    return 'timeout'

tests = [
    (1, 'typeof menuVisible', 'typeof menuVisible'),
    (2, 'menuVisible value', 'String(menuVisible)'),
    (3, 'typeof showMenu', 'typeof showMenu'),
    (4, 'typeof handleStageInteraction', 'typeof handleStageInteraction'),
    (5, 'call showMenu()', '(function(){try{showMenu();return "ok menuVisible="+menuVisible}catch(e){return "ERR:"+String(e)}})()'),
    (6, 'overlay classList', 'document.getElementById("menu-overlay").className'),
    (7, 'overlay computed opacity', 'getComputedStyle(document.getElementById("menu-overlay")).opacity'),
    (8, 'touch listeners count', '(function(){var c=0;var orig=EventTarget.prototype.addEventListener;return document.eventListenerCount || "n/a"})()'),
    (9, 'stage element exists', 'String(!!document.getElementById("stage")) + " " + String(!!document.getElementById("menu-overlay"))'),
]

for mid, label, expr in tests:
    result = evaluate(expr, mid)
    print(f'{label}: {result}')

sock.close()
