# 树莓派落地步骤

这份文档按“先跑通，再接硬件，再装服务”的顺序来。

## 1. 拷贝目录

把整个 `pi_bridge/` 目录拷到树莓派，例如：

`/home/pi/smart_bookshelf/pi_bridge`

## 2. 先做环境自检

进入目录后执行：

```bash
python3 self_check.py
```

你主要看这几项：

- `pigpio.python_module`
- `pigpio.daemon_reachable`
- `http_probe.reachable`

如果这一步里 `pigpio` 还是 `false`，说明你还没装好硬件后端环境，但不影响先用 `memory` 模式联调网页。

## 3. 先跑 memory 模式

```bash
python3 bridge_server.py
```

然后在树莓派本机开一个终端检查：

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/registers
```

如果这一步正常，说明：

- 树莓派本地 HTTP bridge 已经起来了
- 网页以后可以直接请求它

## 4. 用 smoke test 检查寄存器 staging

```bash
python3 smoke_test.py
```

它会自动做：

1. 读 `/health`
2. `POST /reset`
3. `POST /dispatch`
4. 轮询 `/registers`

如果你这时还没接 STM32，通常会看到：

- `reg0` 一直保持 `1`

这是正常的，说明命令已经成功 staging，但还没人来读。

如果已经接上 STM32，而且主控在轮询，你应该看到：

- `reg0` 最终变成 `0`

这表示 STM32 已经把命令取走了。

注意：

- `reg4 == 0` 既可能表示“还没写 ack”，也可能表示“成功 ack”
- 所以实机上最可靠的判断是看 `reg0` 有没有被 STM32 清回 `0`

## 5. 安装 pigpio 后端

如果你准备用真正的 I2C 从机模式，需要在树莓派上装好 `pigpio` 和对应 Python 绑定。

常见 Raspberry Pi OS 环境下可以按你们系统实际情况安装：

- `pigpio`
- `python3-pigpio`

然后启动守护进程，再跑：

```bash
PI_BRIDGE_BACKEND=pigpio_i2c PI_BRIDGE_SLAVE_ADDR=0x30 python3 bridge_server.py
```

再执行一次：

```bash
python3 self_check.py
python3 smoke_test.py
```

## 6. 安装为 systemd 服务

如果目录路径已经定好了，推荐直接用：

```bash
sudo python3 install_service.py --write --enable --start
```

它会按当前目录、当前 Python 路径、当前用户名生成 unit 文件。

如果你只想先看看生成内容：

```bash
python3 install_service.py
```

## 7. 查看服务状态和日志

```bash
systemctl status smart-bookshelf-pi-bridge
journalctl -u smart-bookshelf-pi-bridge -f
tail -f /home/pi/smart_bookshelf/pi_bridge/runtime/bridge.log
```

## 8. 实机联调时怎么判断哪一层有问题

### 网页点了没反应

先看：

```bash
curl http://127.0.0.1:8765/health
```

如果这里不通，问题在树莓派本地 bridge 没起来。

### 网页能调 `/dispatch`，但主控没动作

看：

```bash
curl http://127.0.0.1:8765/registers
```

如果 `reg0 == 1` 且一直不变，通常说明：

- 主控没轮询到树莓派
- I2C 从机硬件层没真正通
- 地址或接线不对

### `reg0` 能清零，但动作异常

这说明：

- 树莓派从机基本通了
- STM32 已经取到命令了

这时就去查 STM32 侧：

- `cmd`
- `floor_id`
- `cell_id`
- `ack`

## 9. 你现在最推荐的顺序

1. `self_check.py`
2. `bridge_server.py` memory 模式
3. `smoke_test.py`
4. 接上 STM32 看 `reg0` 是否清零
5. 再切 `pigpio_i2c`
6. 最后装 systemd
