const PAGE = 200;
export const $ = (selector) => document.querySelector(selector);

export function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
        if (value == null || value === false) continue;
        if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
        else if (key === "class") el.className = value;
        else if (key === "style") el.style.cssText = value;
        else el.setAttribute(key, value === true ? "" : value);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid);
    return el;
}

// Makes a non-button row usable from the keyboard too.
export function pressable(action) {
    return {
        tabindex: 0,
        onclick: action,
        onkeydown: (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                action(event);
            }
        },
    };
}

export async function api(path, body) {
    const options = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined;
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
}

const DEFAULT_SORT = {
    beatmaps: [{ key: "artist", dir: "asc" }, { key: "", dir: "asc" }],
    scores: [{ key: "played_at", dir: "desc" }, { key: "", dir: "asc" }],
};
const QUICK = [
    ["Mode", "mode", [["osu", "osu!"], ["taiko", "taiko"], ["catch", "catch"], ["mania", "mania"]]],
    ["Status", "status", [["ranked,approved", "ranked"], ["loved", "loved"], ["qualified", "qualified"], ["pending,wip,graveyard", "unranked"], ["unsubmitted,modified,unknown", "local"]]],
    ["In", "library", [["stable", "stable"], ["lazer", "lazer"], ["both", "both"]]],
    ["Scores", "has_score", [["yes", "has score"], ["no", "no score"]]],
];

const state = {
    fields: [], view: "beatmaps", q: "", quick: {},
    by: "stars", bucket: null, keys: new Set(), groupRows: [], groupOrder: "natural",
    sort: structuredClone(DEFAULT_SORT.beatmaps), sortedView: "beatmaps", bestBy: "score", flat: false,
    rows: [], total: 0, sets: 0, loading: false, selected: null, openSets: new Set(),
};

function effectiveQuery() {
    const quick = Object.entries(state.quick).map(([key, value]) => `${key}=${value}`);
    return [state.q, ...quick].filter(Boolean).join(" ");
}

function groupSelection() {
    return state.keys.size ? { by: state.by, bucket: state.bucket, keys: [...state.keys].map((k) => JSON.parse(k)) } : null;
}

export function showError(message) {
    $("#qerr").hidden = !message;
    $("#qerr").textContent = message || "";
}

// formatting
const STAR_STOPS = [[0.1, [66, 144, 251]], [1.25, [79, 192, 255]], [2, [79, 255, 213]], [2.5, [124, 255, 79]], [3.3, [246, 240, 92]], [4.2, [255, 128, 104]], [4.9, [255, 78, 111]], [5.8, [198, 69, 184]], [6.7, [101, 99, 222]], [7.7, [24, 21, 142]], [9, [0, 0, 0]]];

function starColor(stars) {
    if (stars == null || stars < 0.1) return "rgb(170,170,170)";
    for (let i = 1; i < STAR_STOPS.length; i++) {
        const [high, to] = STAR_STOPS[i];
        if (stars < high) {
            const [low, from] = STAR_STOPS[i - 1];
            const t = (stars - low) / (high - low);
            return `rgb(${from.map((c, n) => Math.round(c + (to[n] - c) * t)).join(",")})`;
        }
    }
    return "rgb(0,0,0)";
}

export const number = (n) => (n == null ? "–" : Number(n).toLocaleString("en-US"));
export const decimal = (n, digits = 2) => (n == null ? "–" : Number(n).toFixed(digits));
export const stripZero = (n) => (n == null ? "–" : String(Math.round(n * 100) / 100));
export const clock = (s) => (s == null ? "–" : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`);
const day = (epoch) => (epoch == null ? "–" : new Date(epoch * 1000).toISOString().slice(0, 10));
export const coverUrl = (setId, kind) => `https://assets.ppy.sh/beatmaps/${setId}/covers/${kind}.jpg`;
const GRADE_TEXT = { X: "SS", XH: "SS", S: "S", SH: "S", A: "A", B: "B", C: "C", D: "D", F: "F" };
const GRADE_RANK = ["F", "D", "C", "B", "A", "S", "SH", "X", "XH"];
export const MODE_TEXT = { osu: "std", taiko: "taiko", catch: "ctb", mania: "mania" };
const COMBOS = [["", "NM"], ["_hd", "HD"], ["_dt", "DT"], ["_hddt", "HDDT"], ["_hr", "HR"], ["_hdhr", "HDHR"], ["_hrdt", "HRDT"], ["_hdhrdt", "HDHRDT"]];
const NOMOD_RATINGS = [["aim", "aim"], ["tapping", "tap"], ["reading", "read"]];

export function starChip(stars) {
    const dark = stars != null && stars >= 6.5;
    return h("span", { class: "star", style: `background:${starColor(stars)};color:${dark ? "#ffd966" : "rgba(0,0,0,.8)"}` }, `★ ${decimal(stars)}`);
}

const gradeBadge = (grade) => (grade ? h("span", { class: `grade g-${grade}`, title: grade.endsWith("H") ? `${GRADE_TEXT[grade]} silver` : null }, GRADE_TEXT[grade] || grade) : null);
const modPills = (mods) => (mods ? h("span", { class: "mods" }, mods.split(",").map((m) => h("i", {}, m))) : null);
export const statusPill = (status) => h("span", { class: `status ${status}` }, status);

export function hideBroken(event) {
    event.target.remove();
}

// list rendering
// The ratings being sorted by. Rows show them so the order is visible.
function sortedRatingFields() {
    return state.sort.map((s) => state.fields.find((f) => f.key === s.key)).filter((f) => f?.column);
}

function sortedRatingSpans(row, keep = () => true) {
    return sortedRatingFields().filter((f) => row[f.column] != null && keep(f))
        .map((f) => h("span", { class: "on" }, `${f.short} ${decimal(row[f.column])}`));
}

// For rows that already show nomod stars in a chip.
function ratingsLine(row) {
    const sorted = sortedRatingFields();
    const nomod = row.aim == null ? [] : NOMOD_RATINGS.map(([key, label]) =>
        h("span", { class: sorted.some((f) => f.key === key) ? "on" : null }, `${label} ${decimal(row[key])}`));
    const spans = [...nomod, ...sortedRatingSpans(row, (f) => f.key !== "stars" && !NOMOD_RATINGS.some(([key]) => key === f.key))];
    return spans.length ? h("div", { class: "ratings" }, spans) : null;
}

function sortedRatingsLine(row, keep) {
    const spans = sortedRatingSpans(row, keep);
    return spans.length ? h("div", { class: "ratings" }, spans) : null;
}

function playedLine(stars, aim, tapping, reading) {
    if (stars == null) return null;
    const parts = [`★${decimal(stars)}`];
    if (aim != null) parts.push(`aim ${decimal(aim)}`, `tap ${decimal(tapping)}`, `read ${decimal(reading)}`);
    return h("small", { class: "line played" }, `as played ${parts.join(" · ")}`);
}

// The attached score: the beatmap's best match for the current filter and "Best score by" setting.
function playInfo(row, title) {
    return h("div", { class: "play", title },
        h("div", { class: "play-info" },
            h("div", {}, modPills(row.s_mods || "NM"), `${decimal(row.s_accuracy)}%`),
            h("small", {}, `${row.s_nmiss ?? 0} miss · ${day(row.s_played_at)}`),
        ),
        gradeBadge(row.s_grade),
    );
}

function diffRow(row, flat) {
    const stats = `${stripZero(row.bpm)} bpm · ${clock(row.length_s)} · AR${stripZero(row.ar)} CS${stripZero(row.cs)} OD${stripZero(row.od)} HP${stripZero(row.hp)}`;
    return h("div", { class: `diff${flat ? " flat" : ""}${state.selected === row.md5 ? " sel" : ""}`, ...pressable((e) => { e.stopPropagation(); selectBeatmap(row.md5); }) },
        h("span", { class: "mode" }, MODE_TEXT[row.mode] || row.mode),
        starChip(row.stars),
        h("div", { class: "diff-name" },
            h("div", { class: "line" }, flat ? `${row.artist} – ${row.title}` : row.difficulty),
            h("small", { class: "line" }, flat ? `[${row.difficulty}] by ${row.creator ?? "?"} · ${stats}` : stats),
            ratingsLine(row),
        ),
        row.s_grade && playInfo(row),
    );
}

function bestPlay(rows) {
    const rank = (row) => GRADE_RANK.indexOf(row.s_grade);
    return rows.filter((r) => r.s_grade).sort((a, b) => rank(b) - rank(a) || b.s_accuracy - a.s_accuracy)[0];
}

function setPanel(rows) {
    const first = rows[0];
    const single = rows.length === 1;
    const open = !single && state.openSets.has(first.set_key);
    const action = () => {
        if (single) return selectBeatmap(first.md5);
        if (open) state.openSets.delete(first.set_key);
        else {
            state.openSets.add(first.set_key);
            selectBeatmap(first.md5);
        }
        renderList();
    };
    const hasArt = first.set_id != null;
    const play = bestPlay(rows);
    const panel = h("article", { class: `set${open ? " open" : ""}${single && state.selected === first.md5 ? " sel" : ""}`, ...pressable(action) },
        hasArt && h("img", { class: "art", alt: "", loading: "lazy", referrerpolicy: "no-referrer", src: coverUrl(first.set_id, "card"), onerror: hideBroken }),
        hasArt ? h("img", { class: "thumb", alt: "", loading: "lazy", referrerpolicy: "no-referrer", src: coverUrl(first.set_id, "list"), onerror: (e) => e.target.classList.add("none") }) : h("div", { class: "thumb none" }),
        h("div", { class: "set-body" },
            h("div", {},
                h("div", { class: "set-title line" }, first.title),
                h("div", { class: "set-sub line" }, `by ${first.artist}`, h("span", {}, " · mapped by "), h("b", {}, first.creator ?? "?")),
            ),
            h("div", { class: "set-meta" },
                statusPill(first.status),
                single ? [starChip(first.stars), h("span", { class: "line" }, first.difficulty)] : h("span", { class: "dots" },
                    rows.slice(0, 16).map((r) => h("i", { style: `background:${starColor(r.stars)}`, title: `${r.difficulty} (${decimal(r.stars)})` })),
                    rows.length > 16 && h("small", {}, `+${rows.length - 16}`)),
            ),
            single ? ratingsLine(first) : sortedRatingsLine(first),
        ),
        h("div", { class: "set-side" },
            play && playInfo(play, single ? null : `Best score in this set, on ${play.difficulty}`),
            h("span", { class: "set-facts" }, `${stripZero(first.bpm)} bpm · ${clock(first.length_s)} · ${MODE_TEXT[first.mode] || first.mode}`),
        ),
    );
    return open ? [panel, h("div", { class: "diffs" }, rows.map((r) => diffRow(r, false)))] : [panel];
}

function scoreRow(row) {
    return h("div", { class: `score-row${state.selected === row.md5 ? " sel" : ""}`, ...pressable(() => selectBeatmap(row.md5)) },
        gradeBadge(row.s_grade),
        h("div", { class: "line" }, `${row.artist} – ${row.title}`, h("small", { class: "line" }, `[${row.difficulty}] · ${row.s_player ?? "?"} · ${day(row.s_played_at)} · ${row.s_source}`),
            playedLine(row.s_played_stars, row.s_played_aim, row.s_played_tapping, row.s_played_reading),
            sortedRatingsLine(row, (f) => !f.key.startsWith("played_"))),
        h("div", { class: "hide-narrow" }, modPills(row.s_mods)),
        h("div", { class: "num hide-narrow" }, `${number(row.s_total_score)}`, h("small", {}, `${number(row.s_max_combo)}x${row.s_perfect ? " FC" : ""}`)),
        h("div", { class: "num" }, `${decimal(row.s_accuracy)}%`, h("small", {}, row.s_pp != null ? `${Math.round(row.s_pp)}pp` : `${row.s_nmiss ?? 0} miss`)),
    );
}

function emptyState() {
    return h("div", { class: "empty-state" }, "Nothing matches this filter.", h("br"), "Try removing a condition, or clear the selected groups.");
}

function renderList() {
    const list = $("#list");
    const out = [];
    if (state.view === "scores") out.push(...state.rows.map(scoreRow));
    else if (state.flat) out.push(...state.rows.map((r) => diffRow(r, true)));
    else {
        let run = [];
        const flush = () => { if (run.length) out.push(...setPanel(run)); run = []; };
        for (const row of state.rows) {
            if (run.length && run[0].set_key !== row.set_key) flush();
            run.push(row);
        }
        flush();
    }
    if (!state.rows.length && !state.loading) out.push(emptyState());
    if (state.rows.length < state.total) {
        const sentinel = h("div", { class: "empty-state" }, "Loading more…");
        out.push(sentinel);
        moreObserver.disconnect();
        moreObserver.observe(sentinel);
    }
    list.replaceChildren(...out);
    const noun = state.view === "scores" ? "scores" : "beatmaps";
    if (state.view !== "tapping") $("#count").textContent = state.view === "scores" ? `${number(state.total)} ${noun}` : `${number(state.total)} ${noun} in ${number(state.sets)} sets`;
}

const moreObserver = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) loadList(false);
}, { root: $("#list"), rootMargin: "600px" });

// loading. Tokens drop responses that a newer request has superseded.
let listToken = 0;
let groupToken = 0;

async function loadList(reset = true) {
    if (!reset && (state.loading || state.rows.length >= state.total)) return;
    const token = ++listToken;
    state.loading = true;
    try {
        const data = await api("/api/list", {
            view: state.view, q: effectiveQuery(), group: groupSelection(),
            sort: state.sort.filter((s) => s.key), best_by: state.bestBy,
            offset: reset ? 0 : state.rows.length, limit: PAGE,
        });
        if (token !== listToken) return;
        state.rows = reset ? data.rows : state.rows.concat(data.rows);
        state.total = data.total;
        state.sets = data.sets;
        state.loading = false;
        showError(null);
        if (reset) $("#list").scrollTop = 0;
        renderList();
    } catch (error) {
        if (token !== listToken) return;
        state.loading = false;
        showError(error.message);
    }
}

async function loadGroups() {
    const token = ++groupToken;
    try {
        const data = await api("/api/groups", { by: state.by, bucket: state.bucket, view: state.view, q: effectiveQuery(), best_by: state.bestBy });
        if (token !== groupToken) return;
        state.groupRows = data.rows;
        const note = $("#group-note");
        note.hidden = !data.truncated;
        note.textContent = data.truncated ? `Showing the first ${number(data.rows.length)} groups.` : "";
        renderGroups();
    } catch (error) {
        if (token === groupToken) showError(error.message);
    }
}

async function refresh() {
    if (state.view === "tapping") {
        // A separate module, loaded on first use, so the other two tabs don't depend on it.
        try {
            const tapping = await import("./tapping.js");
            await tapping.refresh(effectiveQuery());
        } catch (error) {
            showError(error.message);
        }
        return;
    }
    loadList();
    loadGroups();
}

// groups pane
function renderGroups() {
    const needle = $("#group-search").value.trim().toLowerCase();
    let rows = state.groupRows.filter((r) => !needle || String(r.label).toLowerCase().includes(needle));
    if (state.groupOrder === "count") rows = [...rows].sort((a, b) => b.count - a.count || b.total - a.total);
    const max = Math.max(1, ...rows.map((r) => r.count));
    $("#group-list").replaceChildren(...rows.map((row) => {
        const id = JSON.stringify(row.key);
        const toggle = () => {
            if (state.keys.has(id)) state.keys.delete(id);
            else state.keys.add(id);
            $("#group-clear").hidden = !state.keys.size;
            renderGroups();
            loadList();
        };
        return h("li", { class: `grp${state.keys.has(id) ? " sel" : ""}${row.count ? "" : " empty"}`, style: `--share:${(row.count / max) * 100}%`, ...pressable(toggle) },
            h("span", { class: "grp-label", title: row.label }, row.label),
            h("span", { class: "grp-n" }, number(row.count), row.count !== row.total && h("small", {}, ` / ${number(row.total)}`)),
            row.pin && h("button", {
                class: "pin", type: "button", title: `Add to the filter: ${row.pin}`, "aria-label": `Add ${row.label} to the filter`,
                onclick: (event) => { event.stopPropagation(); addToQuery(row.pin); },
                onkeydown: (event) => event.stopPropagation(),
            }, "+"),
        );
    }));
    $("#group-order").textContent = state.groupOrder;
}

function addToQuery(text) {
    state.q = [state.q, text].filter(Boolean).join(" ");
    $("#q").value = state.q;
    state.keys.clear();
    $("#group-clear").hidden = true;
    refresh();
}

function setGroupField(key, bucket = null) {
    const field = state.fields.find((f) => f.key === key);
    state.by = key;
    state.bucket = bucket ?? field.buckets[0].value;
    state.keys.clear();
    $("#group-clear").hidden = true;
    $("#group-search").value = "";
    $("#group-bucket").replaceChildren(...field.buckets.map((b) => h("option", { value: b.value }, b.label)));
    $("#group-bucket").value = state.bucket;
    $("#group-bucket").disabled = field.buckets.length < 2;
}

// detail pane
async function selectBeatmap(md5) {
    state.selected = md5;
    renderList();
    try {
        renderDetail(await api(`/api/beatmap/${encodeURIComponent(md5)}`));
    } catch (error) {
        showError(error.message);
    }
}

const NOT_RATED_REASONS = {
    no_online_id: "it has no online ID to download its .osu file with, and no local copy was found",
    updated_online: "it has been updated online, so the .osu file for this version can't be downloaded",
    not_online: "its .osu file isn't available online, and no local copy was found",
    download_disabled: "its .osu file wasn't found locally and downloads were turned off",
    download_stopped: "downloading stopped before reaching it. Import again to retry",
    suspicious: "the calculator flagged the map as suspicious and skipped it",
    unreadable: "its .osu file couldn't be read",
    timeout: "the calculation took too long and was stopped",
    crashed: "the calculator crashed on it",
    skipped: "ratings were skipped at import",
};

function ratingTable(b) {
    if (b.aim == null) {
        const reason = b.mode === "osu" ? `Not rated because ${NOT_RATED_REASONS[b.rating_status] ?? "of an unexpected problem"}.` : "Only calculated for osu!standard maps.";
        return h("p", { class: "note rating-note" }, `No aim, tapping or reading. ${reason}`);
    }
    return h("table", { class: "rating-table" },
        h("thead", {}, h("tr", {}, ["Mods", "★", "aim", "tap", "read"].map((text) => h("th", { scope: "col" }, text)))),
        h("tbody", {}, COMBOS.map(([suffix, label]) => h("tr", {},
            h("td", {}, label),
            ["stars", "aim", "tapping", "reading"].map((skill) => h("td", {}, decimal(b[`${skill}${suffix}`])))))),
    );
}

function renderDetail({ beatmap: b, scores, collections }) {
    const fact = (label, value) => h("div", {}, h("small", {}, label), value);
    const bar = (label, value) => [
        h("span", {}, label),
        h("div", { class: "bar" }, h("i", { style: `width:${Math.min(100, (value ?? 0) * 10)}%` })),
        h("span", {}, stripZero(value)),
    ];
    const split = b.circles == null ? null : `${number(b.circles)} / ${number(b.sliders)} / ${number(b.spinners)}`;
    const tags = [...new Set((b.tags || "").toLowerCase().split(/\s+/).filter(Boolean))].slice(0, 24);
    $("#detail").replaceChildren(
        h("div", { class: "cover" },
            b.set_id != null && h("img", { alt: "", referrerpolicy: "no-referrer", src: coverUrl(b.set_id, "cover"), onerror: hideBroken }),
            h("div", {},
                h("h2", {}, b.title_unicode || b.title),
                h("p", {}, `by ${b.artist_unicode || b.artist} · mapped by `, h("b", {}, b.creator ?? "?")),
            ),
        ),
        h("div", { class: "detail-body" },
            h("div", { class: "detail-title" }, starChip(b.stars), b.difficulty, statusPill(b.status)),
            h("div", { class: "facts" },
                fact("Mode", MODE_TEXT[b.mode] || b.mode), fact("BPM", stripZero(b.bpm)), fact("Length", clock(b.length_s)),
                fact("Drain", clock(b.drain_s)), fact("Objects", number(b.objects)), fact("Circ/Sli/Spin", split ?? "–"),
                fact("Last played", day(b.last_played)), b.date_added != null && fact("Added", day(b.date_added)),
                b.date_ranked != null && fact("Ranked", day(b.date_ranked)), fact("In", b.in_stable && b.in_lazer ? "both" : b.in_stable ? "stable" : "lazer"),
            ),
            h("div", { class: "bars" }, bar("CS", b.cs), bar("AR", b.ar), bar("OD", b.od), bar("HP", b.hp)),
            ratingTable(b),
            b.beatmap_id != null && h("div", { class: "links" },
                h("a", { href: `https://osu.ppy.sh/beatmaps/${b.beatmap_id}`, target: "_blank", rel: "noreferrer" }, "beatmap page"),
                h("a", { href: `osu://b/${b.beatmap_id}` }, "open in osu!"),
            ),
            collections.length > 0 && [h("h3", {}, "Collections"), h("div", { class: "chips" }, collections.map((name) => h("button", { type: "button", onclick: () => addToQuery(`collection=="${name}"`) }, name)))],
            tags.length > 0 && [h("h3", {}, "Tags"), h("div", { class: "chips" }, tags.map((tag) => h("button", { type: "button", onclick: () => addToQuery(`tag=="${tag}"`) }, tag)))],
            h("h3", {}, `Scores (${scores.length})`),
            scores.length ? scores.map((s) => h("div", { class: "d-score" },
                gradeBadge(s.grade),
                h("div", { class: "line" }, s.player ?? "?", " ", modPills(s.mods), h("small", {}, `${day(s.played_at)} · ${s.source} · ${s.n300}/${s.n100}/${s.n50}/${s.nmiss}`),
                    playedLine(s.played_stars, s.played_aim, s.played_tapping, s.played_reading)),
                h("div", { class: "num" }, number(s.total_score), h("small", {}, `${decimal(s.accuracy)}% · ${number(s.max_combo)}x${s.perfect ? " FC" : ""}${s.pp != null ? ` · ${Math.round(s.pp)}pp` : ""}`)),
            )) : h("div", { class: "note" }, "No scores on this difficulty."),
        ),
    );
}

// setup
function renderQuick() {
    $("#quick").replaceChildren(...QUICK.map(([label, key, options]) => h("div", { class: "quick-row" },
        h("span", {}, label),
        h("button", { type: "button", class: state.quick[key] ? "" : "on", onclick: () => setQuick(key, null) }, "any"),
        options.map(([value, text]) => h("button", { type: "button", class: state.quick[key] === value ? "on" : "", onclick: () => setQuick(key, value) }, text)),
    )));
}

function setQuick(key, value) {
    if (value) state.quick[key] = value;
    else delete state.quick[key];
    renderQuick();
    refresh();
}

function renderFieldHelp() {
    const cats = [...new Set(state.fields.map((f) => f.cat))];
    $("#fields").replaceChildren(
        h("p", {},
            h("code", {}, "="), " contains for text, about equal for numbers · ", h("code", {}, "=="), " exact · ", h("code", {}, "!="), " · ", h("code", {}, "> >= < <="), " · ", h("code", {}, "~"), " within 5% · mods: ",
            h("code", {}, "mods=HDDT"), " exact, ", h("code", {}, "mods:HD"), " includes, ", h("code", {}, "mods!:FL"), " excludes · dates: ", h("code", {}, "2026"), ", ", h("code", {}, "2026-10"), ", ", h("code", {}, "2026-10-28"),
            " · durations: ", h("code", {}, "90"), ", ", h("code", {}, "2m30s"), ", ", h("code", {}, "2:30"), " · lists: ", h("code", {}, "status=ranked,loved"), " · quote text with spaces. Words without an operator search all song text. Score fields match any score on a beatmap; the best match is the one shown. ",
            h("code", {}, "aim"), ", ", h("code", {}, "tapping"), " and ", h("code", {}, "reading"), " are the calculator's std values, on their own scale rather than in stars. Without a suffix they're nomod; add ", h("code", {}, "_hd"), ", ", h("code", {}, "_dt"), ", ", h("code", {}, "_hddt"), ", ", h("code", {}, "_hr"), ", ", h("code", {}, "_hdhr"), ", ", h("code", {}, "_hrdt"), " or ", h("code", {}, "_hdhrdt"), " for other mods. ", h("code", {}, "played_aim"), " and friends use each score's own mods."),
        h("div", { class: "field-cats" }, cats.map((cat) => h("div", {},
            h("h4", {}, cat),
            state.fields.filter((f) => f.cat === cat).map((f) => h("button", {
                type: "button", title: f.aliases.length ? `Also: ${f.aliases.join(", ")}` : null,
                onclick: () => { const q = $("#q"); q.value = [q.value, `${f.key}=`].filter(Boolean).join(" "); q.focus(); },
            }, f.label, h("i", {}, `${f.key} · ${f.type}`))),
        ))),
    );
}

function fillSelects() {
    const options = (list) => [...new Set(list.map((f) => f.cat))].map((cat) => h("optgroup", { label: cat }, list.filter((f) => f.cat === cat).map((f) => h("option", { value: f.key }, f.label))));
    $("#group-by").replaceChildren(...options(state.fields));
    const sortable = state.fields.filter((f) => f.sortable);
    $("#sort-1").replaceChildren(...options(sortable));
    $("#sort-2").replaceChildren(h("option", { value: "" }, "none"), ...options(sortable));
}

function syncControls() {
    [0, 1].forEach((i) => {
        $(`#sort-${i + 1}`).value = state.sort[i].key;
        $(`#dir-${i + 1}`).textContent = state.sort[i].dir;
        $(`#dir-${i + 1}`).hidden = !state.sort[i].key;
    });
    $("#best-wrap").hidden = state.view === "scores";
    $("#flat-wrap").hidden = state.view === "scores";
    document.body.dataset.view = state.view;
    document.querySelectorAll(".tabs button").forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.view === state.view)));
}

function wire() {
    let debounce;
    $("#q").addEventListener("input", (event) => {
        clearTimeout(debounce);
        debounce = setTimeout(() => {
            state.q = event.target.value.trim();
            refresh();
        }, 250);
    });
    $("#fields-toggle").addEventListener("click", (event) => {
        $("#fields").hidden = !$("#fields").hidden;
        event.target.setAttribute("aria-expanded", String(!$("#fields").hidden));
    });
    $("#group-by").addEventListener("change", (event) => {
        setGroupField(event.target.value);
        refresh();
    });
    $("#group-bucket").addEventListener("change", (event) => {
        state.bucket = event.target.value;
        state.keys.clear();
        $("#group-clear").hidden = true;
        refresh();
    });
    $("#group-search").addEventListener("input", renderGroups);
    $("#group-order").addEventListener("click", () => {
        state.groupOrder = state.groupOrder === "natural" ? "count" : "natural";
        renderGroups();
    });
    $("#group-clear").addEventListener("click", () => {
        state.keys.clear();
        $("#group-clear").hidden = true;
        renderGroups();
        loadList();
    });
    document.querySelectorAll(".tabs button").forEach((tab) => tab.addEventListener("click", () => {
        if (state.view === tab.dataset.view) return;
        state.view = tab.dataset.view;
        // The Tapping tab has its own sort, so visiting it keeps the sort of the tab you came from.
        if (state.view !== "tapping" && state.view !== state.sortedView) {
            state.sort = structuredClone(DEFAULT_SORT[state.view]);
            state.sortedView = state.view;
        }
        syncControls();
        refresh();
    }));
    [0, 1].forEach((i) => {
        $(`#sort-${i + 1}`).addEventListener("change", (event) => {
            state.sort[i].key = event.target.value;
            syncControls();
            loadList();
        });
        $(`#dir-${i + 1}`).addEventListener("click", () => {
            state.sort[i].dir = state.sort[i].dir === "asc" ? "desc" : "asc";
            syncControls();
            loadList();
        });
    });
    $("#best-by").addEventListener("change", (event) => {
        state.bestBy = event.target.value;
        refresh();
    });
    $("#flat").addEventListener("change", (event) => {
        state.flat = event.target.checked;
        renderList();
    });
}

function showDetailPlaceholder() {
    $("#detail").replaceChildren(h("div", { class: "empty-state" }, "Pick a difficulty to see its details and scores."));
}

async function start() {
    wire();
    showDetailPlaceholder();
    state.fields = await api("/api/fields");
    fillSelects();
    renderQuick();
    renderFieldHelp();
    $("#group-by").value = state.by;
    setGroupField(state.by);
    syncControls();
    try {
        const meta = await api("/api/meta");
        const stat = (value, label) => h("span", {}, h("b", {}, number(value)), ` ${label}`);
        $("#stats").replaceChildren(stat(meta.beatmaps, "beatmaps"), stat(meta.sets, "sets"), stat(meta.scores, "scores"), stat(meta.collections, "collections"));
        refresh();
    } catch (error) {
        $("#list").replaceChildren(h("div", { class: "empty-state" }, error.message));
    }
}

start();
