// LANGUAGE REGISTRY — ADAPTED FROM xianslate
// SINGLE SOURCE OF TRUTH FOR LANGUAGE SELECTION, CODES, ENDONYMS, AND QUALITY TIERS.

export type Script =
	| 'han'
	| 'kana'
	| 'hangul'
	| 'latin'
	| 'cyrillic'
	| 'greek'
	| 'arabic'
	| 'hebrew'
	| 'devanagari'
	| 'bengali'
	| 'tamil'
	| 'thai';

export interface Language {
	code: string;
	name: string;
	endonym: string;
	script: Script;
	romanization: string | null;
	wordDelimited: boolean;
	tier: 1 | 2 | 3;
	rtl?: boolean;
}

export interface TargetOption {
	value: string;
	name: string;
	endonym: string;
	tier: 1 | 2 | 3;
	script: Script;
	rtl: boolean;
}

export const DEFAULT_SOURCE_LANG = 'zh-Hans';
export const DEFAULT_TARGET_LANG = 'en';
export const NO_TRANSLATION = 'none';

export const LANGUAGES: Record<string, Language> = {
	en: { code: 'en', name: 'English', endonym: 'English', script: 'latin', romanization: null, wordDelimited: true, tier: 1 },
	'zh-Hans': { code: 'zh-Hans', name: 'Simplified Chinese', endonym: '简体中文', script: 'han', romanization: 'Hanyu Pinyin with NO tone marks', wordDelimited: false, tier: 1 },
	'zh-Hant': { code: 'zh-Hant', name: 'Traditional Chinese', endonym: '繁體中文', script: 'han', romanization: 'Hanyu Pinyin with NO tone marks', wordDelimited: false, tier: 1 },
	ja: { code: 'ja', name: 'Japanese', endonym: '日本語', script: 'kana', romanization: 'Hepburn romaji', wordDelimited: false, tier: 2 },
	ko: { code: 'ko', name: 'Korean', endonym: '한국어', script: 'hangul', romanization: 'Revised Romanization of Korean', wordDelimited: true, tier: 2 },
	es: { code: 'es', name: 'Spanish', endonym: 'Español', script: 'latin', romanization: null, wordDelimited: true, tier: 2 },
	fr: { code: 'fr', name: 'French', endonym: 'Français', script: 'latin', romanization: null, wordDelimited: true, tier: 2 },
	de: { code: 'de', name: 'German', endonym: 'Deutsch', script: 'latin', romanization: null, wordDelimited: true, tier: 2 },
	pt: { code: 'pt', name: 'Portuguese', endonym: 'Português', script: 'latin', romanization: null, wordDelimited: true, tier: 2 },
	it: { code: 'it', name: 'Italian', endonym: 'Italiano', script: 'latin', romanization: null, wordDelimited: true, tier: 2 },
	ru: { code: 'ru', name: 'Russian', endonym: 'Русский', script: 'cyrillic', romanization: null, wordDelimited: true, tier: 2 },
	nl: { code: 'nl', name: 'Dutch', endonym: 'Nederlands', script: 'latin', romanization: null, wordDelimited: true, tier: 2 },
	pl: { code: 'pl', name: 'Polish', endonym: 'Polski', script: 'latin', romanization: null, wordDelimited: true, tier: 2 },
	ar: { code: 'ar', name: 'Arabic', endonym: 'العربية', script: 'arabic', romanization: null, wordDelimited: true, tier: 2, rtl: true },
	tr: { code: 'tr', name: 'Turkish', endonym: 'Türkçe', script: 'latin', romanization: null, wordDelimited: true, tier: 2 },
	vi: { code: 'vi', name: 'Vietnamese', endonym: 'Tiếng Việt', script: 'latin', romanization: null, wordDelimited: true, tier: 2 },
	id: { code: 'id', name: 'Indonesian', endonym: 'Bahasa Indonesia', script: 'latin', romanization: null, wordDelimited: true, tier: 2 },
	hi: { code: 'hi', name: 'Hindi', endonym: 'हिन्दी', script: 'devanagari', romanization: null, wordDelimited: true, tier: 2 },
	th: { code: 'th', name: 'Thai', endonym: 'ไทย', script: 'thai', romanization: null, wordDelimited: false, tier: 3 },
	sv: { code: 'sv', name: 'Swedish', endonym: 'Svenska', script: 'latin', romanization: null, wordDelimited: true, tier: 3 },
	fi: { code: 'fi', name: 'Finnish', endonym: 'Suomi', script: 'latin', romanization: null, wordDelimited: true, tier: 3 },
	uk: { code: 'uk', name: 'Ukrainian', endonym: 'Українська', script: 'cyrillic', romanization: null, wordDelimited: true, tier: 3 },
};

export const SOURCE_LANGUAGE_OPTIONS = [
	{ value: 'zh-Hans', label: '简体中文 (Simplified)' },
	{ value: 'zh-Hant', label: '繁體中文 (Traditional)' },
	{ value: 'ja', label: '日本語 (Japanese)' },
	{ value: 'ko', label: '한국어 (Korean)' },
];

export const TARGET_LANGUAGE_OPTIONS = [
	{ value: 'en', label: 'English' },
	{ value: 'zh-Hans', label: '简体中文 (Simplified)' },
	{ value: 'zh-Hant', label: '繁體中文 (Traditional)' },
	{ value: 'ja', label: '日本語 (Japanese)' },
	{ value: 'ko', label: '한국어 (Korean)' },
	{ value: 'es', label: 'Español (Spanish)' },
	{ value: 'fr', label: 'Français (French)' },
	{ value: 'de', label: 'Deutsch (German)' },
];

export function getLanguage(code: string | null | undefined): Language {
	return (code && LANGUAGES[code]) || LANGUAGES[DEFAULT_SOURCE_LANG];
}

export function languageName(code: string | null | undefined): string {
	if (code === NO_TRANSLATION) return 'Original';
	return getLanguage(code).name;
}

export function targetLanguageOptions(): TargetOption[] {
	return Object.values(LANGUAGES)
		.map((l) => ({
			value: l.code,
			name: l.name,
			endonym: l.endonym,
			tier: l.tier,
			script: l.script,
			rtl: !!l.rtl,
		}))
		.sort((a, b) => a.tier - b.tier || a.name.localeCompare(b.name));
}

const SCRIPT_RESIDUE: Record<Script, RegExp | null> = {
	han: /[㐀-䶿一-鿿豈-﫿]|[\u{20000}-\u{2a6df}]/u,
	kana: /[぀-ヿㇰ-ㇿ]/u,
	hangul: /[가-힣ᄀ-ᇿ㄰-㆏]/u,
	latin: null,
	cyrillic: null,
	greek: null,
	arabic: null,
	hebrew: null,
	devanagari: null,
	bengali: null,
	tamil: null,
	thai: null,
};

export function hasSourceResidue(text: string, sourceLang: string): boolean {
	const { script } = getLanguage(sourceLang);
	const re = SCRIPT_RESIDUE[script];
	if (re?.test(text)) return true;
	if (script === 'kana') return SCRIPT_RESIDUE.han!.test(text);
	return false;
}

export function leakRepairApplies(sourceLang: string): boolean {
	const { script } = getLanguage(sourceLang);
	return script === 'han' || script === 'kana' || script === 'hangul';
}
