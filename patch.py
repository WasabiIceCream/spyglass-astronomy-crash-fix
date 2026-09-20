#!/usr/bin/env python3
"""
Patches Spyglass Astronomy (https://github.com/Nettakrim/Spyglass-Astronomy)
to fix a crash-on-resource-reload bug.

Bug: SkyRendererMixin.closeBuffers() is injected at the HEAD of vanilla
SkyRenderer.close(), which fires on every resource pack reload (menu open/
close, world join, F3+T, etc). It unconditionally calls
SpyglassAstronomyClient.spaceRenderingManager.close() with no null check.
That field stays null until a player actually looks through a spyglass at
least once, so the very first resource reload of a session throws an NPE
inside a CompletableFuture. Minecraft's own recovery from that is to
"remove all selected resourcepacks" -- so every active pack gets kicked
back to "Available", and the client is left in a half-torn-down render
state that has been observed to later segfault natively (SIGSEGV inside
GlyphStitcher/nglTexSubImage2D) once font/texture rendering resumes.

Confirmed via `gh api repos/Nettakrim/Spyglass-Astronomy/...` (2026-09-18)
that this is NOT fixed by any released version, including the unreleased
tip of `main` as of that date -- a related commit (a7480aa, "fixed crash
when reloading resource packs at night") only fixes double-closing of the
*internal* GPU buffers inside SpaceRenderingManager.close(), assuming the
manager itself already exists. It does not add a null guard around the
mixin's own call to spaceRenderingManager.close().

Fix: per this project's established bytecode-patching convention (see
memory `mod-dedicated-server-bytecode-patching` / AGENTS.md's mod-dev
notes), inserting new logic (an `if (x != null)` guard) isn't safe to
hand-patch directly -- it needs new branch offsets and constant-pool
entries. But *removing* a call is safe as long as it's stack-neutral.
closeBuffers()'s whole body is:

    getstatic  SpyglassAstronomyClient.spaceRenderingManager   (push 1)
    invokevirtual SpaceRenderingManager.close()V               (pop 1, push 0)
    return

getstatic pushes exactly what invokevirtual (a void method) pops, so
NOPing out both instructions (6 bytes -> 6x 0x00) leaves a stack-neutral,
now-empty method body ending in the original `return`. This just means
the astronomy renderer's GPU buffers don't get proactively closed on a
resource reload; SpaceRenderingManager rebuilds/replaces its own buffers
on the next updateSpace() pass regardless (see its own close()/
closeBuffer() calls at the start of updateConstellations/updateStars/
updateOrbits), so nothing is actually lost functionally -- this was
purely a defensive early-close that never had a working null guard.

Usage:
    python3 patch.py <input jar> <output jar>
"""
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

CLASS_PATH = "com/nettakrim/spyglass_astronomy/mixin/SkyRendererMixin.class"
# getstatic #13 (0xB2 0x00 0x0D), invokevirtual #29 (0xB6 0x00 0x1D), return (0xB1)
PATTERN = bytes([0xB2, 0x00, 0x0D, 0xB6, 0x00, 0x1D, 0xB1])


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    src_jar, dst_jar = Path(sys.argv[1]), Path(sys.argv[2])

    with zipfile.ZipFile(src_jar) as zf:
        class_bytes = bytearray(zf.read(CLASS_PATH))

    idx = class_bytes.find(PATTERN)
    if idx == -1:
        raise SystemExit(
            f"Expected byte pattern not found in {CLASS_PATH} -- "
            "mod was likely recompiled/updated, re-derive the offset "
            "with `javap -p -c` before patching a new jar."
        )
    if class_bytes.count(PATTERN) != 1:
        raise SystemExit("Pattern is not unique in the class file, refusing to guess.")

    for i in range(idx, idx + 6):
        class_bytes[i] = 0x00

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        class_dir = tmp / "com/nettakrim/spyglass_astronomy/mixin"
        class_dir.mkdir(parents=True)
        (class_dir / "SkyRendererMixin.class").write_bytes(class_bytes)

        dst_jar.write_bytes(src_jar.read_bytes())
        subprocess.run(["zip", str(dst_jar.resolve()), CLASS_PATH], cwd=tmp, check=True)

    print(f"Patched {dst_jar}")


if __name__ == "__main__":
    main()
