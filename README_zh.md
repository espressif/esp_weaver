# ESP-Weaver Integration

[English](README.md) | 简体中文

ESP-Weaver 是一个用于 Home Assistant 的集成组件，让您能够在 Home Assistant 中控制和管理基于 Espressif (ESP) 芯片（ESP32、ESP32-S3、ESP32-C 系列）的 IoT 智能设备。支持**本地控制**（局域网内直接通信）和**云端控制**（通过 ESP RainMaker 云服务）两种模式。

> [!NOTE]
> "Weaver" 取自织巢鸟——一种能将零散草叶编织成精密巢穴的鸟类。ESP-Weaver 的理念与此相似：将网络中分散的 ESP 设备整合为一体，构建成 Home Assistant 中紧密互联的智能家居系统。

## 目录

- [功能特性](#功能特性)
- [安装](#安装)
- [设备固件](#设备固件)
- [设备配置](#设备配置)
- [架构设计](#架构设计)
- [参考文档](#参考文档)

## 功能特性

### 本地控制

- **本地通信**：全部数据交互在局域网内完成
- **自动发现**：基于 mDNS/Zeroconf 协议自动发现并识别网络内的 ESP 设备
- **实时同步**：采用 HTTP 长连接机制，支持设备状态的实时推送
- **安全传输**：支持 ESP Local Control Sec0/Sec1 加密协议
- **自动重连**：具备设备在线状态检测与断线自动恢复能力

### 云端控制（ESP RainMaker）

- **云端通信**：通过 ESP RainMaker 云服务控制设备
- **账号登录**：支持 ESP RainMaker 账号登录（全球区域；中国区域计划中）
- **BLE 配网**：通过 BLE 为新设备配置 WiFi 凭据并完成配网
- **导入已有设备**：添加已注册在 RainMaker 账号中的设备
- **在线状态检测**：定期轮询设备连接状态，离线/上线自动切换可用性
- **CLI 自动安装**：首次使用时自动安装 `esp-rainmaker-cli`

### 通用

- **实体支持**：支持 Light、Sensor、Binary Sensor、Number 等多种实体类型
- **双模式**：每个设备可在配置时独立选择本地控制或云端控制模式

### 支持的实体类型

集成根据设备上报的配置信息自动创建对应的 Home Assistant 实体：

| 实体类型 | 功能描述 |
|----------|----------|
| **Light** | 开关控制、亮度调节、色调/饱和度、色温、灯光效果 |
| **Sensor** | 温度、湿度、气压等环境传感数据 |
| **Binary Sensor** | 门窗状态、运动检测、震动检测、触摸感应 |
| **Number** | 可调节阈值参数 |
| **Battery Energy** | 电池电量、电压、温度、充电状态 |
| **IMU Gesture** | IMU 手势识别（抛掷、翻转、摇晃、旋转等） |
| **Interactive Input** | 交互输入事件与数值 |
| **Low Power Sleep** | 低功耗睡眠状态、唤醒原因、唤醒窗口 |

## 安装

> **环境要求**：
> - Home Assistant Core ≥ 2025.12.5
> - Python ≥ 3.13.2

### 方式一：HACS 安装（推荐）

1. 确保已安装 [HACS](https://hacs.xyz/)
2. 点击左侧边栏 **HACS**，进入 Home Assistant Community Store
3. 点击右上角菜单（⋮）→ **自定义存储库**
4. 在弹出窗口中：
   - **存储库**：输入 `https://github.com/espressif/esp_weaver`
   - **类型**：选择 **集成**
5. 点击 **添加**
6. 返回 HACS 主页，搜索 **ESP-Weaver** 并点击 **下载**
7. 重启 Home Assistant

### 方式二：脚本安装

适用于可通过 SSH/终端访问 Home Assistant 的用户。

```bash
# 克隆仓库
git clone https://github.com/espressif/esp_weaver.git
cd esp_weaver

# 运行安装脚本（将 /config 替换为你的 HA 配置路径）
./install.sh /config
```

常用配置路径：
| 安装类型 | 配置路径 |
|----------|----------|
| Home Assistant OS | `/config` |
| Home Assistant Core | `~/.homeassistant` |
| Home Assistant Supervised | `/usr/share/hassio/homeassistant` |

安装完成后，重启 Home Assistant。

### 方式三：Samba 共享（Home Assistant OS/Supervised）

适用于 Home Assistant OS 或 Supervised 安装环境。

#### 步骤 1：安装 Samba Share 插件

1. 进入 **设置** → **加载项** → **加载项商店**
2. 搜索 `Samba share` 并安装
3. 在插件配置中设置用户名和密码
4. 启动插件

#### 步骤 2：连接 Samba 共享

在文件管理器中输入以下地址：

| 操作系统 | 连接地址 |
|:---------|:---------|
| Windows | `\\homeassistant.local` 或 `\\<IP地址>` |
| macOS | `smb://homeassistant.local/config` 或 `smb://<IP地址>/config` |
| Linux | `smb://homeassistant.local/config` 或 `smb://<IP地址>/config` |

连接时需要输入 Samba Share 插件中设置的**用户名**和**密码**。

> [!TIP]
> - 如果 `homeassistant.local` 无法连接，请改用 Home Assistant 的 IP 地址
> - IP 地址可在 **设置** → **系统** → **网络** 或路由器管理页面中查看
> - **macOS 用户**：在 Finder 中按 `⌘+K` 打开"连接服务器"
> - **Linux 用户**：在文件管理器中按 `Ctrl+L` 显示地址栏，或点击侧边栏的"Other Locations"

#### 步骤 3：复制集成文件

1. 克隆仓库：`git clone https://github.com/espressif/esp_weaver.git`
2. 将仓库中的 `esp_weaver/custom_components` 文件夹复制到 Samba 共享的 `config` 文件夹下

最终目录结构：

```
config/
└── custom_components/
    └── esp_weaver/
        ├── __init__.py
        ├── manifest.json
        └── ...
```

#### 步骤 4：重启 Home Assistant

## 设备固件

### 本地控制固件

本地控制模式需要设备运行支持 ESP Weaver 本地控制协议的固件。设备端 SDK 组件（`esp_weaver`）和示例固件位于 [esp-iot-solution](https://github.com/espressif/esp-iot-solution) 仓库中。

#### 支持的芯片（本地控制）

| ESP32 | ESP32-C2 | ESP32-C3 | ESP32-C5 | ESP32-C6 | ESP32-C61 | ESP32-S3 |
| ----- | -------- | -------- | -------- | -------- | --------- | -------- |
|   ✓   |    ✓     |    ✓     |    ✓     |    ✓     |     ✓     |    ✓     |

#### 获取固件

```bash
# 克隆仓库
git clone https://github.com/espressif/esp-iot-solution.git
cd esp-iot-solution/examples/weaver
```

#### 可用示例

仓库提供了开箱即用的示例项目：

| 示例 | 描述 |
|------|------|
| `led_light` | 智能灯光设备，支持电源开关、亮度调节、HSV 颜色控制、色温调节 |
| `imu_gesture` | IMU 手势传感器，支持多种手势识别（翻转、摇晃、旋转、拍手等） |

#### 编译与烧录

请参阅 [ESP Weaver 组件 README](https://github.com/espressif/esp-iot-solution/tree/master/components/esp_weaver) 及各示例目录中的 README 了解详细的编译和烧录步骤。

> [!TIP]
> 详细的编译说明和设备特定配置，请参阅各示例目录中的 README 文件。

### 云端控制固件（ESP RainMaker）

云端控制模式需要设备运行官方 ESP RainMaker 固件。参考固件为 [esp-rainmaker](https://github.com/espressif/esp-rainmaker) 仓库中的 `led_light` 示例。

#### 支持的芯片（云端控制）

| ESP32 | ESP32-C2 | ESP32-C3 | ESP32-C5 | ESP32-C6 | ESP32-C61 | ESP32-S3 |
| ----- | -------- | -------- | -------- | -------- | --------- | -------- |
|   -   |    -     |    ✓     |    ✓     |    ✓     |     -     |    ✓     |

> [!NOTE]
> 云端控制目前支持 ESP32-C3、ESP32-C5、ESP32-C6 和 ESP32-S3。ESP32、ESP32-C2 和 ESP32-C61 的支持将在后续版本中添加。

#### 获取固件

```bash
# 克隆仓库
git clone https://github.com/espressif/esp-rainmaker.git
cd esp-rainmaker/examples/led_light
```

#### 编译与烧录

请参阅 [ESP RainMaker LED Light 示例](https://github.com/espressif/esp-rainmaker/tree/master/examples/led_light) README 了解详细的编译和烧录步骤。

## 设备配置

### 添加设备

1. 进入 **设置** → **设备与服务** → **添加集成**
2. 搜索并选择 **ESP-Weaver**
3. 选择连接模式：**本地控制** 或 **云端控制**

### 本地控制配置

1. 集成会通过 mDNS 自动发现局域网内的 ESP 设备
2. 从已发现的设备列表中选择目标设备
3. 若设备启用安全模式（Sec1），需输入 PoP 凭据

### 云端控制配置（ESP RainMaker）

1. 选择 **Cloud Control (ESP RainMaker)** 连接模式
2. 选择区域（目前仅支持全球），使用 RainMaker 账号登录
3. 选择操作：
   - **添加已有设备**：选择已注册在 RainMaker 账号中的设备
   - **配网新设备**：输入 BLE 设备名称（如串口日志中的 `PROV_XXXXXX`），配置 WiFi 凭据和 PoP（Proof of Possession），集成将自动完成配网和注册

> [!IMPORTANT]
> **配网新设备**需要运行 Home Assistant 的主机（PC、树莓派等）具备蓝牙（BLE）功能。添加已有设备不需要此条件。

配置完成后，设备实体将自动注册至 Home Assistant。

## 架构设计

本集成采用分层架构，遵循单一职责原则：

```
┌─────────────────────────────────────────────────────────────┐
│                    Home Assistant Core                       │
├─────────────────────────────────────────────────────────────┤
│  平台: light.py, sensor.py, binary_sensor.py, ...           │
├─────────────────────────────────────────────────────────────┤
│  coordinator.py (ESPDataUpdateCoordinator)                  │
│  └── 数据更新、可用性检查、重连逻辑                          │
├─────────────────────────────────────────────────────────────┤
│  iot/client/device_api.py (ESPWeaverApi)                    │
│  └── 主 API 协调器，委托到各管理器                           │
├──────────────────────┬──────────────────────────────────────┤
│  本地控制路径         │  云端控制路径                         │
├──────────────────────┼──────────────────────────────────────┤
│  iot/client/client.py│  iot/cloud/rmaker_cli_client.py      │
│  (ESPLocalCtrlClient)│  (RainMakerCLIClient)                │
│  └── ESP Local Ctrl  │  └── ESP RainMaker CLI               │
├──────────────────────┴──────────────────────────────────────┤
│  iot/managers/ (管理器类)                                   │
│  ├── DeviceRegistry      - 设备状态注册表                    │
│  ├── AvailabilityManager - 可用性检测                        │
│  ├── ConnectionManager   - 连接生命周期                      │
│  ├── PropertyManager     - 属性读写和消息处理                │
│  └── DeviceDiscoveryManager - 实体发现                       │
└─────────────────────────────────────────────────────────────┘
```

### 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| **Coordinator** | `coordinator.py` | 数据轮询、可用性检查、连接监控 |
| **Entity Base** | `entity.py` | 实体基类和可用性混入 |
| **ESPWeaverApi** | `iot/client/device_api.py` | 顶层 API 协调器 |
| **DeviceRegistry** | `iot/managers/device_registry.py` | 集中管理设备状态 |
| **ConnectionManager** | `iot/managers/connection_manager.py` | 连接建立、重连逻辑 |
| **PropertyManager** | `iot/managers/property_manager.py` | 属性操作、事件分发 |
| **DeviceDiscoveryManager** | `iot/managers/entity_discovery.py` | 配置解析、实体发现 |
| **ESPLocalCtrlClient** | `iot/client/client.py` | 协议层本地设备通信 |
| **RainMakerCLIClient** | `iot/cloud/rmaker_cli_client.py` | ESP RainMaker 云端通信（通过 CLI） |


## 参考文档

| 资源 | 链接 |
|------|------|
| ESP Weaver SDK | [esp-iot-solution/components/esp_weaver](https://github.com/espressif/esp-iot-solution/tree/master/components/esp_weaver) |
| ESP RainMaker | [github.com/espressif/esp-rainmaker](https://github.com/espressif/esp-rainmaker) |
| ESP Local Control | [ESP-IDF 编程指南](https://docs.espressif.com/projects/esp-idf/zh_CN/latest/esp32/api-reference/protocols/esp_local_ctrl.html) |
| Home Assistant 开发 | [developers.home-assistant.io](https://developers.home-assistant.io/) |

