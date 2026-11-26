#!/bin/bash
# Build a PDK overlay that uses the Tulip standard-cell libs.
#
# usage: scripts/mk_pdk_overlay.sh PDK_ROOT OUT_DIR [PDK]
#   PDK_ROOT: IHP-Open-PDK checkout (e.g. chip-di-owas/vendor/IHP-Open-PDK)
#   OUT_DIR : PDK root to create (replaced if it exists)
#   PDK     : ihp-sg13cmos5l (default) or ihp-sg13g2
# OUT_DIR mirrors PDK_ROOT with symlinks; only the timing libs of the
# standard cells (<PDK>/libs.ref/<scl>/lib/<scl>_*.lib) are replaced by the
# Tulip libs from libs/<PDK>. Then use OUT_DIR as PDK_ROOT, e.g.
#   make -C units/top/rtl2gds/librelane LibreLane PDK_ROOT=$PWD/OUT_DIR
set -euo pipefail

src=$(readlink -f "$1")
out=$2
pdk=${3:-ihp-sg13cmos5l}
here=$(cd "$(dirname "$0")/.." && pwd)
scl=${pdk#ihp-}_stdcell
lib=$pdk/libs.ref/$scl/lib
tulip=$here/libs/$pdk
[ -d "$src/$lib" ] || { echo "no $src/$lib"; exit 1; }
[ -d "$tulip" ] || { echo "no Tulip libs in $tulip"; exit 1; }

# link every entry of directory $1 into $2, except the one named $3
mirror() {
    mkdir -p "$2"
    for e in "$1"/* "$1"/.[!.]*; do
        [ -e "$e" ] || continue
        [ "$(basename "$e")" = "$3" ] && continue
        ln -s "$e" "$2/"
    done
}

rm -rf "$out"
mirror "$src" "$out" "$pdk"
mirror "$src/$pdk" "$out/$pdk" libs.ref
mirror "$src/$pdk/libs.ref" "$out/$pdk/libs.ref" "$scl"
mirror "$src/$pdk/libs.ref/$scl" "$out/$pdk/libs.ref/$scl" lib
mirror "$src/$lib" "$out/$lib" ""
n=0
for f in "$tulip"/"$scl"_*.lib; do
    name=$(basename "$f")
    [ -e "$out/$lib/$name" ] || echo "warning: $name is not in the PDK, added anyway"
    ln -sfn "$f" "$out/$lib/$name"
    n=$((n + 1))
done
echo "PDK overlay $out: $n Tulip libs in $lib"
