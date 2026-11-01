// Shared helpers for reading a COPY of an osu!lazer client.realm from Node, read-only and schema-driven.
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

/** Formats this tool has been tested against. Anything else is refused before Realm is touched. */
export const SUPPORTED_FILE_FORMATS = new Set([24]);
/** Highest lazer schema this tool has been tested against. Newer ones get a warning plus a property check. */
export const TESTED_SCHEMA_VERSION = 52;

export const EXIT = Object.freeze({
  OK: 0,
  USAGE: 2,
  NOT_A_REALM: 10,
  UNSUPPORTED_FUTURE_FORMAT: 11,
  OLDER_FORMAT_NEEDS_UPGRADE: 12,
  OPEN_FAILED: 13,
  REQUIRED_SCHEMA_MISSING: 14,
  REALM_MODULE_MISSING: 15,
});

export class RealmProbeError extends Error {
  constructor(exitCode, kind, message, details = {}) {
    super(message);
    this.exitCode = exitCode;
    this.kind = kind;
    this.details = details;
  }
}

export function refuseLivePath(p) {
  if (/AppData[\\/]Roaming[\\/]osu([\\/]|$)/i.test(path.resolve(p))) {
    throw new RealmProbeError(EXIT.USAGE, "live_path_refused", `Refusing to open a file inside the live osu! folder: ${p}. Copy it first.`);
  }
}

/** Parses the 24-byte Realm header without opening the file through Realm. */
export function readRealmHeader(file) {
  const fd = fs.openSync(file, "r");
  try {
    const size = fs.fstatSync(fd).size;
    const b = Buffer.alloc(24);
    const got = fs.readSync(fd, b, 0, 24, 0);
    if (got < 24) return { valid: false, size, reason: `file is only ${got} bytes` };
    const mnemonic = b.subarray(16, 20).toString("latin1");
    const flags = b[23];
    const selected = flags & 1; // bit 0 picks which of the two top-ref/format slots is current
    return {
      valid: mnemonic === "T-DB",
      size,
      mnemonic,
      slots: [b[20], b[21]],
      flags,
      fileFormat: b[20 + selected],
      topRef: selected ? b.readBigUInt64LE(8) : b.readBigUInt64LE(0),
    };
  } finally {
    fs.closeSync(fd);
  }
}

/** Fails with a specific, human-readable message before Realm can emit its misleading "requires an upgrade". */
export function assertSupportedFormat(file) {
  const h = readRealmHeader(file);
  if (!h.valid) {
    throw new RealmProbeError(EXIT.NOT_A_REALM, "not_a_realm",
      `${file} is not a Realm file (${h.reason ?? `mnemonic ${JSON.stringify(h.mnemonic)}`}). It may be encrypted, corrupted, or a torn copy.`, { header: serialisable(h) });
  }
  if (h.topRef >= BigInt(h.size)) {
    throw new RealmProbeError(EXIT.NOT_A_REALM, "truncated",
      `${file} looks truncated: its top ref ${h.topRef} is past the end of the ${h.size}-byte file. Recopy it.`, { header: serialisable(h) });
  }
  if (!SUPPORTED_FILE_FORMATS.has(h.fileFormat)) {
    const newest = Math.max(...SUPPORTED_FILE_FORMATS);
    if (h.fileFormat > newest) {
      throw new RealmProbeError(EXIT.UNSUPPORTED_FUTURE_FORMAT, "unsupported_future_format",
        `This osu!lazer database uses Realm file format ${h.fileFormat}, newer than the newest format this importer supports (${newest}). ` +
        `osu!lazer has probably upgraded its database engine; the importer needs a newer Realm reader. Your osu! data was not changed.`,
        { header: serialisable(h), supported: [...SUPPORTED_FILE_FORMATS] });
    }
    throw new RealmProbeError(EXIT.OLDER_FORMAT_NEEDS_UPGRADE, "older_format_needs_upgrade",
      `This osu!lazer database uses older Realm file format ${h.fileFormat}. A read-only open cannot read it; only a private copy may be opened read-write to upgrade it.`,
      { header: serialisable(h), supported: [...SUPPORTED_FILE_FORMATS] });
  }
  return h;
}

export function serialisable(h) {
  return JSON.parse(JSON.stringify(h, (_, v) => (typeof v === "bigint" ? v.toString() : v)));
}

export function loadRealm(moduleBaseDir) {
  try {
    const req = createRequire(path.join(path.resolve(moduleBaseDir), "noop.js"));
    const m = req("realm");
    const version = JSON.parse(fs.readFileSync(path.join(path.resolve(moduleBaseDir), "node_modules/realm/package.json"), "utf8")).version;
    return { Realm: m.default ?? m.Realm ?? m, version };
  } catch (e) {
    throw new RealmProbeError(EXIT.REALM_MODULE_MISSING, "realm_module_missing",
      `Could not load the realm npm package from ${moduleBaseDir}: ${String(e?.message).split("\n")[0]}`);
  }
}

export function openReadOnly(Realm, file) {
  try {
    return new Realm({ path: file, readOnly: true });
  } catch (e) {
    throw new RealmProbeError(EXIT.OPEN_FAILED, "open_failed", `Realm refused to open ${file}: ${e?.message}`, { name: e?.name, code: e?.code });
  }
}

/** Classes and properties the importer depends on. Checked at runtime, so a lazer schema bump fails loudly, not silently. */
export const REQUIRED_SCHEMA = {
  Beatmap: ["ID", "DifficultyName", "Ruleset", "Difficulty", "Metadata", "UserSettings", "BeatmapSet", "Status", "OnlineID", "Length", "BPM", "Hash", "StarRating", "MD5Hash", "OnlineMD5Hash", "LastLocalUpdate", "LastOnlineUpdate", "Hidden", "EndTimeObjectCount", "TotalObjectCount", "LastPlayed", "BeatDivisor"],
  BeatmapCollection: ["ID", "Name", "BeatmapMD5Hashes", "LastModified"],
  BeatmapDifficulty: ["DrainRate", "CircleSize", "OverallDifficulty", "ApproachRate", "SliderMultiplier", "SliderTickRate"],
  BeatmapMetadata: ["Title", "TitleUnicode", "Artist", "ArtistUnicode", "Author", "Source", "Tags", "PreviewTime", "AudioFile", "BackgroundFile"],
  BeatmapSet: ["ID", "OnlineID", "DateAdded", "DateSubmitted", "DateRanked", "Beatmaps", "Files", "Status", "DeletePending", "Hash", "Protected"],
  BeatmapUserSettings: ["Offset"],
  File: ["Hash"],
  RealmNamedFileUsage: ["File", "Filename"],
  RealmUser: ["OnlineID", "Username", "CountryCode"],
  Ruleset: ["ShortName", "OnlineID", "Name", "Available"],
  Score: ["ID", "BeatmapInfo", "ClientVersion", "BeatmapHash", "Ruleset", "Files", "Hash", "DeletePending", "TotalScore", "TotalScoreWithoutMods", "TotalScoreVersion", "LegacyTotalScore", "MaxCombo", "Accuracy", "Date", "PP", "OnlineID", "LegacyOnlineID", "User", "Mods", "Statistics", "MaximumStatistics", "Pauses", "Rank", "Combo", "IsLegacyScore"],
};

/** Returns human-readable problems; empty means every required class and property is present. */
export function checkRequiredSchema(realm) {
  const byName = new Map(realm.schema.map((s) => [s.name, s]));
  const problems = [];
  for (const [cls, props] of Object.entries(REQUIRED_SCHEMA)) {
    const s = byName.get(cls);
    if (!s) { problems.push(`missing class ${cls}`); continue; }
    for (const p of props) if (!(p in s.properties)) problems.push(`missing property ${cls}.${p}`);
  }
  return problems;
}

/** Indexes schema by class name, with the set of embedded classes. */
export function schemaIndex(realm) {
  const classes = new Map();
  for (const s of realm.schema) classes.set(s.name, s);
  return classes;
}

export function primitive(v, type) {
  if (v === null || v === undefined) return null;
  switch (type) {
    case "date": {
      const t = v.getTime();
      return Number.isFinite(t) ? v.toISOString() : null;
    }
    case "uuid":
    case "objectId":
    case "decimal128":
      return String(v);
    case "data":
      return Buffer.from(v).toString("base64");
    case "float":
      // A float32 read into a double shows as 5.300000190734863; 7 significant digits restores 5.3.
      return Number(v.toPrecision(7));
    default:
      return v;
  }
}

/**
 * How each link is written in the plain export. Unlisted embedded/no-PK links are inlined, so a property
 * lazer adds later still comes through; PK links are written as the PK value under "<Prop>_ref".
 */
export function makeSerializer(realm) {
  const classes = schemaIndex(realm);
  const pkOf = (name) => classes.get(name)?.primaryKey ?? null;

  function value(v, p) {
    if (v === null || v === undefined) return null;
    const t = p.type;
    if (t === "list" || t === "set") {
      const out = [];
      for (const item of v) out.push(p.objectType && classes.has(p.objectType) ? link(item, p.objectType) : primitive(item, p.objectType));
      return out;
    }
    if (t === "dictionary") {
      const out = {};
      for (const [k, item] of Object.entries(v)) out[k] = p.objectType && classes.has(p.objectType) ? link(item, p.objectType) : primitive(item, p.objectType);
      return out;
    }
    if (t === "object") return link(v, p.objectType);
    if (t === "linkingObjects") return undefined;
    return primitive(v, t);
  }

  function link(v, cls) {
    if (v === null || v === undefined) return null;
    const pk = pkOf(cls);
    // PK classes become references; embedded and PK-less ones are inlined.
    if (pk) return primitive(v[pk], classes.get(cls).properties[pk].type);
    return object(v, cls);
  }

  function object(o, cls) {
    const s = classes.get(cls);
    const out = {};
    for (const [name, p] of Object.entries(s.properties)) {
      const val = value(o[name], p);
      if (val !== undefined) out[name] = val;
    }
    return out;
  }

  return { object, link, value, classes };
}
