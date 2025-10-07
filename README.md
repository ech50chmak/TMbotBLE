# TMbot — BLE GATT сервер для приёма «сетки плиток»

## Что это

Raspberry Pi 4 раздаёт BLE с именем `TMbot` и поднимает GATT‑сервис, чтобы сайт (через Web Bluetooth) отправлял «разметку плиток» — массив вида `grid[row][tile][x,y]`. Pi принимает данные chunk'ами, собирает, валидирует и отдаёт ACK/статусы через notify.

## Где лежит код и конфиги

- Скрипт сервера: `/opt/tmbot/tilebot_ble.py`
- Юнит systemd: `/etc/systemd/system/tilebot.service`
- Виртуальное окружение Python: `/home/user/tilebot-venv`

Имя BLE-устройства: `TMbot` (задаётся и в рекламе, и как alias адаптера).

## Архитектура BLE

- Service UUID: `12345678-1234-5678-1234-56789abc0000`
- TX (notify) UUID: `12345678-1234-5678-1234-56789abc0001`
  - Pi → сайт: отправляем уведомления об ACK/ошибках/статусах.
- RX (write / write without response) UUID: `12345678-1234-5678-1234-56789abc0002`
  - Сайт → Pi: отправка данных (фреймы begin/chunk/end или маленький JSON сразу).

Реклама BLE содержит `Complete Local Name = TMbot`.

## Простой протокол передачи

Чтобы обойти ограничение MTU BLE, используем фрагментацию:

1. Заголовок

   ```json
   {"type":"begin","msgId":"<uuid>","total":N,"bytes":M}
   ```

2. Фрагменты

   ```json
   {"type":"chunk","msgId":"<uuid>","seq":i,"data":"<base64>"}
   ```

3. Завершение

   ```json
   {"type":"end","msgId":"<uuid>"}
   ```

Ответы от Pi приходят через TX/notify:

```json
{"status":"begin-ack","msgId":"..."}
{"status":"chunk-ack","msgId":"...","seq":i}
{"ok":true,"msgId":"...","tiles":123}
{"ok":false,"error":"...","msgId":"..."}
```

## Установка зависимостей (один раз)

```bash
sudo apt update
sudo apt install -y bluez bluez-tools \
  python3-gi python3-gi-cairo gobject-introspection \
  libgirepository1.0-dev libglib2.0-dev libcairo2-dev \
  libdbus-1-dev pkg-config build-essential python3-venv

# venv
python3 -m venv /home/user/tilebot-venv
source /home/user/tilebot-venv/bin/activate
pip install --upgrade pip wheel setuptools
pip install bluezero
```

## Код сервера (ключевые моменты)

Файл: `/opt/tmbot/tilebot_ble.py`. Основные особенности:

- Имя устройства и alias адаптера жёстко проставляются в `TMbot`.
- Добавляется один GATT-сервис и две характеристики (RX/TX).
- В `rx_write_callback` собираем фрагменты, проверяем длины, парсим JSON.
- Место для интеграции с роботом — внутри ветки `ptype == 'end'` после сборки JSON:
  - `grid` — уже нормальный Python-список.
  - TODO: вызвать ваш обработчик, например: `drive_tiles(grid)`.

## Юнит systemd

Файл: `/etc/systemd/system/tilebot.service`

```ini
[Unit]
Description=TileBot BLE GATT Server
After=bluetooth.service
Wants=bluetooth.service

[Service]
ExecStart=/home/user/tilebot-venv/bin/python /opt/tmbot/tilebot_ble.py
WorkingDirectory=/opt/tmbot
User=user
Restart=on-failure
RestartSec=2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Команды управления:

- `sudo systemctl daemon-reload`
- `sudo systemctl enable --now tilebot`
- `sudo systemctl restart tilebot`
- `systemctl status tilebot -n 30`
- `sudo journalctl -u tilebot -f`

## Проверка, что реклама — именно TMbot

```bash
bluetoothctl show | egrep 'Name|Alias|Powered|Discoverable'
# Alias: TMbot

sudo timeout 3 btmon | grep -A4 "Complete Local Name"
# должно показать: Complete Local Name: TMbot
```

## Мини-клиент для сайта (Web Bluetooth)

Работает из HTTPS (или `http://localhost`). Рекомендуемые браузеры: Chrome/Edge (Android/desktop).

```html
<button id="bt">Connect to TMbot</button>
<script>
const SERVICE='12345678-1234-5678-1234-56789abc0000';
const TX='12345678-1234-5678-1234-56789abc0001';
const RX='12345678-1234-5678-1234-56789abc0002';
let rxChar, txChar;

async function connectBLE() {
  const device = await navigator.bluetooth.requestDevice({
    filters:[{ namePrefix:'TMbot' }],
    optionalServices:[SERVICE],
  });
  const server = await device.gatt.connect();
  const service = await server.getPrimaryService(SERVICE);
  txChar = await service.getCharacteristic(TX);
  rxChar = await service.getCharacteristic(RX);
  await txChar.startNotifications();
  txChar.addEventListener('characteristicvaluechanged', e => {
    console.log('TMbot notify:', new TextDecoder().decode(e.target.value));
  });
  console.log('Connected to', device.name);
}

async function sendGrid(grid) {
  const enc = new TextEncoder();
  const raw = enc.encode(JSON.stringify(grid));
  const CHUNK = 180; // безопасный размер
  const total = Math.ceil(raw.length/CHUNK);
  const msgId = crypto.randomUUID();

  await rxChar.writeValueWithoutResponse(enc.encode(JSON.stringify({type:'begin',msgId,total,bytes:raw.length})));
  for (let i=0;i<total;i++){
    const part = raw.slice(i*CHUNK,(i+1)*CHUNK);
    const b64 = btoa(String.fromCharCode(...part));
    await rxChar.writeValueWithoutResponse(enc.encode(JSON.stringify({type:'chunk',msgId,seq:i,data:b64})));
  }
  await rxChar.writeValueWithoutResponse(enc.encode(JSON.stringify({type:'end',msgId})));
}

document.getElementById('bt').onclick = async ()=>{
  await connectBLE();
  // пример
  const grid = [[[0,0],[1,0]], [[0,1],[1,1]]];
  await sendGrid(grid);
};
</script>
```

## Типовые проблемы и решения

- Сайт не видит устройство — включить Bluetooth и геолокацию; проверить, что реклама идёт (Advertisement registered в логах).
- Имя в списке не `TMbot` — «забыть» старое устройство на телефоне, перезапустить Bluetooth, убедиться в `bluetoothctl show` (Alias: TMbot) и в `btmon` (Complete Local Name).
- `pip` ругается на `pycairo`/`pygobject` — используем системные пакеты `python3-gi`, `python3-gi-cairo` и venv с `--system-site-packages`, а `bluezero` ставим в venv.

## Быстрый чек-лист для нового развёртывания

1. Установить системные пакеты (см. раздел «Установка зависимостей»).
2. Создать venv в `/home/user/tilebot-venv` и поставить `bluezero`.
3. Положить `tilebot_ble.py` в `/opt/tmbot/`, сделать исполняемым, владельца `user:user`.
4. Создать/проверить unit-файл `tilebot.service` (пути и `User=user`).
5. `sudo systemctl daemon-reload && sudo systemctl enable --now tilebot`
6. Убедиться в логах: Advertising as TMbot, затем проверить `btmon` (Complete Local Name: TMbot).
7. Подключиться со страницы и отправить тестовую сетку.

