# Device profiles in detail

[中文](device_profiles_in_detail_zh.md)

This document is about how the integration treats a product profile: how
the list is assembled, how a device is bound, how an edit takes effect,
what changing each field does, and troubleshooting by symptom. It is the
reference to consult after a first profile exists. Writing that first
profile - what to check on the firmware side, a complete worked example,
and the first test - is covered in
[Developing a product profile](developing_a_product_profile.md).

Every ESP-Weaver device is bound to **one product profile**: a YAML file that
says, for every device type the product reports, which Home Assistant entities
it becomes. When a device is added, the user picks "vendor › product" from a
list; from then on the device resolves against that one file and nothing else.
Files are never merged, overridden or fallen back to.

This guide assumes the integration is installed and the device can be
discovered; installation and discovery problems are covered in the
[README](../README.md). Changing an entity's displayed name requires no
profile; see
[An entity exists but is not as expected](#an-entity-exists-but-is-not-as-expected).

## Authoring a profile

The full procedure for a first profile - what to check on the firmware
side, how to copy an existing definition and edit it to match - is in
[Developing a product profile](developing_a_product_profile.md). What follows
here is how the integration uses such a file.

## File structure

The canonical specification of this file format lives in the profile
repository, in [Profile format](https://github.com/espressif/esp-weaver-profiles/blob/master/docs/profile_format.md):
field rules, language codes, the content hash and the index. This section
is the working summary; the rest of this guide is how the integration
uses such a file.

A complete, working file - the same example as in
[Developing a product profile](developing_a_product_profile.md#complete-example),
for a dimmable bulb whose firmware reports model `L3`:

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
  - type: esp.device.lightbulb        # the device type the firmware declares
    entities:
      - platform: light
        name: "Lightbulb"
        key: lightbulb
        attributes:
          state:                       # the light's switch, from Power
            param: "Power"
            data_type: bool
            properties: [read, write]
      - platform: number               # the custom parameter, as a slider
        name: "Light Intensity"
        key: light_intensity
        attributes:
          value:
            param: "Intensity"
            data_type: int
            properties: [read, write]
            bounds: { min: 0, max: 100, step: 1 }
```

`services:` (optional, same block structure) is not used in this example;
see below. A device block may also carry a `name:` narrowing it to one
device instance name.

- **`version: 1`**: the file is rejected when it is missing or different.
- **`vendor` / `product`** are required and together form the product id,
  `acme/bulb`, which is how the product appears in the list, in diagnostics and
  in a device's binding. Nothing is read off the file's name or folder, so a
  file keeps its identity wherever it is put or however it is renamed; a file
  missing either key is refused and reported as a repair.
- **`product_label`** is the default name shown in the list; the id is shown
  when absent. Optional **`product_labels`** entries replace it when their
  language matches Home Assistant's configured language (a regional tag
  such as `en-GB` uses the base language's entry), and otherwise fall
  back to `product_label`. Keys must be language codes Home Assistant
  reports (`en`, `zh-Hans`, `zh-Hant`, ...); any other key, such as
  `en-Hans`, is refused by validation, since it would match no user.
- **`vendor_label`** and **`vendor_labels`** provide the corresponding display
  names for the stable, case-sensitive `vendor` id.
- **`model`** is required: the model the product's devices report
  (`esp_rmaker_node_add_model`), compared byte for byte. `"L3"` is for devices
  reporting `L3` and no other, which is what tells two lights of one vendor
  apart; it follows the name in the list (`Acme dimmable bulb-L3`) and is the
  device page's model id. Espressif's per-type reference profiles name `"*"`,
  and are for devices whose firmware reports `*` - a device built on the
  reference firmware. A vendor's device is listed under the vendor's own
  product, never under a reference one. An empty string is refused.

  Leaving the key out is an error, so every file says what it is for. No field
  in the file lets the integration choose for the user: which product a device
  gets is decided in the list, by the user alone.
- **`devices:`** holds every **device** type the product reports, the
  `esp.device.*` entries of the node's `devices[]`. One `(type, name)` pair may
  appear only once per file.
- **`services:`** holds **service** types, the `esp.service.*` entries of the
  node's `services[]`. The ten shared RainMaker services (`ota`, `time`,
  `schedule`, `scenes`, `system`, `local_control`, `connectivity`, `groups`,
  `rmaker-controller`, `rmaker-user-auth`) come from the built-in
  `device_profiles/service_layer.yaml`, so a product profile need not repeat them; a
  type it does declare takes the place of the built-in one. This is filling in,
  not overriding: only what the product leaves out comes from the layer.

`devices:` and `services:` are two independent lookup tables: a service type
written under `devices:` raises no error and never matches.

## Reusing an existing definition

The shipped files and how to copy their blocks by device type are covered
in the [developing guide](developing_a_product_profile.md#writing-a-profile).

## Matching the firmware

How firmware and profile correspond item by item - model, device type,
instance name, parameter names and parameter shapes - is covered in the
developing guide under
[Firmware-side checklist](developing_a_product_profile.md#firmware-side-checklist)
and [Complete example](developing_a_product_profile.md#complete-example).

## Placement and selection

### Placing the file

Profiles of your own go in the `esp_weaver/` folder of the configuration
directory, which over Samba is `\\homeassistant\config\esp_weaver\`. Subfolders
are fine for sorting but mean nothing: the file's own `vendor` and `product`
decide where it is listed. Create the folder if it is not there; `.yaml` and
`.yml` both work. The folder is outside the integration,
so an upgrade leaves it alone.

Three equivalent ways, differing only in the access they need:

| Method | Steps |
|--------|-------|
| **Put the file there** | Samba or the File Editor add-on on Home Assistant OS; a plain copy on Core. Per-platform access is in the next section |
| **Upload** (browser only) | **Settings** → **Devices & services** → **Add integration** → **ESP-Weaver**; on the first step pick the file and submit. It is saved into `esp_weaver/` under its own name and appears in the list that follows. Tick "Replace a profile of the same name" when re-uploading an edit |
| **Download from a link** (the profile is published, and at least one device exists) | **Settings** → **Tools** → **Actions** → `esp_weaver.reload` → fill in `url` → run. Tick `overwrite` for an update |

> **The Actions page moves between Home Assistant versions.** It is currently
> **Settings** → **Tools** → **Actions**. Before 2026.2 it sat in the sidebar as
> **Developer tools**; from 2026.8 it is **Tools** under Settings. On any
> version, press `C` in the interface to open the command palette and search
> for the action.

Placing a file only **puts it in the list**. It changes no device that has
already been added.

### Choosing when adding a device

Before asking how to reach the device, the flow asks "Which device are you
adding?": the vendor first, then one of that vendor's products. You know what
you bought; the device need not be connected to say so. Between them the two
steps hold **every file** found in four sources:

| Source | Where | Notes |
|--------|-------|-------|
| Built-in | `custom_components/esp_weaver/device_profiles/espressif/` | Ships with the integration; always available. A copy of the repository's `vendors/espressif/` taken at packaging time; while the two are identical, each Espressif product is one line |
| Your folder | `<config>/esp_weaver/` | Files you placed |
| Cached | `.storage/esp_weaver.profiles` | The copy a device was bound to, kept so it resolves offline; may be older than the file it came from, and is marked *cached* in the list |
| Repository | [`espressif/esp-weaver-profiles`](https://github.com/espressif/esp-weaver-profiles) on GitHub, read over HTTPS | A local copy under `.storage/esp_weaver.repository`, in two layers. The index - every product, a few hundred bytes each - is fetched as the list opens and the copy's listing brought up to it. A file is downloaded when you pick its product, and only that one - unless the copy already holds it at the hash the index promises, in which case nothing is fetched. A product already held is downloaded again by the refresh only when the index shows its hash moved, which is what raises the update repair for the devices bound to it. Each download is validated and hash-checked against the index. When the repository cannot be reached, the copy is listed as last synced and the list says so at the top; a product downloaded once is picked offline for good, while a product never downloaded is refused offline with `profile_fetch_failed` |

**Step one: the vendor.** A list of vendor names, one per vendor with a
profile. A file is listed under the `vendor` it names, wherever it sits.

**Step two: that vendor's products.** Each entry is the product's name and its
model (`product_label` and `model` joined by a dash, say `Light-A10`) - what is printed on the box. The
device type and the kind of product are not the user's concern and are not
shown, and neither is where the file came from, as long as the product is
held in one place only:

```
Lightbulb
Light
Humidifier
Air conditioner
Switch
Temperature sensor
```

Copies of a product that are the same - same name, same mapping - are one
line, wherever they sit: the built-in Espressif profiles and the
repository's, a file you copied into your folder unchanged, the copy kept
from a device's binding while its original is unchanged. There is nothing to
pick between them, so the first in list order stands for all (your folder,
then built-in, then cached, then repository).

A product held in more than one **version** is listed once per version, and
each of those lines says where its version is from in a word - `built-in`,
`repository`, `your folder`, `cached` - so you can pick between them.
Nothing is chosen for you, and no path or hash is shown, since the word is
enough to pick by. Lines of products with one version stay bare, whatever
the others do. A vendor with built-in files, one of yours describing the same
product differently, and a repository whose `humidifier.yaml` has moved on
since this version of the integration was packaged:

```
Switch  [built-in]
My switch A10  [your folder]
Lightbulb
Humidifier  [built-in]
Humidifier  [repository]
```

While the repository is read from a pull request, its lines say so instead
of `repository` - `Switch  [pull request #123]` - which is what the pull
request's author is looking for.

Only when two lines would still read the same is something more added: two of
your files naming the same `vendor` and `product` but mapping it differently
get their file name, two cached copies of one product their hash, and two
different products that share a `product_label` and `model` their file:

```
acme/light A10  [your folder: hall.yaml]
acme/light A10  [your folder: porch.yaml]
```

Once a device has been added, the product it was bound to is kept as a cached
copy. While the original is unchanged the list looks as before; when the
original has moved on, the copy the device is still bound to is a second
line:

```
Lightbulb  [built-in]
Lightbulb  [cached]
Light
```

The list is ordered by kind, so like products sit together: Lighting (light),
Climate (climate, fan, humidifier, water_heater), Covers & valves (cover,
valve), Security (lock, alarm_control_panel, siren), Media (media_player,
remote), Cleaning (vacuum, lawn_mower), Switches & outlets (switch), Sensors
(sensor, binary_sensor, event), Multi-device, Other. The kind is not a field
of the file and is not shown; it is read off the `platform` of the product's
entities, the first to match in that order.

Neither step chooses for you or preselects: a product present in two versions
is listed twice, and each such entry names its source in a word so the two
can be told apart; only identical copies are folded into one line. Both
fields filter as you type, so
`lightbulb` finds the first entry above. Typed text is not itself a choice; an
entry must be picked from the filtered list.

With the product chosen, the flow goes on to the connection (local or cloud),
discovery and the PoP.

**Local discovery lists only that product's devices.** The firmware advertises
its model in its mDNS TXT record - `model`, what `esp_rmaker_node_add_model`
set - which the integration can read before any handshake. Firmware that set
no model omits this field. (The record also carries `type`, the node type given to
`esp_rmaker_node_init`; it is not used.) Everything on the network is narrowed
to the devices the chosen product is for, so the user is not left to work out
which one that is. The rule is one line, and is applied twice:

- **Model**: the device's model has to be the one the product names, byte
  for byte. A product naming `A1` is for that model and no other, which is
  exactly what tells a vendor's two lightbulbs (`A1`, `A2`) apart. `"*"` is a
  model like any other: Espressif's reference profiles name it, and are for
  devices whose firmware reports it.

Device types are not part of this rule. They are settled once the device is
connected and its configuration read, entity by entity: a device type the
product declares but the device lacks builds nothing, and a device type the
device reports but the product does not declare is listed as unknown in the
summary.

Each line is the product's name and model, numbered - `Light-A1-1`,
`Light-A1-2`. Neither the node id, the address nor the security level is
shown; the user cannot match any of them to anything on the shelf.

**Older firmware that advertises no `model`** cannot be told from its
record, and is listed after the rest as `Other device-<n>`: what it is
is settled once it is connected. When nothing on the network advertised
itself as the product and nothing was silent, the flow ends and says how many
devices were found.

A cloud device has no mDNS record, and relies - as a local device did before
its handshake - on the check made after its configuration is read. Once the
device is connected and its configuration read, the same rule is applied
again to `info.model` in it: the TXT record was the device's headline, the
configuration is its full account. A device that advertised nothing is not
judged at all, now or on a later rebind: such firmware reports its project
name as `info.model`, which says nothing about the product it is. A device
that does not fit is **not added**; the flow says what the device reported and
what the product wanted, and the only way on is another product. One that fits
is bound at once. How many entities the product builds, which it cannot for
want of a parameter, and which device types and services it does not declare,
are written to the log.

Home Assistant's dialogs have no back button, so every form with a step
before it ends with the choice **Continue / Back to the previous step**, set
to continue: vendors go back to the import step, products to the vendors,
devices to the products, and the PoP form to the device list. Going back from
the device list scans the network again when a product is picked.

A device is named what its firmware calls it: the value of its `Name`
parameter (say "Living room light", set in the app), or the name it is
declared under (`Light`). A node of several devices goes by its own name. A
name advertised over mDNS is used only when the node names nothing; when
neither did, the device is `Other device`, to be renamed on its page -
never a vendor's name, since the products are many vendors', and never the
node id.

The device page shows two things side by side: what the device is treated
as, and what it says it is. Manufacturer and model are the bound product's
`vendor` and `product_label` - `espressif` and `Lightbulb` for a built-in profile -
written when the device is added and rewritten when its product is changed;
the device list filters by manufacturer. The serial number (the node id),
the firmware version (`info.fw_version`) and the model id are the device's
own. The model id is `info.model` when the firmware advertised a model in
mDNS - even if that deliberately equals the project name - except that the
reference firmware's generic `*` identifies no particular hardware and is
left blank. A device reporting `A2` while bound to the `A1` product shows
`Desk lamp (A2)`, so a mismatch after an OTA is visible on the page. The
name is left alone.

A device that is reached but does not report its configuration cannot be
compared against anything, so the flow ends there and says why. **The device
is not added**, and nothing is bound on its behalf; add it again once it is
online.

### Switching a device already added

**Settings** → **Devices & services** → **ESP-Weaver** → the device →
**Configure**. The vendor and product steps are the ones from adding, with the
current binding named at the top. Confirming reloads the device and rebuilds its entities from the new
product; entities whose `key` is unchanged keep their entity id and history.

### Reaching the configuration directory

On a Raspberry Pi or other Home Assistant OS device the configuration
directory is the `config` Samba share (internally `/config`), and the profile
folder is `esp_weaver/` under it. Connecting to that share from Windows,
macOS or Linux is covered once, in the README under
[Samba Share installation](../README.md#option-2-samba-share-home-assistant-ossupervised);
it is not repeated here.

Without Samba, the **File Editor** add-on creates and edits files in the
browser on any platform, and with the SSH add-on a file goes in directly:

```bash
scp plug.yaml root@homeassistant:/config/esp_weaver/acme/
```

The add-on store exists only on Home Assistant OS and Supervised; Container
and Core installations reach the configuration directory on the host
directly, or use upload or download.

## How an edit takes effect

A device is bound to **the file content at the moment it was confirmed**,
identified by a content hash and kept as a copy under `.storage`, not to a file
path. Editing, deleting or replacing the file afterwards, or the repository
publishing a new version, changes no device by itself. That is deliberate: a
device that works must not change because something else did.

After a file changes, the integration re-reads the folders and compares on
start-up, when a device is added, when a Configure dialog is opened, and when
`esp_weaver.reload` runs. Where a product's content differs from what the
devices on it are bound to, a repair "`<label>` has a newer profile" appears
under **Settings** → **System** → **Repairs**, naming every device on that
product. Opening it shows, per device, how many entities it has now and how
many it would have; confirming moves all of them to the new content in one go
and reloads them. Left alone, nothing changes.

`esp_weaver.reload` therefore no longer rewrites entities directly. It re-reads
the folders, refreshes the repository copy and the repairs above, and reloads
every device, each still resolving against its own binding. Leave the three
fields empty; with `url` it downloads first and then does the same. **Settings**
→ **Tools** → **YAML** → **Reload all YAML configuration** triggers the same
action.

Common pitfalls:

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| The file changed, the device did not | The device keeps the content it was bound to | Confirm the update under **Repairs**, or re-pick the product in the device's **Configure** dialog |
| Uploaded under a new name | Two entries appear once the two files differ, both marked `your folder` and each carrying its file name | Keep the name and tick replace, or delete the old file |
| `overwrite` not enabled on the **Actions** page | Shown ticked but submitted as `false`; the file is reported as already existing | Turn on the field's enable switch first, then set the value; or write `overwrite: true` in YAML mode |
| Looking for `esp_weaver.reload` before any device exists | The action is not on the **Actions** page | It is registered when the first device is added; upload or place the file instead |
| The device is offline | Its entities stay as they are | Reload again once the device is online |

## Publishing to the repository

A product profile meant for many users can be submitted by pull request to
the profile repository, [`espressif/esp-weaver-profiles`](https://github.com/espressif/esp-weaver-profiles) on GitHub, as
`vendors/<vendor>/<product>.yaml`. Once merged, every user sees it in the list
when adding a device. The repository's README says how to submit and what
review covers.

`index.json` at the repository root lists each product's id, vendor and product
display names, `model`, path and content hash, generated by the repository's
own tooling (`tools/build_index.sh`, on `tools/profilelib/`), which
implements the same schema and content hash as the integration; the format
it judges against is the repository's
[Profile format](https://github.com/espressif/esp-weaver-profiles/blob/master/docs/profile_format.md).
What a vendor's pull request carries, what its CI checks, how the index is
regenerated and what review covers are the repository README's subject, not
repeated here. On this side, the integration fetches the whole index and
never queries by model; it downloads the files whose hash is new to its
local copy, and each has to hash to what the index promised. Espressif's own
products are edited in the repository like any vendor's; the built-in copies
under `device_profiles/espressif/` are updated by maintainers when those
change. No CI syncs or compares the two trees (or the two copies of the
profile code) across the repositories; maintainers run
`tools/check_shipped_profiles.py` and `tools/check_profile_code_sync.py`
by hand against a local checkout.

A pull request's files are prepared so they can be tried before the merge,
once the field below is enabled. Its CI
(`.github/workflows/profile_index.yml`) builds the pull request's own
`index.json` with the tool on each push and publishes it on the `ci-indexes`
branch of `espressif/esp-weaver-profiles` as `pr-<number>/index.json`, naming the fork
and commit it was built from; the directory goes when the pull request
closes. The integration can read that index in place of the published one:
it lists the pull request's products and downloads a product's file by the
named commit when it is picked, as with the published repository. A file CI
refused is refused on the form with CI's words. The choice is kept in
`.storage/esp_weaver.repository` and read again at each start,
`esp_weaver.reload` and each opening of the list; the list says at the top
which pull request, at which commit, it shows. The index is read through
GitHub's anonymous API, 60 calls an hour, one per read; past that as a raw
file, which may lag a few minutes.

The field for this - the pull request number (`123`, `#123` or the URL),
on the first step of **Add device** - is not
shown in this release. The mechanism stays in place; the field is turned on
with `REPOSITORY_SOURCE_FIELD_ENABLED` in `const.py`, and when it is, it shows
what is followed next time and clearing it returns to the published index.

## Further notes

### Lookup rules

Within a product profile an entry is identified by the **pair** `type` and
`name`. Under one `type` there may be several entries narrowed to device
instance names and one entry without `name:`, the **default entry**. Resolving a
device looks first for an entry matching both `type` and `name`, then falls back
to the type's default entry. With only narrowed entries, other devices of the
type have no mapping.

The lookup covers the bound file plus the built-in service layer, and no other
file.

### What editing each field does

| Field changed | Effect on existing entities |
|---------------|----------------------------|
| `name`, `icon`, `device_class`, `state_class`, `unit`, `display_precision`, `entity_category`, and under an attribute `param`, `bounds`, `scale`, `map`, `options`, `invert`, `properties`, `optional`, `auto_reset`, `alert`, `extra_attributes` | **Safe.** Entity id, history and automation references are kept; only attributes or behaviour change |
| `key` or `platform` | **The entity is replaced.** These decide the `unique_id`, so the change deletes the entity and creates another: registry customisations and area assignment are lost |
| Deleting an entity block; changing `param` to a name the device does not report without `optional: true`; changing `platform` to `none` | **The entity disappears.** Parameter names are matched exactly, and a missing required attribute drops the whole entity |
| `model` | **An update.** The content hash covers `model` along with `devices` and `services` as written in the file (after the file validates), since it decides which devices the product is for; bound devices get a "newer profile" repair, and until they accept it they keep matching the old model. Schema defaults do not enter the hash |
| `product_label`, `product_labels`, `vendor_label`, `vendor_labels`, `vendor`, `product` | **Not an update.** Outside the content hash, so these never raise a repair. Changing `vendor` or `product` changes the product id, which is another product as far as bound devices are concerned |

Do not change `key` once in use. Use `name` for the displayed name; several
devices of one type need no `key` change.

`entity_id` derives from the device and entity `name`, **not** from `key`: a
`key`-only change usually keeps the `entity_id`, and history follows. Only when
`name` changed too, or the old `entity_id` was not released in time, does the
new entity get another id (`_2` appended) and dashboards and automations
referring to the old one break. Judge identity by `unique_id` in diagnostics,
not by the name shown.

### Several devices

**Devices of one product are bound to the same profile and each get their own
entities, without conflict.** Ten identical lamps share one
`esp.device.lightbulb` definition and one `key`.

**Device names are set in Home Assistant**, not in the profile. After renaming,
the front end shows "device name + entity name". That name lives in Home
Assistant and survives reloads and restarts.

Only one case needs separate mappings within a profile: **device instances of
one type on one node that report different parameters.** Add `name:` to the
device block, matching the instance name in the firmware:

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
    name: "Living Room Light"      # the instance name in the firmware, not the Home Assistant device name
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

`name:` must match the instance name the firmware reports **verbatim, case
included**, that is the first argument of the call that created the device -
`esp_rmaker_device_create()`, or a standard device helper such as
`esp_rmaker_lightbulb_device_create()`. On a
mismatch the device falls back to the default entry, or has no entities when
there is none, with no error. The reported instance name is `reported.name` in
diagnostics.

### Several files

Each file under `<config>/esp_weaver/` is an independent product. Files do not
affect one another; there is no load order and nothing overrides anything. Two
files declaring the same device type is normal: they are two products'
definitions, and whichever is picked is used.

Two files with the same id (the same `vendor`/`product` in both) are both
listed as long as they differ, each marked with where it is from, and where
that is the same place, with its file name; it is you who picks. A file of
yours with the same id as a built-in product, such as
`esp_weaver/espressif/switch.yaml`, is listed next to it as `your folder`
once you have changed it; unchanged, it is the same line.

Deleting a file leaves the devices bound to it as they are; they keep the
content they were bound to. The file is simply no longer in the list, and no
update can reach those devices.

## Troubleshooting

### Look at what the device reports first

**Settings** → **Devices & services** → **ESP-Weaver** → the device →
**Download diagnostics**. The JSON is long; a few parts matter.

**`profile_files.binding`** is the product the device is bound to: id, source
and content hash. **`profile_files.products`** lists every product
on offer with source, location and hash; **`profile_files.problems`** the files
that could not be read.

**`profiles.reported`** is what the device reports, to be compared with the
profile and the firmware:

```json
{
  "kind": "device",
  "name": "Light",
  "type": "esp.device.lightbulb",
  "params": ["Name", "Power", "Brightness", "Hue", "Saturation",
             "CCT", "Intensity", "Light Mode"]
}
```

**`profiles.registry.product`** is the product actually resolved against, which
should equal `binding.id`. **`profiles.entities`** is the mapping in effect,
with `params` keyed by YAML attribute and valued by device parameter; against
`reported.params` it shows which parameters no entity uses yet.
**`profiles.skipped`** lists skipped entities with reasons, and the device
types the product does not declare.

### The profile has no effect

Check in this order:

1. **Which product the device is bound to.** `profile_files.binding.id` in
   diagnostics, or "current binding" at the top of the **Configure** dialog.
   Switch there if it is not the intended one.
2. **Whether it is bound to the current content.** A device does not follow
   an edited file on its own; see [How an edit takes effect](#how-an-edit-takes-effect).
   Confirm a "newer profile" repair if there is one.
3. **Whether the file is in the list.** If adding a device or opening Configure
   does not show it, it was not read: check it is under `<config>/esp_weaver/`,
   ends in `.yaml`/`.yml` and does not start with `.`; then look under
   **Settings** → **System** → **Repairs**, where a file that failed validation
   is reported with the error.
4. **Whether the device type matches.** The summary after choosing lists the
   device types the product does not declare, as does
   `profiles.skipped.device_types`. Compare `type` with the firmware.
5. **Whether the top-level keys are right.** Device types under `devices:`,
   service types under `services:`.
6. **Encoding.** The file must be UTF-8. One re-saved in a local codepage by
   Windows Notepad is rejected whole and reported as `is not UTF-8 encoded`.
7. **Location.** A file in `custom_components/esp_weaver/device_profiles/` is
   read and listed but is overwritten on upgrade, and a repair says so; move it
   to `<config>/esp_weaver/`.

### Some entities are not created

Most often a parameter name does not match. The summary after choosing lists
the entities short of a parameter; the log shows it under `is not created`:

```text
Node 6055F9F91114: 'Ceiling Light' of esp.device.lightbulb needs 'Power222',
which 'Light' does not report, so the entity is not created.
Parameter names are matched exactly
```

Either change `param` to the name the device reports, or mark the attribute
`optional: true` where not every device has the capability. Under one
`esp.device.lightbulb` type a dimmer reports `Brightness`, a colour lamp adds
`Hue` and `Saturation`, and a plain switchable lamp only `Power`, so everything
but `Power` should be `optional: true`.

Without that log line there are two other causes:

**The device block's `name` does not match.** The device falls back to the
default entry and everything the narrowed entry would provide is missing,
silently. Compare `reported.name` in diagnostics with `name:` verbatim.

**`platform` is not an implemented platform.** A typo, or a platform this
version does not implement, validates but is skipped when resolving, leaving
only a debug-level `platform ... is not implemented`. Valid values are under
[entity block](#entity-block).

### The device shows "Failed to set up" and every entity is unavailable

The integration page marks the device as failed, with the reason: the content
it is bound to is no longer stored, or it has no binding. A device resolves
against exactly the content confirmed when it was bound; when that cannot be
produced, neither a file of the same name nor a shipped product is put in its
place, since the entities they would build are not what you confirmed. Remove
the device and add it again, choosing its product; entities whose `key` is
unchanged keep their entity id and history.

### The device has no entities at all

Search the log for `no profile declares`. The device reports a type the bound
product does not declare, and the line suggests the closest one: a typo in the
firmware, the wrong product chosen, or a block missing from the profile.

```text
Node 6055F9F91114: no profile declares device type 'esp.device.lightbolb',
so 'Light' contributes no entities. Did you mean 'esp.device.lightbulb'?
```

### An entity exists but is not as expected

**The entity id changed and history is gone.** `key` or `platform` changed; see
[What editing each field does](#what-editing-each-field-does). Restore the
value and update the binding, and the entity comes back under its old id,
usually with its history.

**Changing `name` changed nothing on screen.** Two possibilities. The `key` has
a built-in translation, in which case the shown name comes from the translation
and `name` is only the fallback; among the built-in keys, `light`,
`binary_sensor` and the sensor and threshold keys are translated, `lightbulb`,
`fan`, `switch` and others are not - search
`custom_components/esp_weaver/translations/<language>.json` for
`entity.<platform>.<key>.name`. Or what you meant to change is the device name,
set from the device page's settings and unaffected by the profile. `name` also
decides a new entity's id, but only when the entity is **first** created.

**An entity deleted from the YAML is still there.** Usually the last resolve
did not complete, because the device was offline or reported something odd. No
entity is removed when nothing at all resolved. Reload once the device is
online.

### Verbose logging

Turning on debug logging, temporarily or permanently, is covered once in
the README under [Troubleshooting](../README.md#troubleshooting); it is not
repeated here. With it on, the lines worth searching for are:
`Profile catalog holds N products` for what was read;
`resolved N bindings: [...]` for the entity keys resolved; `Registered new ...
entity` and `Removing ...` for creation and removal.

## Field reference

### Top level

The top-level fields — `version`, `vendor`, `vendor_label`,
`vendor_labels`, `product`, `product_label`, `product_labels`, `model`,
`devices` and `services` — are specified in the profile repository's
[Profile format](https://github.com/espressif/esp-weaver-profiles/blob/master/docs/profile_format.md),
which is the canonical text for their rules (the ids, the language codes,
the model). They are not repeated here; this reference covers the blocks
below, whose fields the integration itself interprets.

### Device block

Blocks under `services:` have the same structure.

| Field | Required | Meaning |
|-------|----------|---------|
| `type` | yes | Device or service type as the firmware reports it, **exact match** |
| `name` | no | Applies only to the device instance of this name, **exact, case-sensitive**. Without it the block applies to every device of the type; see [Lookup rules](#lookup-rules) |
| `entities` | yes | Entity definitions |

### Entity block

| Field | Required | Meaning |
|-------|----------|---------|
| `platform` | yes | Home Assistant platform, see below. `none` marks a parameter that by design has no entity |
| `key` | yes | Entity identity, **never to be changed once set** |
| `name` | yes | Source of the entity id, and the fallback display name |
| `attributes` | yes | Attribute name to device parameter, except for `platform: none` |
| `icon` | no | MDI icon |
| `device_class` | no | Device class |
| `state_class` | no | Statistics class, affects long-term statistics |
| `unit` | no | Unit |
| `display_precision` | no | Display precision, an integer ≥ 0 |
| `entity_category` | no | `config` or `diagnostic` |
| `extra_attributes` | no | Read-only extra attributes |
| `auto_reset` | no | Reset of a momentary state, `{ to, after }` both required, `after` in seconds |
| `alert` | no | Fires an `esp_weaver_alert` event when `attribute` enters one of `levels`, and another when it leaves; see [Alert events](#alert-events). `attribute` must be declared under this entity's `attributes` or `extra_attributes`, or the file fails validation |
| `threshold` | no | On a `number` only: `{ of, end }` names a sensor of the same device block and which end of its range (`min` or `max`) this number sets. The sensor fires an `esp_weaver_alert` event when its reading passes the number, and again when it comes back |

`platform` takes one of 25 values: `alarm_control_panel`, `binary_sensor`,
`button`, `climate`, `cover`, `date`, `datetime`, `event`, `fan`, `humidifier`,
`lawn_mower`, `light`, `lock`, `media_player`, `number`, `remote`,
`select`, `sensor`, `siren`, `switch`, `text`, `time`, `vacuum`,
`valve`, `water_heater`, and `none` for no entity. Other values validate but are skipped silently when
resolving; see [Some entities are not created](#some-entities-are-not-created).

### Alert events

Nothing in a profile makes the integration notify anyone. An `alert` block, or
a `threshold` on a number, makes the sensor fire an `esp_weaver_alert` event
on the Home Assistant event bus when the alert begins and another when it ends.
What happens then - a notification on a phone, a light, nothing - is for an
automation to decide, which is where Home Assistant expects that decision to
be made.

Every event carries:

| Field | Meaning |
|-------|---------|
| `kind` | `level` for an `alert` block, `threshold` for a threshold number |
| `active` | `true` when the alert begins, `false` when it ends |
| `node_id` | The device's node id |
| `device` | The device's name |
| `entity_id` | The sensor's entity id |
| `sensor` | The sensor's name in the profile |

An alert that begins also carries its wording, `title` and `message`, plus
`level` and `state` for `kind: level`, or `end` (`high` or `low`), `value`,
`threshold` and `unit` for `kind: threshold`. An alert that ends carries only
`level` or `end`.

The `title` and `message` of an `alert` level may use the placeholders
`{device}`, `{state}` and `{level}`; a placeholder that does anything more
than name one of them - `{state.__class__}`, `{device[0]}` - is refused and
logged, and no event is fired. Threshold wording comes from the integration's
translations, in the server's language.

An automation reacting to a low battery:

```yaml
automation:
  - alias: Battery low
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

### Attribute block

With only a parameter name the block collapses to one line: `state: "Power"`.

| Field | Required | Meaning |
|-------|----------|---------|
| `param` | yes | Device parameter name, **exact match** |
| `optional` | no | Default `false`. When `true`, the entity is built even if the device does not report the parameter |
| `properties` | no | `read`, `write` or both. Left out, the access is whatever the device reports. Must equal the device's read/write flags; the device's storage flags (`time_series`, `simple_ts`, `persist`) are not part of the comparison |
| `data_type` | no | `bool`, `int`, `float`, `string`, `array`, `object`. Left out, taken from the device; otherwise must equal the device's |
| `bounds` | no | `{ min, max, step }`, `min` and `max` required, `step` optional. Left out, taken from the device; otherwise the whole block must equal the device's. Used by a `number` (slider range), a `light` colour temperature, a `climate` or `water_heater` target temperature, and by `scale`; a read-only reading has no use for it |
| `scale` | no | `{ to: [low, high] }`, a linear mapping from device to Home Assistant values. Exactly two numbers |
| `map` | no | Device value to Home Assistant value table |
| `options` | no | Allowed values, as strings. Left out, taken from the device's `valid_strs`; otherwise must hold the same values, in any order. A device reporting an empty value reads as unknown; any other value outside the list is shown as reported |
| `invert` | no | Negate on read and write |
| `state_when` | no | Rewrite a sensor reading by a companion parameter's value |
| `type` / `ui_type` | no | RainMaker type annotations. Left out, taken from the device; otherwise must equal the device's |

A field the profile states that differs from what the device reports leaves the
entity out; the log names the field and both values, and the summary shown when
the device is added lists the entity.
