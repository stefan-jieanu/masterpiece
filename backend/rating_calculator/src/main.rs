//! Calculates osu!standard ratings for one beatmap per request, in its own process so the importer can
//! stop a stuck calculation by killing it.
//!
//! Reads one .osu path per line and answers each with one line:
//!   ok<TAB>stars<TAB>aim<TAB>speed<TAB>reading... for every mod combination, in MOD_COMBOS order
//!   skip<TAB>reason
//!
//! `--describe` prints the calculator identity and MOD_COMBOS. The importer checks them and keys its cache on them.

use std::{
    env,
    io::{self, BufRead, Write},
};

use rosu_pp::{Beatmap, Difficulty, any::DifficultyAttributes};

// Bump when vendor/rosu-pp changes, so cached ratings are recalculated.
const VERSION: &str = "rosu-pp@072ed25+hd-slider-fade";
// NM, HD, DT, HDDT, HR, HDHR, HRDT, HDHRDT as legacy mod bits.
const MOD_COMBOS: [u32; 8] = [0, 8, 64, 72, 16, 24, 80, 88];

fn ratings(path: &str) -> Result<String, &'static str> {
    let map = Beatmap::from_path(path).map_err(|_| "unreadable")?;
    if map.check_suspicion().is_err() {
        return Err("suspicious");
    }

    let mut values = Vec::with_capacity(MOD_COMBOS.len() * 4);
    for mods in MOD_COMBOS {
        match Difficulty::new().mods(mods).calculate(&map) {
            DifficultyAttributes::Osu(attrs) => {
                values.extend([attrs.stars, attrs.aim, attrs.speed, attrs.reading]);
            }
            _ => return Err("not_std"),
        }
    }
    if values.iter().any(|value| !value.is_finite()) {
        return Err("invalid");
    }

    let fields: Vec<String> = values.iter().map(f64::to_string).collect();
    Ok(fields.join("\t"))
}

fn main() {
    let stdout = io::stdout();
    let mut out = stdout.lock();

    if env::args().nth(1).as_deref() == Some("--describe") {
        let combos: Vec<String> = MOD_COMBOS.iter().map(u32::to_string).collect();
        writeln!(out, "{VERSION}\t{}", combos.join(",")).unwrap();
        return;
    }

    for path in io::stdin().lock().lines() {
        let path = path.unwrap();
        match ratings(&path) {
            Ok(values) => writeln!(out, "ok\t{values}").unwrap(),
            Err(reason) => writeln!(out, "skip\t{reason}").unwrap(),
        }
        out.flush().unwrap();
    }
}
