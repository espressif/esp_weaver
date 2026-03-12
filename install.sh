#!/bin/bash
# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

set -e

# ESP-Weaver Integration Installer
# Usage: ./install.sh /path/to/homeassistant/config

# Check the number of input parameters
if [ $# -ne 1 ]; then
    echo "ESP-Weaver Integration Installer"
    echo ""
    echo "Usage: $0 [config_path]"
    echo ""
    echo "Examples:"
    echo "  $0 /config                          # Home Assistant OS/Supervised"
    echo "  $0 ~/.homeassistant                 # Home Assistant Core"
    echo "  $0 /usr/share/hassio/homeassistant  # Home Assistant Supervised"
    exit 1
fi

# Get the config path
config_path=$1

# Check if config path exists
if [ ! -d "$config_path" ]; then
    echo "Error: $config_path does not exist"
    exit 1
fi

# Check if config path is writable
if [ ! -w "$config_path" ]; then
    echo "Error: $config_path is not writable. Try running with sudo or check permissions."
    exit 1
fi

# Get the script path
script_path=$(cd "$(dirname "$0")" && pwd)

# Set source and target
component_name=esp_weaver
source_path="$script_path/custom_components/$component_name"
target_root="$config_path/custom_components"
target_path="$target_root/$component_name"

# Check if source exists
if [ ! -d "$source_path" ]; then
    echo "Error: Source path $source_path does not exist"
    exit 1
fi

# Safety check: ensure source and target are not the same (avoid deleting source)
# Run unconditionally before any copy/delete operations
if [ "$source_path" -ef "$target_path" ] 2>/dev/null; then
    echo "Error: Source and target paths are the same. Cannot install from config directory."
    exit 1
fi

# Remove the old version if exists
if [ -d "$target_path" ]; then
    # Safety check: ensure target_path ends exactly with /custom_components/<component_name>
    # Using exact match to prevent /custom_components_backup/ or similar from matching
    expected_suffix="/custom_components/$component_name"
    if [[ ! "$target_path" == *"$expected_suffix" ]]; then
        echo "Error: Invalid target path (must end with $expected_suffix): $target_path"
        exit 1
    fi
    # Additional check: ensure the path component before custom_components is valid
    # (no extra segments after component_name)
    if [[ "$target_path" != "${target_path%/}" ]]; then
        echo "Error: Target path must not have trailing slash: $target_path"
        exit 1
    fi
    echo "Removing old version..."
    rm -rf "$target_path"
fi

# Copy the new version
echo "Installing ESP-Weaver integration..."
mkdir -p "$target_root"
cp -r "$source_path" "$target_path"

# Done
echo ""
echo "✓ ESP-Weaver installation completed!"
echo ""
echo "Next steps:"
echo "  1. Restart Home Assistant"
echo "  2. Go to Settings → Devices & Services → Add Integration"
echo "  3. Search for 'ESP-Weaver' and follow the setup wizard"
echo ""
exit 0

