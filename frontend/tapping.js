// The Tapping tab: a custom metric kept apart from the other tabs, with its versions side by side so they can be checked on real maps.
import { $, api, clock, coverUrl, decimal, h, hideBroken, MODE_TEXT, number, pressable, showError, starChip, statusPill, stripZero } from "./app.js";

const PAGE = 100;
const STRIP_WIDTH = 1000;
const BIN_OPACITY = [1, 0.72, 0.48, 0.28, 0.12];
const SORTS = [
    ["Value", [["value_stream", "Streams value"], ["value_burst", "Bursts value"], ["value_dt", "Doubles/triples value"]]],
    ["Gap between the two versions (pick Compare with first)", [["diff_stream", "Streams gap"], ["diff_burst", "Bursts gap"], ["diff_dt", "Doubles/triples gap"]]],
    ["Number of groups", [["count_stream", "Number of streams"], ["count_burst", "Number of bursts"], ["count_double", "Number of doubles"], ["count_triple", "Number of triples"]]],
    ["Map", [["longest", "Longest group"], ["stars", "Star rating"], ["artist", "Artist"], ["title", "Title"]]],
];

const state = {
    meta: null, q: "", version: "a", compare: "", sort: "value_stream", direction: "desc", scale: "scaled", status: "rated",
    rows: [], total: 0, statuses: {}, loading: false, selected: null, selectedRow: null, openLists: new Set(),
};
let token = 0;
let ready = null;

const versionOf = (key) => state.meta.versions.find((v) => v.key === key);
const label = (key) => key.toUpperCase();
const shown = (entry) => (entry == null ? "–" : state.scale === "scaled" ? decimal(entry.scaled, 1) : decimal(entry.raw));
const sortedKind = () => (state.sort.startsWith("value_") || state.sort.startsWith("diff_") ? state.sort.split("_")[1] : null);

function svg(tag, attrs, ...kids) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
    el.append(...kids);
    return el;
}

function badge(key) {
    return h("span", { class: "tap-badge", style: `--v:var(--v-${key})` }, label(key));
}

// Shares of the distance sizes, closest first. The length of each part is the share, and the brightness is how close that size is.
function shareBar(shares, kind) {
    const bins = state.meta.bins;
    if (!shares) return h("div", { class: "shares empty", title: "No distances to count" });
    const text = shares.map((s, i) => `${bins[i]}: ${Math.round(s * 100)}%`).join("\n");
    return h("div", { class: "shares", title: text, style: `--k:var(--k-${kind})` },
        shares.map((s, i) => s > 0 && h("i", { style: `flex-grow:${s};opacity:${BIN_OPACITY[i]}` })));
}

// Where the groups are in the map, one lane per type. Doubles are drawn fainter than triples in their shared lane.
function strip(data) {
    const end = Math.max(1, data.last_tap);
    const paths = { stream: "", burst: "", triple: "", double: "" };
    for (const [start, stop, taps] of data.groups) {
        const lane = taps >= 9 ? "stream" : taps >= 4 ? "burst" : taps === 3 ? "triple" : "double";
        const top = { stream: 0, burst: 10, triple: 20, double: 20 }[lane];
        const x = (Math.max(0, start) / end) * STRIP_WIDTH;
        const width = Math.max(2, ((stop - start) / end) * STRIP_WIDTH);
        paths[lane] += `M${x.toFixed(1)} ${top}h${width.toFixed(1)}v8h-${width.toFixed(1)}z`;
    }
    return svg("svg", { class: "tap-strip", viewBox: `0 0 ${STRIP_WIDTH} 28`, preserveAspectRatio: "none", "aria-hidden": "true" },
        svg("rect", { width: STRIP_WIDTH, height: 8, class: "lane" }), svg("rect", { y: 10, width: STRIP_WIDTH, height: 8, class: "lane" }), svg("rect", { y: 20, width: STRIP_WIDTH, height: 8, class: "lane" }),
        svg("path", { d: paths.stream, fill: "var(--k-stream)" }), svg("path", { d: paths.burst, fill: "var(--k-burst)" }),
        svg("path", { d: paths.double, fill: "var(--k-dt)", opacity: 0.5 }), svg("path", { d: paths.triple, fill: "var(--k-dt)" }));
}

function ignoredReason(row) {
    const reasons = {
        no_quarter: "Ignored: this map has no 1/4 rhythms.",
        many_bpm_changes: `Ignored: ${row.bpm_changes} BPM changes, of which ${row.bpm_changes_in_play} are between the first and last tap. The limit is ${state.meta.max_bpm_changes}.`,
        no_local_file: "Ignored: no .osu file for this map on this machine, and nothing is downloaded for this metric.",
        not_std: "Ignored: only osu!standard maps are analysed.",
        skipped: "This import skipped the analysis (--no-tapping).",
    };
    return reasons[row.tap_status] ?? "Ignored.";
}

function valueCell(row, kind) {
    const entry = row.data?.kinds[kind.key];
    const on = sortedKind() === kind.key;
    if (!entry) return h("div", { class: `tap-cell none${on ? " on" : ""}` }, h("small", {}, kind.label), h("b", {}, "–"), h("small", {}, "none"));
    const main = entry.versions[state.version];
    const count = kind.key === "dt" ? `${number(row.doubles)} + ${number(row.triples)}` : number(entry.count);
    return h("div", { class: `tap-cell${on ? " on" : ""}` },
        h("small", {}, kind.label),
        h("div", { class: "tap-values" },
            h("b", { style: `color:var(--v-${state.version})`, title: `Version ${label(state.version)}` }, shown(main)),
            state.compare && h("b", { style: `color:var(--v-${state.compare})`, title: `Version ${label(state.compare)}` }, shown(entry.versions[state.compare]))),
        shareBar(main.shares, kind.key),
        h("small", { title: kind.key === "dt" ? "Doubles + triples" : "Groups" }, count),
    );
}

function mapLine(row) {
    return `[${row.difficulty}] by ${row.creator ?? "?"} · ${stripZero(row.bpm)} bpm · ${clock(row.length_s)}`;
}

function mapRow(row) {
    return h("article", { class: `tap-row${state.selected === row.md5 ? " sel" : ""}`, "data-md5": row.md5, ...pressable(() => select(row)) },
        row.set_id != null ? h("img", { class: "tap-thumb", alt: "", loading: "lazy", referrerpolicy: "no-referrer", src: coverUrl(row.set_id, "list"), onerror: hideBroken }) : h("div", { class: "tap-thumb none" }),
        h("div", { class: "tap-main" },
            h("div", { class: "tap-title" }, starChip(row.stars), h("span", { class: "line" }, `${row.artist} – ${row.title}`)),
            h("small", { class: "line" }, mapLine(row)),
            row.data ? strip(row.data) : h("small", { class: "tap-reason" }, ignoredReason(row)),
        ),
        row.data && state.meta.kinds.map((kind) => valueCell(row, kind)),
    );
}

function select(row) {
    state.selected = row.md5;
    state.selectedRow = row;
    document.querySelectorAll(".tap-row.sel").forEach((el) => el.classList.remove("sel"));
    document.querySelector(`.tap-row[data-md5="${CSS.escape(row.md5)}"]`)?.classList.add("sel");
    renderDetail(row);
}

// list
const moreObserver = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) load(false);
}, { root: $("#tap-list"), rootMargin: "600px" });

function renderList(added) {
    const list = $("#tap-list");
    list.querySelector(".tap-more")?.remove();
    moreObserver.disconnect();
    if (added === state.rows) list.replaceChildren();
    list.append(...added.map(mapRow));
    if (!state.rows.length && !state.loading) list.append(h("div", { class: "empty-state" }, "No maps here with this filter."));
    if (state.rows.length < state.total) {
        const sentinel = h("div", { class: "empty-state tap-more" }, "Loading more…");
        list.append(sentinel);
        moreObserver.observe(sentinel);
    }
    if (document.body.dataset.view === "tapping") $("#count").textContent = `${number(state.total)} maps`;
}

async function load(reset = true) {
    if (!reset && (state.loading || state.rows.length >= state.total)) return;
    const mine = ++token;
    state.loading = true;
    try {
        const data = await api("/api/tapping/list", {
            q: state.q, status: state.status, version: state.version, compare: state.compare, sort: state.sort, direction: state.direction,
            scale: state.scale, offset: reset ? 0 : state.rows.length, limit: PAGE,
        });
        if (mine !== token) return;
        state.loading = false;
        state.total = data.total;
        state.statuses = data.statuses;
        state.rows = reset ? data.rows : state.rows.concat(data.rows);
        showError(null);
        if (reset) $("#tap-list").scrollTop = 0;
        renderList(reset ? state.rows : data.rows);
        if (reset) {
            renderSide();
            // Its numbers and highlighted version follow the controls.
            if (state.selectedRow) renderDetail(state.selectedRow);
        }
    } catch (error) {
        if (mine !== token) return;
        state.loading = false;
        showError(error.message);
    }
}

// side panel
function versionCard(version) {
    const on = state.version === version.key;
    return h("button", {
        type: "button", class: `tap-version${on ? " on" : ""}`, style: `--v:var(--v-${version.key})`, "aria-pressed": String(on), title: on ? null : version.note, "data-key": `v-${version.key}`,
        onclick: () => {
            if (state.version === version.key) return;
            state.version = version.key;
            if (state.compare === version.key) state.compare = "";
            syncControls();
            load();
        },
    }, h("span", { class: "tap-version-name" }, badge(version.key), version.name), h("small", {}, version.note));
}

function renderSide() {
    const families = [...new Set(state.meta.versions.map((v) => v.family))];
    const statuses = state.meta.statuses.filter((s) => s.key === "rated" || s.key === state.status || state.statuses[s.key]);
    const focused = document.activeElement?.closest?.("#tap-side button")?.dataset.key;
    $("#tap-side").replaceChildren(
        h("p", { class: "tap-intro" }, "A custom metric, separate from star rating. It doesn't change with mods or BPM."),
        ...families.flatMap((family) => [h("h3", {}, family), ...state.meta.versions.filter((v) => v.family === family).map(versionCard)]),
        h("h3", {}, "Scale"),
        h("div", { class: "tap-choice" },
            ["scaled", "raw"].map((scale) => h("button", {
                type: "button", class: state.scale === scale ? "on" : "", "aria-pressed": String(state.scale === scale), "data-key": `scale-${scale}`,
                onclick: () => {
                    if (state.scale === scale) return;
                    state.scale = scale;
                    load();
                },
            }, scale === "scaled" ? "stretched" : "as calculated"))),
        h("p", { class: "note" }, state.scale === "scaled"
            ? "Stretched per version and group type, so the top 1% of your library reach 10. The order of the maps stays the same."
            : `The plain weighted average: ${state.meta.bins.map((bin, i) => `${bin} scores ${state.meta.scores[i]}`).join(", ")}.`),
        h("h3", {}, "Show"),
        h("ul", { class: "tap-statuses" }, statuses.map((s) => h("li", {},
            h("button", {
                type: "button", class: state.status === s.key ? "on" : "", "aria-pressed": String(state.status === s.key), "data-key": `status-${s.key}`,
                onclick: () => {
                    if (state.status === s.key) return;
                    state.status = s.key;
                    load();
                },
            }, h("span", {}, s.label), h("small", {}, number(state.statuses[s.key] ?? 0)))))),
        h("h3", {}, "How to read a map"),
        h("ul", { class: "tap-legend" },
            state.meta.kinds.map((kind) => h("li", {}, h("i", { style: `background:var(--k-${kind.key})` }), `${kind.label}: ${kind.sizes}`)),
            h("li", {}, h("i", { class: "faint", style: "background:var(--k-dt)" }), "Doubles are the fainter marks in their lane"),
        ),
        h("p", { class: "note" }, "The bar under each value shows the shares it's calculated from: bright is close together, faint is far apart. Hover over it for the numbers. Click a map to see where the versions differ."),
    );
    if (focused) $(`#tap-side button[data-key="${focused}"]`)?.focus();
}

function syncControls() {
    const others = state.meta.versions.filter((v) => v.key !== state.version);
    $("#tap-compare").replaceChildren(h("option", { value: "" }, "none"), ...others.map((v) => h("option", { value: v.key }, `${label(v.key)}: ${v.name}`)));
    $("#tap-compare").value = state.compare;
    if (!state.compare && state.sort.startsWith("diff_")) state.sort = `value_${state.sort.split("_")[1]}`;
    $("#tap-sort").replaceChildren(...SORTS.map(([group, options]) => h("optgroup", { label: group },
        options.map(([value, text]) => h("option", { value, disabled: value.startsWith("diff_") && !state.compare }, text)))));
    $("#tap-sort").value = state.sort;
    $("#tap-dir").textContent = state.direction;
    $("#tap-showing").replaceChildren("Showing ", badge(state.version), ...(state.compare ? [" and ", badge(state.compare)] : []));
}

// detail
const stamp = (ms) => {
    const whole = Math.max(0, Math.round(ms));
    return `${String(Math.floor(whole / 60000)).padStart(2, "0")}:${String(Math.floor(whole / 1000) % 60).padStart(2, "0")}:${String(whole % 1000).padStart(3, "0")}`;
};
const beats = (n) => `${stripZero(n)} ${n === 1 ? "beat" : "beats"}`;

function bigTimeline(data) {
    const end = Math.max(1, data.last_tap);
    const lane = (kind, match) => h("div", { class: "tl-lane" },
        h("small", {}, kind.label),
        svg("svg", { viewBox: `0 0 ${STRIP_WIDTH} 14`, preserveAspectRatio: "none", role: "img", "aria-label": `Where the ${kind.label.toLowerCase()} are in the map` },
            ...data.groups.filter(match).map(([start, stop, taps]) => svg("rect", {
                x: (Math.max(0, start) / end) * STRIP_WIDTH, width: Math.max(2, ((stop - start) / end) * STRIP_WIDTH), height: 14,
                fill: `var(--k-${kind.key})`, opacity: taps === 2 ? 0.5 : 1,
            }, svg("title", {}, `${stamp(start)}, ${taps} taps`)))),
    );
    const [stream, burst, dt] = state.meta.kinds;
    return h("div", { class: "timeline" },
        lane(stream, (g) => g[2] >= 9), lane(burst, (g) => g[2] >= 4 && g[2] <= 8), lane(dt, (g) => g[2] <= 3),
        h("div", { class: "tl-axis" }, h("span", {}, "0:00"), h("span", {}, clock(end / 2000)), h("span", {}, clock(end / 1000))),
    );
}

function groupsList(data, kind, entry) {
    const inKind = data.groups.filter(([, , taps]) => (kind.key === "stream" ? taps >= 9 : kind.key === "burst" ? taps >= 4 && taps <= 8 : taps <= 3));
    return h("details", {
        class: "tap-groups", open: state.openLists.has(kind.key),
        ontoggle: (event) => (event.target.open ? state.openLists.add(kind.key) : state.openLists.delete(kind.key)),
    },
        h("summary", {}, `All ${number(inKind.length)} groups, with times as in the editor`),
        h("table", { class: "rating-table" },
            h("thead", {}, h("tr", {}, ["Starts", "Taps", "After the previous"].map((text) => h("th", { scope: "col" }, text)))),
            h("tbody", {}, inKind.map(([start, , taps], i) => h("tr", {},
                h("td", {}, stamp(start)), h("td", {}, taps), h("td", {}, i ? beats(entry.gaps[i - 1]) : "–"))))));
}

function kindSection(data, kind) {
    const entry = data.kinds[kind.key];
    if (!entry) return h("section", { class: "tap-kind" }, h("h3", { style: `color:var(--k-${kind.key})` }, kind.label), h("p", { class: "note" }, `None in this map. All versions leave the value blank.`));
    return h("section", { class: "tap-kind" },
        h("h3", { style: `color:var(--k-${kind.key})` }, kind.label, h("small", {}, ` ${number(entry.count)} ${entry.count === 1 ? "group" : "groups"}, longest ${entry.longest} taps`)),
        h("table", { class: "tap-compare" }, h("tbody", {}, state.meta.versions.map((version) => {
            const value = entry.versions[version.key];
            return h("tr", { class: version.key === state.version ? "on" : null },
                h("td", {}, badge(version.key)),
                h("td", { class: "bar-cell" }, shareBar(value.shares, kind.key)),
                h("td", { class: "num", style: `color:var(--v-${version.key})` }, shown(value)),
            );
        }))),
        entry.gaps.length ? h("table", { class: "tap-bins" },
            h("caption", {}, "Distances between groups, in beats"),
            h("thead", {}, h("tr", {}, state.meta.bins_short.map((text) => h("th", { scope: "col" }, text)))),
            h("tbody", {}, h("tr", {}, entry.distances.map((n) => h("td", {}, number(n)))))) : h("p", { class: "note" }, "Only one group, so there are no distances between groups."),
        h("p", { class: "note" }, `Song start to first group: ${beats(entry.song_lead)} (used by A).`),
        h("p", { class: "note" }, `First tap to first group: ${beats(entry.first_lead)}. Last group to last tap: ${beats(entry.tail)} (used by C, D and E).`),
        groupsList(data, kind, entry),
    );
}

function renderDetail(row) {
    const sourceText = { lazer: "lazer's files", songs: "the Songs folder", cache: "an earlier download" }[row.source];
    $("#tap-detail").replaceChildren(
        h("div", { class: "cover" },
            row.set_id != null && h("img", { alt: "", referrerpolicy: "no-referrer", src: coverUrl(row.set_id, "cover"), onerror: hideBroken }),
            h("div", {},
                h("h2", {}, row.title_unicode || row.title),
                h("p", {}, `by ${row.artist_unicode || row.artist} · mapped by `, h("b", {}, row.creator ?? "?")),
            ),
        ),
        h("div", { class: "detail-body" },
            h("div", { class: "detail-title" }, starChip(row.stars), row.difficulty, statusPill(row.status)),
            h("p", { class: "note tap-facts" }, [`${stripZero(row.bpm)} bpm`, clock(row.length_s), MODE_TEXT[row.mode] || row.mode, sourceText && `.osu from ${sourceText}`,
                row.bpm_changes != null && `${row.bpm_changes} BPM ${row.bpm_changes === 1 ? "change" : "changes"}`].filter(Boolean).join(" · ")),
            row.beatmap_id != null && h("div", { class: "links" },
                h("a", { href: `https://osu.ppy.sh/beatmaps/${row.beatmap_id}`, target: "_blank", rel: "noreferrer" }, "beatmap page"),
                h("a", { href: `osu://b/${row.beatmap_id}` }, "open in osu!"),
            ),
            ...(row.data ? [bigTimeline(row.data), ...state.meta.kinds.map((kind) => kindSection(row.data, kind))] : [h("p", { class: "note" }, ignoredReason(row))]),
        ),
    );
}

function wire() {
    $("#tap-sort").addEventListener("change", (event) => {
        state.sort = event.target.value;
        state.direction = state.sort === "artist" || state.sort === "title" ? "asc" : "desc";
        syncControls();
        load();
    });
    $("#tap-dir").addEventListener("click", () => {
        state.direction = state.direction === "asc" ? "desc" : "asc";
        syncControls();
        load();
    });
    $("#tap-compare").addEventListener("change", (event) => {
        state.compare = event.target.value;
        syncControls();
        load();
    });
    $("#tap-detail").replaceChildren(h("div", { class: "empty-state" }, "Pick a map to see where its groups are and what each version gives it."));
}

export async function refresh(q) {
    state.q = q;
    // Shared, so that typing in the filter box while the tab first opens can't set it up twice.
    ready ??= api("/api/tapping/meta").then((meta) => {
        state.meta = meta;
        wire();
        syncControls();
    }).catch((error) => {
        ready = null;
        throw error;
    });
    await ready;
    await load();
}
