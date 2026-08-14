// MANHUA PAGE TRANSLATION — ADAPTED FROM xianslate's translate.ts (SAME GLOSSARY-BLOCK + SYSTEM-MESSAGE
// PATTERN), BUT THE UNIT OF WORK IS A *PAGE OF REGIONS* INSTEAD OF A CHAPTER OF PARAGRAPHS: THE MODEL
// RECEIVES THE REGION LIST AS JSON AND RETURNS A {regionId: translation} OBJECT.
//
// PROMPT STRUCTURE (PINS THE FORMAT THE TESTS VERIFY):
//   [system]   manhua-localization system prompt (tone, SFX rules, no annotations)
//   [system]   glossary block (★source (also: a, b) = target [gender] — context) — ONLY WHEN TERMS EXIST
//   [user]     JSON region list {id, text, category} + output contract (raw JSON object, no fences)
//
// ROBUSTNESS: SALVAGE JSON PARSING, MISSING/EMPTY/DEGENERATE REGIONS GET ONE REFILL CALL, USAGE FROM
// THE API RESPONSE (computeUsage), ALL LLM CALLS GO THROUGH queued() + withRetry().
import type { LangPair, TermDraft, TranslationUsage } from '$lib/types';
import type OpenAI from 'openai';
import { languageName } from '$lib/languages';
// IMPORTED MODULES
import { computeUsage, deepseek, queued, resolveModel, thinkingParam, withRetry } from './deepseek';

// -- TYPES -- //

export type RegionCategory = 'dialogue' | 'sfx' | 'mono' | 'other';

export interface RegionSource {
	id: string;
	text: string;
	category: RegionCategory;
}

export interface PageTranslationOptions {
	/** INJECTABLE CLIENT — TESTS PASS A FAKE; DEFAULT IS THE PRODUCTION SINGLETON. */
	client?: OpenAI;
	model?: string;
	signal?: AbortSignal;
}

export interface PageTranslation {
	byRegion: Map<string, string>;
	usage: TranslationUsage;
}

// -- CONSTANTS -- //

// PART OF THE CACHE KEY — BUMP WHEN THE PROMPTS CHANGE SO STALE CACHED TRANSLATIONS NEVER RESURFACE.
export const PROMPT_VERSION = 'v7';

// A TRANSLATION LONGER THAN 6× THE SOURCE IS ALMOST CERTAINLY THE MODEL REWRITING THE PROMPT / ADDING
// EXPLANATIONS — FLAGGED FOR A REFILL (SAME HEURISTIC AS xianslate's looksOverExpanded).
const MAX_EXPANSION = 6;

// -- PROMPTS -- //

export function systemPrompt(src: string, tgt: string): string {
	const srcName = languageName(src);
	const tgtName = languageName(tgt);
	const srcLabel = srcName === src ? src : `${srcName} (${src})`;
	const tgtLabel = tgtName === tgt ? tgt : `${tgtName} (${tgt})`;
	return `You are a professional manhua (comic) localizer translating ${srcLabel} dialogue into natural, fluent, and immersive ${tgtLabel}.

Manhua Conversation & Dialogue Style:
- Conversational Flow: Write natural spoken dialogue as real characters speak in comics. Use natural contractions (I'm, don't, you're, can't, he'll, what's, let's) and lively phrasing suitable for voice acting. Avoid stiff, robotic, or overly literal translations.
- Tone & Character Personality: Reflect the speaker's personality, emotion, age, and social standing (e.g. arrogant young masters, wise masters, energetic protagonists, stern villains, flustered heroines, playful sidekicks).
- Wuxia / Xianxia / Cultivation Dialogue & Idioms:
  * Localize common Chinese idioms into punchy, idiomatic ${tgtName}:
    - 找死！ → "You're asking for death!" / "You have a death wish!"
    - 好大的胆子！ → "How dare you!" / "What insolence!"
    - 手下留情！ → "Show mercy!" / "Spare him!"
    - 雕虫小技！ → "Trifling tricks!" / "Child's play!"
    - 休想！ → "In your dreams!" / "Don't even think about it!"
    - 废话少说！ → "Cut the crap!" / "Save the talk!"
    - 多管闲事！ → "Mind your own business!" / "Meddlesome pest!"
    - 井底之蛙！ → "A frog in a well!"
  * Naturally preserve martial and kinship forms of address:
    - 师尊 / 师父 → Master / Shifu
    - 师兄 / 师姐 / 师弟 / 师妹 → Senior Brother / Senior Sister / Junior Brother / Junior Sister
    - 掌门 / 宗主 → Sect Leader / Patriarch
    - 前辈 / 阁下 → Senior / Your Excellency
    - 本座 / 本王 / 老子 / 本少 → This Seat / This King / I / Yours truly / This Young Master
    - 臭丫头 / 臭小子 → Brat / Little rascal
- Bubble Space & Brevity: Comic speech bubbles have limited space. Keep dialogue concise, punchy, and impactful without dropping meaning.
- Categories:
  * dialogue: Natural spoken conversation with character voice and emotion.
  * mono: Inner thoughts, reflections, or narration in a smooth, introspective tone.
  * sfx: Comic onomatopoeia in ALL-CAPS (e.g. 轰 → BOOM!, 呼 → WHOOSH!, 唰 → SWOOSH!, 砰 → THUD!, 咔嚓 → CRACK!, 哐当 → CLANG!).
  * other: UI, system prompts, stat cards, or narrator captions.

Punctuation & Reaction Bubbles:
- NEVER invent or use em-dashes (— or --) for pauses, thinking, or sentence breaks. Use natural commas (,), periods (.), or ellipses (...) matching the source punctuation.
- Character Speech & Reaction Bubbles: If a dialogue region contains ellipses, exclamation marks, question marks, or reaction punctuation (e.g. "……", "……！", "……？", "？！", "！", "？"), ALWAYS translate it to natural ${tgtName} punctuation (e.g. "...", "...!", "...?", "?!", "!", "?"). Do NOT return an empty string for character speech or pause bubbles!
- Spoken Pauses: Preserve ellipses in spoken pauses (e.g. "你……是李婉儿，" → "You... are Li Wan'er,").
- Only output a dash if the original source text explicitly contains a dash/hyphen.

Watermark & Scanlation Tag Filtering:
- Piracy Watermarks & Aggregator Ads: If a text region is a third-party pirate watermark, scanlation group recruitment ad, website URL/domain, aggregator watermark, scanlation QQ/Discord group, or uploader logo (e.g. BaoziManhua, Colamanga, Qumanku, 速漫库, 包子漫画, "扫图", "汉化组招募", "严禁转载", "独家", "修图", "首发", etc.), return an EMPTY STRING "" for its id.
- Official Comic Staff & Production Credits: ALWAYS TRANSLATE official manga/manhua author, artist, and studio production credits (e.g. STAFF, 原作 [Original Work], 主笔 [Main Artist], 助手 [Assistants], 承制 [Production], 分镜 [Storyboard], 线稿 [Line Art], 总监制 [Executive Producer], 监制 [Supervisor], 上色 [Coloring], 出品 [Presented by], 制作 [Production], etc.).
- If a dialogue bubble contains legitimate character speech mixed with a trailing watermark or website URL, translate ONLY the dialogue portion and omit the watermark entirely.

Comic Titles & Chapter Headers:
- For comic title and chapter banner regions, be resilient to common Chinese calligraphy OCR misrecognitions (e.g. 快神记 → 妖神记 / Tales of Demons and Gods). Translate the true canonical title/chapter text accurately.

General Rules:
- Never add narration, explanations, or stage directions outside the text itself.
- Never translate glossary terms differently from the glossary block.
- Preserve names exactly as the glossary says.

Wuxia/manhua stat-panel and item-card rules (apply when the text has 【】title brackets or a rarity grade):
- Title lines enclosed in 【】brackets → output as [${tgtName.toUpperCase()} TITLE IN CAPS] (first line, keep the square brackets). Example: 【铁滑车】→ [IRON CHARIOT]. ONLY add [brackets] when the SOURCE text has 【】 — do NOT add brackets to items that start directly with a rarity grade word.
- Rarity-grade items WITHOUT 【】 (e.g. 神话级火箭铁滑车): output the rarity+name as the FIRST line with no brackets. Example: 神话级火箭铁滑车 → MYTHIC ROCKET IRON CHARIOT (no brackets, first line).
- Translate vehicle/weapon names accurately: 滑车 = chariot (war vehicle), not sledge or cart; 战刀 = battle saber; 弩 = crossbow, etc.
- Rarity grade words (传说级, 史诗级, 稀有级, 精良级, 普通级, 神话级, etc.) → translate as LEGENDARY, EPIC, RARE, FINE, COMMON, MYTHIC etc. Keep fused with item type on same line.
- Parenthetical qualifiers （改良版）, （强化版）, （威力加强版）etc. → translate as (IMPROVED VERSION), (ENHANCED VERSION), (POWER-ENHANCED VERSION) etc., on their OWN line immediately after the rarity+type or title line. Always keep the () parentheses in the output.
- Body/description paragraphs: use natural sentence case (not all-caps). Punctuate naturally.
- Preserve all \\n line breaks from the source text in the translation so the panel layout is maintained.
- Flavour remarks starting with * (e.g. *食我压路机哒！) → keep the * prefix, translate in the character's voice.

Reply with ONLY a JSON object; no markdown fences, no commentary.`;
}

// PORTED FROM xianslate — PINNED TERMS FIRST, ALIASES, GENDER, CONTEXT NOTE. INJECTED AS A SEPARATE
// SYSTEM MESSAGE BETWEEN THE SYSTEM PROMPT AND THE USER MESSAGE (NOT INSIDE THE PROMPT STRING).
export function glossaryBlock(terms: TermDraft[], src: string, tgt: string): string | null {
	if (terms.length === 0) return null;
	const srcName = languageName(src);
	const tgtName = languageName(tgt);
	const lines = [...terms]
		// NaN-PROOF: A MISSING pinned (undefined) MUST SORT AS false — NEVER NaN (V8 TREATS NaN AS "EQUAL")
		.sort((a, b) => Number(b.pinned ?? false) - Number(a.pinned ?? false))
		.map((t) => {
			const aliases = t.aliases && t.aliases.length > 0 ? ` (also: ${t.aliases.join(', ')})` : '';
			const gender = t.gender === 'masculine' ? ' [masculine]' : t.gender === 'feminine' ? ' [feminine]' : '';
			const context = t.context ? ` — ${t.context}` : '';
			return `★${t.source}${aliases} = ${t.target}${gender}${context}`;
		});
	return `Glossary (${srcName} → ${tgtName}) — use these exact renderings for the listed terms, even when the context would suggest otherwise:
${lines.join('\n')}`;
}

export function regionPayload(regions: RegionSource[]): string {
	return JSON.stringify(
		regions.map((r) => ({ id: r.id, text: r.text, category: r.category })),
		null,
		1,
	);
}

export function userPrompt(regions: RegionSource[]): string {
	return `Translate the following regions of a manhua page. Each entry has an id, the source text, and its category (dialogue / sfx / mono / other).

${regionPayload(regions)}

Return a JSON object mapping each id to its ${'translation'}, e.g. {"r0": "Hello", "r1": "BOOM!"}. Every id must appear exactly once. No markdown fences.`;
}

export function buildMessages(regions: RegionSource[], terms: TermDraft[], pair: LangPair): OpenAI.Chat.ChatCompletionMessageParam[] {
	const messages: OpenAI.Chat.ChatCompletionMessageParam[] = [
		{ role: 'system', content: systemPrompt(pair.sourceLang, pair.targetLang) },
	];
	const glossary = glossaryBlock(terms, pair.sourceLang, pair.targetLang);
	if (glossary) messages.push({ role: 'system', content: glossary });
	messages.push({ role: 'user', content: userPrompt(regions) });
	return messages;
}

// -- PARSING -- //

// SALVAGE-PARSE THE MODEL'S JSON OBJECT (PORTED PATTERN FROM xianslate's parseTermObjects): STRIP CODE
// FENCES, THEN EITHER PARSE THE WHOLE OBJECT OR SALVAGE `{"id": "text"}` FRAGMENTS. NULL WHEN NOTHING
// USABLE SURVIVES. PURE — UNIT-TESTED.
export function parseTranslations(raw: string, knownIds: Set<string>): Map<string, string> | null {
	const cleaned = raw
		.replace(/```(?:json)?/gi, '')
		.trim()
		.replace(/^\{/, '')
		.replace(/\}$/, '');
	const out = new Map<string, string>();
	for (const m of cleaned.matchAll(/"([A-Za-z0-9_-]+)"\s*:\s*"((?:[^"\\]|\\.)*)"/g)) {
		const id = m[1];
		if (!knownIds.has(id)) continue;
		const text = m[2]
			.replace(/\\n/g, '\n') // PRESERVE PARAGRAPH LINE BREAKS (MULTI-LINE BUBBLES)
			.replace(/\\"/g, '"')
			.replace(/\\\\/g, '\\')
			.trim();
		if (text) out.set(id, text);
	}
	return out.size > 0 ? out : null;
}

// DEGENERATE = EMPTY, OR EXPANDED > 6× THE SOURCE (THE MODEL EXPLAINED INSTEAD OF TRANSLATING).
export function looksDegenerate(translated: string, source: string): boolean {
	if (!translated || !source) return true;
	return translated.length > source.length * MAX_EXPANSION;
}

// -- CORE -- //

async function callTranslate(
	regions: RegionSource[],
	terms: TermDraft[],
	pair: LangPair,
	opts: PageTranslationOptions,
): Promise<{ raw: string; usage: TranslationUsage }> {
	const client = opts.client ?? deepseek;
	const model = resolveModel(opts.model);
	const messages = buildMessages(regions, terms, pair);
	// ~2 TOKENS PER SOURCE CHAR + ROOM FOR THE JSON ENVELOPE — THE SOURCE TEXT DRIVES THE BUDGET
	const sourceChars = regions.reduce((n, r) => n + r.text.length, 0);
	const maxTokens = Math.max(256, Math.ceil(sourceChars * 2 + 256));
	const resp = await queued(() =>
		withRetry(async () => {
			const r = await client.chat.completions.create(
				{
					model,
					messages,
					temperature: 0.3,
					max_tokens: maxTokens,
					...thinkingParam(),
				},
				{ signal: opts.signal },
			);
			return r;
		}),
	);
	const raw = resp.choices[0]?.message?.content ?? '';
	const usage = computeUsage(resp.usage, model);
	return { raw, usage };
}

// TRANSLATE ONE PAGE'S REGIONS. MISSING/DEGENERATE REGIONS GET ONE REFILL CALL WITH JUST THOSE.
export async function translatePage(
	regions: RegionSource[],
	terms: TermDraft[],
	pair: LangPair,
	opts: PageTranslationOptions = {},
): Promise<PageTranslation> {
	const model = resolveModel(opts.model);
	const usage = { model, promptTokens: 0, cachedTokens: 0, completionTokens: 0, costUsd: 0 } as TranslationUsage;

	if (regions.length === 0) return { byRegion: new Map(), usage };

	// FIRST PASS — THE FULL REGION LIST
	const { raw, usage: u1 } = await callTranslate(regions, terms, pair, opts);
	mergeUsage(usage, u1);
	const byRegion = parseTranslations(raw, new Set(regions.map((r) => r.id))) ?? new Map();

	// REFILL — REGIONS THAT CAME BACK EMPTY / MISSING / DEGENERATE GET ONE TARGETED CALL
	const missing = regions.filter((r) => {
		const t = byRegion.get(r.id);
		return !t || looksDegenerate(t, r.text);
	});
	if (missing.length > 0) {
		const { raw: raw2, usage: u2 } = await callTranslate(missing, terms, pair, opts);
		mergeUsage(usage, u2);
		const refill = parseTranslations(raw2, new Set(missing.map((r) => r.id))) ?? new Map();
		for (const r of missing) {
			const t = refill.get(r.id);
			if (t && !looksDegenerate(t, r.text)) byRegion.set(r.id, t);
		}
	}

	return { byRegion, usage };
}

function mergeUsage(acc: TranslationUsage, u: TranslationUsage): void {
	acc.promptTokens += u.promptTokens;
	acc.cachedTokens += u.cachedTokens;
	acc.completionTokens += u.completionTokens;
	acc.costUsd += u.costUsd;
}

// -- AI TERM EXTRACTION -- //

import { chunkForExtraction } from './glossary';

export const MAX_CONTEXT_TERMS = 100;

export function extractionSystemPrompt(src: string, tgt: string): string {
	const srcName = languageName(src);
	const tgtName = languageName(tgt);
	const srcLabel = srcName === src ? src : `${srcName} (${src})`;
	const tgtLabel = tgtName === tgt ? tgt : `${tgtName} (${tgt})`;
	return `You build a translation glossary from a ${srcLabel} manhua (comic) chapter so that names, titles, and techniques stay 100% consistent across all pages.

Return ONLY a JSON object of exactly this shape — no markdown fences, no comments, no extra text:
{"terms":[{"source":"<exact ${srcName} characters verbatim from text>","target":"<natural ${tgtName} translation>","category":"character|location|organization|technique|item|realm|creature|title|concept|other","gender":"neuter|masculine|feminine","aliases":["<other ${srcName} forms/nicknames>"],"pinned":false,"context":"<brief description>"}]}

Rules:
1. "source": MUST be copied EXACTLY as it appears in the text (identical characters, no added or removed spaces) so exact string match will find it on every page.
2. "target":
   - Personal character names: Romanize into Pinyin / Latin script with Title Case (e.g. 叶凡 → Ye Fan; 李澈 → Li Che). Space-separate family and given names.
   - Place names: Romanize the proper name and translate the place type (e.g. 雲霄村 → Yunxiao Village; 紫山 → Purple Mountain).
   - Descriptive terms (techniques, martial arts, cultivation realms, weapons, items, artifacts): Translate by MEANING into natural ${tgtName} (e.g. 斩龙剑 → Dragon Slaying Sword; 筑基期 → Foundation Establishment).
3. "category": 'character', 'location', 'organization', 'technique', 'item', 'realm', 'creature', 'title', 'concept', 'other'.
4. "gender": 'masculine' or 'feminine' ONLY when the text explicitly indicates it (pronouns, titles like master/sister/brother/prince); otherwise 'neuter'.
5. "aliases": Array of other short forms, nicknames, or forms of address in the text (e.g. for 叶凡, aliases: ["小凡", "凡儿"]).
6. Skip generic words, common dialogue phrases, numbers, and pronouns. Focus on recurring proper nouns, character names, and unique terminology.`;
}

// ROBUST PARSE: ACCEPT CLEAN JSON, A BARE ARRAY, OR A TRUNCATED RESPONSE (SALVAGE COMPLETE {…} OBJECTS)
// SO A SLIGHTLY MALFORMED / CUT-OFF REPLY STILL YIELDS THE TERMS IT DID CONTAIN INSTEAD OF ZERO.
export function parseTermObjects(text: string): unknown[] {
	const tryParse = (s: string): unknown => {
		try {
			return JSON.parse(s);
		} catch {
			return undefined;
		}
	};
	const cleaned = text.replace(/```(?:json)?/gi, '').trim();
	const whole = tryParse(cleaned);
	if (Array.isArray(whole)) return whole;
	const terms = (whole as { terms?: unknown })?.terms;
	if (Array.isArray(terms)) return terms;
	// SALVAGE EVERY COMPLETE {…} OBJECT (HANDLES A TRUNCATED ARRAY THAT NEVER GOT ITS CLOSING `]`)
	const objs: unknown[] = [];
	const re = /\{[^{}]*\}/g;
	let m: RegExpExecArray | null;
	while ((m = re.exec(cleaned))) {
		const o = tryParse(m[0]);
		if (o) objs.push(o);
	}
	return objs;
}

export function parseExtractedTerms(raw: string, contentSource?: string): TermDraft[] {
	const validCategories = new Set([
		'character',
		'location',
		'organization',
		'technique',
		'item',
		'realm',
		'creature',
		'title',
		'concept',
		'other',
	]);
	const validGenders = new Set(['neuter', 'masculine', 'feminine']);
	const rawObjects = parseTermObjects(raw);
	const results: TermDraft[] = [];
	const seen = new Set<string>();

	for (const item of rawObjects) {
		const t = item as {
			source?: unknown;
			target?: unknown;
			gender?: unknown;
			context?: unknown;
			category?: unknown;
			aliases?: unknown;
			pinned?: unknown;
		};
		const sourceTerm = String(t?.source ?? '').trim();
		const target = String(t?.target ?? '').trim();
		if (!sourceTerm || !target) continue;

		// VERBATIM VERIFICATION: If contentSource is supplied, only keep terms whose source
		// actually appears in the OCR text (prevents hallucinations).
		if (contentSource && !contentSource.includes(sourceTerm)) continue;

		if (seen.has(sourceTerm)) continue;
		seen.add(sourceTerm);

		const category = validCategories.has(String(t?.category))
			? (String(t?.category) as TermDraft['category'])
			: 'other';

		const gender =
			category && category !== 'character'
				? 'neuter'
				: validGenders.has(String(t?.gender))
					? (String(t?.gender) as TermDraft['gender'])
					: 'neuter';

		const context = typeof t?.context === 'string' && t.context.trim() ? t.context.trim() : null;
		const pinned = t?.pinned === true;

		// ALIASES: Other source forms that appear in contentSource
		const aliases = Array.isArray(t?.aliases)
			? [
					...new Set(
						(t.aliases as unknown[])
							.map((a) => String(a ?? '').trim())
							.filter((a) => a && a !== sourceTerm && (!contentSource || contentSource.includes(a))),
					),
				].slice(0, 8)
			: [];

		results.push({
			source: sourceTerm,
			target,
			category,
			gender,
			context,
			pinned,
			aliases: aliases.length ? aliases : null,
			status: 'ai' as const,
		});
	}

	return results;
}

export async function extractTerms(
	content: string,
	pair: LangPair,
	opts: PageTranslationOptions & { knownTerms?: TermDraft[] } = {},
): Promise<{ terms: TermDraft[]; usage: TranslationUsage }> {
	const client = opts.client ?? deepseek;
	const model = resolveModel(opts.model);
	const usage = { model, promptTokens: 0, cachedTokens: 0, completionTokens: 0, costUsd: 0 } as TranslationUsage;

	if (!content.trim()) return { terms: [], usage };

	// Chunk content if text is long (split by lines/bubbles)
	const chunks = chunkForExtraction(content);
	const bySource = new Map<string, TermDraft>();

	for (let i = 0; i < chunks.length; i++) {
		if (opts.signal?.aborted) throw Object.assign(new Error('Extraction aborted'), { name: 'AbortError' });
		const chunk = chunks[i];

		// Cumulative context: DB existing terms + terms extracted from earlier chunks of this chapter
		const established = new Map<string, TermDraft>();
		for (const t of bySource.values()) established.set(t.source, t);
		for (const t of opts.knownTerms ?? []) established.set(t.source, t);

		const all = [...established.values()].filter((t) => t.source);
		const inChunk = (t: TermDraft) => chunk.includes(t.source);
		const cmpSource = (a: TermDraft, b: TermDraft) => a.source.localeCompare(b.source);
		const ctx =
			all.length <= MAX_CONTEXT_TERMS
				? [...all.filter((t) => t.pinned).sort(cmpSource), ...all.filter((t) => !t.pinned).sort(cmpSource)]
				: [
						...all.filter((t) => t.pinned),
						...all.filter((t) => !t.pinned && inChunk(t)),
						...all.filter((t) => !t.pinned && !inChunk(t)),
					].slice(0, MAX_CONTEXT_TERMS);

		const messages: OpenAI.Chat.ChatCompletionMessageParam[] = [
			{ role: 'system', content: extractionSystemPrompt(pair.sourceLang, pair.targetLang) },
		];

		if (ctx.length > 0) {
			messages.push({
				role: 'system',
				content:
					`ESTABLISHED GLOSSARY (already known — reuse each EXACT ${pair.targetLang} rendering everywhere you name these entities and never contradict one):\n` +
					ctx.map((t) => `${t.source} = ${t.target}`).join('\n'),
			});
		}

		messages.push({ role: 'user', content: `Extract terms from the following chapter text passage:\n\n${chunk}` });

		try {
			const resp = await queued(() =>
				withRetry(async () => {
					return await client.chat.completions.create(
						{
							model,
							messages,
							temperature: 0,
							max_tokens: 4096,
							...thinkingParam(),
						},
						{ signal: opts.signal },
					);
				}),
			);

			const raw = resp.choices[0]?.message?.content ?? '';
			const u = computeUsage(resp.usage, model);
			mergeUsage(usage, u);

			const extracted = parseExtractedTerms(raw, content);
			for (const term of extracted) {
				if (!bySource.has(term.source)) {
					bySource.set(term.source, term);
				}
			}
		} catch (err) {
			if (opts.signal?.aborted) throw err;
			// Chunk-local failure: continue with already extracted terms
		}
	}

	return { terms: [...bySource.values()], usage };
}

// TRANSLATE A SINGLE STRING (BOOK TITLE, CHAPTER TITLE, TERM, ETC.)
export async function translateSingleText(
	text: string,
	pair: LangPair,
	opts: {
		kind?: 'title' | 'chapter' | 'term' | 'general';
		client?: OpenAI;
		model?: string;
		signal?: AbortSignal;
	} = {},
): Promise<{ text: string; usage: TranslationUsage }> {
	const trimmed = text.trim();
	const model = resolveModel(opts.model);
	const usage = { model, promptTokens: 0, cachedTokens: 0, completionTokens: 0, costUsd: 0 } as TranslationUsage;

	if (!trimmed) {
		return { text: '', usage };
	}

	const client = opts.client ?? deepseek;
	const srcName = languageName(pair.sourceLang);
	const tgtName = languageName(pair.targetLang);

	let systemContent = '';
	if (opts.kind === 'chapter') {
		systemContent = `You are a professional comic and novel localizer. Translate the provided chapter title from ${srcName} to ${tgtName}.
Rules:
- Translate concisely and naturally (e.g. "第1话 重生" -> "Chapter 1: Rebirth", "第12话" -> "Chapter 12", "第30章 突破" -> "Chapter 30: Breakthrough").
- Maintain chapter numbering in English/Target language (Chapter X or Ch. X: Subtitle).
- Do NOT output commentary, quotes, explanations, or markdown fences. Output ONLY the translated chapter title string.`;
	} else if (opts.kind === 'title') {
		systemContent = `You are a professional comic and novel localizer. Translate the provided book/series title from ${srcName} to ${tgtName}.
Rules:
- Translate concisely into natural title case (e.g. "妖神记" -> "Tales of Demons and Gods", "斗破苍穹" -> "Battle Through the Heavens").
- Do NOT output commentary, explanations, notes, quotes, or markdown fences. Output ONLY the translated title string.`;
	} else if (opts.kind === 'term') {
		systemContent = `You are a professional localization translator. Translate the provided term, character name, technique, or proper noun from ${srcName} to natural ${tgtName}.
Rules:
- For personal names, use capitalized romanization / Pinyin with proper spacing (e.g. 叶凡 -> Ye Fan).
- For terms / items / techniques, translate the meaning into natural ${tgtName}.
- Output ONLY the translated term without quotes or explanation.`;
	} else {
		systemContent = `You are a professional translator translating text from ${srcName} to natural ${tgtName}.
Output ONLY the translated text without commentary, quotes, or markdown fences.`;
	}

	try {
		const res = await queued(async () =>
			withRetry(
				() =>
					client.chat.completions.create(
						{
							model,
							messages: [
								{ role: 'system', content: systemContent },
								{ role: 'user', content: trimmed },
							],
							temperature: 0.2,
							...thinkingParam(),
						},
						{ signal: opts.signal },
					),
				3,
			),
		);

		const u = computeUsage(res.usage, model);
		mergeUsage(usage, u);

		let out = res.choices[0]?.message?.content?.trim() || '';
		// Strip outer quotes if returned
		if (
			(out.startsWith('"') && out.endsWith('"')) ||
			(out.startsWith('“') && out.endsWith('”')) ||
			(out.startsWith('\'') && out.endsWith('\'')) ||
			(out.startsWith('「') && out.endsWith('」'))
		) {
			out = out.slice(1, -1).trim();
		}
		return { text: out || trimmed, usage };
	} catch (err) {
		// Fallback regex for chapter patterns if LLM fails or is unconfigured
		if (opts.kind === 'chapter') {
			const match = trimmed.match(/^第?\s*(\d+)\s*(?:话|章|回|集)?(?:\s*[:：\-—]\s*(.*))?$/);
			if (match) {
				const num = match[1];
				const rest = match[2]?.trim();
				return { text: rest ? `Chapter ${num}: ${rest}` : `Chapter ${num}`, usage };
			}
		}
		throw err;
	}
}

