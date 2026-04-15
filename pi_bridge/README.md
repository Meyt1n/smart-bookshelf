# Raspberry Pi Bridge

这个目录是给树莓派单独拷贝用的本地桥接服务。

它对应的真实链路是：

`电脑 Flask 服务` -> `树莓派浏览器页面` -> `树莓派本地 bridge` -> `STM32 作为 I2C 主机轮询`

## 现在已经做好的部分

- 网页从电脑加载，但会请求树莓派本地 `http://127.0.0.1:8765`
- `POST /dispatch` 会把命令写进寄存器
- 电脑端页面在本地 dispatch 成功后，立即回电脑服务器做 `/api/motion/commit`
- 页面不等待 STM32 ack，符合你当前的演示逻辑

## 协议确认

你仓库里的 `vision_link.c / vision_link.h` 已经确认 STM32 端使用的是标准 8 位寄存器地址访问：

- `HAL_I2C_Mem_Read(..., I2C_MEMADD_SIZE_8BIT, ...)`
- `HAL_I2C_Mem_Write(..., I2C_MEMADD_SIZE_8BIT, ...)`

共享寄存器区如下：

- `reg0` -> `new_cmd_flag`
- `reg1` -> `cmd`
- `reg2` -> `floor_id`
- `reg3` -> `cell_id`
- `reg4` -> `ack`

因此树莓派从机侧按这个顺序 staging：

1. `reg1 = cmd`
2. `reg2 = floor_id`
3. `reg3 = cell_id`
4. `reg0 = 1`

STM32 侧会：

1. 读取 `reg0~reg3`
2. 处理任务
3. 写 `reg4 = ack`
4. 再写 `reg0 = 0`

## 后端模式

### 1. `memory`

默认模式。

只在本地维护一份 `reg0~reg4`，适合你现在在电脑上联调网页逻辑。

### 2. `pigpio_i2c`

实验性硬件模式。

这个模式会尝试使用 `pigpio` 的 BSC/I2C slave 能力，把寄存器映射真正暴露给 STM32。

它按“先写寄存器地址，再读/写数据”的寄存器指针协议处理主控访问，这正好匹配你 STM32 侧 `HAL_I2C_Mem_Read/Write` 的用法。

## 运行

### 默认 mock 模式

```bash
python bridge_server.py
```

### 启用 pigpio I2C 从机模式

先在树莓派上安装并启动 `pigpio` 守护进程，然后：

```bash
PI_BRIDGE_BACKEND=pigpio_i2c python bridge_server.py
```

如果需要显式指定从机地址：

```bash
PI_BRIDGE_BACKEND=pigpio_i2c PI_BRIDGE_SLAVE_ADDR=0x30 python bridge_server.py
```

## 自检

树莓派上可以先跑：

```bash
python self_check.py
```

这个脚本会输出一份 JSON，主要看：

- `pigpio.python_module`
- `pigpio.daemon_reachable`
- `http_probe.reachable`

如果 bridge 已经启动，还可以直接看：

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/diagnostics
curl http://127.0.0.1:8765/registers
```

完整的上板顺序可以直接看：

`DEPLOY_PI.md`

## 开机自启

目录里已经附了一个 systemd 模板：

`smart-bookshelf-pi-bridge.service`

你拷到树莓派后，按你的实际目录改一下里面的：

- `WorkingDirectory`
- `ExecStart`
- `User`

然后执行：

```bash
sudo cp smart-bookshelf-pi-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable smart-bookshelf-pi-bridge
sudo systemctl start smart-bookshelf-pi-bridge
sudo systemctl status smart-bookshelf-pi-bridge
```

如果要看运行日志：

```bash
journalctl -u smart-bookshelf-pi-bridge -f
tail -f /home/pi/smart_bookshelf/pi_bridge/runtime/bridge.log
```

如果你不想手改 service 文件，也可以直接运行：

```bash
sudo python install_service.py --write --enable --start
```

## 接口

### `GET /health`

返回服务状态、当前寄存器快照、当前后端类型。

### `GET /registers`

返回当前寄存器快照。

### `GET /diagnostics`

返回寄存器、后端状态、服务配置、日志路径等诊断信息。

### `POST /dispatch`

请求体：

```json
{
  "cmd": 1,
  "floor_id": 2,
  "cell_id": 3,
  "cid": 7,
  "title": "乡土中国"
}
```

成功后表示命令已经在树莓派本地 staging 完成，等待 STM32 轮询读取。

### `POST /reset`

调试时手动清空寄存器。

## 环境变量

- `PI_BRIDGE_HOST`
  默认 `127.0.0.1`
- `PI_BRIDGE_PORT`
  默认 `8765`
- `PI_BRIDGE_ALLOW_ORIGIN`
  默认 `*`
- `PI_BRIDGE_MIRROR_PATH`
  默认 `./runtime/registers.json`
- `PI_BRIDGE_BACKEND`
  默认 `memory`
- `PI_BRIDGE_SLAVE_ADDR`
  默认 `0x30`
- `PI_BRIDGE_LOG_PATH`
  默认 `./runtime/bridge.log`

## 说明

`pigpio_i2c` 这层我已经把软件结构接好了，但因为当前开发机不是树莓派，也没有装 `pigpio`，所以我这边做的是“可选启用的硬件后端”。真正上板时你需要在树莓派上验证：

- `pigpio` 是否可用
- 你的树莓派型号是否支持当前 BSC/I2C slave 方案
- 你们的接线、电平、上拉是否正确

如果你上树莓派后需要，我可以继续帮你把这一层再收紧到“开机即跑 + 实机自检 + 失败日志”。
