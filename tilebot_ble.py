#!/usr/bin/env python3
# TMbot: BLE GATT сервер для приёма «сетки плиток»

import json, base64
from gi.repository import GLib
from bluezero import adapter, peripheral

DEVICE_NAME = 'TMbot'
SERVICE_UUID = '12345678-1234-5678-1234-56789abc0000'
TX_UUID      = '12345678-1234-5678-1234-56789abc0001'  # notify
RX_UUID      = '12345678-1234-5678-1234-56789abc0002'  # write/wwr

inflight = {}
_tx_obj = None

def notify_callback(notifying, characteristic):
    global _tx_obj
    _tx_obj = characteristic if notifying else None

def _send_notify(obj: dict):
    if _tx_obj:
        data = json.dumps(obj).encode('utf-8')
        _tx_obj.set_value(list(data))  # triggers notify

def rx_write_callback(value, options):
    try:
        raw = bytes(value)
        payload = json.loads(raw.decode('utf-8'))
        ptype = payload.get('type')
        mid = payload.get('msgId')

        if ptype == 'begin':
            inflight[mid] = {'total': int(payload['total']),
                             'bytes': int(payload['bytes']),
                             'chunks': {}, 'received': 0}
            _send_notify({'status': 'begin-ack', 'msgId': mid})
            return

        if ptype == 'chunk':
            c = inflight.get(mid)
            if not c:
                _send_notify({'ok': False, 'error': 'unknown msgId', 'msgId': mid})
                return
            seq = int(payload['seq'])
            data = base64.b64decode(payload['data'])
            if seq not in c['chunks']:
                c['chunks'][seq] = data
                c['received'] += len(data)
            _send_notify({'status': 'chunk-ack', 'msgId': mid, 'seq': seq})
            return

        if ptype == 'end':
            c = inflight.get(mid)
            if not c:
                _send_notify({'ok': False, 'error': 'unknown msgId', 'msgId': mid})
                return
            ok = (len(c['chunks']) == c['total'] and c['received'] == c['bytes'])
            if not ok:
                _send_notify({'ok': False, 'error': 'incomplete', 'msgId': mid})
                inflight.pop(mid, None)
                return
            assembled = b''.join(c['chunks'][i] for i in range(c['total']))
            try:
                grid = json.loads(assembled.decode('utf-8'))
                tiles = sum(len(row) for row in grid)
                print(f"[TMbot] Принято плиток: {tiles}")
                _send_notify({'ok': True, 'msgId': mid, 'tiles': tiles})
            except Exception as e:
                _send_notify({'ok': False, 'error': f'bad JSON: {e}', 'msgId': mid})
            finally:
                inflight.pop(mid, None)
            return

        # no 'type' => возможно, это маленький grid "одним куском"
        if isinstance(payload, list):
            tiles = sum(len(r) for r in payload)
            print(f"[TMbot] one-shot tiles={tiles}")
            _send_notify({'ok': True, 'tiles': tiles})
            return

        print("[TMbot] Unknown payload:", payload)

    except Exception as e:
        print("[TMbot][ERR]", e)

def main():
    addr = list(adapter.Adapter.available())[0].address
    adp = adapter.Adapter(addr)

    # Жёстко задаём alias адаптера — иногда влияет на рекламируемое имя
    try:
        adp.alias = DEVICE_NAME
    except Exception as e:
        print("[TMbot] WARN: can't set adapter alias:", e)

    dev = peripheral.Peripheral(addr, local_name=DEVICE_NAME)
    try:
        dev.local_name = DEVICE_NAME
    except Exception:
        pass

    dev.add_service(srv_id=1, uuid=SERVICE_UUID, primary=True)
    dev.add_characteristic(srv_id=1, chr_id=1, uuid=RX_UUID,
                           value=[], notifying=False,
                           flags=['write','write-without-response'],
                           write_callback=rx_write_callback)
    dev.add_characteristic(srv_id=1, chr_id=2, uuid=TX_UUID,
                           value=[], notifying=False,
                           flags=['notify'],
                           notify_callback=notify_callback)

    print(f"[TMbot] Adapter addr: {addr}")
    try: print(f"[TMbot] Adapter alias: {adp.alias}")
    except Exception: pass
    try: print(f"[TMbot] Peripheral local_name: {getattr(dev,'local_name','(n/a)')}")
    except Exception: pass

    print(f"[TMbot] Advertising as {DEVICE_NAME}")
    dev.publish()

if __name__ == '__main__':
    print("[TMbot] BLE GATT server starting...")
    main()
