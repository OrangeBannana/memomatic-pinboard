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

# Reset
cdp('Runtime.evaluate', {'expression': 'hideMenu(); window._hcount=0;', 'returnByValue': True}, 1)

# Wrap handleStageInteraction to count calls
wrap_code = '''
(function() {
  var orig = handleStageInteraction;
  window._hcount = 0;
  window._hcountWrapped = true;
  handleStageInteraction = function(e) {
    window._hcount++;
    window._lastEventType = e.type;
    return orig.call(this, e);
  };
  return "wrapped";
})()
'''
r = cdp('Runtime.evaluate', {'expression': wrap_code, 'returnByValue': True}, 2)
print('wrap result:', r.get('result', {}).get('value'))

# Check if listeners were re-registered (they won't be - they point to the old function)
# Instead, dispatch a raw JS event and see if the WRAPPER catches it
js_click = '''
(function() {
  var e = new MouseEvent("click", {bubbles:true, cancelable:true, clientX:160, clientY:160});
  document.dispatchEvent(e);
  return "dispatched";
})()
'''
r = cdp('Runtime.evaluate', {'expression': js_click, 'returnByValue': True}, 3)
print('JS click dispatch:', r.get('result', {}).get('value'))

time.sleep(0.1)
r = cdp('Runtime.evaluate', {'expression': 'String(window._hcount) + " evtype=" + String(window._lastEventType) + " mv=" + String(menuVisible)', 'returnByValue': True}, 4)
print('after JS click:', r.get('result', {}).get('value'))

# Now reset and try CDP Input event
cdp('Runtime.evaluate', {'expression': 'hideMenu(); window._hcount=0;', 'returnByValue': True}, 5)

r = cdp('Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': 160, 'y': 160, 'button': 'left', 'clickCount': 1}, 6)
print('CDP mousePressed:', r)
time.sleep(0.05)
r = cdp('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': 160, 'y': 160, 'button': 'left', 'clickCount': 1}, 7)
print('CDP mouseReleased:', r)

time.sleep(0.2)
r = cdp('Runtime.evaluate', {'expression': 'String(window._hcount) + " evtype=" + String(window._lastEventType) + " mv=" + String(menuVisible)', 'returnByValue': True}, 8)
print('after CDP click:', r.get('result', {}).get('value'))

# Also check what event types fire on document for CDP input
cdp('Runtime.evaluate', {'expression': 'hideMenu(); window._hcount=0; window._allEvents=[];', 'returnByValue': True}, 9)

add_listener = '''
(function() {
  var types = ["mousedown","mouseup","click","pointerdown","pointerup","touchstart","touchend"];
  types.forEach(function(t) {
    document.addEventListener(t, function(e) {
      window._allEvents.push(e.type);
    }, {capture:true});
  });
  return "listeners added for: " + types.join(",");
})()
'''
r = cdp('Runtime.evaluate', {'expression': add_listener, 'returnByValue': True}, 10)
print('listener setup:', r.get('result', {}).get('value'))

r = cdp('Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': 160, 'y': 160, 'button': 'left', 'clickCount': 1}, 11)
time.sleep(0.05)
r = cdp('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': 160, 'y': 160, 'button': 'left', 'clickCount': 1}, 12)
time.sleep(0.2)

r = cdp('Runtime.evaluate', {'expression': 'JSON.stringify(window._allEvents)', 'returnByValue': True}, 13)
print('all events fired by CDP input:', r.get('result', {}).get('value'))

sock.close()
