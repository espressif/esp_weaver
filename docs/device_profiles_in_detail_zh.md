# 设备 Profile 详细说明

[English](device_profiles_in_detail.md)

本文讲集成如何处理一份产品 profile：列表如何汇总、设备如何绑定、改动
如何生效、每个字段改了有什么影响，以及按症状排查。它是写完第一份
profile 之后的查询手册。第一次写 profile——固件侧要确认什么、怎么照着
例子写完、怎么第一次联调——请先看
[开发产品 Profile：从固件到 Home Assistant](developing_a_product_profile_zh.md)。

每台 ESP-Weaver 设备绑定**一个产品 profile**：一份 YAML 文件，写明这款产品上报的全部设
备类型各自对应哪些 Home Assistant 实体。添加设备时先选厂商、再选产品，此后该设
备只按这份文件解析，不与任何其他文件合并、覆盖或回退。

本指南假设集成已安装、设备能被发现；安装与发现问题见
[README](../README_zh.md)。修改实体显示名称无需编写 profile，见
[实体存在但不符合预期](#实体存在但不符合预期)。

## Profile 的编写

编写第一份 Profile 的完整步骤——固件侧应确认的事项、现有定义的复制
方法、与固件的对照调整——见
[开发产品 Profile：从固件到 Home Assistant](developing_a_product_profile_zh.md)。
下文说明集成如何使用此类文件。

## 文件结构

本文件格式的规范说明在 Profile 仓库的
[产品 Profile 文件格式](https://github.com/espressif/esp-weaver-profiles/blob/master/docs/profile_format_zh.md)：
字段规则、语言代码、内容 Hash 与索引以该文为准。本节是速览，下文讲的是
集成如何使用这样一份文件。

一份完整可用的文件——与
[开发产品 Profile：从固件到 Home Assistant](developing_a_product_profile_zh.md#完整示例)
中相同的示例，固件上报型号为 `L3` 的一台调光灯：

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

本示例没用到 `services:`（可选，块结构与 `devices` 相同），见下文。
设备块还可以带 `name:`，把自己限定到某个设备实例名。

- **`version: 1`** 缺失或取值不符时整份文件被拒绝。
- **`vendor` / `product`** 必填，合起来是产品的标识 `acme/bulb`，在列表、诊断信息和设备
  绑定中都以此出现。不从文件名或所在目录推断任何东西，因此文件放在哪里、改成什么名字都
  保持身份；缺任一字段的文件被拒绝并生成一条修复提示。
- **`product_label`** 是列表中显示的名字，不写时显示标识。可选的 **`product_labels`**
  按语言给出译名：键必须是 Home Assistant 的语言代码（如 `en`、`zh-Hans`、`zh-Hant`），
  写成 `en-Hans` 这类不存在的代码会被校验拒绝；地区变体（如 `en-GB`）会用其主语言
  （`en`）的译名，没有译名时回退到 `product_label`。
- **`vendor_label`** 与 **`vendor_labels`** 是厂商 id 对应的显示名与译名，规则与
  `product_label` / `product_labels` 相同；不写时列表里直接显示 `vendor` id。
- **`model`** 必填：写产品的设备上报的型号（`esp_rmaker_node_add_model` 设的值），
  逐字节比对。写 `"L3"` 就只配上报恰好是 `L3` 的设备，同一厂商两款灯就靠它分开；
  它在列表里紧跟名字（`Acme 调光灯-L3`），也是设备页的「型号标识」。乐鑫按设备类型
  写的参考 profile 都写 `"*"`，对应固件上报型号为 `*` 的设备——即基于参考固件的设备。
  厂商的设备列在厂商自己的产品下，不会列到参考 profile 下。写空字符串会被拒绝。

  省略这个键会报错，每份文件都要写明自己对应什么。文件里没有任何字段能让集成替用户挑选：
  选哪个产品完全由用户在列表中决定。
- **`devices:`** 承载该产品上报的全部**设备**类型，即节点配置 `devices[]` 中的
  `esp.device.*`。同一 `(type, name)` 在一份文件里只能出现一次。
- **`services:`** 承载**服务**类型，即节点配置 `services[]` 中的 `esp.service.*`。十个
  RainMaker 公共服务（`ota`、`time`、`schedule`、`scenes`、`system`、`local_control`、
  `connectivity`、`groups`、`rmaker-controller`、`rmaker-user-auth`）已由内置的
  `device_profiles/service_layer.yaml` 提供，产品 profile 不必重复；写了同一类型则以产品
  profile 为准。这是填空而不是覆盖：产品 profile 没写的才用内置。

`devices:` 与 `services:` 分属彼此独立的两张查找表：把服务类型写进 `devices:` 不会报错，
也永不匹配。

## 现有定义的复用

乐鑫内置文件的完整清单，以及如何按设备类型复制定义块，见
[开发指南](developing_a_product_profile_zh.md#编写步骤)。

## 与固件的对照

固件与 Profile 如何逐项对应——型号、设备类型、实例名、参数名与参数
形态——见开发指南的
[固件侧核查清单](developing_a_product_profile_zh.md#固件侧核查清单)
与[完整示例](developing_a_product_profile_zh.md#完整示例)。

## 部署与选择

### 放入目录

自定义 profile 放在配置目录下的 `esp_weaver/` 文件夹，即 Samba 中的
`\\homeassistant\config\esp_weaver\`。可以按子目录整理，但子目录和文件名不参与任何判断：
文件列在哪个厂商下，由它自己写的 `vendor`、`product` 决定。目录不存在时新建；`.yaml` 与
`.yml` 均可。该目录位于集成之外，升级不会覆盖。

三种放入方式等效，区别仅在所需的访问权限：

| 方式 | 步骤 |
|------|------|
| **直接放置文件** | Home Assistant OS 上经 Samba 或 File Editor 加载项；Core 上直接复制。各平台访问方式见下一节 |
| **上传**（仅需浏览器） | **设置** → **设备与服务** → **添加集成** → **ESP-Weaver**，第一步选择文件并提交。文件保存到 `esp_weaver/` 下，沿用本地文件名；随后的选择列表中即可看到它。修改后重新上传时勾选「替换同名的 profile」 |
| **从链接下载**（profile 已发布至网络，且已存在至少一台设备） | **设置** → **工具** → **操作** → `esp_weaver.reload` → 填写 `url` → 执行。更新时勾选 `overwrite` |

> **动作页面的位置随 Home Assistant 版本变化。** 当前版本位于 **设置** → **工具**
> （Tools）→ **操作**（Actions）。2026.2 之前该页面在侧边栏中，名为**开发者工具**；
> 2026.8 起更名为**工具**并归入设置。任何版本均可在界面中按 `C` 打开命令面板，直接搜索
> 动作名。

将文件放入目录后，该产品即**出现在列表中**；此操作不会改变任何已
添加设备。

### 添加设备时选择

添加设备时，在选择连接方式之前先问「要添加什么设备？」：先选厂商，再选该厂商的产品。您
知道自己买的是什么，不必先连上设备。两步合起来列出四个来源中找到的**每一份**文件：

| 来源 | 位置 | 说明 |
|------|------|------|
| 内置 | `custom_components/esp_weaver/device_profiles/espressif/` | 随集成安装，始终可用。它是仓库 `vendors/espressif/` 在打包时的一份拷贝；两处相同时每个乐鑫产品只有一行 |
| 您的文件夹 | `<配置目录>/esp_weaver/` | 自己放的文件 |
| 缓存 | `.storage/esp_weaver.profiles` | 某台设备绑定时留存的内容副本，可能比它来源的文件旧；列表中明确标为「缓存」 |
| 仓库 | GitHub 上的 [`espressif/esp-weaver-profiles`](https://github.com/espressif/esp-weaver-profiles)，经 HTTPS 读取 | 本地副本存于 `.storage/esp_weaver.repository`，分两层。索引层（每个产品一条，几百字节）在弹出列表时拉取并更新到索引所列；文件按产品下载：选中哪个产品才下载那一个文件，副本里已经持有且哈希与索引一致时连这一个也不拉。已持有的产品只在刷新时索引显示其哈希变了才重下一次——这也是绑定它的设备收到更新提示的来源。每个下载都校验格式与哈希。无法联网时按上次同步的副本列出，列表顶部会说明；下载过一次的产品离线永远可选，从没下载过的仓库产品离线选中会报 `profile_fetch_failed` |

**第一步：厂商。** 一列厂商名，每个有 profile 的厂商一项。文件放在哪里都列在它自己写的
`vendor` 下。

**第二步：该厂商的产品。** 每项是产品名加型号（`product_label` 和 `model` 以横杠相连，如 `Light-A10`），也就是
包装盒上写的东西；设备类型、品类都不写，用户不关心。只要一个产品只在一处有，文件从哪来
也不写：

```
Lightbulb
Light
Humidifier
Air conditioner
Switch
Temperature sensor
```

同一产品的几份拷贝只要完全一样——名字一样、映射一样——就合成一行，不管放在哪里：内置的
乐鑫 profile 和仓库里的那份、您原样拷进文件夹的一份、设备绑定时留下而原件未变的缓存副本。
它们之间没有可选的，所以按列表顺序取第一份代表全部（您的文件夹、内置、缓存、仓库）。

同一个产品有多个**版本**时，每个版本列一行，每行用一个词说明来自哪里——「内置」「仓库」
「您的文件夹」「缓存」——由您在它们之间挑选。不替您决定，也不显示路径或哈希，这个词足够
分辨。只有一个版本的产品，不管别的产品来自几处，行上什么都不带。一个既有内置文件、又有您
一份改过的同名产品文件、且仓库里的 `humidifier.yaml` 在插件打包之后又改过的厂商：

```
Switch  [内置]
我的开关 A10  [您的文件夹]
Lightbulb
Humidifier  [内置]
Humidifier  [仓库]
```

仓库正按某个 pull request 读取时，来自它的行不写「仓库」而写 `Switch  [pull request #123]`，
这正是 PR 作者要找的行。

只有当两行仍然完全一样时才再加一点信息：您的两份文件 `vendor`、`product` 相同而映射不同，就各附
文件名；同一产品的两份缓存副本，各附哈希；两个不同产品的 `product_label`、`model` 恰好相同，各附
文件：

```
acme/light A10  [您的文件夹: hall.yaml]
acme/light A10  [您的文件夹: porch.yaml]
```

添加过一台设备之后，它绑定的那份产品会留一份缓存副本。原件没变时列表和之前一样；原件
变了之后，设备仍绑着的那份副本成为第二行：

```
Lightbulb  [内置]
Lightbulb  [缓存]
Light
```

列表的顺序按产品的品类排：照明（light）、气候（climate、fan、humidifier、water_heater）、
遮阳门窗（cover、valve）、安防（lock、alarm_control_panel、siren）、影音（media_player、
remote）、清洁（vacuum、lawn_mower）、开关插座（switch）、传感器（sensor、binary_sensor、
event）、多设备、其它——同类产品挨在一起。品类不用在文件里写，也不显示，由产品中各实体的
`platform` 推导，按上述顺序取第一个命中的。

两步都不替用户挑选、不预选任何一项：同一产品有两个版本就列两项，条目上用一个词标明各自的
来源，由用户分辨；只有完全相同的拷贝才合成一行。两步都可以在输入框里
键入关键字过滤，键入 `lightbulb` 或「灯泡」都能找到上面第一项；输入本身不能当作选择，只
能从过滤后的列表里点选一项。

选定产品后进入连接方式（本地 / 云端）、发现设备、输入 PoP。

**本地发现只列出该产品的设备。** 固件在 mDNS TXT 记录里广播自己的型号（`model`，即
`esp_rmaker_node_add_model` 设的值；没设就不带这个字段），集成在握手之前就能读到，据此把
网络上所有设备筛成所选产品的设备，不必用户自己判断哪台是。（记录里还有 `type`，即
`esp_rmaker_node_init` 传入的节点类型，目前不使用。）判定规则只有一条，前后两次都用它：

- **型号**：设备上报的型号必须与产品写的逐字节相同。产品写 `A1` 就只配这个型号，
  这正是同一厂商两款灯（`A1`、`A2`）的区别所在。`"*"` 也是一个普通型号：乐鑫的参考
  profile 写它，对应固件上报 `*` 的设备。

设备类型不参与这条规则。它在连接并读到设备配置之后逐个实体处理：产品声明了而设备没有的
设备类型不会建出任何实体；设备上报了而产品没声明的设备类型，在报告里列为未声明。

列表里每行是产品名加型号加编号——`Light-A1-1`、`Light-A1-2`；不显示 node id、IP
和安全等级，用户对不上任何实物。

**没有广播 `model` 的旧固件**从记录上分不出是什么，排在其他设备之后，显示为
`Other device-<n>`，连接后再核对它是什么。网络上没有一台自报为该产品、也没有一台沉默时，流程
结束并说明找到了几台。

云端设备没有 mDNS 记录，和握手前的本地设备一样，靠的是读到配置以后的那次判断。连上并
读到设备配置后，先用配置里的 `info.model` 按同一规则再判一次——TXT
记录仅为设备的简要自述，节点配置才是完整依据。没有广播型号的设备不做这一判断，添加时和以后换产品时都是：这种
固件把工程名当 `info.model` 上报，说明不了它是什么产品。不符则明确说明设备报了什么、
产品要什么，**不添加**，只能换一个产品；相符则立即绑定。该产品会建出多少实体、哪些
实体因缺少参数不会建出、哪些设备类型和服务该产品没有声明，写在日志里。

Home Assistant 的对话框没有返回按钮，所以每个有上一步的页面底部都有一组单选「继续 /
返回上一步」，默认「继续」：厂商列表回到导入页，产品列表回到厂商，设备列表回到产品，PoP 页
回到设备列表。从设备列表返回后，再选产品时会重新扫描网络。

设备叫什么，以固件上报为准：`Name` 参数的值（如在 App 里改的「客厅灯」），没有就
用它声明时的名字（如 `Light`）；一个节点有多个 device 时用节点自己的名字。节点什么名
字都没给，才用 mDNS 里广播的名字；两者都没有时叫 `Other device`，可在设备页改名——不写任何厂商名（产品来自多家厂商），也不写 node id。

设备页并排显示两类信息：HA 把设备当什么用，以及设备自己说自己是什么。厂商和型号取所
绑定产品的 `vendor` 和 `product_label`——内置 profile 就是 `espressif` 和 `Lightbulb`——添加
时写入，换产品后重写；设备列表可以按厂商筛选。序列号（node id）、固件版本
（`info.fw_version`）和型号标识则是设备自己上报的。固件曾在 mDNS 广播型号时，型号标识取
`info.model`——即使它是有意设成与工程名相同；只有参考固件的通用值 `*` 不代表具体硬件，
仍留空。绑定在 `A1` 产品上的设备上报 `A2`，页上显示 `Desk lamp (A2)`，
OTA 更新后型号标识随之更新，差异可直接识别。设备名称不随之变更。

设备连上后读不到配置时，没有任何东西可供比对，流程就此结束并说明原因，**不会添加设备**，
也不会替它绑定任何产品。确认设备在线后重新添加即可。

### 已添加的设备更换

**设置** → **设备与服务** → **ESP-Weaver** → 该设备 → **配置**，厂商、产品两步与添加时
相同，当前绑定标在顶部。选定并确认后设备重载，实体按新产品重建；`key` 不变的实体保留实体 ID 与历
史。

### 各平台访问配置目录的方式

树莓派等 Home Assistant OS 设备上，配置目录即 Samba 共享中的 `config`（系统内部路径为
`/config`），profile 目录为其下的 `esp_weaver/`。从 Windows、macOS、Linux 连接该共享
的方法只在 README 的[方式二：Samba 共享](../README_zh.md#方式二samba-共享home-assistant-ossupervised)
中说明，此处不重复。

不用 Samba 时，**File Editor** 加载项可在浏览器中创建和编辑文件，各平台均可；已安装
SSH 加载项时可直接拷入：

```bash
scp plug.yaml root@homeassistant:/config/esp_weaver/acme/
```

加载项商店仅在 Home Assistant OS 与 Supervised 安装方式下可用；Container 与 Core 安装可
直接访问宿主机上的配置目录，或使用上传与从链接下载方式。

## 修改后如何生效

设备绑定的是**确认时的文件内容**（以内容哈希标识，副本保存在 `.storage`），不是文件路径。
之后编辑、删除、替换文件，或仓库发布新版本，都不会自动改变任何设备——这是有意的：一台正
常工作的设备不应因为别处的改动而变化。

文件变化后，集成在下列时机重新读取目录并比对：启动、添加设备、打开配置对话框、执行
`esp_weaver.reload`。发现某个产品的内容与绑定它的设备不一致时，**设置** → **系统** →
**修复**出现一条「`<label>` 有更新的 profile」，列出绑定该产品的全部设备。点开后显示每台
设备现在有几个实体、更新后有几个；确认则这些设备一起换到新内容并重载。不确认则一切照旧。

`esp_weaver.reload` 因此不再直接改变实体，它的作用是：重新读取目录、刷新仓库副本与上述
修复提示、重载全部设备（重载后设备仍按各自绑定的内容解析）。三字段留空即可；填 `url` 时
先下载再执行同样的动作。**设置** → **工具** → **YAML** → **重新加载所有 YAML 配置**触发
的是同一个动作。

常见易错点：

| 易错点 | 表现 | 处理 |
|-------|------|-----|
| 改了文件，设备没变 | 设备保留绑定时的内容 | 到**修复**中确认更新；或打开设备**配置**重新选择该产品 |
| 上传时更换了文件名 | 列表里出现两份：文件夹里的每一份都列出，不去重，同一产品 id 的两份各附文件名 | 保持文件名不变并勾选替换，或删掉旧文件 |
| **操作**页面中 `overwrite` 未启用字段 | 界面显示为已勾选，实际提交的仍是 `false`，覆盖不会发生并报告文件已存在 | 先点开字段右侧的启用开关，再将取值拨至开启；或在 YAML 模式中直接写 `overwrite: true` |
| 尚无设备时查找 `esp_weaver.reload` | **操作**页面中不存在该动作 | 该动作在首台设备添加后注册，此前须改用上传或直接放置文件 |
| 设备离线 | 该设备的实体保持原状，不按新内容重建 | 确认设备在线后再次重新加载 |

## 发布到仓库

面向多个用户的产品 profile 可以 pull request 提交到 profile 仓库——GitHub 上的
[`espressif/esp-weaver-profiles`](https://github.com/espressif/esp-weaver-profiles)——路径为 `vendors/<厂商>/<产品>.yaml`。合入后所有用户在添加设备时都能在列表中看到它。
怎么提交、评审看什么，见该仓库的 README。

仓库根目录的 `index.json` 列出每个产品的标识、厂商与产品的显示名及译名（`vendor_label`、`vendor_labels`、`product_label`、`product_labels`）、`model`、路径与内容哈希，由仓库
自己的工具（`tools/build_index.sh`，基于 `tools/profilelib/`）生成：它实现与集成相同的 schema
和内容 Hash，工具接受的文件就是集成会列出并加载的文件；判定的格式规范见仓库的
[产品 Profile 文件格式](https://github.com/espressif/esp-weaver-profiles/blob/master/docs/profile_format_zh.md)。
厂商的 PR 带什么、仓库 CI 检查什么、索引如何重新生成、评审看什么，以该仓库的 README
为准，此处不重复。集成这一侧只拉取整份索引，不按型号查询；只下载本地副本里没有的哈希
对应的文件，并要求每份下载内容的哈希与索引一致。乐鑫自己的产品也在该仓库里
（`vendors/espressif/`）编辑；插件内置的 `device_profiles/espressif/` 由维护者在改仓库
的同时手改并提交。两仓的 CI 都不跨仓同步或对比（包括两份 profile 代码），
由维护者用 `tools/check_shipped_profiles.py` 与
`tools/check_profile_code_sync.py` 对着本地 checkout 手动校验。

PR 的文件已备好在合入前试用（输入框开启后，见下）。PR 的 CI（`.github/workflows/profile_index.yml`）每次 push 都用该工具为 PR
的文件生成一份 `index.json`，发布到 `espressif/esp-weaver-profiles` 的 `ci-indexes` 分支下的 `pr-<号>/index.json`，里面写明
fork 与所基于的提交；PR 关闭后该目录删除。集成可以改读这份索引：列出该 PR 的产品，选中某个产品时才按索引写明的提交下载那一个文件——和已发布仓库的方式完全一样。CI
被拒绝的文件在表单提交时同样被拒绝，提示信息与 CI 一致。所跟随的 PR 保存在 `.storage/esp_weaver.repository`；每次启动、`esp_weaver.reload`
和每次弹出列表都重新读取一次，列表顶部会说明当前读的是哪个 PR、哪个提交。索引走 GitHub
的匿名 API 读取，每小时 60 次，一次读取用一次；用完后改读 raw 文件，可能滞后几分钟。

对应的输入框——**添加设备**第一步填 PR 号（`123`、`#123` 或 PR 的 URL）——本版本不显示。机制本身保留，由 `const.py` 中的
`REPOSITORY_SOURCE_FIELD_ENABLED` 开启；开启后，输入框会预填当前跟随的 PR，清空即回到已发布的索引。

## 进阶说明

### 查找规则

产品 profile 内条目的标识是 `type` 与 `name` 的**组合**。同一 `type` 之下可并存若干个限
定到具体设备实例名的条目，以及一个不带 `name:` 的**默认条目**。解析一台设备时先查找
`type` 与 `name` 均精确匹配的条目，不存在时回退到该 `type` 的默认条目。仅有带 `name:`
的条目时，其余同类型设备没有映射。

查找范围只有绑定的这份文件加内置服务层，不涉及任何其他文件。

### 字段修改的影响

| 修改的字段 | 对既有实体的影响 |
|-----------|-----------------|
| `name`、`icon`、`device_class`、`state_class`、`unit`、`display_precision`、`entity_category`，以及属性下的 `param`、`bounds`、`scale`、`map`、`options`、`invert`、`properties`、`optional`、`auto_reset`、`alert`、`extra_attributes` | **安全。** 实体 ID、历史数据与自动化引用全部保留，仅属性或行为发生变化 |
| `key` 或 `platform` | **实体身份被替换。** 二者决定实体的 `unique_id`，修改等同于删除原实体并新建一个：注册表中的原记录被删除，该实体的界面自定义与区域归属随之丢失 |
| 删除整个 entity 块；`param` 改为设备未上报的参数名且未标记 `optional: true`；`platform` 改为 `none` | **实体消失。** 参数名为精确匹配，缺少任一必需属性时整个实体不会生成 |
| `model` | **构成更新。** 内容哈希覆盖文件里写的 `devices`、`services` 与 `model`（文件先通过校验再算）；它决定哪些设备属于这个产品，已绑定的设备会收到「有更新」提示，接受之前仍按旧型号匹配。schema 默认值不进哈希 |
| `product_label`、`product_labels`、`vendor_label`、`vendor_labels`、`vendor`、`product` | **不构成更新。** 不在内容哈希内，不会触发提示。改 `vendor` 或 `product` 会改变产品 id，对已绑定的设备而言那是另一个产品 |

`key` 一经使用不应再修改。修改显示名称请使用 `name`，区分同类型的多台设备无需修改
`key`。

`entity_id` 由设备与实体的 `name` 派生，**不随 `key` 变化**：仅修改 `key` 时，新实体通
常沿用原 `entity_id`，历史数据因而延续。若同时修改了 `name`，或原 `entity_id` 未及时释
放，新实体将获得新的 `entity_id`（形如末尾追加 `_2`），此时引用原 `entity_id` 的仪表盘
与自动化才真正失效。判断实体身份是否被替换应查看诊断信息中的 `unique_id`，而非界面上的
名称。

### 多台设备

**同一产品的多台设备绑定同一份 profile，各自生成独立的实体，互不冲突。** 十台同款灯共用
一个 `esp.device.lightbulb` 定义与同一个 `key`。

**设备名称须在 Home Assistant 界面中设置**，而非在 profile 中设置。设备重命名后，前端
显示「设备名 + 实体名」。该名称保存在 Home Assistant 中，重新加载与重启均不会覆盖。

仅有一种情况需要在一份 profile 内分别映射：**同一节点上同类型的设备实例上报的参数不同**。
此时在 device 块中添加 `name:`，其值须与固件中的设备实例名一致：

```yaml
version: 1
vendor: acme
vendor_label: "Acme"
vendor_labels:
  zh-Hans: "Acme"
product: dual_light
product_label: "Acme dual light"
product_labels:
  zh-Hans: "Acme 双灯"
model: "D2"
devices:
  - type: esp.device.lightbulb
    name: "Living Room Light"      # 固件中的设备实例名，非 Home Assistant 设备名
    entities:
      - platform: light
        name: "Ceiling Light"
        key: light
        attributes:
          state: { param: "Power", data_type: bool, properties: [read, write] }

  - type: esp.device.lightbulb
    name: "Bedroom Light"
    entities:
      - platform: light
        name: "Bedside Light"
        key: light_bedroom
        attributes:
          state: { param: "Power2", data_type: bool, properties: [read, write] }
```

`name:` 的取值须与固件上报的设备实例名**逐字一致，区分大小写**，即与
创建该设备的调用的首个参数完全相同——`esp_rmaker_device_create()`，或
`esp_rmaker_lightbulb_device_create()` 这类标准设备 helper。不一致时该设备回退到默认条目，没有默认条
目则没有实体，且不产生任何错误提示。设备实际上报的实例名可在诊断信息的 `reported.name`
中查看。

### 多份文件

`<配置目录>/esp_weaver/` 下的每份文件是一个独立的产品，彼此不相互影响，不存在加载顺序
或覆盖问题。两份文件声明同一设备类型完全正常——那是两个产品各自的定义，选哪个用哪个。

两份文件得到同一标识（写了相同的 `vendor`/`product`）而内容不同时两份都列出，各自标明来自
哪里，来自同一处的再附文件名，由您挑选。自定义文件的标识与内置产品相同时（例如
`esp_weaver/espressif/switch.yaml`），改过之后它以「您的文件夹」列在内置那行旁边；没改过则是同一行。

删除文件不影响已绑定它的设备，设备保留绑定时的内容；删除后该产品不
再出现在列表中，也不再向已绑定设备提供更新。

## 故障排查

### 先查看设备实际上报的内容

**设置** → **设备与服务** → **ESP-Weaver** → 选择设备 → **下载诊断信息**。JSON 内容较
长，只需查看以下几处。

**`profile_files.binding`** 为该设备绑定的产品：标识、来源、内容哈希。
**`profile_files.products`** 为当前可选的全部产品及各
自的来源、位置与哈希；**`profile_files.problems`** 为读取失败的文件。

**`profiles.reported`** 为设备实际上报的类型与参数名，将其与 profile、固件代码三方对照
即可定位不一致之处：

```json
{
  "kind": "device",
  "name": "Light",
  "type": "esp.device.lightbulb",
  "params": ["Name", "Power", "Brightness", "Hue", "Saturation",
             "CCT", "Intensity", "Light Mode"]
}
```

**`profiles.registry.product`** 为实际解析所用的产品，应与 `binding.id` 一致。
**`profiles.entities`** 为当前生效的映射关系，其中 `params` 的键为 YAML 中的属性名、值
为设备参数名；与 `reported.params` 对照可确认哪些参数尚未被任何实体使用。
**`profiles.skipped`** 为被跳过的实体及原因，以及该产品未声明的设备类型。

### profile 未生效

按以下顺序检查：

1. **设备绑定的是哪个产品。** 诊断信息 `profile_files.binding.id`，或设备**配置**对话框
   顶部的「当前绑定」。绑定的不是目标产品时，在对话框中更换。
2. **绑定的是不是最新内容。** 文件改过之后设备不会自动跟随，见
   [修改后如何生效](#修改后如何生效)。**修复**中有「有更新的 profile」时确认它。
3. **文件是否在列表里。** 添加设备或打开配置对话框时找不到它，则文件未被读取：确认位于
   `<配置目录>/esp_weaver/` 下、扩展名为 `.yaml`/`.yml`、文件名不以 `.` 开头；再查看
   **设置** → **系统** → **修复**，校验失败的文件会在此报告错误信息。
4. **设备类型是否匹配。** 选定产品后的汇总步骤会列出「该产品未声明的设备类型」；诊断信
   息 `profiles.skipped.device_types` 亦然。对照固件代码核对 `type`。
5. **顶级字段是否正确。** 设备类型须置于 `devices:` 之下，服务类型须置于 `services:` 之
   下，两者不可互换。
6. **文件编码。** 文件须为 UTF-8 编码。用 Windows 记事本等按本地代码页另存的文件会被整
   份拒绝，并在修复中报告 `is not UTF-8 encoded`。
7. **文件位置。** 放在 `custom_components/esp_weaver/device_profiles/` 下的文件会被读取并
   列出，但集成升级时会被覆盖，**修复**中会有相应提示；请移到 `<配置目录>/esp_weaver/`。

### 部分实体未创建

最常见的原因是参数名不匹配。选定产品后的汇总步骤会列出缺少参数的实体；日志中检索
`is not created` 也能看到：

```text
Node 6055F9F91114: 'Ceiling Light' of esp.device.lightbulb needs 'Power222',
which 'Light' does not report, so the entity is not created.
Parameter names are matched exactly
```

有两种处理方式：将 `param` 改为设备实际上报的参数名，或为该属性标记 `optional: true`，
适用于并非所有设备都具备该能力的场景。同为 `esp.device.lightbulb` 类型，调光灯上报
`Brightness`，彩光灯额外上报 `Hue` 与 `Saturation`，开关型灯仅上报 `Power`，因此除
`Power` 之外的属性均应标记 `optional: true`。

日志中无上述记录时，还有另外两种原因：

**device 块的 `name` 不匹配。** 该设备回退到默认条目，本应由其条目提供的实体全部缺失，且
不产生任何记录。逐字对照诊断信息中的 `reported.name` 与 profile 中的 `name:`。

**`platform` 取值不是已实现的平台。** 拼写错误或使用了当前版本尚未实现的平台时，该实体通
过校验但在解析阶段被跳过，仅在 debug 级别日志中留下 `platform ... is not implemented`。
可用取值见[entity 块](#entity-块)。

### 设备显示「设置失败」，实体全部不可用

集成页面上该设备标为设置失败，说明为「绑定的内容已不在存储中」或「没有绑定」。设备只按
它绑定时确认的那份内容解析，那份内容拿不到时不会用同名文件或某个内置产品顶替——
那样建出来的实体并不是您确认过的。删除该设备，重新添加并选择产品即可；`key` 不变的实体
会保留实体 ID 与历史。

### 设备未创建任何实体

在日志中检索 `no profile declares`。该记录表示设备上报的类型未被绑定的产品声明，并会给
出最接近的候选类型：或是固件中的类型名有拼写错误，或是选错了产品，或是该产品 profile 少
写了一块。

```text
Node 6055F9F91114: no profile declares device type 'esp.device.lightbolb',
so 'Light' contributes no entities. Did you mean 'esp.device.lightbulb'?
```

### 实体存在但不符合预期

**实体 ID 变化、历史数据丢失。** 原因是 `key` 或 `platform` 被修改，见
[字段修改的影响](#字段修改的影响)。将其改回原值并更新绑定后，实体将以原有 ID 恢复，历史
数据通常也会恢复关联。

**修改 `name` 后界面无变化。** 有两种可能。一是该 `key` 存在内置翻译，此时显示名称取自
翻译，`name` 仅作为无翻译时的回退值；内置 profile 使用的 key 中，`light`、
`binary_sensor` 以及各类传感器和阈值 key 均有翻译，`lightbulb`、`fan`、`switch` 等没
有，可在 `custom_components/esp_weaver/translations/<语言>.json` 中检索
`entity.<平台>.<key>.name` 确认。二是实际需要修改的是设备名称，设备名称在 **设置** →
**设备与服务** → 进入设备页面 → 右上角设置中修改，此处不受 profile 修改影响。`name` 的
另一个作用是决定新建实体的实体 ID，但仅在实体**首次**创建时生效。

**已从 YAML 删除的实体仍然存在。** 通常表示本次解析未成功完成，原因可能是设备离线或上
报内容异常。出于安全考虑，未解析出任何绑定时不会删除实体。请确认设备在线后重新加载。

### 启用详细日志

临时或长期开启 debug 日志的方法只在 README 的[故障排查](../README_zh.md#故障排查)中
说明，此处不重复。开启后值得检索的日志记录：`Profile catalog holds N products` 为
读到的产品数；`resolved N bindings: [...]` 列出解析得到的实体 `key`；
`Registered new ... entity` 表示实体已新建；`Removing ...` 表示实体已删除。

## 字段参考

### 顶级字段

顶级字段——`version`、`vendor`、`vendor_label`、`vendor_labels`、`product`、
`product_label`、`product_labels`、`model`、`devices`、`services`——的规范在
Profile 仓库的
[产品 Profile 文件格式](https://github.com/espressif/esp-weaver-profiles/blob/master/docs/profile_format_zh.md)，
id、语言代码、型号等规则以该文为准，此处不重复。本节往下是集成自己解释的
各块字段。

### device 块

`services:` 下的块使用同一结构。

| 字段 | 必填 | 说明 |
|------|------|------|
| `type` | 是 | 固件上报的设备或服务类型，**精确匹配** |
| `name` | 否 | 仅对固件中同名的设备实例生效，**精确匹配且区分大小写**。未指定时对该类型所有设备生效，见[查找规则](#查找规则) |
| `entities` | 是 | 实体定义列表 |

### entity 块

| 字段 | 必填 | 说明 |
|------|------|------|
| `platform` | 是 | Home Assistant 平台，取值见下表。`none` 表示该参数按设计无需对应实体 |
| `key` | 是 | 实体身份标识，**一经确定不应修改** |
| `name` | 是 | 实体 ID 的来源，以及显示名称的回退值 |
| `attributes` | 是 | 属性名到设备参数的映射，`platform: none` 时除外 |
| `icon` | 否 | MDI 图标 |
| `device_class` | 否 | 设备类别 |
| `state_class` | 否 | 统计类别，影响长期统计 |
| `unit` | 否 | 单位 |
| `display_precision` | 否 | 显示精度，须为不小于 0 的整数 |
| `entity_category` | 否 | `config` 或 `diagnostic` |
| `extra_attributes` | 否 | 只读的附加属性 |
| `auto_reset` | 否 | 瞬时状态的自动复位，`{ to, after }` 两字段均必填，`after` 为秒数 |
| `alert` | 否 | `attribute` 进入 `levels` 中某一级别时触发 `esp_weaver_alert` 事件，离开时再触发一次，见[告警事件](#告警事件)。`attribute` 须为该实体 `attributes` 或 `extra_attributes` 中已声明的属性名，否则整份文件校验失败 |
| `threshold` | 否 | 仅用于 `number`：`{ of, end }` 指定同一 device 块中的某个传感器，以及本数值实体设定的是其范围的哪一端（`min` 或 `max`）。传感器读数越过该数值时触发 `esp_weaver_alert` 事件，回到范围内时再触发一次 |

`platform` 的可用取值共 25 个：`alarm_control_panel`、`binary_sensor`、`button`、
`climate`、`cover`、`date`、`datetime`、`event`、`fan`、`humidifier`、`lawn_mower`、
`light`、`lock`、`media_player`、`number`、`remote`、`select`、`sensor`、
`siren`、`switch`、`text`、`time`、`vacuum`、`valve`、`water_heater`，以及
表示不生成实体的 `none`。其余取值可通过校验，但在解析阶段被静默跳过，见
[部分实体未创建](#部分实体未创建)。

### 告警事件

profile 中没有任何配置会让集成主动通知用户。`alert` 块或 number 上的
`threshold` 只会让传感器在告警开始时向 Home Assistant 事件总线发出一个
`esp_weaver_alert` 事件，结束时再发一个。之后做什么——推送到手机、亮一盏灯、
或什么都不做——由自动化决定，这也是 Home Assistant 期望这类决定所在的位置。

每个事件都携带：

| 字段 | 含义 |
|------|------|
| `kind` | `alert` 块为 `level`，阈值 number 为 `threshold` |
| `active` | 告警开始为 `true`，结束为 `false` |
| `node_id` | 设备的 node id |
| `device` | 设备名称 |
| `entity_id` | 传感器实体 ID |
| `sensor` | 传感器在 profile 中的名称 |

告警开始的事件还携带文案 `title` 与 `message`；`kind: level` 时另有 `level` 与
`state`，`kind: threshold` 时另有 `end`（`high` 或 `low`）、`value`、`threshold`
与 `unit`。告警结束的事件只携带 `level` 或 `end`。

`alert` 各级别的 `title` 与 `message` 可使用占位符 `{device}`、`{state}`、
`{level}`；任何超出"引用其中一个名字"的写法——如 `{state.__class__}`、
`{device[0]}`——都会被拒绝并记录日志，且不发出事件。阈值告警的文案来自集成的
翻译文件，使用服务器语言。

对低电量做出反应的自动化示例：

```yaml
automation:
  - alias: 电量低
    triggers:
      - trigger: event
        event_type: esp_weaver_alert
        event_data:
          kind: level
          active: true
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "{{ trigger.event.data.title }}"
          message: "{{ trigger.event.data.message }}"
```

### attribute 块

仅指定参数名时可简写为一行：`state: "Power"`。

| 字段 | 必填 | 说明 |
|------|------|------|
| `param` | 是 | 设备参数名，**精确匹配** |
| `optional` | 否 | 默认 `false`。为 `true` 时，设备未上报该参数也不影响实体生成 |
| `properties` | 否 | `read`、`write` 或两者。不写则读写属性取设备上报值；写了必须与设备上报的读写标志相同。设备端的存储标志（`time_series`、`simple_ts`、`persist`）不参与比较 |
| `data_type` | 否 | `bool`、`int`、`float`、`string`、`array`、`object`。不写则取设备上报值；写了必须与设备相同 |
| `bounds` | 否 | `{ min, max, step }`，其中 `min` 与 `max` 必填、`step` 可选。不写则取设备上报值；写了整块必须与设备相同。用于 `number` 的滑块范围、`light` 的色温范围、`climate` / `water_heater` 的目标温度范围，以及 `scale` 换算；只读读数用不到 |
| `scale` | 否 | `{ to: [下限, 上限] }`，将设备取值线性映射到 Home Assistant 取值。`to` 须为恰好两个数值 |
| `map` | 否 | 设备取值到 Home Assistant 取值的映射表 |
| `options` | 否 | 可选值列表，取值为字符串。不写则取设备上报的 `valid_strs`；写了必须与设备包含相同的值，顺序不限。设备上报空值时读作未知；上报列表之外的其他值则按原样显示 |
| `invert` | 否 | 读写时取反 |
| `state_when` | 否 | 按伴随参数的取值改写传感器读数 |
| `type` / `ui_type` | 否 | RainMaker 的类型标注。不写则取设备上报值；写了必须与设备相同 |

profile 写了、且与设备上报不同的字段导致该实体不创建；日志记录字段名和两侧的值，添加设
备时的摘要中列出该实体。
