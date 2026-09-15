# ESP-Weaver Integration

[English](README.md) | 简体中文

ESP-Weaver 是面向 Home Assistant 的集成组件，用于在 Home Assistant 中控制和管理基于乐鑫（Espressif）ESP32、ESP32-S3、ESP32-C 系列芯片的 IoT 智能设备。设备通过 ESP 本地控制协议在局域网内直接接入。

> [!NOTE]
> 「Weaver」取自织巢鸟——一种能将零散草叶编织成精密巢穴的鸟类。ESP-Weaver 的设计理念与此相似：将网络中分散的 ESP 设备整合为一体，在 Home Assistant 中构建紧密互联的智能家居系统。

## 目录

- [功能特性](#功能特性)
- [安装](#安装)
- [设备固件](#设备固件)
- [设备配置](#设备配置)
- [设备 Profile](#设备-profile)
- [已知限制](#已知限制)
- [故障排查](#故障排查)
- [架构设计](#架构设计)
- [参考文档](#参考文档)

## 功能特性

### 本地控制

- **本地通信**：全部数据交互在局域网内完成
- **自动发现**：基于 mDNS/Zeroconf 协议自动发现并识别网络内的 ESP 设备
- **状态轮询**：每 60 秒经 HTTP 读取一次设备状态，写入出错时立即再读一次；固件不会向 Home Assistant 主动推送
- **安全模式**：支持 ESP Local Control Sec0（不加密）、Sec1（PoP + AES-CTR）与 Sec2（SRP6a + AES-GCM）
- **自动重连**：具备设备在线状态检测与断线自动恢复能力

### 通用

- **实体支持**：覆盖 25 个 Home Assistant 平台，从灯与传感器到扫地机与安防面板

> [!NOTE]
> 本版本仅开放**本地控制**。仓库中仍保留 RainMaker 云端控制相关代码，但 UI
> 不提供该选项（`CLOUD_CONTROL_ENABLED` 为关）。

### 支持的实体类型

集成根据设备上报的配置信息自动创建对应的 Home Assistant 实体：

| 平台 | 典型设备与参数 |
|------|----------------|
| **light** | 开关、亮度、色调/饱和度、色温、灯光效果 |
| **switch** | 开关、插座、插头 |
| **sensor** | 温湿度、气压、电量、IMU 手势、交互输入 |
| **binary_sensor** | 门窗状态、运动、震动、触摸 |
| **number** | 可调阈值与上报间隔 |
| **select** | 取值来自固定候选列表的参数 |
| **text** | 自由文本参数，例如时区 |
| **button** | 触发一次动作的参数，例如重启 |
| **lock** | 门锁，以及车库门上的锁 |
| **cover** | 按位置控制的窗帘与车库门 |
| **fan** | 风速、风向、摇头 |
| **climate** | 空调与温控器 |
| **media_player** | 音箱与电视 |
| **humidifier** | 目标湿度、模式、实测湿度 |
| **water_heater** | 目标水温、运行模式 |
| **valve** | 开、关，或一个开度 |
| **siren** | 鸣响与静音 |
| **vacuum** | 工作状态、吸力档位、电量 |
| **lawn_mower** | 割草、暂停、回坞 |
| **remote** | 电源与当前情景 |
| **alarm_control_panel** | 布防状态与各类报警探测 |
| **event** | 一次性事件，例如按键 |
| **date**、**time**、**datetime** | 由参数结构决定的时间设定点 |

设备生成哪些实体，由添加时为它选定的产品 profile 决定。乐鑫内置了 RainMaker 标准物
模型与 ESP-Weaver 扩展类型的 profile；其他产品的 profile 放入 `<配置目录>/esp_weaver/`
或发布到 profile 仓库即可选用。

## 安装

> **环境要求**：
> - Home Assistant ≥ 2025.12.5（指核心版本；OS、Supervised、Container、Core 各安装方式均可）
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

### 方式二：Samba 共享（Home Assistant OS/Supervised）

适用于树莓派等 Home Assistant OS 或 Supervised 安装环境。此类环境没有常规终端，Samba 是
最直接的文件访问方式。

#### 步骤 1：安装 Samba Share 加载项

1. 进入 **设置** → **加载项** → **加载项商店**
2. 搜索 `Samba share` 并安装
3. 在加载项配置中设置用户名和密码，两者不可留空
4. 启动加载项

#### 步骤 2：连接 Samba 共享

在文件管理器中输入以下地址：

| 操作系统 | 连接地址 |
|:---------|:---------|
| Windows | `\\homeassistant.local` 或 `\\<IP 地址>` |
| macOS | `smb://homeassistant.local/config` 或 `smb://<IP 地址>/config` |
| Linux | `smb://homeassistant.local/config` 或 `smb://<IP 地址>/config` |

连接时需输入 Samba Share 加载项中设置的**用户名**和**密码**。

> [!TIP]
> - 若 `homeassistant.local` 无法连接，请改用 Home Assistant 的 IP 地址
> - IP 地址可在 **设置** → **系统** → **网络** 或路由器管理页面中查看
> - **macOS**：在 Finder 中按 `⌘+K` 打开「连接服务器」
> - **Linux**：在文件管理器中按 `Ctrl+L` 显示地址栏，或点击侧边栏的「其他位置」

#### 步骤 3：复制集成文件

以下两步在**自己的电脑**上完成，再经 Samba 共享把文件拷入 Home Assistant：

1. 克隆仓库：`git clone https://github.com/espressif/esp_weaver.git`
2. 将仓库中的 `custom_components/esp_weaver` 目录复制到 Samba 共享 `config` 下的
   `custom_components/`，该目录不存在时新建

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

### 方式三：脚本安装（Core/Container，或已安装 SSH 加载项）

适用于可通过 SSH 或终端访问配置目录的环境。Home Assistant OS 默认不提供常规终端，请改用
方式一或方式二。

```bash
# 克隆仓库
git clone https://github.com/espressif/esp_weaver.git
cd esp_weaver

# 运行安装脚本，将 /config 替换为实际的 Home Assistant 配置路径
./install.sh /config
```

常用配置路径：
| 安装类型 | 配置路径 |
|----------|----------|
| Home Assistant Core | `~/.homeassistant` |
| Home Assistant Container | 宿主机上映射到容器 `/config` 的目录 |
| Home Assistant Supervised | `/usr/share/hassio/homeassistant` |
| Home Assistant OS（需 SSH 加载项） | `/config` |

安装完成后，重启 Home Assistant。

### 卸载集成

1. 进入 **设置** → **设备与服务** → **ESP-Weaver**
2. 对每个设备，打开其菜单（⋮）并选择 **删除**
3. 删除配置目录下的 `custom_components/esp_weaver`（或在 HACS 中移除）
4. 重启 Home Assistant

你放在 `<config>/esp_weaver/` 中的 profile 不会被删除；若不再需要，请一并删除该目录。

## 设备固件

### 本地控制固件

本地控制模式要求设备运行启用了本地控制的 ESP RainMaker 固件（`CONFIG_ESP_RMAKER_LOCAL_CTRL_AUTO_ENABLE`，RainMaker 中默认关闭；没开的固件根本不广播本地控制）。[esp-rainmaker](https://github.com/espressif/esp-rainmaker) 的示例——首先是 `led_light`——已在 `sdkconfig.defaults` 中启用它，且使用标准 RainMaker API（即[《开发产品 Profile：从固件到 Home Assistant》](docs/developing_a_product_profile_zh.md)中使用的 `esp_rmaker_*` 调用）编写。

#### 支持的芯片（本地控制）

| ESP32 | ESP32-C2 | ESP32-C3 | ESP32-C5 | ESP32-C6 | ESP32-C61 | ESP32-S3 |
| ----- | -------- | -------- | -------- | -------- | --------- | -------- |
|   ✓   |    ✓     |    ✓     |    ✓     |    ✓     |     ✓     |    ✓     |

#### 获取固件

```bash
# 克隆仓库
git clone https://github.com/espressif/esp-rainmaker.git
cd esp-rainmaker/examples/led_light
```

#### 可用示例

仓库提供以下开箱即用的示例项目：

| 示例 | 描述 |
|------|------|
| `led_light` | 智能灯光，支持电源开关、亮度调节、HSV 颜色控制、色温调节 |
| `switch` | 开关设备 |
| `fan` | 带调速的风扇 |
| `temperature_sensor` | 温度传感器 |
| `multi_device` | 一个节点上多个设备 |

其他 RainMaker 示例与固件同理，只要启用了本地控制即可；标准设备类型已由内置 profile 覆盖。

#### 编译与烧录

这些就是普通的 ESP RainMaker 示例：按 [esp-rainmaker](https://github.com/espressif/esp-rainmaker) 仓库说明用 ESP-IDF 编译烧录即可。每个示例的 `sdkconfig.defaults` 已启用本地控制。

## 设备配置

### 添加设备

1. 进入 **设置** → **设备与服务** → **添加集成**
2. 搜索并选择 **ESP-Weaver**
3. 上传您自己的 profile，或跳过这一步，然后选择设备的厂商和产品。把 profile 发布到仓库见 [docs/device_profiles_in_detail_zh.md](docs/device_profiles_in_detail_zh.md#发布到仓库)

### 本地控制配置

1. 集成通过 mDNS 自动发现局域网内的 ESP 设备
2. 从列表中选择目标设备。广播的型号与上一步所选产品一致的设备，每行形如
   `产品-型号-序号`；没有广播型号的固件（加 `model` TXT 记录之前的
   ESP RainMaker）排在其后、显示为 `Other device-<n>`。设备由哪些设备类型组成，连接后逐个实体核对
3. 若设备启用安全模式（Sec1 或 Sec2），需输入 PoP 凭据

### 后续步骤的说明

连接设备之前，流程将先要求选择该设备的**产品**，分两步进行：先选择
厂商，再选择该厂商的产品。找到的每一份文件都列出，不预选任何一项；
同一产品内容完全相同的几份副本合并为一行，内容不同的版本各列一行，
并标明来源（「内置」「仓库」「您的文件夹」「缓存」）。
列表如何汇总和标注，见指南的[添加设备时选择](docs/device_profiles_in_detail_zh.md#添加设备时选择)。

连上设备后即绑定到所选产品。所选产品将为该设备创建多少个实体、哪些实体不会被创建、该
产品未声明的设备类型，写在日志里。若设备在握手之后、这一步之前掉线，流程结束并说明原
因，不添加设备；设备在线后重新添加即可。

## 设备 Profile

设备上报自身具备哪些参数，profile 则定义这些参数在 Home Assistant 中对应什么。该映
射层以数据而非代码形式实现，因此支持一款新设备只需编写 YAML，无需修改集成代码。

### 一台设备一个产品

一份 profile 就是一个**产品**：一个 YAML 文件，在 `vendor` 与 `product` 之下声明该产
品上报的全部设备类型。每台设备添加时先选定一个产品（厂商 → 产品），连上后即按这份文件加内置的十
个 RainMaker 公共服务解析。文件之间不合并、不覆盖、不回退：用户选哪个就用哪个。

列表由四处汇总而成：您的文件夹（`<配置目录>/esp_weaver/`）、内置文件、此前绑定时留存
的缓存，以及 profile 仓库
（[`espressif/esp-weaver-profiles`](https://github.com/espressif/esp-weaver-profiles)）。
一台设备属于哪个产品由它的 `model` 判定；
设备上报的设备类型随后逐个实体解析，产品声明了而设备没有上报的类型只是不创建任何实体。
列表如何汇总、标注和判定，见指南的[添加设备时选择](docs/device_profiles_in_detail_zh.md#添加设备时选择)。

### 内置产品

乐鑫在 `device_profiles/espressif/` 下内置了 35 份文件，每种有实际用途的已知设备类型
一份、文件名即类型名（19 种 RainMaker 标准设备类型，加 16 种扩展类型），外加所有产品
共用的服务层 `service_layer.yaml`。`esp.device.other` 与 `esp.device.thread-br` 没有
实体映射，因此不提供；使用前者的真实产品需要自己的 profile。每份内置文件只声明一种
类型，因此有多种类型的节点（例如 esp-rainmaker 的 `multi_device` 示例）需要一份同时
声明这几种类型的 profile，内置不提供此类组合文件，须将对应类型的
定义块合并至一份自定义 profile（见下）。完整
清单见指南的[现有定义的复用](docs/device_profiles_in_detail_zh.md#现有定义的复用)。

例外是 `date`、`time`、`datetime`：三者并非设备类型，而是由参数结构决定的实体——某个
字符串参数承载的内容是日期时，该参数即对应 date 实体——需由固件开发者在 profile 中指定
参数。

### 编写产品 profile

编写分为三步：从对应类型的内置文件中，将节点上报的每种设备类型的
`- type:` 块复制到一份文件；对照固件修改这些块；将文件放入
`<配置目录>/esp_weaver/`，下次添加设备或打开设备的**配置**对话框时
即可选用。详细步骤见[开发产品 Profile：从固件到 Home Assistant](docs/developing_a_product_profile_zh.md#编写步骤)。

编写前须注意以下三条规则：

- `type`、`name` 与 `param` 均为精确匹配，区分大小写；不一致时不报错，对应实体静默缺失。
- **一份产品 profile 要写全该节点的全部设备类型**，不会从其他文件补上。
- `key` 一经使用不应修改，否则实体被替换、历史数据丢失。

文件顶部还要加上 `vendor`、`product`、`model`，以及显示名 `vendor_label`、
`product_label` 和它们的多语言译名 `vendor_labels`、`product_labels`。该目录位于集成目录
之外，升级 ESP-Weaver 不会覆盖；放在 `custom_components/esp_weaver/device_profiles/`
下的文件同样会被列出，但升级时丢失，「修复」中会有提示。放入文件有三种方式：经
Samba、SSH 或文件编辑器加载项复制；在**添加集成** → **ESP-Weaver** 第一步上传；或给
`esp_weaver.reload` 一个 `url` 下载。

### 更新

设备绑定的是确认时的文件内容，因此编辑文件或仓库发布新版本都不会自行改变任何设备。
当某个产品的当前内容与绑定它的设备不一致时，「修复」中出现一条更新提示，确认后对该
产品的全部设备一起应用；单台设备换用别的产品，在它的**配置**对话框中操作。
`esp_weaver.reload` 重新读取目录、刷新仓库副本与上述提示、重载全部设备；该动作仅
管理员可用。修改后如何生效及常见易错点，见指南的[修改后如何生效](docs/device_profiles_in_detail_zh.md#修改后如何生效)。

加载失败的 profile 以修复形式报告而不会导致 setup 失败；绑定到它的设备保留绑定时的
内容。

### 更详细的指南

以下两份指南分别面向编写与查阅：
[开发产品 Profile：从固件到 Home Assistant](docs/developing_a_product_profile_zh.md) 面向固件开发
者，涵盖从固件声明、第一份可用的 profile 到首次联调的完整过程；
[设备 Profile 详细说明](docs/device_profiles_in_detail_zh.md) 说明列表的汇总与选择、
修改后如何生效、各字段修改对已有实体的影响、多台设备与多份文件、
发布到仓库、按现象定位问题，以及完整的字段参考。

## 已知限制

- **只通过 mDNS 发现设备。** 设备从集成在局域网上发现的列表
  （`_esp_local_ctrl._tcp.local.`）中添加，没有填写 IP 地址或主机名的地方。
  Home Assistant 与设备须在同一网段，或在网段之间转发 mDNS。
- **一台设备一个产品。** 每台设备只绑定一份 profile。上报多种设备类型的节点需要
  一份同时声明这几种类型的 profile；内置产品每份只声明一种类型。
- **匹配是精确的。** profile 中的设备类型名和参数名必须与固件上报的写法一致，
  不做猜测，也不回退到别的文件。
- **只有固件上报的参数才存在。** 设备没有上报参数的实体不会创建；添加设备前的
  汇总步骤会列出它们。
- **本版本不提供云端控制。** RainMaker 代码保留在仓库中，但不在界面中提供，设备
  只经局域网控制，固件须带本地控制。
- **不提供固件更新与 RainMaker 场景。** 设备页会显示当前运行的固件版本，但安装固
  件以及存储在设备上的场景仍由 RainMaker App 负责；场景请使用 Home Assistant 自带
  的场景。

## 故障排查

**未在网络上发现 ESP 设备。** 设备没有在 Home Assistant 能听到的地方广播
`_esp_local_ctrl._tcp`。确认固件启用了本地控制、设备已连上 Wi-Fi、并与 Home
Assistant 在同一网段（或网段间转发了 mDNS）。设备启动后等几秒，再打开**添加集成**。

**发现的设备都已配置。** 网络上每台设备都已有条目。要给某台设备换产品，打开它的
**配置**对话框，而不是再添加一次。

**PoP 码无效 / 需要 PoP 码。** 设备运行 Sec1 或 Sec2，所填 PoP 与固件编译时设定
的不一致。PoP 是固件设定的那一个，不是 Wi-Fi 密码。

**设备没有说明运行的安全模式** 或 **没有上报自身构成。** 设备被发现但没有及时应
答。什么都没添加；等几秒再添加一次。

**设备工作过一段时间后变为不可用。** 集成每 60 秒轮询一次，一次请求失败后以递增间
隔（1 秒到 30 秒）重连；设备恢复后在下一次尝试时接回。IP 地址变了，新地址从 mDNS
取得。长期不可用的设备通常已离线，或重启后本地控制没有运行。

**设置失败，或设备没有任何实体。** 设备绑定的 profile 读不出来，或它的参数与设备
上报的没有一个匹配。**设置** → **系统** → **修复**会说明是哪一种；profile 指南的
[按现象定位问题](docs/device_profiles_in_detail_zh.md#故障排查)一节有详细说明。

**写入被拒绝或失败。** 错误信息会区分是设备拒绝了该值（参数在固件中只读，或数值超
出范围）还是设备不可达；后者在设备恢复可达后即消失。

**详细日志。** **设置** → **工具** → **动作** → `logger.set_level`，填
`custom_components.esp_weaver: debug`，立即生效；写入 `configuration.yaml` 的
`logger:` 段则永久生效。设备页面的诊断下载（**⋮** → **下载诊断信息**）包含节点上报
的结构和所绑定的 profile，报告问题时请先附上它。

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
├─────────────────────────────────────────────────────────────┤
│  iot/client/client.py (ESPLocalCtrlClient)                  │
│  └── ESP Local Control 协议                                 │
├─────────────────────────────────────────────────────────────┤
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
| **Entity Base** | `entity.py` | 实体基类；可用性跟随 Coordinator |
| **ESPWeaverApi** | `iot/client/device_api.py` | 顶层 API 协调器 |
| **DeviceRegistry** | `iot/managers/device_registry.py` | 集中管理设备状态 |
| **ConnectionManager** | `iot/managers/connection_manager.py` | 连接建立、重连逻辑 |
| **PropertyManager** | `iot/managers/property_manager.py` | 属性操作、事件分发 |
| **DeviceDiscoveryManager** | `iot/managers/entity_discovery.py` | 配置解析、实体发现 |
| **ESPLocalCtrlClient** | `iot/client/client.py` | 协议层本地设备通信 |


## 参考文档

| 资源 | 链接 |
|------|------|
| ESP Weaver 固件示例 | [esp-rainmaker 的 `led_light`](https://github.com/espressif/esp-rainmaker/tree/master/examples/led_light) |
| ESP RainMaker | [github.com/espressif/esp-rainmaker](https://github.com/espressif/esp-rainmaker) |
| ESP Local Control | [ESP-IDF 编程指南](https://docs.espressif.com/projects/esp-idf/zh_CN/latest/esp32/api-reference/protocols/esp_local_ctrl.html) |
| Home Assistant 开发 | [developers.home-assistant.io](https://developers.home-assistant.io/) |
