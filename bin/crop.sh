#!/usr/bin/env bash
# Use ImageMagick to slice out the MacOS titlebar from qualifying screenshots in the docs/ folder.
#
#  - Outer screenshot dimensions: `1836px` wide x `2272px` tall (at 144 DPI).
#  - Position of MacOS window chrome: `0px` right x `1684px` up.
#  - Size of the chop: `76px` tall x `2272px` (full width) across.

set -euo pipefail
shopt -s nullglob

TOO_TALL_PX=1760
CHOP_UP_PX=1684
CHOP_HEIGHT_PX=76
IMAGES_GLOB=( ./docs/*.png )


if ! command -v magick >/dev/null || ! command -v identify >/dev/null; then
    echo "ImageMagick cli tools not found. Aborting."
    exit 1
fi

# For each images from our glob,
for IMG in "${IMAGES_GLOB[@]}"; do

    # If the image is taller than our chopped height,
    if [ $(identify -format "%h" "$IMG")>/dev/null -gt "$TOO_TALL_PX" ]; then

        # Apply the `magick -chop` command to each image.
        echo "Cropping '${IMG}'..."
        magick "$IMG" \
            -gravity SouthWest \
            -chop 0x${CHOP_HEIGHT_PX}+0+${CHOP_UP_PX} \
            +repage \
            "$IMG"

    fi

done
