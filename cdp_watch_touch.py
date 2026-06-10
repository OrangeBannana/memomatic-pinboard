"""Monitor menuVisible state and add touch event listener for 15 seconds."""
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
    sock.settimeout(0.3)
    try:
        hdr = sock.recv(2)
    except socket.timeout:
        return None
    if len(hdr) < 2:
        return None
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

def cdp(method, params, mid, timeout=3):
    ws_send(json.dumps({'id': mid, 'method': method, 'params': params}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = ws_recv_raw()
        if raw is None:
            continue
        try:
            obj = json.loads(raw.decode(errors='replace'))
        except Exception:
            continue
        if obj.get('id') == mid:
            return obj.get('result', {})
    return {}

# Add event counter
setup = '''
window._touchEvents = [];
var _orig = handleStageInteraction;
document.removeEventListener('touchstart', _orig, {capture:true});
document.removeEventListener('pointerdown', _orig, {capture:true});
document.removeEventListener('click', _orig, {capture:true});
function _tracked(e) {
  window._touchEvents.push({type:e.type, t:Date.now(), x:e.clientX||0, y:e.clientY||0});
  _orig.call(this, e);
}
document.addEventListener('touchstart', _tracked, {capture:true, passive:false});
document.addEventListener('pointerdown', _tracked, {capture:true});
document.addEventListener('click', _tracked, {capture:true});
"setup ok"
'''
r = cdp('Runtime.evaluate', {'expression': setup, 'returnByValue': True}, 1)
print('setup:', r.get('result', {}).get('value'))

# Reset menu
cdp('Runtime.evaluate', {'expression': 'hideMenu()', 'returnByValue': True}, 2)
print('Touch the screen now! Monitoring for 15 seconds...')
sys.stdout.flush()

start = time.time()
mid = 10
prev_mv = None
prev_count = 0

while time.time() - start < 15:
    r = cdp('Runtime.evaluate', {
        'expression': 'JSON.stringify({mv:menuVisible, ec:window._touchEvents.length, last:window._touchEvents.slice(-3)})',
        'returnByValue': True
    }, mid)
    mid += 1
    val = r.get('result', {}).get('value')
    if val:
        try:
            data = json.loads(val)
            mv = data['mv']
            ec = data['ec']
            if mv != prev_mv or ec != prev_count:
                print(f't={time.time()-start:.1f}s menuVisible={mv} eventCount={ec} last={data["last"]}')
                sys.stdout.flush()
                prev_mv = mv
                prev_count = ec
        except Exception:
            pass
    time.sleep(0.2)

# Final state
r = cdp('Runtime.evaluate', {
    'expression': 'JSON.stringify({mv:menuVisible, events:window._touchEvents})',
    'returnByValue': True
}, mid)
result = r.get('result', {}).get('value')
if result:
    data = json.loads(result)
    print(f'\nFinal: menuVisible={data["mv"]}, total events: {len(data["events"])}')
    for ev in data['events'][:10]:
        print(f'  {ev}')

sock.close()
