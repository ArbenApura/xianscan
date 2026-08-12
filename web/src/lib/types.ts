// -- TYPES -- //

export type Gender = 'neuter' | 'masculine' | 'feminine';
export type GlossaryScope = 'global' | 'book';

// THE KIND OF ENTITY A GLOSSARY TERM NAMES — STRUCTURES THE EDITOR (GROUP / FILTER / COLOUR) AND HELPS
// EXTRACTION STAY CONSISTENT. NOT FED TO THE TRANSLATION PROMPT (THE target RENDERING ALREADY ENCODES IT).
export type TermCategory =
	| 'character'
	| 'location'
	| 'organization'
	| 'technique'
	| 'item'
	| 'realm'
	| 'creature'
	| 'title'
	| 'concept'
	| 'other';
export const TERM_CATEGORIES: TermCategory[] = [
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
];

// WHO CREATED / LAST CONFIRMED A TERM — 'ai' = AUTO-EXTRACTED (UNREVIEWED), 'user' = HUMAN-ADDED OR EDITED.
// A 'user' TERM IS AUTHORITATIVE AND NEVER DOWNGRADED BY EXTRACTION.
export type TermStatus = 'ai' | 'user';

/** A SOURCE/TARGET LANGUAGE PAIR (BCP-47-ISH CODES FROM $lib/languages) */
export interface LangPair {
	sourceLang: string;
	targetLang: string;
}

/** A GLOSSARY TERM AS EXTRACTED OR EDITED (BEFORE PERSISTENCE) */
export interface TermDraft {
	source: string;
	target: string;
	gender: Gender;
	// WHEN THE TERM WAS FIRST SAVED (MS EPOCH) — POPULATED WHEN READING FROM THE DB SO THE UI CAN FLAG
	// RECENTLY-DISCOVERED TERMS. UNSET ON FRESHLY-EXTRACTED DRAFTS THAT AREN'T PERSISTED YET.
	createdAt?: number;
	// A SHORT TRANSLATOR-FACING NOTE (WHO/WHAT THE TERM IS) — DISAMBIGUATES THE TERM DURING TRANSLATION
	context?: string | null;
	tags?: string | null;
	// WHAT KIND OF ENTITY THIS NAMES (character / location / technique / …). null = UNCATEGORISED.
	category?: TermCategory | null;
	// HIGH-PRIORITY: LISTED FIRST AND EMPHASISED IN THE TRANSLATION PROMPT, NEVER TRUNCATED.
	pinned?: boolean;
	// 'ai' (AUTO-EXTRACTED) vs 'user' (HUMAN-CONFIRMED). UNSET ON A FRESH DRAFT.
	status?: TermStatus;
	// ALTERNATE SOURCE-LANGUAGE FORMS OF THE SAME ENTITY (EPITHETS / SHORT FORMS) — ALL RENDER TO `target`
	// AND ALL MATCH IN A CHAPTER. EMPTY / null = NONE.
	aliases?: string[] | null;
	// THE chapters.id WHERE THIS TERM WAS FIRST EXTRACTED — ITS FIRST APPEARANCE. null FOR GLOBAL TERMS.
	firstChapterId?: number | null;
}

/** ONE PERSISTED GLOSSARY ROW AS THE EDITOR CONSUMES IT — aliases PARSED TO AN ARRAY, firstSeq RESOLVED */
export interface GlossaryRow {
	id: number;
	source: string;
	target: string;
	gender: Gender;
	context: string | null;
	tags: string | null;
	category: TermCategory | null;
	pinned: boolean;
	status: TermStatus;
	aliases: string[];
	firstChapterId: number | null;
	// THE seq OF THE first-appearance CHAPTER (RESOLVED VIA JOIN) — null FOR GLOBAL TERMS OR A DELETED CHAPTER.
	firstSeq: number | null;
	// THE first-appearance CHAPTER'S TITLE(S).
	firstChapterTitle: string | null;
	firstChapterTitleTarget: string | null;
	createdAt: number;
}

/** TOKEN USAGE + COST FOR ONE TRANSLATION CALL */
export interface TranslationUsage {
	model: string;
	promptTokens: number;
	cachedTokens: number;
	completionTokens: number;
	costUsd: number;
}
