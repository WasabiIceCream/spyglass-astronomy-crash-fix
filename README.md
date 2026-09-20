# Spyglass Astronomy Crash Fix

Bytecode patch for the client-only mod
[Spyglass Astronomy](https://github.com/Nettakrim/Spyglass-Astronomy)
(`automodpack/host-modpack/main/mods/spyglass_astronomy-1.0.25-mc26.1.2.jar`),
fixing a crash that fires on every single resource pack reload.

## The bug

`SkyRendererMixin.closeBuffers()` is injected at the `HEAD` of vanilla
`SkyRenderer.close()`, which Minecraft calls on **every** resource
reload (opening/closing the resource pack menu, joining a world, F3+T,
anything). It unconditionally calls
`SpyglassAstronomyClient.spaceRenderingManager.close()` with no null
check. That static field stays `null` until a player has actually
looked through a spyglass at least once in the session, so the very
first reload throws an `NullPointerException` inside a
`CompletableFuture`.

Minecraft's own recovery from a failed reload is to **remove every
currently-selected resource pack**, not just whatever was just
toggled — this is what looked like "the new resourcepack fails to
load" when it was really "any reload at all crashes and wipes every
pack." Left in that half-torn-down render state, later font/texture
uploads have been observed to segfault the JVM natively (`SIGSEGV`
inside `GlyphStitcher`/`nglTexSubImage2D`).

Confirmed via the GitHub API (2026-09-18) that no released version
fixes this, including the unreleased tip of `main` as of that date. A
related commit (`a7480aa`, "fixed crash when reloading resource packs
at night") only fixes double-closing of the buffers *inside*
`SpaceRenderingManager.close()` — it assumes the manager itself
already exists, and doesn't guard the mixin's own call to it.

## The fix

Per this project's established bytecode-patching convention (see
memory `mod-dedicated-server-bytecode-patching`), adding new logic (an
`if (x != null)` guard) isn't safe to hand-patch — it needs new branch
offsets and constant-pool entries. But *removing* a call is safe as
long as it's stack-neutral. `closeBuffers()`'s whole body is:

```
getstatic     SpyglassAstronomyClient.spaceRenderingManager   ; push 1
invokevirtual SpaceRenderingManager.close()V                   ; pop 1, push 0
return
```

`getstatic` pushes exactly what `invokevirtual` (a void method) pops,
so NOPing out both instructions (6 bytes → six `0x00`s) leaves a
stack-neutral, now-empty method body ending in the original `return`.

This costs nothing functionally: `SpaceRenderingManager` already closes
and rebuilds its own buffers itself on the next `updateSpace()` pass
(see its own `close()`/`closeBuffer()` calls inside
`updateConstellations`/`updateStars`/`updateOrbits`) — this mixin was
purely a defensive early-close that never had a working null guard.

## Usage

```
python3 patch.py <input jar> <output jar>
```

If Spyglass Astronomy ever gets updated, re-derive the byte offset with
`javap -p -c` against the new `SkyRendererMixin.class` before assuming
this pattern still matches — check upstream first too, since this may
finally get a proper fix (`main` didn't have one as of this writing).

## Where the patched jar lives

`automodpack/host-modpack/main/mods/spyglass_astronomy-1.0.25-mc26.1.2.jar`
— replaces the unmodified jar of the same name/version. Client-only mod,
so per AGENTS.md's convention the server needs a restart after swapping
it (`generateModpackOnStart` only regenerates the manifest clients check
against at boot).
