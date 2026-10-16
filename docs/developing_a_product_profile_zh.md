# 开发产品 Profile：从固件到 Home Assistant

[English](developing_a_product_profile.md)

本指南面向首次为自有产品编写 Profile 的固件开发者。完成本指南的步骤后，
将得到一份可直接使用的 Profile；实体未按预期生成时，亦可据此定位原因。
集成对 Profile 的处理方式（列表汇总、改动生效、各字段的影响）见
[设备 Profile 详细说明](device_profiles_in_detail_zh.md)；字段合法性以 Profile 仓库的
[产品 Profile 文件格式](https://github.com/espressif/esp-weaver-profiles/blob/master/docs/profile_format_zh.md)
为准。

运行标准 RainMaker 固件的设备无需自行编写 Profile，添加设备时选择乐鑫
内置产品即可。以下两种情形需要编写自定义 Profile：固件声明了非标准设备
类型；在标准设备上增加了规范之外的参数。

## 对应关系

Profile 的作用是将固件上报的设备与参数映射为 Home Assistant 实体。

| 固件侧 | profile 里写 | Home Assistant 侧 |
|--------|---------------|-------------------|
| 一个节点（node） | 一份产品 profile | 一台设备 |
| 设备——`esp_rmaker_device_create()` 建的，或 `esp_rmaker_lightbulb_device_create()` 这类标准设备 helper 建的，类型为 `esp.device.*` | `devices:` 下的一个 `- type:` 块 | 一组实体 |
| 参数——`esp_rmaker_intensity_param_create()` 这类标准参数 helper 建的，或 `esp_rmaker_param_create()` 建的 | entity 下的一个 attribute，`param:` 写参数名 | 实体的一个属性（开关状态、亮度、数值……） |
| 参数长什么样 | entity 的 `platform` 决定它变成哪种实体 | light、switch、sensor、number…… |

本文使用以下术语：

- **产品（product）**：一款硬件产品。`vendor` 与 `product` 共同构成其
  标识，`model` 为设备上报的型号。每台设备仅绑定一个产品。
- **设备类型（type）**：固件为设备声明的类别名，如
  `esp.device.lightbulb`。设备类型与 Home Assistant 的实体平台
  （platform）不是同一概念。
- **实体（entity）**：Home Assistant 中可查看与控制的对象，如一盏灯或
  一个温度读数。一种设备类型可生成多个实体。
- **`key`**：实体的唯一标识。投入使用后不得变更；变更后实体将被视为
  新实体，原有历史数据不再关联。

以下三条规则适用于全部映射过程：

- `type`、设备实例名与参数名（`param`）均为**精确匹配**，且区分大小写。
  填写不一致时不产生错误提示，对应实体不生成。
- **一份 Profile 必须声明该节点的全部设备类型。** 设备仅按所绑定文件
  解析，未声明的类型不会从其他文件补齐。公共服务（`esp.service.*`）
  为例外，由集成内置的服务层提供，无需在 Profile 中声明。
- 固件未上报的参数不会生成对应实体。仅部分型号上报的参数，应在
  Profile 中标记 `optional: true`。

## 固件侧核查清单

编写 Profile 前，应依据固件代码确认以下信息。Profile 与固件构成同一
映射约定的两端，任一项不一致，对应实体即不会生成。

| 检查项 | 固件侧在哪儿 | profile 里对应 |
|--------|--------------|----------------|
| 型号 | `esp_rmaker_node_add_model(node, "L3")`，须在 `esp_rmaker_start()` 之前调用，才会进 mDNS 广播 | 顶层 `model: "L3"`，逐字节一致 |
| 设备类型 | 标准设备 helper 已定死：`esp_rmaker_lightbulb_device_create("Light", NULL, false)` 上报 `esp.device.lightbulb`；自创设备则把类型传给 `esp_rmaker_device_create()` | `- type: esp.device.lightbulb` |
| 设备实例名 | 上面的第一个参数 `"Light"`；同类型有多台时才用得到 | device 块的 `name:`（可选） |
| 参数名 | 标准参数 helper 的名字，如 `esp_rmaker_intensity_param_create(ESP_RMAKER_DEF_INTENSITY_NAME, ...)` 就是 `"Intensity"`；自创参数在 `esp_rmaker_param_create("Intensity", ...)` 里命名 | attribute 的 `param: "Intensity"` |
| 参数形态 | 数据类型、读写标志、数值范围、候选值 | `data_type`、`properties`、`bounds`、`options`——写了就必须与固件一致 |

参数形态字段可采用简化写法。attribute 中描述参数形态的六个字段
（`type`、`data_type`、`ui_type`、`properties`、`bounds`、`options`）
未填写时，按固件实际上报的值生效；**一旦填写，必须与固件一致，不一致
时该实体不会生成。** 因此编写初期可仅填写 `param`，其余六项留空，以
减少两端不一致的风险。此规则不适用于其他字段：`platform`、`name`、
`key`、`param` 为必填；`optional` 未填写默认为 `false`；`scale`、`map`
未填写表示不进行数值转换；实体的 `unit`、`device_class` 未填写表示
不设置。

`Name` 参数无需映射为实体，集成使用该参数作为设备名称。

## 完整示例

本例为一台灯具：固件仅上报 `Power`（开关量）与 `Intensity`（0–100 的
可读写整数），型号为 `L3`。目标配置为一个 light 实体（含开关）与一个
number 实体（滑块）。

固件侧代码（节选，仅保留与 Profile 相关的部分）：

```c
/* 型号，须在 esp_rmaker_start() 之前设置 */
esp_rmaker_node_add_model(node, "L3");

/* 标准灯泡设备，与 RainMaker led_light 例程相同写法：
 * Name 与 Power 随设备自带，名字用默认值 */
light_device = esp_rmaker_lightbulb_device_create("Light", NULL, false);

/* 标准参数：helper 已带类型与 0–100 范围，
 * ESP_RMAKER_DEF_INTENSITY_NAME 即 "Intensity"。自创参数用
 * esp_rmaker_param_create()，再按需用 esp_rmaker_param_add_bounds() /
 * esp_rmaker_param_add_ui_type() 补齐。 */
esp_rmaker_device_add_param(light_device,
        esp_rmaker_intensity_param_create(ESP_RMAKER_DEF_INTENSITY_NAME, 50));

esp_rmaker_node_add_device(node, light_device);
```

对应的 profile，一份完整可用的文件：

```yaml
version: 1
vendor: acme
vendor_label: "Acme"
vendor_labels:
  zh-Hans: "Acme"
product: bulb
product_label: "Acme dimmable bulb"
product_labels:
  zh-Hans: "Acme 调光灯"
model: "L3"
devices:
  - type: esp.device.lightbulb        # 与固件的设备类型一致
    entities:
      - platform: light
        name: "Lightbulb"
        key: lightbulb
        attributes:
          state:                       # 灯的开关，对应 Power 参数
            param: "Power"
            data_type: bool
            properties: [read, write]
      - platform: number               # 自加参数映射成滑块
        name: "Light Intensity"
        key: light_intensity
        attributes:
          value:
            param: "Intensity"
            data_type: int
            properties: [read, write]
            bounds: { min: 0, max: 100, step: 1 }
```

顶层字段说明：

- `vendor` / `product` 是标识，只能用小写字母、数字、`_`、`-`、`.`，发布
  后不要改。显示用的名字写在 label 里，不要写进标识。
- `vendor_label` / `product_label` 是默认显示名，用你自己的语言写即可
  （中文厂商可以直接写中文）；`*_labels` 是按语言给的译名，键必须是 HA
  的语言代码（`en`、`zh-Hans`、`zh-Hant`……）。四个 label 都是可选的，
  不写就显示标识本身，但正式产品建议写上。
- `model` 必填，与固件上报的逐字节一致。乐鑫内置文件写的是 `"*"`，那是
  给参考固件用的；你自己的产品要写自己的型号，不要跟着写 `"*"`。

在 Home Assistant 中添加该设备并选择 `acme/bulb` 后，将生成两个实体：
灯实体（开关）与 Light Intensity（滑块）。固件上报的其他参数如未在
Profile 中声明，对应实体不会生成，且不产生错误提示。

## 编写步骤

实际产品通常包含多个参数与多种设备类型，应以乐鑫内置定义为基础复制
并修改，步骤如下：

1. **复制现有定义。** 节点上报的每种设备类型，在
   `custom_components/esp_weaver/device_profiles/espressif/` 下均有一份以
   类型命名的文件（`lightbulb.yaml`、`switch.yaml` 等）。将该文件的整个
   `- type: ...` 块复制到本文件的 `devices:` 下，每种类型复制一块。
   某类型无现成文件时，应以结构最接近的定义为基础进行修改。
2. **对照固件调整。** 使 `type` 与固件上报的设备类型一致、每个 `param`
   与固件参数名一致，再按需增删实体与 attribute。复制得到的块必须整块
   保留：仅声明新增实体时，原有实体将全部缺失。
3. **部署并选择。** 将文件放入配置目录下的 `esp_weaver/` 文件夹（见
   下一节），在添加设备时于列表中选择该产品。

参数名应优先采用 RainMaker 规范定义的名称。采用自定义参数名时，手机
App 与乐鑫内置 Profile 均无法识别，该设备在任何环境中都只能依赖自定义
Profile。

## 参数与实体类型的对应

增加自定义参数时，应根据参数的可读性、可写性与值类型，参照下表选择
实体类型：

| 参数形态 | platform | attribute | 备注 |
|----------|----------|-----------|------|
| 可读写的开关量，想当开关用 | `switch` | `state` | 灯的开关跟灯走，用 `light` 的 `state` |
| 只读的开关量（门磁、人体感应） | `binary_sensor` | `state` | 配 `device_class` 说明它是什么 |
| 只读的数值或状态文字（温度、电量、当前模式） | `sensor` | `state` | 数值配 `device_class`、`unit`；文字配 `device_class: enum` |
| 可读写的数值（阈值、延时、强度） | `number` | `value` | 用 `bounds` 给范围和步进 |
| 可读写、只能在几个固定值里选 | `select` | `state` | 候选值写 `options`，或不写、用固件上报的 |
| 可读写的自由文本（时区、名称） | `text` | `value` | |
| 按一下触发一次的动作（重启、复位） | `button` | `press` | |

灯具、空调、窗帘等完整设备类型应直接复用内置定义块，不应按上表逐项
拼装。此类设备的 attribute 名（如灯具的 `brightness`、`hue`）已由规范
固定，复用内置定义可避免遗漏或误写。

attribute 名（`state`、`value`、`press`）是 profile 内部的名字，必须用
上表这些；`param` 后面才是固件的参数名，两者不要混。

某个参数只有部分型号才有，给它标 `optional: true`，没有这个参数的型号
就跳过它、其余实体照常创建。不标的话，缺一个参数会连累整个实体。

## 部署与联调

文件放在配置目录下的 `esp_weaver/` 文件夹，即 Samba 中的
`\\homeassistant\config\esp_weaver\`。文件名和子目录不参与判断，文件列在
哪个厂商下由它自己写的 `vendor` 决定。三种放法等效：直接拷文件、在
**添加集成** 第一步上传、用 `esp_weaver.reload` 给 `url` 下载。访问配置
目录的方法见详细说明的[各平台访问配置目录的方式](device_profiles_in_detail_zh.md#各平台访问配置目录的方式)。

部署完成后，Profile 仅进入可选产品列表，尚未应用于设备。首次联调应
按以下顺序进行：

1. **添加设备**：依次选择厂商、产品与设备，并输入 PoP。
2. **核对添加完成时的汇总信息**：汇总列明已生成的实体数量、未生成的
   实体，以及 Profile 未声明的设备类型，是首要的核对依据。
3. **实体与预期不一致时，先下载诊断信息**：在设备页选择**下载诊断
   信息**，查看其中的 `profiles.reported`，即设备实际上报的设备类型与
   全部参数名。将该内容与 Profile、固件代码三方对照，可定位不一致之处。
4. **修改文件后不会自动生效**：设备始终按添加时确认的 Profile 内容
   生成实体。应在 **设置** → **系统** → **修复** 中确认「有更新的
   profile」修复项，设备将按新内容重建实体；也可对单台设备打开其
   **配置**对话框、重新选择产品，效果相同。
5. 重复第 2 步核对，直至生成的实体与预期一致。

Profile 文件本身存在错误（YAML 语法或字段不合法）时，不会导致集成
异常终止；该文件将被拒绝，并以修复提示形式报告错误位置。文件须采用
UTF-8 编码。

## 常见问题

| 现象 | 原因 |
|------|------|
| 实体未生成，且无任何错误提示 | `param` 或 `type` 与固件上报值不完全一致（含大小写）。应核对诊断信息中的 `profiles.reported` |
| 设备未出现在可选列表中 | 固件未设置 `model`，或与 Profile 中的 `model` 不一致，设备被列为 Other device |
| 修改文件后设备无变化 | 尚未在「修复」中确认更新，或未在**配置**对话框中重新选择产品 |
| 仅声明新增实体后，原有实体全部缺失 | 设备仅按所绑定的这一份 Profile 解析，未声明的内容不会从其他文件补齐 |
| 修改显示名后实体 ID 变更、历史数据丢失 | 误改了 `key`。显示名应通过 `name` 修改，`key` 不得变更 |

## 后续步骤

- **面向全部用户发布**：将 Profile 提交 PR 至
  [`espressif/esp-weaver-profiles`](https://github.com/espressif/esp-weaver-profiles)，
  路径为 `vendors/<厂商>/<产品>.yaml`。提交与评审要求见该仓库 README。
- **查询各字段的全部取值**：见[字段参考](device_profiles_in_detail_zh.md#字段参考)。
- **查阅集成的完整处理方式**：见[设备 Profile 详细说明](device_profiles_in_detail_zh.md)，
  包括列表汇总规则、改动生效方式与按症状排查。
