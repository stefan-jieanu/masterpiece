# Vendored rosu-pp

Source files of [rosu-pp](https://github.com/MaxOhn/rosu-pp) (MIT, see `LICENSE`), copied because the code we need isn't released yet.

- **Branch:** `pp-update`
- **Commit:** `072ed255fa043aba495212c441030dc7a5e61469` (2026-09-22)
- **Why this branch:** released rosu-pp 4.0.x predates lazer's 2026 Q2 rating rework (ppy/osu #37850), so it has no osu!standard reading skill and its star ratings don't match the current client.

Only `src/`, `benches/`, `Cargo.toml`, `README.md` and `LICENSE` are kept. Test data and tooling are left out.

## Changes from upstream

Only one, in `src/osu/difficulty/object.rs`, in `opacity_at`. Sliders keep their default fade-in time under HD, matching lazer since ppy/osu #38164 (2026-07-09), which the branch hasn't ported:

```diff
         if hidden {
             // * Taken from OsuModHidden.
+            // * Sliders retain their default TimeFadeIn (OsuModHidden.applyFadeInAdjustment).
+            let time_fade_in = if self.base.is_slider() { fade_in_duration } else { time_fade_in };
             let fade_out_start_time = self.base.start_time - time_preempt + time_fade_in;
```

## Updating

When rosu-pp ships a release with this logic, replace this folder with that release, drop the patch if upstream has the fix, and bump `VERSION` in `../../src/main.rs` so cached ratings are recalculated. Then run `tests/smoke.py`, which compares results with what the clients stored.
