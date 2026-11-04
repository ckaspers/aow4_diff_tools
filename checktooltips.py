"""Tooltip links that lead nowhere new, on the website itself.

Opens a page of the website in Edge (Playwright), runs every text the site holds through the site's own link code
(AddTagIconsForStatusEffects) and follows each link the way its tooltip code does (HandleExtraTooltips): a concept
tooltip (Data/EN/concept_tooltips.json), an ability's (by its slug or name) or a unit's. A link is reported when
its tooltip is no news to the reader:

  self       the tooltip is the very thing the text belongs to: a concept whose text links to itself (the Curse of the
             Damned spell's link once showed the spell again), an ability naming itself
  same text  the tooltip says what the text it is in says, word for word
  repeats    the tooltip has the text's name and nearly its words (a spell's link to the spell's own description)

Texts: every text field of the data the site loads (the dumps, all.json, concept_tooltips.json and the site's own
Data/GEN files). A text shown in several places is checked once.

    python checktooltips.py [--site E:\\Repositories\\evolved] [--json report.json]

Needs Playwright and Edge (pip install playwright). Takes about a minute.
"""
import argparse, functools, http.server, json, os, sys, threading

from playwright.sync_api import sync_playwright

import aow4diff

# the data the site loads, as its globals (Data/src/dataloader.js), with the texts in them
DATASETS = [
    'jsonUnitAbilitiesLocalized', 'jsonSpellsLocalized', 'jsonHeroSkillsLocalized', 'jsonHeroItemsLocalized',
    'jsonSiegeProjectsLocalized', 'jsonFactionCreation2Localized', 'jsonTomesLocalized', 'jsonEmpireLocalized',
    'jsonStructureUpgradesLocalized', 'jsonHeroAmbitionsLocalized', 'jsonHeroGovernanceLocalized',
    'jsonItemForgeTypesLocalized', 'jsonItemForgeUpgradesLocalized', 'jsonUnitsLocalized', 'jsonAllFromPOLocalized',
    'jsonConceptTooltipsLocalized', 'jsonExtraTooltips', 'jsonLinkedAbilities', 'jsonSpellBook', 'jsonRelics',
    'jsonPantheon', 'jsonDestinyTriggers', 'jsonStatusEffects', 'jsonFreeCities', 'jsonExtraAscendedInfo',
    'jsonStructurePredictions',
]

HARNESS = r"""
(datasets) => {
    if (!abilityTagCache) buildAbilityTagCache();
    const NAME_FIELDS = ['name', 'title', 'loadout_name', 'screen_name'];
    const SKIP = /^(id|slug|resid|icon|image|globalName|displayHtml|name|title|type|DLC|lore|loadout_name|screen_name|.*_id|.*icon.*|.*image.*|.*link.*|.*slug.*)$/i;
    // the words of a text, markup and links taken out, as a reader sees them
    const words = (s) => String(s || '')
        .replace(/\[hyperlink,[^\]]*\]|\[\/hyperlink\]|\[\/?\w+\/?\]|<<m:|>>|<[^>]*>/g, ' ')
        .replace(/[^\p{L}\p{N}%+-]+/gu, ' ').trim().toLowerCase();
    const bag = (s) => new Set(words(s).split(' ').filter(Boolean));
    const similar = (a, b) => {
        const x = bag(a), y = bag(b);
        if (!x.size || !y.size) return 0;
        let both = 0;
        for (const w of x) if (y.has(w)) both++;
        return both / (x.size + y.size - both);
    };
    const plainName = (s) => words(String(s || '').split('^')[0]);

    // every text with what it belongs to
    const sources = [];
    const seen = new Set();
    function walk(node, dataset, owner, key, depth) {
        if (depth > 6 || node == null) return;
        if (Array.isArray(node)) { node.forEach((n) => walk(n, dataset, owner, key, depth + 1)); return; }
        if (typeof node !== 'object') return;
        const name = NAME_FIELDS.map((f) => node[f]).find((v) => typeof v === 'string' && v) ?? owner?.name;
        const ident = node.slug ?? node.resid ?? node.id ?? (dataset === 'jsonConceptTooltipsLocalized' ? key : null) ?? owner?.ident;
        const me = { name, ident: ident == null ? null : String(ident) };
        for (const [k, v] of Object.entries(node)) {
            if (typeof v === 'string') {
                if (SKIP.test(k) || v.length < 12 || !/\s/.test(v)) continue;
                const id = (me.name || '') + '\u0001' + v;
                if (seen.has(id)) continue;
                seen.add(id);
                sources.push({ dataset, field: k, name: me.name || '', ident: me.ident, concept: dataset === 'jsonConceptTooltipsLocalized' ? key : null, text: v });
            } else walk(v, dataset, me, k, depth + 1);
        }
    }
    for (const d of datasets) {
        const data = window[d];
        if (!data) continue;
        if (d === 'jsonConceptTooltipsLocalized') for (const [k, v] of Object.entries(data)) walk(v, d, null, k, 0);
        else walk(data, d, null, null, 0);
    }

    const abilityOf = (name, slug) => (slug && findBy(jsonUnitAbilitiesLocalized, 'slug', slug)) || findBy(jsonUnitAbilitiesLocalized, 'name', name);
    const found = [];
    const box = document.createElement('div');
    let links = 0, failed = 0;
    for (const s of sources) {
        let html;
        try { html = AddTagIconsForStatusEffects(s.text); } catch (e) { failed++; continue; }
        if (!html || typeof html !== 'string') continue;
        box.innerHTML = html;
        const targets = [];
        box.querySelectorAll('.conceptTooltipHandler').forEach((el) => {
            const key = el.dataset.global, c = jsonConceptTooltipsLocalized?.[key];
            if (c) targets.push({ kind: 'concept', id: key, name: c.name, text: c.description, words: el.previousElementSibling?.textContent });
        });
        box.querySelectorAll('.statusEffectHandler').forEach((el) => {
            const name = el.dataset.ability ?? el.textContent, a = abilityOf(name, el.dataset.slug);
            if (a) targets.push({ kind: 'ability', id: a.slug, name: a.name, text: a.description, words: el.textContent });
        });
        box.querySelectorAll('.unitTooltipHandler').forEach((el) => {
            const u = jsonUnits.find((x) => String(x.resid) === el.dataset.unit);
            if (u) targets.push({ kind: 'unit', id: String(u.resid), name: u.name, text: '', words: el.previousElementSibling?.textContent });
        });
        links += targets.length;
        const sw = words(s.text);
        for (const t of targets) {
            let why = null;
            if ((t.kind === 'concept' && s.concept === t.id) ||
                (t.kind === 'ability' && s.dataset === 'jsonUnitAbilitiesLocalized' && String(s.ident) === String(t.id)) ||
                (t.kind === 'unit' && s.dataset === 'jsonUnitsLocalized' && String(s.ident) === t.id)) why = 'self';
            else if (t.text && sw.length > 12 && words(t.text) === sw) why = 'same text';
            else if (t.text && plainName(t.name) && plainName(t.name) === plainName(s.name) && similar(t.text, s.text) >= 0.75) why = 'repeats';
            if (why) found.push({ why, kind: t.kind, target: t.id, targetName: String(t.name).split('^')[0], words: (t.words || '').trim(),
                dataset: s.dataset, field: s.field, name: String(s.name).split('^')[0], ident: s.ident, text: s.text.slice(0, 220) });
        }
    }
    return { sources: sources.length, links, failed, found };
}
"""


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--site', help='the website\'s folder (default: "site" in config.json)')
    ap.add_argument('--page', default='Search.html', help='the page to run on (it loads all the data)')
    ap.add_argument('--json', help='write the findings here too')
    args = ap.parse_args()
    site = os.path.abspath(args.site or aow4diff.load_config().get('site') or sys.exit('which website? pass --site'))
    # the site's paths start with /<its folder>/
    handler = functools.partial(Quiet, directory=os.path.dirname(site))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = 'http://127.0.0.1:%d/%s/HTML/%s' % (server.server_address[1], os.path.basename(site), args.page)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge')
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_function("() => window.jsonConceptTooltipsLocalized && window.jsonUnitAbilitiesLocalized && "
                               "window.jsonAllFromPOLocalized && typeof AddTagIconsForStatusEffects === 'function'",
                               timeout=60000)
        page.wait_for_timeout(3000)   # the dumps' links restored (RestoreDumpLinks)
        result = page.evaluate(HARNESS, DATASETS)
        browser.close()
    server.shutdown()

    found = result['found']
    print('%d texts, %d tooltip links followed%s' % (result['sources'], result['links'],
          ', %d texts the link code failed on' % result['failed'] if result['failed'] else ''))
    if errors: print('page errors:', '; '.join(errors[:5]))
    # one line per link target and finding, with where it is seen
    groups = {}
    for f in found:
        groups.setdefault((f['why'], f['kind'], f['target']), []).append(f)
    print('%d links whose tooltip is no news (%d targets)' % (len(found), len(groups)))
    for (why, kind, target), fs in sorted(groups.items(), key=lambda kv: (kv[0][0], -len(kv[1]), kv[0][2])):
        f = fs[0]
        where = sorted({'%s %s' % (x['dataset'].replace('json', '').replace('Localized', ''), x['name']) for x in fs})
        print('  [%s] %s %s "%s" (link "%s"), in %d: %s' % (why, kind, target, f['targetName'], f['words'], len(fs),
                                                            '; '.join(where[:4]) + (' ...' if len(where) > 4 else '')))
        print('      text: %s' % f['text'][:160].replace('\n', ' '))
    if args.json:
        json.dump(result, open(args.json, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
        print('->', args.json)


if __name__ == '__main__':
    main()
