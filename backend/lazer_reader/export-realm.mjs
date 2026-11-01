// Exports a COPY of an osu!lazer client.realm to plain JSON, read-only. Intended to run only at import time.
//
// Usage: node export-realm.mjs --module <dir with node_modules/realm> --realm <copy> --out <dir> [--classes A,B,C]
//
// stdout on success: one JSON line, {"ok":true,...manifest}
// stdout on failure: one JSON line, {"ok":false,"kind":...,"message":...}, with a specific exit code (see EXIT).
import fs from "node:fs";
import path from "node:path";
import { once } from "node:events";
import {
  EXIT, RealmProbeError, TESTED_SCHEMA_VERSION, assertSupportedFormat, checkRequiredSchema, loadRealm,
  makeSerializer, openReadOnly, refuseLivePath, serialisable,
} from "./lib/realm-common.mjs";

const OUTPUTS = {
  BeatmapSet: "beatmapsets.json",
  Beatmap: "beatmaps.json",
  Score: "scores.json",
  BeatmapCollection: "collections.json",
  Ruleset: "rulesets.json",
};

function parseArgs(argv) {
  const a = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (!argv[i].startsWith("--") || argv[i + 1] === undefined) {
      throw new RealmProbeError(EXIT.USAGE, "usage", `bad argument ${argv[i]}`);
    }
    a[argv[i].slice(2)] = argv[i + 1];
  }
  if (!a.module || !a.realm || !a.out) throw new RealmProbeError(EXIT.USAGE, "usage", "required: --module --realm --out");
  return a;
}

async function writeArray(file, results, toPlain) {
  // One object per line, so the output stays a valid JSON array and large libraries never buffer whole.
  const stream = fs.createWriteStream(file, { encoding: "utf8" });
  let n = 0;
  let serializeMs = 0;
  stream.write("[");
  for (const o of results) {
    const t = performance.now();
    const line = JSON.stringify(toPlain(o));
    serializeMs += performance.now() - t;
    if (!stream.write((n ? ",\n" : "\n") + line)) await once(stream, "drain");
    n++;
  }
  stream.end(n ? "\n]\n" : "]\n");
  await once(stream, "finish");
  return { count: n, serializeMs };
}

async function main() {
  const tStart = performance.now();
  const args = parseArgs(process.argv.slice(2));
  refuseLivePath(args.realm);
  const header = assertSupportedFormat(args.realm); // before Realm is loaded, so a clear message always wins
  const { Realm, version } = loadRealm(args.module);

  const t0 = performance.now();
  const realm = openReadOnly(Realm, args.realm);
  const openMs = performance.now() - t0;

  const problems = checkRequiredSchema(realm);
  if (problems.length) {
    const sv = realm.schemaVersion;
    realm.close();
    throw new RealmProbeError(EXIT.REQUIRED_SCHEMA_MISSING, "required_schema_missing",
      `This osu!lazer database (schema ${sv}) lacks fields the importer needs: ${problems.join(", ")}.`, { schemaVersion: sv, problems });
  }

  const warnings = [];
  if (realm.schemaVersion > TESTED_SCHEMA_VERSION) {
    warnings.push(`schema version ${realm.schemaVersion} is newer than the tested ${TESTED_SCHEMA_VERSION}; required fields are present, but meanings may have changed`);
  }

  fs.mkdirSync(args.out, { recursive: true });
  const ser = makeSerializer(realm);
  const wanted = args.classes ? args.classes.split(",") : Object.keys(OUTPUTS);
  const classes = {};
  for (const cls of wanted) {
    const t = performance.now();
    const results = realm.objects(cls);
    const { count, serializeMs } = await writeArray(path.join(args.out, OUTPUTS[cls] ?? `${cls}.json`), results, (o) => ser.object(o, cls));
    classes[cls] = { count, file: OUTPUTS[cls] ?? `${cls}.json`, serializeMs: +serializeMs.toFixed(1), totalMs: +(performance.now() - t).toFixed(1) };
  }

  const counts = {};
  for (const s of realm.schema) if (!s.embedded) counts[s.name] = realm.objects(s.name).length;

  const manifest = {
    realmNpm: version,
    node: process.version,
    schemaVersion: realm.schemaVersion,
    header: serialisable(header),
    counts,
    classes,
    warnings,
    timingsMs: { open: +openMs.toFixed(1), total: +(performance.now() - tStart).toFixed(1) },
  };
  realm.close();
  fs.writeFileSync(path.join(args.out, "manifest.json"), JSON.stringify(manifest, null, 2));
  console.log(JSON.stringify({ ok: true, ...manifest }));
}

try {
  await main();
  process.exit(EXIT.OK);
} catch (e) {
  const known = e instanceof RealmProbeError;
  console.log(JSON.stringify({
    ok: false,
    kind: known ? e.kind : "unexpected",
    message: String(e?.message),
    details: known ? e.details : { stack: String(e?.stack).split("\n").slice(0, 6) },
  }));
  process.exit(known ? e.exitCode : 1);
}
