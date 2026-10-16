# Developing a product profile: from firmware to Home Assistant

[中文](developing_a_product_profile_zh.md)

This guide is for firmware developers writing a profile for their own
product for the first time. On completion it yields a profile that can be
used as written, and a basis for locating why an entity was not created
as expected. How the integration treats a profile - how the list is
assembled, how an edit takes effect, what changing each field does - is
specified in [Device profiles in detail](device_profiles_in_detail.md); what a file
may legally contain is specified in the profile repository's
[Profile format](https://github.com/espressif/esp-weaver-profiles/blob/master/docs/profile_format.md).

A device running standard RainMaker firmware requires no profile: select
the built-in Espressif product when adding it. A custom profile is
required in two cases: the firmware declares a non-standard device type,
or a parameter outside the specification is added to a standard device.

## Correspondence

A profile maps the devices and parameters the firmware reports to
Home Assistant entities.

| Firmware side | Written in the profile | Home Assistant side |
|---------------|------------------------|---------------------|
| One node | One product profile | One device |
| A device - built with `esp_rmaker_device_create()`, or a standard device helper such as `esp_rmaker_lightbulb_device_create()` - of type `esp.device.*` | One `- type:` block under `devices:` | A set of entities |
| A parameter - a standard parameter helper such as `esp_rmaker_intensity_param_create()`, or `esp_rmaker_param_create()` | One attribute under an entity, its `param:` naming the parameter | One aspect of an entity (on/off, brightness, a number…) |
| What the parameter looks like | The entity's `platform` decides what it becomes | light, switch, sensor, number… |

Terminology used in this guide:

- **Product**: one hardware product. `vendor` and `product` together form
  its id; `model` is the model the device reports. A device is bound to
  exactly one product.
- **Device type (`type`)**: the category name the firmware declares for a
  device, such as `esp.device.lightbulb`. A device type is not a Home
  Assistant entity platform.
- **Entity**: an object visible and controllable in Home Assistant, such
  as a light or a temperature reading. One device type can produce
  several entities.
- **`key`**: an entity's unique identity. It must not be changed once in
  use; a changed `key` produces a new entity, and the previous entity's
  history is no longer associated with it.

Three rules apply to every mapping:

- `type`, device instance names and parameter names (`param`) are matched
  **exactly**, including case. A mismatch raises no error; the entity is
  not created.
- **A profile must declare every device type of the node.** The device is
  resolved against this one file only; a type it does not declare is not
  supplied from any other file. The shared services (`esp.service.*`) are
  the exception: they are provided by the integration's built-in service
  layer and need not be declared.
- A parameter the firmware does not report creates no entity. A parameter
  only some models report is marked `optional: true` in the profile.

## Firmware-side checklist

Before writing the profile, confirm the following from the firmware
code. The profile and the firmware are two ends of one mapping contract;
wherever they disagree, the corresponding entity is not created.

| Check | Where in the firmware | In the profile |
|-------|-----------------------|----------------|
| Model | `esp_rmaker_node_add_model(node, "L3")`, before `esp_rmaker_start()` so it reaches the mDNS record | Top-level `model: "L3"`, byte for byte |
| Device type | A standard device helper fixes it: `esp_rmaker_lightbulb_device_create("Light", NULL, false)` reports `esp.device.lightbulb`; a device of your own passes its type to `esp_rmaker_device_create()` | `- type: esp.device.lightbulb` |
| Instance name | Its first argument, `"Light"`; only needed with several devices of one type | The device block's `name:` (optional) |
| Parameter name | A standard parameter helper's name, e.g. `esp_rmaker_intensity_param_create(ESP_RMAKER_DEF_INTENSITY_NAME, ...)` is `"Intensity"`; a parameter of your own is named in `esp_rmaker_param_create("Intensity", ...)` | The attribute's `param: "Intensity"` |
| Parameter shape | Data type, read/write flags, bounds, valid values | `data_type`, `properties`, `bounds`, `options` - written values must agree with the firmware |

The six fields under an attribute that describe the parameter's shape -
`type`, `data_type`, `ui_type`, `properties`, `bounds` and `options` -
admit a simplified form. **Left out, each is taken from what the firmware
reports; written, it must agree with the firmware, or the entity is not
created.** A first profile should therefore state only `param` and leave
the six empty, which minimises the surface for disagreement. No other
field works this way: `platform`, `name`, `key` and `param` are required
outright; `optional` defaults to `false`; leaving out `scale` or `map`
means no conversion; an entity's `unit` or `device_class` left out is
simply unset.

The `Name` parameter requires no entity: the integration uses it as the
device name.

## Complete example

In this example the firmware reports only `Power` (on/off) and an added
`Intensity` (a read/write integer, 0–100), with model `L3`. The target
configuration is one light entity and one number entity (a slider).

The firmware side (excerpt, limited to the parts relevant to the profile):

```c
/* The model; set before esp_rmaker_start() */
esp_rmaker_node_add_model(node, "L3");

/* A standard lightbulb device, as in the RainMaker led_light example:
 * Name and Power come with it, under the default names */
light_device = esp_rmaker_lightbulb_device_create("Light", NULL, false);

/* A standard parameter: the helper already carries the type and the
 * 0-100 bounds, and ESP_RMAKER_DEF_INTENSITY_NAME is "Intensity".
 * A parameter of your own uses esp_rmaker_param_create(), with
 * esp_rmaker_param_add_bounds() / esp_rmaker_param_add_ui_type()
 * as needed. */
esp_rmaker_device_add_param(light_device,
        esp_rmaker_intensity_param_create(ESP_RMAKER_DEF_INTENSITY_NAME, 50));

esp_rmaker_node_add_device(node, light_device);
```

The profile, a complete working file:

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

The top-level fields:

- `vendor` / `product` are the id: lowercase letters, digits, `_`, `-`,
  `.` only, and never changed once published. Names for display go in the
  labels, never in the id.
- `vendor_label` / `product_label` are the default display names, in
  whatever language you write them; `*_labels` add translations keyed by
  Home Assistant language code (`en`, `zh-Hans`, `zh-Hant`, …). All four
  labels are optional - the id itself is shown when absent - but a real
  product should carry them.
- `model` is required and compared byte for byte against what the device
  reports. The built-in files name `"*"` because they are for the reference
  firmware; your own product names its own model, never `"*"`.

Adding this device and selecting `acme/bulb` creates two entities: the
light (on/off) and Light Intensity (a slider). A parameter the firmware
reports but the profile does not declare produces no entity, and no error
is reported.

## Writing a profile

A production product usually reports more parameters, and often several
device types. The profile should be derived from the built-in Espressif
definitions, as follows:

1. **Copy an existing definition.** For every device type the node reports,
   the file named after the type under
   `custom_components/esp_weaver/device_profiles/espressif/`
   (`lightbulb.yaml`, `switch.yaml`, …) holds the block for it; copy its
   whole `- type: ...` block under `devices:` in the file, one block per
   type. Where a type has no file of its own, start from the closest
   definition.
2. **Adjust it against the firmware.** Make `type` match the type the
   firmware reports and every `param` its parameter name, then add and
   remove entities and attributes as required. A copied block must be
   kept whole: a file that declares only the added entity loses every
   entity the block originally produced.
3. **Place the file and select the product.** Put the file in the
   `esp_weaver/` folder under the configuration directory (next section)
   and select the product when adding the device.

Parameter names defined by the RainMaker specification should be used
wherever one applies. A custom parameter name is recognised by neither
the phone app nor the built-in profiles, so a device using one depends
on the custom profile in every environment.

## Choosing an entity type for a custom parameter

For a custom parameter, determine whether it can be read, whether it can
be written, and what type of value it carries, then select the entity
type from this table:

| Parameter shape | platform | attribute | Notes |
|-----------------|----------|-----------|-------|
| Read/write on-off, wanted as a switch | `switch` | `state` | A light's switch goes with the light: `light`'s `state` |
| Read-only on-off (door contact, motion) | `binary_sensor` | `state` | Add `device_class` to say what it senses |
| Read-only number or state text (temperature, battery, current mode) | `sensor` | `state` | `device_class` and `unit` for numbers; `device_class: enum` for text |
| Read/write number (threshold, delay, intensity) | `number` | `value` | `bounds` gives the range and step |
| Read/write, one of a few fixed values | `select` | `state` | Write `options`, or leave them to the firmware's valid values |
| Read/write free text (time zone, a name) | `text` | `value` | |
| A momentary action (reboot, reset) | `button` | `press` | |

Complete device types - lights, air conditioners, covers - must not be
assembled from this table; the built-in definition block should be
reused instead. Its attribute names (a light's `brightness`, `hue`, …)
are fixed by the specification, and reusing the block avoids omissions
and misspellings.

The attribute name (`state`, `value`, `press`) is internal to the profile
and must be one of these; the firmware's parameter name goes after
`param`. Do not confuse the two.

A parameter only some models report takes `optional: true`; models without
it skip that attribute and keep the rest of the entity. Without the mark, a
missing parameter costs the whole entity.

## Placement and first verification

The file is placed in the `esp_weaver/` folder under the configuration
directory - `\\homeassistant\config\esp_weaver\` over Samba. File names and
subfolders play no part; which vendor a file is listed under is decided by
the `vendor` it declares. Three placement methods are equivalent: copy
the file, upload it on the first step of adding the integration, or give
`esp_weaver.reload` a `url`. Reaching the configuration directory is
covered in the detailed guide under
[Reaching the configuration directory](device_profiles_in_detail.md#reaching-the-configuration-directory).

Placement makes the profile selectable; it does not apply it to a
device. First verification should proceed in this order:

1. **Add the device**: select the vendor, then the product, then the
   device, and enter the PoP.
2. **Review the summary shown when the flow completes.** It states how
   many entities were created, which were not, and which device types
   the profile does not declare; it is the primary basis for comparison.
3. **Where an entity does not match, download diagnostics**: on the
   device page, select **Download diagnostics** and inspect
   `profiles.reported` - the device types and parameter names the device
   actually reported. Comparing it against the profile and the firmware
   code locates the disagreement.
4. **An edit does not take effect by itself.** A device is always built
   from the profile content confirmed when it was added. Under
   **Settings** → **System** → **Repairs**, confirm the "has a newer
   profile" repair and the device is rebuilt from the new content; for a
   single device, re-selecting the product in its **Configure** dialog
   has the same effect.
5. Repeat step 2 until the entities created match the expectation.

A file that is itself invalid (YAML syntax, an illegal field) does not
stop the integration: the file is rejected, and a repair issue reports
what is wrong and where. The file must be UTF-8.

## Common problems

| Symptom | Cause |
|---------|-------|
| An entity was not created, with no error reported | `param` or `type` does not match the value the firmware reports, including case. Compare `profiles.reported` in diagnostics |
| The device does not appear in the product list | The firmware sets no `model`, or a different one than the profile declares, so the device is listed as Other device |
| The file was changed and the device did not change | The update has not been confirmed under **Repairs**, or the product not re-selected in **Configure** |
| Only the added entity was declared and the original entities are missing | A device is resolved against this one file only; what it does not declare is not supplied from elsewhere |
| A rename changed the entity id and its history was lost | `key` was changed instead of the display name. Rename with `name`; `key` must not be changed |

## Next steps

- **To publish the profile for all users**: submit it by pull request to
  [`espressif/esp-weaver-profiles`](https://github.com/espressif/esp-weaver-profiles),
  as `vendors/<vendor>/<product>.yaml`. The repository's README specifies
  the submission and review requirements.
- **For every value a field may take**: the [Field reference](device_profiles_in_detail.md#field-reference).
- **For the complete treatment by the integration**: [Device profiles in detail](device_profiles_in_detail.md) -
  the list's assembly rules, how edits take effect, troubleshooting by
  symptom.
